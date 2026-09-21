#!/usr/bin/env python3
"""
Interface Flask pour dcma14.py et montecarlo.py.

Les scripts existants ne sont pas modifiés : ils sont appelés en subprocess.
Chaque analyse démarre un processus Python/JVM isolé. Le premier appel peut
être plus lent à cause du démarrage de la JVM.

Limitation d'usage anonyme : chaque adresse IP dispose d'un quota d'analyses
par jour calendaire (DAILY_ANALYSIS_LIMIT, défaut 5), compté dans un fichier
SQLite partagé par les workers Gunicorn. Le nombre d'analyses lourdes menées
de front est plafonné par MAX_CONCURRENT_ANALYSES.

POINT D'EXTENSION AUTH :
Ajouter flask-httpauth ou Flask-BasicAuth, puis protéger index() et analyse().
Le healthcheck /healthz peut rester public pour le reverse proxy.
Aucune authentification n'est intégrée : voir la section « Sécurité » du
README avant toute exposition sur Internet.
"""

import base64
import csv
import io
import logging
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import xlsxwriter
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DCMA_SCRIPT = BASE_DIR / "dcma14.py"
MONTECARLO_SCRIPT = BASE_DIR / "montecarlo.py"

app = Flask(__name__)

# Les analyses journalisent leur durée et leur pic mémoire : ces lignes sont
# utiles pour dimensionner les limites, elles restent en niveau INFO.
app.logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

# Taille maximale acceptée pour un planning. Chaque analyse démarre une JVM
# dont la mémoire est bornée (JAVA_TOOL_OPTIONS, voir Dockerfile) : au-delà de
# cette taille, le fichier est refusé avec un message explicite, plutôt que de
# laisser l'analyse échouer faute de mémoire.
MAX_UPLOAD_MB = max(int(os.environ.get("MAX_UPLOAD_MB", "5")), 1)

# Garde-fou absolu de Flask, volontairement plus large que la limite ci-dessus :
# si Flask interrompt la requête en pleine réception, la connexion est coupée et
# le visiteur voit une erreur réseau au lieu d'un message. Le refus « propre »
# est donc prononcé dans analyse(), la limite publique restant MAX_UPLOAD_MB.
HARD_UPLOAD_MB = max(MAX_UPLOAD_MB * 4, 30)
app.config["MAX_CONTENT_LENGTH"] = HARD_UPLOAD_MB * 1024 * 1024

TIMEOUT_SECONDS = int(os.environ.get("ANALYSIS_TIMEOUT", "900"))

# Un seul reverse proxy (NPMplus) est placé devant l'application : x_for=1 fait
# lire l'IP réelle du visiteur dans l'en-tête X-Forwarded-For.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Quota d'analyses par adresse IP et par jour calendaire.
DAILY_ANALYSIS_LIMIT = int(os.environ.get("DAILY_ANALYSIS_LIMIT", "5"))

# Nombre d'analyses lourdes menées de front. Chaque analyse démarre une JVM
# complète : au-delà de 2, la machine sature avant d'être utile. La valeur est
# partagée par tous les workers et threads via SQLite.
MAX_CONCURRENT_ANALYSES = max(int(os.environ.get("MAX_CONCURRENT_ANALYSES", "2")), 1)

# Exports facultatifs demandés au dépôt du formulaire, en plus de la page de
# résultat qui est toujours rendue. Le fichier est produit dans la même requête
# et transmis sous forme de lien de téléchargement autonome (data:), sans
# aucune écriture côté serveur.
EXPORT_FORMATS = {
    "csv": ("Fichier CSV", "text/csv", "csv"),
    "xlsx": ("Classeur Excel",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "xlsx"),
}

# Chemin du compteur d'usage dans l'image. USAGE_DB_PATH n'est utile que pour
# lancer l'application hors conteneur (tests locaux).
DEFAULT_USAGE_DB = "/app/data/usage.db"

# Ligne parasite émise par la JVM embarquée avant le vrai début de la sortie
# des scripts. Filtrée dans run_script() : dcma14.py et montecarlo.py ne sont
# pas modifiables.
LOG4J_NOISE = re.compile(
    r"^.*ERROR Log4j API could not find a logging provider\.\s*\n?",
    re.MULTILINE,
)


class AnalysisError(RuntimeError):
    pass


class AnalysisCancelled(AnalysisError):
    """Analyse interrompue à la demande du visiteur."""


def usage_db_path():
    return Path(os.environ.get("USAGE_DB_PATH", DEFAULT_USAGE_DB))


def usage_connect():
    return sqlite3.connect(str(usage_db_path()), timeout=10)


def init_usage_db():
    """Crée le fichier et les tables de comptage s'ils sont absents (idempotent).

    - usage   : quota quotidien par adresse IP ;
    - running : créneaux d'analyse en cours, partagés par tous les workers.
    """
    try:
        usage_db_path().parent.mkdir(parents=True, exist_ok=True)
        with closing(usage_connect()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS usage ("
                "ip TEXT NOT NULL, "
                "date TEXT NOT NULL, "
                "count INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (ip, date))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS running ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ip TEXT NOT NULL, "
                "started_at TEXT NOT NULL, "
                "token TEXT, "
                "cancelled INTEGER NOT NULL DEFAULT 0)"
            )
            # Bases créées par une version antérieure : ajout des colonnes
            # nécessaires à l'annulation (sans effet si elles existent déjà).
            for colonne, definition in (("token", "TEXT"), ("cancelled", "INTEGER NOT NULL DEFAULT 0")):
                try:
                    conn.execute(f"ALTER TABLE running ADD COLUMN {colonne} {definition}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
    except (sqlite3.Error, OSError):
        app.logger.exception(
            "Initialisation du compteur d'usage impossible (%s)", usage_db_path()
        )


def client_ip():
    # ProxyFix a déjà remplacé remote_addr par l'IP réelle du visiteur.
    return (request.remote_addr or "").strip() or "inconnu"


def today_key():
    return date.today().isoformat()


def quota_remaining(ip):
    """Analyses restantes aujourd'hui pour l'IP, sans consommer de quota.

    Retourne None si le compteur est illisible : l'appelant laisse alors passer
    l'analyse (la disponibilité prime sur la limitation d'usage).
    """
    try:
        with closing(usage_connect()) as conn:
            row = conn.execute(
                "SELECT count FROM usage WHERE ip = ? AND date = ?",
                (ip, today_key()),
            ).fetchone()
    except (sqlite3.Error, OSError):
        app.logger.exception("Lecture du compteur d'usage impossible")
        return None

    used = int(row[0]) if row else 0
    return max(DAILY_ANALYSIS_LIMIT - used, 0)


def consume_quota(ip):
    """Incrémente le compteur du jour avant de lancer une analyse.

    Retourne True si le quota a été consommé, False s'il est épuisé. L'incrément
    est conditionnel dans une seule requête SQL : le comptage reste cohérent
    entre les 2 workers Gunicorn, contrairement à un compteur en mémoire.
    """
    try:
        with closing(usage_connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO usage (ip, date, count) VALUES (?, ?, 1) "
                "ON CONFLICT(ip, date) DO UPDATE SET count = count + 1 "
                "WHERE usage.count < ?",
                (ip, today_key(), DAILY_ANALYSIS_LIMIT),
            )
            conn.commit()
            return cursor.rowcount > 0
    except (sqlite3.Error, OSError):
        app.logger.exception("Mise à jour du compteur d'usage impossible")
        return True


def too_large_message(taille_octets=None):
    taille = ""
    if taille_octets:
        taille = f" ({taille_octets / 1024 / 1024:.1f} Mo reçus)"
    memoire = configured_heap_mb()
    allocation = f" La mémoire allouée à l'analyse est de {memoire} Mo." if memoire else ""
    return (
        f"Le fichier dépasse la taille maximale autorisée{taille} : "
        f"{MAX_UPLOAD_MB} Mo. Les plannings volumineux demandent beaucoup de "
        f"mémoire pour être lus.{allocation} Exportez un extrait du planning "
        "(moins de tâches, ou un périmètre réduit) puis relancez l'analyse."
    )


def quota_exceeded_message():
    return (
        f"Vous avez atteint la limite de {DAILY_ANALYSIS_LIMIT} analyses par jour "
        "pour votre adresse IP. Le quota se réinitialise à minuit."
    )


def acquire_analysis_slot(ip, token=None):
    """Réserve un créneau d'analyse lourde (MAX_CONCURRENT_ANALYSES au total).

    Retourne l'identifiant du créneau, 0 si le compteur est indisponible (on
    laisse alors passer l'analyse sans protection : la disponibilité du service
    prime), ou None si tous les créneaux sont occupés.

    L'insertion conditionnelle est atomique : le comptage reste juste entre les
    workers Gunicorn et leurs threads, contrairement à un sémaphore en mémoire.
    Le jeton permet au visiteur d'annuler son analyse depuis la page d'attente.
    """
    started = datetime.now(timezone.utc)
    stale_before = (started - timedelta(seconds=TIMEOUT_SECONDS + 120)).isoformat()
    try:
        with closing(usage_connect()) as conn:
            # Créneaux orphelins (worker tué en pleine analyse) : purgés pour ne
            # pas bloquer le service définitivement.
            conn.execute("DELETE FROM running WHERE started_at < ?", (stale_before,))
            cursor = conn.execute(
                "INSERT INTO running (ip, started_at, token) "
                "SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM running) < ?",
                (ip, started.isoformat(), token, MAX_CONCURRENT_ANALYSES),
            )
            conn.commit()
            return cursor.lastrowid if cursor.rowcount > 0 else None
    except (sqlite3.Error, OSError):
        app.logger.exception("Réservation d'un créneau d'analyse impossible")
        return 0


def release_analysis_slot(slot_id):
    """Libère un créneau réservé. Sans effet si le compteur était indisponible."""
    if not slot_id:
        return
    try:
        with closing(usage_connect()) as conn:
            conn.execute("DELETE FROM running WHERE id = ?", (slot_id,))
            conn.commit()
    except (sqlite3.Error, OSError):
        app.logger.exception("Libération du créneau d'analyse impossible")


def valid_token(token):
    """Jeton d'annulation : 32 caractères hexadécimaux, non devinable."""
    return bool(token) and re.fullmatch(r"[0-9a-f]{32}", token or "") is not None


def cancel_requested(token):
    """Vrai si le visiteur a demandé l'arrêt de cette analyse."""
    if not valid_token(token):
        return False
    try:
        with closing(usage_connect()) as conn:
            row = conn.execute(
                "SELECT cancelled FROM running WHERE token = ?", (token,)
            ).fetchone()
    except (sqlite3.Error, OSError):
        app.logger.exception("Lecture de l'état d'annulation impossible")
        return False
    return bool(row and row[0])


def request_cancellation(token, ip):
    """Marque l'analyse comme à interrompre. Seule l'IP émettrice peut le faire.

    Retourne True si une analyse correspondante était bien en cours.
    """
    if not valid_token(token):
        return False
    try:
        with closing(usage_connect()) as conn:
            cursor = conn.execute(
                "UPDATE running SET cancelled = 1 WHERE token = ? AND ip = ?",
                (token, ip),
            )
            conn.commit()
            return cursor.rowcount > 0
    except (sqlite3.Error, OSError):
        app.logger.exception("Demande d'annulation impossible")
        return False


def configured_heap_mb():
    """Mémoire (Mo) allouée à la JVM d'analyse, déduite des options Java."""
    for variable in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JAVA_OPTS"):
        options = os.environ.get(variable) or ""
        correspondance = re.search(r"-Xmx(\d+)([mMgG]?)", options)
        if correspondance:
            valeur = int(correspondance.group(1))
            unite = correspondance.group(2).lower()
            return valeur if unite in ("m", "") else valeur * 1024
    return None


# Signatures d'un manque de mémoire côté JVM ou côté processus.
MEMORY_ERROR_MARKERS = (
    "outofmemoryerror", "java heap space", "gc overhead limit exceeded",
    "cannot allocate memory", "memoryerror", "unable to create new native thread",
    "native memory allocation", "too small maximum heap",
)


def memory_shortage(stderr, stdout=""):
    contenu = f"{stderr or ''}\n{stdout or ''}".lower()
    return any(marqueur in contenu for marqueur in MEMORY_ERROR_MARKERS)


def memory_shortage_message():
    memoire = configured_heap_mb()
    allocation = f" ({memoire} Mo alloués à l'analyse)" if memoire else ""
    return (
        "Le planning est trop volumineux pour la mémoire disponible"
        f"{allocation}. Réduisez le périmètre analysé (extrait du planning, "
        "suppression des tâches inutiles) ou demandez à l'administrateur "
        f"d'augmenter MAX_UPLOAD_MB et la mémoire allouée à l'analyse."
    )


def peak_rss_mb(pid):
    """Pic de mémoire résidente du sous-processus (VmHWM), en Mo."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as fichier:
            for ligne in fichier:
                if ligne.startswith("VmHWM:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def stop_process(proc):
    """Arrête le sous-processus et toute sa descendance (JVM comprise)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            app.logger.error("Sous-processus récalcitrant : %s", proc.pid)


def busy_message():
    return (
        f"{MAX_CONCURRENT_ANALYSES} analyse(s) sont déjà en cours sur ce service. "
        "Réessayez dans quelques instants : cette tentative n'a pas décompté "
        "votre quota du jour."
    )


def count_analysis(ip, already_counted):
    """Décompte l'analyse du quota, une seule fois et après un run_script réussi.

    Un échec de script, une attente sur le sémaphore ou un export interrompu ne
    consomment donc rien. Le contrôle fait au début de analyse() reste une
    simple barrière : deux requêtes simultanées d'une même IP peuvent dépasser
    le quota d'au plus MAX_CONCURRENT_ANALYSES analyses, ce qui est borné.
    """
    if already_counted:
        return True
    if not consume_quota(ip):
        app.logger.warning(
            "Quota épuisé après une analyse réussie (IP %s) : dépassement borné "
            "par MAX_CONCURRENT_ANALYSES.", ip,
        )
    return True


init_usage_db()


DCMA_TARGETS = {
    1: "≤ 5 %",
    2: "0 %",
    3: "≤ 5 %",
    4: "≥ 90 %",
    5: "≤ 5 % (idéal 0 %)",
    6: "≤ 5 %",
    7: "0 %",
    8: "≤ 5 %",
    9: "0 %",
    10: "Indicatif : 100 % des tâches critiques",
    11: "≤ 5 %",
    12: "Test manuel concluant",
    13: "0,95 à 1,05 en pratique",
    14: "≥ 0,95",
}

DCMA_PRIORITY = {
    1: 1, 9: 2, 7: 3, 14: 4, 11: 5, 4: 6, 6: 7, 8: 8,
    3: 9, 5: 10, 2: 11, 13: 12, 10: 13, 12: 14,
}

KIND_CSS = {
    "ok": "badge-ok",
    "ko": "badge-ko",
    "info": "badge-info",
    "na": "badge-na",
}

# Intitulés des 14 contrôles tels qu'affichés : le script dcma14.py les renvoie
# sans accents. Source unique, partagée par la page de résultat et les exports.
DCMA_LABELS = {
    1: "Logic (tâches sans lien amont/aval)",
    2: "Leads (lag négatif)",
    3: "Lags (lag positif)",
    4: "Relations Finish-to-Start",
    5: "Contraintes dures",
    6: "Marge totale excessive (plus de 44 j)",
    7: "Marge totale négative",
    8: "Durée excessive (plus de 44 j)",
    9: "Dates invalides (vs date d’état)",
    10: "Tâches de détail sans ressource",
    11: "Tâches terminées en retard vs baseline",
    12: "Test chemin critique (CPTest)",
    13: "Critical Path Length Index (CPLI)",
    14: "Baseline Execution Index (BEI)",
}

# Barème du score de conformité. La méthode DCMA-14 ne définit aucun score
# composite : celui-ci est une convention MPPCR, versionnée et affichée sur la
# page de résultat. Contrôles exclus du calcul :
#   - le 12 (CPTest) est une vérification manuelle, jamais mesurable ici ;
#   - le 10 (ressources) est un indicateur sans seuil, ni conforme ni fautif.
SCORE_VERSION = "v1"
SCORE_EXCLUDED = {10, 12}
# Un contrôle N/A faute de données n'est ni conforme ni fautif : il sort du
# calcul, mais il plafonne le score — sinon un planning vide obtiendrait 100.
SCORE_CAP_ONE_MISSING = 75
SCORE_CAP_TWO_MISSING = 60
SCORE_BANDS = (
    (85, "Conforme", "b-ok", "cf-low"),
    (70, "Acceptable", "b-info", "cf-low"),
    (55, "Fragile", "b-warn", "cf-med"),
    (0, "Insuffisant", "b-err", "cf-high"),
)

MC_LABELS = {
    "deterministic": "Durée déterministe (CPM)",
    "p50": "P50 — médiane simulée",
    "p80": "P80 — 80 % de confiance",
    "p90": "P90 — 90 % de confiance",
}

# Libellés de verdict affichés (le script renvoie « A CORRIGER » en majuscules).
VERDICT_LABELS = {
    "ok": "OK",
    "ko": "À corriger",
    "info": "Info",
    "na": "N/A",
}


def first_float(text):
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(text).replace(",", "."))
    return float(match.group()) if match else None


def first_int(text):
    value = first_float(text)
    return int(value) if value is not None else None


def split_ratio(detail):
    match = re.match(r"\s*(\d+)\s*/\s*(\d+)", detail or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def row_number(name):
    match = re.match(r"\s*(\d+)\.", name or "")
    return int(match.group(1)) if match else None


def verdict_kind(verdict):
    v = (verdict or "").upper()
    if v.startswith("OK"):
        return "ok"
    if "CORRIGER" in v:
        return "ko"
    if v.startswith("INFO"):
        return "info"
    if "N/A" in v:
        return "na"
    return "na"


def dcma_comment(num, value, kind, detail, verdict):
    bad, total = split_ratio(detail)

    if kind == "na":
        if num == 9:
            return "Renseigner la date d'état du projet pour contrôler la cohérence des dates."
        if num == 11:
            return "Renseigner une baseline pour mesurer les tâches terminées en retard."
        if num == 12:
            return "Vérifier manuellement que le chemin critique est continu, crédible et piloté par la logique."
        if num == 13:
            return "Fiabiliser la logique du réseau pour qu'un chemin critique exploitable soit identifié."
        if num == 14:
            return "Renseigner une baseline et une date d'état pour calculer le BEI."
        return "Compléter les données nécessaires au contrôle."

    if kind == "info":
        if num == 10:
            if value is not None and value >= 50:
                return (
                    "Affecter au minimum les ressources aux tâches critiques, puis étendre aux tâches "
                    "de détail pour permettre le lissage de charge et l'analyse capacitaire."
                )
            if value and value > 0:
                return "Compléter l'affectation des ressources sur les tâches restantes."
            return "Ressources affectées."
        return "Indicateur informatif."

    if kind == "ok":
        if num == 3 and bad:
            return f"Les {bad} lags positifs restent dans la tolérance, mais doivent être justifiés."
        if num == 5 and bad:
            return f"Les {bad} contraintes dures restent dans la tolérance, mais doivent être justifiées."
        if num == 13 and value is not None and value > 1.05:
            return (
                "CPLI accepté par le script mais élevé : surveiller l'excès de marge "
                "ou une logique insuffisamment tendue."
            )
        return {
            1: "Logique acceptable. Maintenir un prédécesseur et un successeur sur toute tâche active.",
            2: "Aucun lead détecté.",
            3: "Aucun lag positif détecté.",
            4: "La structure de liens est suffisamment orientée Finish-to-Start.",
            5: "Aucune contrainte dure détectée.",
            6: "Les marges restent dans la tolérance.",
            7: "Aucune marge négative.",
            8: "Les durées restent dans la tolérance.",
            9: "Les dates sont cohérentes avec la date d'état.",
            11: "Le retard par rapport à la baseline reste acceptable.",
            13: "CPLI cohérent avec un chemin critique réaliste.",
            14: "L'exécution par rapport à la baseline est conforme.",
        }.get(num, "Contrôle conforme. Maintenir la discipline de planification.")

    if num == 1:
        tasks_txt = f"{bad} tâches" if bad is not None else "tâches en écart"
        return (
            f"Priorité absolue : relier les {tasks_txt} à un prédécesseur et un successeur logiques. "
            "Sans cela, le chemin critique et le Monte Carlo ne sont pas fiables."
        )
    if num == 2:
        links_txt = f"{bad} liens" if bad is not None else "liens concernés"
        return (
            f"Supprimer les {links_txt} en lag négatif. Modéliser les chevauchements par des liens "
            "SS/FF justifiés plutôt que par des leads."
        )
    if num == 3:
        lags_txt = f"{bad} liens" if bad is not None else "liens concernés"
        return (
            f"Réduire les {lags_txt} en lag positif : remplacer les attentes par des tâches explicites "
            "ou revoir la logique."
        )
    if num == 4:
        non_fs = (total - bad) if total is not None and bad is not None else None
        if non_fs is not None:
            return (
                f"Analyser les {non_fs} liens non Finish-to-Start et convertir ceux qui ne correspondent "
                "pas à une logique réelle d'enchaînement."
            )
        return "Augmenter la part de liens Finish-to-Start ; les liens SS/FF doivent rester justifiés."
    if num == 5:
        constraints_txt = f"{bad} contraintes" if bad is not None else "contraintes concernées"
        return (
            f"Remplacer les {constraints_txt} dures par de la logique de dépendance ou par des contraintes "
            "souples justifiées."
        )
    if num == 6:
        tasks_txt = f"{bad} tâches" if bad is not None else "tâches concernées"
        return (
            f"Trop de tâches ont une marge > 44 j : relier les {tasks_txt}, re-séquencer le réseau "
            "ou supprimer les dates flottantes."
        )
    if num == 7:
        tasks_txt = f"{bad} tâches" if bad is not None else "tâches concernées"
        return (
            f"Corriger les {tasks_txt} en marge négative : contraintes, dates imposées, logique incohérente "
            "ou capacité insuffisante."
        )
    if num == 8:
        tasks_txt = f"{bad} tâches" if bad is not None else "tâches concernées"
        return (
            f"Décomposer les {tasks_txt} de plus de 44 jours en livrables plus courts, mesurables "
            "et pilotables."
        )
    if num == 9:
        tasks_txt = f"{bad} tâches" if bad is not None else "tâches concernées"
        return (
            f"Corriger les {tasks_txt} en dates invalides : saisir l'avancement réel et recalculer le "
            "prévisionnel après la date d'état."
        )
    if num == 11:
        tasks_txt = f"{bad} tâches" if bad is not None else "tâches concernées"
        return (
            f"Analyser les {tasks_txt} terminées en retard, puis décider d'un plan de rattrapage ou d'un "
            "re-baselining gouverné."
        )
    if num == 13:
        if value is not None:
            if value < 0.95:
                return (
                    "CPLI < 0,95 : chemin critique trop court ou marges négatives. Corriger la logique, "
                    "les contraintes et la date d'état."
                )
            if value > 1.05:
                return (
                    "CPLI > 1,05 : trop de marge ou réseau insuffisamment tendu. Vérifier le chemin critique."
                )
        return "Vérifier le CPLI et le chemin critique associé."
    if num == 14:
        return (
            "BEI sous 0,95 : le planning décroche de la baseline. Identifier les causes, produire un plan "
            "de rattrapage ou re-baseliner selon la gouvernance."
        )

    return "Corriger l'écart identifié par le contrôle."


def finalize_dcma(parsed):
    rows = parsed["rows"]
    counts = parsed["kpi"]
    counts["total"] = len(rows)

    for row in rows:
        kind = row["kind"]
        counts[kind] = counts.get(kind, 0) + 1

    if not rows:
        parsed["status_label"] = "Aucun contrôle analysé"
        parsed["status_css"] = "status-na"
    elif counts["ko"] > 0:
        parsed["status_label"] = f"{counts['ko']} contrôle(s) à corriger"
        parsed["status_css"] = "status-ko"
    elif counts["na"] > 0:
        parsed["status_label"] = "Données incomplètes"
        parsed["status_css"] = "status-na"
    else:
        parsed["status_label"] = "Conforme"
        parsed["status_css"] = "status-ok"

    row_by_num = {row.get("num"): row for row in rows}
    if row_by_num.get(1, {}).get("kind") == "ko":
        parsed["overall_comment"] = (
            "La logique de réseau est le point bloquant. Tant que le contrôle Logic dépasse 5 %, "
            "le chemin critique, les marges et le Monte Carlo restent peu fiables."
        )
    elif counts["ko"] >= 5:
        parsed["overall_comment"] = (
            "Qualité de planning insuffisante : corriger en priorité la logique, les dates invalides "
            "et le suivi de la baseline."
        )
    elif counts["ko"] > 0:
        parsed["overall_comment"] = (
            "Des écarts DCMA doivent être corrigés avant d'utiliser ce planning pour un pilotage fiable."
        )
    elif counts["na"] > 0:
        parsed["overall_comment"] = (
            "Les contrôles sont incomplets : renseigner la date d'état et/ou la baseline."
        )
    elif rows:
        parsed["overall_comment"] = "Le planning respecte les seuils DCMA analysés."
    else:
        parsed["overall_comment"] = "Aucun contrôle analysé."

    parsed["priorities"] = build_dcma_priorities(rows)
    parsed["score"] = dcma_score(parsed)


def dcma_score(parsed):
    """Score de conformité sur 100 (barème MPPCR, version SCORE_VERSION).

    - seuls les contrôles évaluables sont notés : les contrôles 10 et 12 sont
      hors barème, et un contrôle N/A ne compte ni comme conforme ni comme faute ;
    - tous les contrôles notés pèsent le même poids ;
    - le score est plafonné quand une donnée structurante manque (date d'état,
      baseline), sans quoi un planning vide obtiendrait 100/100.
    """
    rows = parsed.get("rows") or []
    by_num = {row.get("num"): row for row in rows}

    missing = []
    if by_num.get(9, {}).get("kind") == "na":
        missing.append("date d’état du projet")
    if by_num.get(11, {}).get("kind") == "na":
        missing.append("baseline")

    scored = [row for row in rows if row.get("num") not in SCORE_EXCLUDED]
    evaluated = [row for row in scored if row.get("kind") in {"ok", "ko"}]
    conformes = [row for row in evaluated if row.get("kind") == "ok"]

    if len(missing) >= 2:
        cap = SCORE_CAP_TWO_MISSING
    elif len(missing) == 1:
        cap = SCORE_CAP_ONE_MISSING
    else:
        cap = 100

    base = round(100 * len(conformes) / len(evaluated)) if evaluated else None
    score = min(base, cap) if base is not None else None

    label, badge_css, band_css = "Non évaluable", "b-na", "cf-med"
    if score is not None:
        for seuil, texte, badge, bande in SCORE_BANDS:
            if score >= seuil:
                label, badge_css, band_css = texte, badge, bande
                break

    return {
        "version": SCORE_VERSION,
        "score": score,
        "base": base,
        "cap": cap,
        "capped": base is not None and base > cap,
        "evaluated": len(evaluated),
        "conformes": len(conformes),
        "universe": len(scored),
        "na": len([row for row in scored if row.get("kind") == "na"]),
        "missing": missing,
        "label": label,
        "badge_css": badge_css,
        "band_css": band_css,
        "excluded": sorted(SCORE_EXCLUDED),
    }


def build_dcma_priorities(rows):
    selected = []
    for row in sorted(rows, key=lambda r: DCMA_PRIORITY.get(r.get("num") or 99, 99)):
        num = row.get("num")
        if num == 12:
            continue
        if row.get("kind") in {"ko", "na"}:
            selected.append(row)
        elif num == 10 and row.get("kind") == "info" and (row.get("value_num") or 0) > 0:
            selected.append(row)
    return selected[:10]


DETAILS_MARKER = "DETAILS_TACHES"


def parse_task_details(text):
    """Bloc « N|id,id,id » produit par dcma14.py --details -> {num: [ids]}.

    Le bloc est émis après la ligne « Resume » : les lecteurs qui s'arrêtent à
    cette ligne (dont parse_dcma_output) ne sont pas perturbés. La liste est
    incomplète si le script n'a pas été appelé avec l'option.
    """
    details = {}
    capture = False
    for line in (text or "").splitlines():
        contenu = line.strip()
        if not capture:
            if DETAILS_MARKER in contenu:
                capture = True
            continue
        if not contenu or set(contenu) == {"="}:
            continue
        correspondance = re.match(r"^(\d+)\|(.*)$", contenu)
        if not correspondance:
            continue
        details[int(correspondance.group(1))] = [
            int(valeur) for valeur in re.findall(r"\d+", correspondance.group(2))
        ]
    return details


def parse_dcma_output(text):
    parsed = {
        "raw": text,
        "title": None,
        "status": None,
        "rows": [],
        "summary": None,
        "overall_comment": "",
        "kpi": {"total": 0, "ok": 0, "ko": 0, "info": 0, "na": 0},
        "status_label": "",
        "status_css": "",
        "priorities": [],
        "task_details": {},
    }

    separator_count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if set(stripped) == {"="}:
            separator_count += 1
            continue

        if separator_count == 1:
            if parsed["title"] is None:
                parsed["title"] = stripped
            elif parsed["status"] is None and stripped.lower().startswith("date d'etat"):
                parsed["status"] = stripped
            continue

        if separator_count >= 2:
            if stripped.lower().startswith("resume"):
                parsed["summary"] = stripped
                break

            if re.match(r"^\d+\.", stripped):
                parts = re.split(r"\s{2,}", stripped)
                if len(parts) >= 4:
                    name = parts[0].strip()
                    value = parts[1].strip()
                    detail = parts[2].strip()
                    verdict = " ".join(parts[3:]).strip()

                    kind = verdict_kind(verdict)
                    num = row_number(name)
                    value_num = first_float(value) if value.upper() not in {"-", "N/A"} else None

                    parsed["rows"].append(
                        {
                            "num": num,
                            "name": name,
                            "value": value,
                            "detail": detail,
                            "verdict": verdict,
                            "kind": kind,
                            "css": KIND_CSS.get(kind, "badge-na"),
                            "target": DCMA_TARGETS.get(num, "—"),
                            "value_num": value_num,
                            "comment": dcma_comment(num, value_num, kind, detail, verdict),
                        }
                    )

    parsed["task_details"] = parse_task_details(text)
    finalize_dcma(parsed)
    return parsed


def mc_criticality_level(pct):
    if pct >= 90:
        return "Quasi systématique", "high"
    if pct >= 70:
        return "Driver majeur", "high"
    if pct >= 40:
        return "Driver fréquent", "medium"
    return "Driver occasionnel", "low"


def enrich_montecarlo(parsed):
    if parsed.get("stop_message"):
        low = parsed["stop_message"].lower()
        if "aucun lien" in low:
            parsed["stop_title"] = "Aucune dépendance exploitable"
            parsed["stop_action"] = (
                "Corriger d'abord le contrôle Logic DCMA : chaque tâche de détail doit avoir un "
                "prédécesseur et un successeur, sauf tâches de début et de fin de projet."
            )
        elif "boucle" in low:
            parsed["stop_title"] = "Boucle de dépendances"
            parsed["stop_action"] = "Supprimer le cycle identifié, puis relancer la simulation."
        else:
            parsed["stop_title"] = "Simulation arrêtée"
            parsed["stop_action"] = "Corriger les erreurs de réseau signalées, puis relancer la simulation."
        return

    metrics_by_key = {m["key"]: m for m in parsed["metrics"] if m.get("key")}
    det = metrics_by_key.get("deterministic", {}).get("value_num")
    p50 = metrics_by_key.get("p50", {}).get("value_num")
    p80 = metrics_by_key.get("p80", {}).get("value_num")
    p90 = metrics_by_key.get("p90", {}).get("value_num")

    for metric in parsed["metrics"]:
        key = metric.get("key")

        if key == "deterministic":
            metric["target"] = "Référence CPM, pas un engagement"
            if det is not None and p50 is not None:
                delta = p50 - det
                if delta > 0:
                    metric["comment"] = (
                        f"La médiane simulée est {delta:+.1f} j au-dessus du CPM : la durée planifiée "
                        "est optimiste."
                    )
                else:
                    metric["comment"] = f"La médiane simulée est {delta:+.1f} j par rapport au CPM."
            else:
                metric["comment"] = "Base sans aléa, utile pour mesurer la contingence simulée."

        elif key == "p50":
            metric["target"] = "50 % de confiance seulement"
            if det is not None and p50 is not None:
                metric["comment"] = (
                    f"Médiane : {p50 - det:+.1f} j vs CPM. Environ une chance sur deux de dépasser "
                    "cette durée."
                )
            else:
                metric["comment"] = "Environ une chance sur deux de dépasser cette durée."

        elif key == "p80":
            metric["target"] = "Minimum recommandé pour un objectif de planning"
            if det is not None and p80 is not None:
                delta = p80 - det
                if delta > 0:
                    metric["comment"] = (
                        f"Prévoir {delta:.1f} j de contingence vs CPM pour atteindre 80 % de confiance."
                    )
                else:
                    metric["comment"] = "Le CPM est déjà au niveau ou au-dessus du P80 dans ce modèle."
            else:
                metric["comment"] = "80 % des simulations finissent avant cette durée."

        elif key == "p90":
            metric["target"] = "Engagement prudent ou contractuel"
            if det is not None and p90 is not None:
                delta = p90 - det
                if delta > 0:
                    metric["comment"] = (
                        f"Prévoir {delta:.1f} j de contingence vs CPM pour atteindre 90 % de confiance."
                    )
                else:
                    metric["comment"] = "Le CPM est déjà au niveau ou au-dessus du P90 dans ce modèle."
            else:
                metric["comment"] = "90 % des simulations finissent avant cette durée."

        else:
            metric["target"] = metric.get("target") or "—"

    if p50 is not None:
        sub = f"{p50 - det:+.1f} j vs CPM" if det is not None else "Médiane simulée"
        parsed["kpi"].append(
            {
                "label": "P50",
                "value": f"{p50:.1f} j",
                "sub": sub,
                "target": "50 % de confiance seulement",
                "css": "kpi-warn",
            }
        )

    if p80 is not None:
        sub = f"{p80 - det:+.1f} j vs CPM" if det is not None else "80 % de confiance"
        parsed["kpi"].append(
            {
                "label": "P80",
                "value": f"{p80:.1f} j",
                "sub": sub,
                "target": "Objectif planning minimum recommandé",
                "css": "kpi-ok",
            }
        )

    if p90 is not None:
        sub = f"{p90 - det:+.1f} j vs CPM" if det is not None else "90 % de confiance"
        parsed["kpi"].append(
            {
                "label": "P90",
                "value": f"{p90:.1f} j",
                "sub": sub,
                "target": "Engagement prudent ou contractuel",
                "css": "kpi-alert",
            }
        )

    if p50 is not None and p90 is not None and p50 > 0:
        spread = p90 - p50
        spread_pct = spread / p50 * 100.0

        if spread_pct < 10:
            level = "faible"
            css = "kpi-ok"
        elif spread_pct < 25:
            level = "modérée"
            css = "kpi-warn"
        elif spread_pct < 50:
            level = "élevée"
            css = "kpi-alert"
        else:
            level = "très élevée"
            css = "kpi-alert"

        parsed["kpi"].append(
            {
                "label": "Incertitude P50 → P90",
                "value": f"{spread:.1f} j",
                "sub": f"{spread_pct:.1f} % du P50 — incertitude {level}",
                "target": "Repère : < 25 %",
                "css": css,
            }
        )

    if det is not None and p80 is not None:
        contingency = p80 - det
        decision = f"Pour un objectif de planning à 80 % de confiance, viser {p80:.1f} j"
        if contingency > 0:
            decision += (
                f", soit {contingency:.1f} j de contingence par rapport à la durée déterministe "
                f"({det:.1f} j)."
            )
        else:
            decision += "."

        if p90 is not None:
            decision += (
                f" Pour un engagement prudent à 90 %, viser {p90:.1f} j "
                f"({p90 - det:+.1f} j vs CPM)."
            )

        parsed["decision"] = decision

    tasks = first_int(parsed["info"].get("tasks"))
    links = first_int(parsed["info"].get("links"))
    if tasks and links is not None:
        ratio = (links / tasks * 100.0) if tasks else 0.0
        parsed["network"] = {
            "tasks": tasks,
            "links": links,
            "ratio_pct": round(ratio, 1),
        }
        if ratio < 90:
            parsed["network_warning"] = (
                f"Réseau peu maillé : {links} liens pour {tasks} tâches ({ratio:.1f} %). "
                "Le Monte Carlo reste indicatif tant que le contrôle Logic DCMA n'est pas revenu sous 5 %."
            )


def parse_montecarlo_output(text):
    parsed = {
        "raw": text,
        "title": None,
        "info": {},
        "metrics": [],
        "criticality": [],
        "stop_message": None,
        "stop_title": "",
        "stop_action": "",
        "kpi": [],
        "decision": "",
        "network": None,
        "network_warning": "",
    }

    stop_mode = False
    stop_lines = []
    in_criticality = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if set(stripped) == {"="}:
            continue

        if stripped.startswith("ARRET :"):
            stop_mode = True

        if stop_mode:
            stop_lines.append(stripped)
            continue

        if stripped.startswith("SIMULATION MONTE CARLO"):
            parsed["title"] = stripped
            continue

        if stripped.startswith("Taches de detail :"):
            parsed["info"]["tasks"] = stripped.split(":", 1)[1].strip()
            continue

        if stripped.startswith("Liens de dependance :"):
            parsed["info"]["links"] = stripped.split(":", 1)[1].strip()
            continue

        if stripped.startswith("Simulations :"):
            parsed["info"]["sims"] = stripped.split(":", 1)[1].strip()
            continue

        if stripped.startswith("Duree deterministe") or stripped.startswith(("P50", "P80", "P90")):
            if ":" in stripped:
                raw_label, value = stripped.split(":", 1)
                raw_label = raw_label.strip()

                key = None
                if raw_label.startswith("Duree deterministe"):
                    key = "deterministic"
                elif raw_label.startswith("P50"):
                    key = "p50"
                elif raw_label.startswith("P80"):
                    key = "p80"
                elif raw_label.startswith("P90"):
                    key = "p90"

                parsed["metrics"].append(
                    {
                        "key": key,
                        "label": MC_LABELS.get(key, raw_label),
                        "value": value.strip(),
                        "value_num": first_float(value),
                        "target": "",
                        "comment": "",
                    }
                )
            continue

        if stripped.startswith("Indice de criticite"):
            in_criticality = True
            continue

        if in_criticality:
            match = re.match(r"^(.+?)\s+([0-9]+(?:\.[0-9]+)?)%$", stripped)
            if match:
                pct = float(match.group(2))
                level, css = mc_criticality_level(pct)
                parsed["criticality"].append(
                    {
                        "name": match.group(1).strip(),
                        "pct": match.group(2),
                        "pct_num": pct,
                        "width": min(max(pct, 0.0), 100.0),
                        "level": level,
                        "css": css,
                    }
                )

    if stop_lines:
        parsed["stop_message"] = "\n".join(stop_lines)

    enrich_montecarlo(parsed)
    return parsed


def parse_montecarlo_options(form):
    sims_raw = (form.get("sims") or "5000").strip() or "5000"
    opt_raw = (form.get("opt") or "0.8").strip().replace(",", ".") or "0.8"
    pess_raw = (form.get("pess") or "1.5").strip().replace(",", ".") or "1.5"

    try:
        sims = int(float(sims_raw))
    except ValueError:
        raise ValueError("Le nombre de simulations doit être un entier.")

    if sims < 1 or sims > 50000:
        raise ValueError("Le nombre de simulations doit être compris entre 1 et 50000.")

    try:
        opt = float(opt_raw)
        pess = float(pess_raw)
    except ValueError:
        raise ValueError("Les facteurs optimiste et pessimiste doivent être des nombres décimaux.")

    if opt <= 0 or pess <= 0:
        raise ValueError("Les facteurs optimiste et pessimiste doivent être strictement positifs.")

    if pess < opt:
        raise ValueError("Le facteur pessimiste doit être supérieur ou égal au facteur optimiste.")

    return sims, opt, pess


def run_script(cmd, label, token=None):
    """Exécute un script d'analyse et surveille le délai et l'annulation.

    Le script est lancé dans son propre groupe de processus : l'arrêt demandé
    par le visiteur, comme le dépassement du délai maximal, interrompt le script
    et la JVM embarquée qu'il a démarrée. La sortie standard est filtrée du
    warning Log4j avant d'être rendue à l'appelant.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "UTF-8"

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError:
        app.logger.error("Script introuvable : %s", cmd[1] if len(cmd) > 1 else cmd)
        raise AnalysisError("Un script d'analyse est introuvable dans l'application.")
    except Exception:
        app.logger.exception("Erreur d'exécution pendant %s", label)
        raise AnalysisError("Une erreur est survenue pendant l'exécution de l'analyse.")

    deadline = time.monotonic() + TIMEOUT_SECONDS
    commence = time.monotonic()
    stdout = stderr = ""
    pic_memoire = 0

    # Boucle d'attente : on guette l'annulation toutes les demi-secondes plutôt
    # que d'attendre la fin du script sans pouvoir l'interrompre, et on relève
    # au passage le pic de mémoire du sous-processus (utile pour dimensionner).
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            pic_memoire = max(pic_memoire, peak_rss_mb(proc.pid))
            if token and cancel_requested(token):
                stop_process(proc)
                app.logger.info(
                    "%s interrompu à la demande du visiteur après %.1f s",
                    label, time.monotonic() - commence,
                )
                raise AnalysisCancelled("Analyse interrompue à votre demande.")
            if time.monotonic() > deadline:
                stop_process(proc)
                app.logger.error("Timeout pendant %s", label)
                raise AnalysisError(
                    "L'analyse a dépassé le délai maximal. Réduisez le nombre de "
                    "simulations ou vérifiez le fichier."
                )

    duree = time.monotonic() - commence
    # La JVM annonce ses options sur stderr : bruit inutile dans les journaux.
    bruit_java = re.compile(r"^Picked up (JAVA_TOOL_OPTIONS|_JAVA_OPTIONS):.*$", re.MULTILINE)
    stderr_utile = bruit_java.sub("", stderr or "").strip()

    if stderr_utile:
        app.logger.warning("%s stderr : %s", label, stderr_utile[:2000])

    app.logger.info(
        "%s terminé en %.1f s (pic mémoire du sous-processus : %s Mo)",
        label, duree, pic_memoire or "?",
    )

    if proc.returncode != 0:
        if memory_shortage(stderr_utile, stdout):
            app.logger.error(
                "%s : mémoire insuffisante (pic %s Mo, code %s) — %s",
                label, pic_memoire or "?", proc.returncode, stderr_utile[:800],
            )
            raise AnalysisError(memory_shortage_message())
        app.logger.error(
            "%s a échoué avec le code %s\nstdout=%s\nstderr=%s",
            label, proc.returncode, stdout, stderr_utile[:2000],
        )
        raise AnalysisError(
            f"L'exécution de {label} a échoué. Vérifiez que le fichier .mpp est valide et lisible."
        )

    stdout = stdout or ""
    if not stdout.strip():
        app.logger.error("%s n'a produit aucune sortie standard", label)
        raise AnalysisError(f"Aucun résultat produit par {label}.")

    # La JVM embarquée écrit systématiquement ce warning Log4j avant le
    # séparateur « ==== » marquant le vrai début de la sortie : on le retire
    # pour ne pas polluer la « Sortie brute » ni le parsing.
    stdout = LOG4J_NOISE.sub("", stdout)

    return stdout


def export_basename(filename):
    """Nom de fichier d'export, sans accent ni espace : MPPCR_<projet>_<date>."""
    stem = secure_filename(Path(filename).stem) or "planning"
    return f"MPPCR_{stem}_{date.today().isoformat()}"


def build_downloads(exports, results, filename):
    """Liens de téléchargement autonomes pour les exports demandés.

    Le fichier est encodé dans la page elle-même (« data: ») : aucun stockage
    côté serveur, un seul aller-retour, et le téléchargement fonctionne sans
    JavaScript ni requête supplémentaire.
    """
    if not exports:
        return []

    base = export_basename(filename)
    downloads = []
    for fmt in exports:
        if fmt == "csv":
            payload = csv_export_payload(results, filename)
        elif fmt == "xlsx":
            payload = xlsx_export_payload(results, filename)
        else:
            continue
        libelle, mimetype, extension = EXPORT_FORMATS[fmt]
        downloads.append(
            {
                "label": libelle,
                "filename": f"{base}.{extension}",
                "size": len(payload),
                "href": f"data:{mimetype};base64,{base64.b64encode(payload).decode('ascii')}",
            }
        )
    return downloads


def parsed_by_kind(results, kind):
    return next((r["parsed"] for r in results if r.get("kind") == kind), None)


def dcma_export_rows(parsed):
    """Tableau des contrôles : n°, contrôle, résultat, détail, cible, verdict,
    tâches concernées (N° MS Project, liste complète), action recommandée."""
    details = parsed.get("task_details") or {}
    rows = []
    for row in parsed.get("rows") or []:
        detail = row.get("detail")
        identifiants = details.get(row.get("num")) or []
        rows.append(
            [
                row.get("num") or "",
                DCMA_LABELS.get(row.get("num"), row.get("name") or ""),
                row.get("value") or "",
                "" if detail in (None, "-") else detail,
                row.get("target") or "",
                VERDICT_LABELS.get(row.get("kind"), row.get("verdict") or ""),
                ", ".join(str(i) for i in identifiants),
                row.get("comment") or "",
            ]
        )
    return rows


def priority_export_rows(parsed):
    details = parsed.get("task_details") or {}
    rows = []
    for index, item in enumerate(parsed.get("priorities") or [], start=1):
        identifiants = details.get(item.get("num")) or []
        rows.append(
            [
                f"{index:02d}",
                DCMA_LABELS.get(item.get("num"), item.get("name") or ""),
                VERDICT_LABELS.get(item.get("kind"), item.get("verdict") or ""),
                item.get("value") or "",
                item.get("target") or "",
                ", ".join(str(i) for i in identifiants),
                item.get("comment") or "",
            ]
        )
    return rows


def mc_export_rows(parsed):
    """Durées Monte Carlo avec l'écart calculé par rapport au chemin critique."""
    det = next((m for m in parsed.get("metrics") or [] if m.get("key") == "deterministic"), None)
    cpm = det.get("value_num") if det else None
    rows = []
    for metric in parsed.get("metrics") or []:
        value = metric.get("value_num")
        delta = "" if (cpm is None or value is None) else round(value - cpm, 1)
        rows.append(
            [
                MC_LABELS.get(metric.get("key"), metric.get("label") or ""),
                "" if value is None else value,
                delta,
                metric.get("target") or "",
                metric.get("comment") or "",
            ]
        )
    return rows


def dispersion_export_line(parsed):
    """Indicateur de dispersion P50 → P90 déjà calculé par enrich_montecarlo()."""
    for kpi in parsed.get("kpi") or []:
        if str(kpi.get("label", "")).startswith("Incertitude"):
            return [kpi.get("label"), kpi.get("value"), kpi.get("sub"), kpi.get("target")]
    return None


def csv_export_payload(results, filename):
    """CSV séparé par des points-virgules, avec BOM UTF-8 (Excel FR).

    Un seul fichier ne porte pas d'onglets : les sections sont introduites par
    une ligne « # … ».
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["MPPCR — MS Project Check & Risk"])
    writer.writerow(["Fichier analysé", filename])
    writer.writerow(["Date de l'export", date.today().isoformat()])
    writer.writerow(["Barème du score de conformité", SCORE_VERSION])

    dcma = parsed_by_kind(results, "dcma")
    montecarlo = parsed_by_kind(results, "montecarlo")

    if dcma:
        score = dcma.get("score") or {}
        writer.writerow([])
        writer.writerow(["# SCORE DE CONFORMITÉ"])
        writer.writerow(["Score", score.get("score", ""), "sur 100", score.get("label", "")])
        if score.get("capped"):
            writer.writerow(
                ["Score avant plafond", score.get("base", ""), "plafonné à", score.get("cap", ""),
                 "; ".join(score.get("missing") or [])]
            )
        writer.writerow(["Contrôles évalués", score.get("evaluated", ""), "sur", score.get("universe", "")])
        writer.writerow(["Contrôles non évaluables", score.get("na", "")])
        writer.writerow(["Contrôles hors barème", ", ".join(str(n) for n in score.get("excluded") or [])])

        writer.writerow([])
        writer.writerow(["# SYNTHÈSE DCMA-14"])
        writer.writerow(["Contrôles analysés", dcma["kpi"]["total"]])
        writer.writerow(["Conformes", dcma["kpi"]["ok"]])
        writer.writerow(["À corriger", dcma["kpi"]["ko"]])
        writer.writerow(["Non applicables", dcma["kpi"]["na"]])
        writer.writerow(["Informatifs", dcma["kpi"]["info"]])
        if dcma.get("status"):
            writer.writerow(["Détail", dcma["status"]])

        writer.writerow([])
        writer.writerow(["# CONTRÔLES DCMA-14"])
        writer.writerow(["N°", "Contrôle", "Résultat", "Détail", "Cible", "Verdict",
                         "Tâches concernées (N° MS Project)", "Action recommandée"])
        writer.writerows(dcma_export_rows(dcma))

        rows = priority_export_rows(dcma)
        if rows:
            writer.writerow([])
            writer.writerow(["# PRIORITÉS D'AMÉLIORATION"])
            writer.writerow(["Ordre", "Contrôle", "Verdict", "Résultat", "Cible",
                             "Tâches concernées (N° MS Project)", "Action recommandée"])
            writer.writerows(rows)

    if montecarlo:
        info = montecarlo.get("info") or {}
        writer.writerow([])
        writer.writerow(["# SIMULATION MONTE CARLO"])
        writer.writerow(["Tâches de détail", info.get("tasks", "")])
        writer.writerow(["Liens de dépendance", info.get("links", "")])
        writer.writerow(["Simulations", info.get("sims", "")])
        if montecarlo.get("network"):
            writer.writerow(["Densité de liens (%)", montecarlo["network"].get("ratio_pct", "")])
        writer.writerow([])
        writer.writerow(["Indicateur", "Durée (j)", "Écart vs CPM (j)", "Cible / usage", "Commentaire"])
        writer.writerows(mc_export_rows(montecarlo))

        dispersion = dispersion_export_line(montecarlo)
        if dispersion:
            writer.writerow([])
            writer.writerow(dispersion)

        if montecarlo.get("decision"):
            writer.writerow([])
            writer.writerow(["Lecture décisionnelle"])
            writer.writerow([montecarlo["decision"]])
        if montecarlo.get("network_warning"):
            writer.writerow([])
            writer.writerow(["Avertissement réseau"])
            writer.writerow([montecarlo["network_warning"]])
        if montecarlo.get("stop_message"):
            writer.writerow([])
            writer.writerow(["Simulation arrêtée", montecarlo.get("stop_title", "")])
            writer.writerow([montecarlo["stop_message"]])
            writer.writerow([montecarlo.get("stop_action") or ""])
        if montecarlo.get("criticality"):
            writer.writerow([])
            writer.writerow(["# INDICE DE CRITICITÉ PAR TÂCHE"])
            writer.writerow(["Tâche", "Criticité (%)", "Lecture"])
            for item in montecarlo["criticality"]:
                writer.writerow([item["name"], item["pct"], item["level"]])

    if dcma and dcma.get("summary"):
        writer.writerow([])
        writer.writerow(["# RÉSUMÉ DU SCRIPT DCMA-14"])
        writer.writerow([dcma["summary"]])

    for kind, titre in (("dcma", "DCMA-14"), ("montecarlo", "MONTE CARLO")):
        parsed = parsed_by_kind(results, kind)
        if parsed and parsed.get("raw"):
            writer.writerow([])
            writer.writerow([f"# SORTIE BRUTE {titre}"])
            for line in parsed["raw"].splitlines():
                writer.writerow([line])

    # BOM UTF-8 : sans lui, Excel FR n'interprète pas les accents.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def xlsx_export_payload(results, filename):
    """Classeur Excel : une feuille par analyse lancée."""
    dcma = parsed_by_kind(results, "dcma")
    montecarlo = parsed_by_kind(results, "montecarlo")

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    titre = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#16181d"})
    entete = workbook.add_format({
        "bold": True, "bg_color": "#fbfcfd", "border": 1, "border_color": "#e3e6ea",
        "align": "left", "valign": "vcenter", "text_wrap": True,
    })
    libelle = workbook.add_format({"bold": True, "font_color": "#4b5563"})
    texte = workbook.add_format({"text_wrap": True, "valign": "top"})
    nombre = workbook.add_format({"num_format": "0.0", "align": "right"})
    centre = workbook.add_format({"align": "center"})
    ok_fmt = workbook.add_format({"bg_color": "#e6f7ee", "font_color": "#0f7042", "align": "center"})
    ko_fmt = workbook.add_format({"bg_color": "#fdecec", "font_color": "#a52f2f", "align": "center"})
    na_fmt = workbook.add_format({"bg_color": "#eef0f3", "font_color": "#6b7480", "align": "center"})
    info_fmt = workbook.add_format({"bg_color": "#eef3ff", "font_color": "#1c46c4", "align": "center"})
    verdict_formats = {"ok": ok_fmt, "ko": ko_fmt, "na": na_fmt, "info": info_fmt}
    score_fmt = workbook.add_format({"bold": True, "font_size": 16, "font_color": "#1c46c4"})

    if dcma:
        score = dcma.get("score") or {}
        sheet = workbook.add_worksheet("Contrôles DCMA-14")
        sheet.set_column("A:A", 5)
        sheet.set_column("B:B", 42)
        sheet.set_column("C:C", 12)
        sheet.set_column("D:D", 16)
        sheet.set_column("E:E", 22)
        sheet.set_column("F:F", 12)
        sheet.set_column("G:G", 34)
        sheet.set_column("H:H", 70)

        sheet.write("A1", "MPPCR — Diagnostic qualité DCMA-14", titre)
        sheet.write("A2", "Fichier analysé", libelle)
        sheet.write("B2", filename)
        sheet.write("A3", "Date de l'export", libelle)
        sheet.write("B3", date.today().isoformat())
        sheet.write("A4", "Score de conformité", libelle)
        sheet.write("B4", score.get("score", ""), score_fmt)
        sheet.write("C4", f"sur 100 — {score.get('label', '')}")
        if score.get("capped"):
            sheet.write("D4", f"plafonné à {score.get('cap')} ; "
                              f"{', '.join(score.get('missing') or [])} non renseigné")
        sheet.write("A5", "Contrôles évalués", libelle)
        sheet.write("B5", f"{score.get('evaluated', '')} sur {score.get('universe', '')}")
        sheet.write("A6", "Non évaluables", libelle)
        sheet.write("B6", score.get("na", ""))
        sheet.write("A7", "Barème", libelle)
        sheet.write("B7", f"score {score.get('version', SCORE_VERSION)} — contrôles "
                          f"{', '.join(str(n) for n in score.get('excluded') or [])} hors barème")

        row = 9
        for column, label in enumerate(
            ["N°", "Contrôle", "Résultat", "Détail", "Cible", "Verdict",
             "Tâches concernées (N° MS Project)", "Action recommandée"]
        ):
            sheet.write(row, column, label, entete)
        row += 1
        for line in dcma_export_rows(dcma):
            kind = next((r.get("kind") for r in dcma["rows"] if r.get("num") == line[0]), None)
            for column, value in enumerate(line):
                if column == 5:
                    sheet.write(row, column, value, verdict_formats.get(kind, centre))
                elif column in (6, 7):
                    sheet.write(row, column, value, texte)
                else:
                    sheet.write(row, column, value)
            row += 1

        rows = priority_export_rows(dcma)
        if rows:
            row += 2
            sheet.write(row, 0, "Priorités d’amélioration", titre)
            row += 1
            for column, label in enumerate(
                ["Ordre", "Contrôle", "Verdict", "Résultat", "Cible",
                 "Tâches concernées (N° MS Project)", "Action recommandée"]
            ):
                sheet.write(row, column, label, entete)
            row += 1
            for line in rows:
                for column, value in enumerate(line):
                    if column in (5, 6):
                        sheet.write(row, column, value, texte)
                    else:
                        sheet.write(row, column, value)
                row += 1

    if montecarlo:
        info = montecarlo.get("info") or {}
        sheet = workbook.add_worksheet("Monte Carlo")
        sheet.set_column("A:A", 42)
        sheet.set_column("B:B", 14)
        sheet.set_column("C:C", 16)
        sheet.set_column("D:D", 30)
        sheet.set_column("E:E", 70)

        sheet.write("A1", "MPPCR — Simulation Monte Carlo", titre)
        sheet.write("A2", "Fichier analysé", libelle)
        sheet.write("B2", filename)
        sheet.write("A3", "Tâches de détail", libelle)
        sheet.write("B3", info.get("tasks", ""))
        sheet.write("A4", "Liens de dépendance", libelle)
        sheet.write("B4", info.get("links", ""))
        sheet.write("A5", "Simulations", libelle)
        sheet.write("B5", info.get("sims", ""))
        if montecarlo.get("network"):
            sheet.write("A6", "Densité de liens (%)", libelle)
            sheet.write("B6", montecarlo["network"].get("ratio_pct", ""))

        row = 8
        for column, label in enumerate(
            ["Indicateur", "Durée (j)", "Écart vs CPM (j)", "Cible / usage", "Commentaire"]
        ):
            sheet.write(row, column, label, entete)
        row += 1
        for line in mc_export_rows(montecarlo):
            sheet.write(row, 0, line[0])
            if isinstance(line[1], (int, float)):
                sheet.write_number(row, 1, line[1], nombre)
            else:
                sheet.write(row, 1, line[1])
            if isinstance(line[2], (int, float)):
                sheet.write_number(row, 2, line[2], nombre)
            else:
                sheet.write(row, 2, line[2])
            sheet.write(row, 3, line[3], texte)
            sheet.write(row, 4, line[4], texte)
            row += 1

        dispersion = dispersion_export_line(montecarlo)
        if dispersion:
            row += 1
            sheet.write(row, 0, dispersion[0], libelle)
            sheet.write(row, 1, dispersion[1])
            sheet.write(row, 3, dispersion[2] or "")
            row += 1
        if montecarlo.get("decision"):
            row += 1
            sheet.write(row, 0, "Lecture décisionnelle", libelle)
            sheet.write(row, 1, montecarlo["decision"], texte)
            row += 1
        if montecarlo.get("network_warning"):
            row += 1
            sheet.write(row, 0, "Avertissement réseau", libelle)
            sheet.write(row, 1, montecarlo["network_warning"], texte)
            row += 1
        if montecarlo.get("stop_message"):
            row += 1
            sheet.write(row, 0, montecarlo.get("stop_title") or "Simulation arrêtée", libelle)
            sheet.write(row, 1, montecarlo["stop_message"], texte)
            row += 1
            sheet.write(row, 1, montecarlo.get("stop_action") or "", texte)

        if montecarlo.get("criticality"):
            row += 2
            sheet.write(row, 0, "Indice de criticité par tâche", titre)
            row += 1
            for column, label in enumerate(["Tâche", "Criticité (%)", "Lecture"]):
                sheet.write(row, column, label, entete)
            row += 1
            for item in montecarlo["criticality"]:
                sheet.write(row, 0, item["name"])
                sheet.write(row, 1, f"{item['pct']} %")
                sheet.write(row, 2, item["level"])
                row += 1

    workbook.close()
    return output.getvalue()


@app.errorhandler(413)
def handle_413(_error):
    return render_template(
        "error.html",
        title="Fichier trop volumineux",
        variant="warn",
        message=too_large_message(request.content_length),
    ), 413


@app.errorhandler(400)
def handle_400(_error):
    return render_template("error.html", message="Requête invalide."), 400


@app.errorhandler(500)
def handle_500(_error):
    app.logger.exception("Erreur interne")
    return render_template(
        "error.html",
        message="Une erreur interne est survenue. Réessayez ou contactez l'administrateur.",
    ), 500


@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        quota_remaining=quota_remaining(client_ip()),
        daily_limit=DAILY_ANALYSIS_LIMIT,
        max_upload_mb=MAX_UPLOAD_MB,
        client_token=secrets.token_hex(16),
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok"


@app.route("/annuler/<token>", methods=["POST", "GET"])
def annuler(token):
    """Demande l'arrêt de l'analyse en cours (bouton de la page d'attente).

    Seule l'adresse IP qui a lancé l'analyse peut l'interrompre. Le POST est
    utilisé par le bouton (réponse vide) ; le GET existe pour un lien direct.
    """
    if not valid_token(token):
        return render_template("error.html", message="Demande d'annulation invalide."), 400

    stopped = request_cancellation(token, client_ip())
    if request.method == "POST":
        return "", 204 if stopped else 404

    if stopped:
        return render_template(
            "error.html",
            title="Arrêt demandé",
            variant="warn",
            message="L'analyse en cours va s'interrompre dans quelques instants. "
                    "Votre quota n'a pas été décompté pour cette tentative.",
        )
    return render_template(
        "error.html",
        title="Aucune analyse à interrompre",
        variant="warn",
        message="Cette analyse est terminée, déjà interrompue, ou n'existe plus.",
    ), 404


@app.route("/analyse", methods=["POST"])
def analyse():
    analysis = (request.form.get("analysis") or "dcma").strip()
    if analysis not in {"dcma", "montecarlo", "both"}:
        return render_template("error.html", message="Type d'analyse inconnu."), 400

    # Exports facultatifs demandés en plus de la page (cases à cocher).
    requested_exports = [fmt for fmt in request.form.getlist("export") if fmt in EXPORT_FORMATS]

    # Jeton d'annulation : fourni par le formulaire, sinon régénéré (le bouton
    # d'arrêt n'est alors pas proposé).
    client_token = (request.form.get("client_token") or "").strip()
    if not valid_token(client_token):
        client_token = secrets.token_hex(16)

    ip = client_ip()

    # Refus immédiat, sans lire ni valider le fichier, quand le quota du jour est
    # déjà épuisé. Le compteur n'est incrémenté qu'après une analyse réussie.
    if quota_remaining(ip) == 0:
        return render_template(
            "error.html",
            title="Quota quotidien atteint",
            variant="warn",
            message=quota_exceeded_message(),
        ), 429

    # Refus avant même d'écrire le fichier sur disque : la taille annoncée par
    # le navigateur suffit à savoir que l'analyse n'aura pas la mémoire requise.
    if (request.content_length or 0) > MAX_UPLOAD_MB * 1024 * 1024:
        return render_template(
            "error.html",
            title="Fichier trop volumineux",
            variant="warn",
            message=too_large_message(request.content_length),
        ), 413

    mpp_file = request.files.get("mpp_file")
    if mpp_file is None or not mpp_file.filename:
        return render_template("error.html", message="Aucun fichier planning fourni."), 400

    if not mpp_file.filename.lower().endswith(".mpp"):
        return render_template("error.html", message="Seuls les fichiers .mpp sont acceptés."), 400

    include_montecarlo = analysis in {"montecarlo", "both"}
    sims = opt = pess = None

    if include_montecarlo:
        try:
            sims, opt, pess = parse_montecarlo_options(request.form)
        except ValueError as exc:
            return render_template("error.html", message=str(exc)), 400

    # Créneau d'analyse : au plus MAX_CONCURRENT_ANALYSES analyses lourdes en
    # parallèle, tous workers et threads confondus. Une demande refusée ici ne
    # consomme pas de quota.
    slot = acquire_analysis_slot(ip, client_token)
    if slot is None:
        return render_template(
            "error.html",
            title="Analyse en cours",
            variant="warn",
            message=busy_message(),
        ), 503, {"Retry-After": "30"}

    try:
        with tempfile.TemporaryDirectory(prefix="mppweb_") as tmpdir:
            tmp_path = Path(tmpdir)

            mpp_path = tmp_path / "planning.mpp"
            mpp_file.save(str(mpp_path))

            if mpp_path.stat().st_size == 0:
                return render_template("error.html", message="Le fichier planning est vide."), 400

            estimates_path = None
            estimates_file = request.files.get("estimates_file")

            if estimates_file is not None and estimates_file.filename:
                if not estimates_file.filename.lower().endswith(".csv"):
                    return render_template(
                        "error.html",
                        message="Le fichier d'estimations optionnel doit être au format .csv.",
                    ), 400

                estimates_path = tmp_path / "estimates.csv"
                estimates_file.save(str(estimates_path))

                if estimates_path.stat().st_size == 0:
                    return render_template(
                        "error.html",
                        message="Le fichier CSV d'estimations est vide.",
                    ), 400

            results = []
            quota_counted = False

            if analysis in {"dcma", "both"}:
                # --details ajoute le bloc « numéros de tâches en écart », qui
                # alimente la colonne « Tâches (N°) » du tableau.
                stdout = run_script(
                    [sys.executable, str(DCMA_SCRIPT), str(mpp_path), "--details"],
                    "le diagnostic DCMA-14",
                    token=client_token,
                )
                quota_counted = count_analysis(ip, quota_counted)
                results.append(
                    {
                        "kind": "dcma",
                        "title": "Diagnostic qualité DCMA-14",
                        "parsed": parse_dcma_output(stdout),
                    }
                )

            if include_montecarlo:
                cmd = [
                    sys.executable,
                    str(MONTECARLO_SCRIPT),
                    str(mpp_path),
                    "--sims",
                    str(sims),
                    "--opt",
                    str(opt),
                    "--pess",
                    str(pess),
                ]

                if estimates_path is not None:
                    cmd.extend(["--estimates", str(estimates_path)])

                stdout = run_script(cmd, "la simulation Monte Carlo", token=client_token)
                quota_counted = count_analysis(ip, quota_counted)
                results.append(
                    {
                        "kind": "montecarlo",
                        "title": "Simulation Monte Carlo",
                        "parsed": parse_montecarlo_output(stdout),
                    }
                )

            filename = secure_filename(mpp_file.filename) or "planning.mpp"

            # La page de résultat est toujours rendue ; les exports demandés y
            # sont ajoutés sous forme de liens autonomes (data:), produits dans
            # la même requête : rien n'est écrit côté serveur.
            return render_template(
                "result.html",
                results=results,
                filename=filename,
                labels=DCMA_LABELS,
                verdicts=VERDICT_LABELS,
                downloads=build_downloads(requested_exports, results, filename),
            )

    except AnalysisCancelled:
        app.logger.info("Analyse annulée par le visiteur (IP %s)", ip)
        return render_template(
            "error.html",
            title="Analyse interrompue",
            variant="warn",
            message="L'analyse a été interrompue à votre demande. Aucune analyse "
                    "n'a été décomptée de votre quota du jour.",
        )

    except AnalysisError as exc:
        return render_template("error.html", message=str(exc)), 400

    except Exception:
        app.logger.exception("Erreur inattendue pendant l'analyse")
        return render_template(
            "error.html",
            message="Une erreur inattendue est survenue pendant l'analyse.",
        ), 500

    finally:
        release_analysis_slot(slot)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

