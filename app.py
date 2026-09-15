#!/usr/bin/env python3
"""
Interface Flask pour dcma14.py et montecarlo.py.

Les scripts existants ne sont pas modifiés : ils sont appelés en subprocess.
Chaque analyse démarre un processus Python/JVM isolé. Le premier appel peut
être plus lent à cause du démarrage de la JVM.

Limitation d'usage anonyme : chaque adresse IP dispose d'un quota d'analyses
par jour calendaire (DAILY_ANALYSIS_LIMIT, défaut 5), compté dans un fichier
SQLite partagé par les workers Gunicorn.

POINT D'EXTENSION AUTH :
Ajouter flask-httpauth ou Flask-BasicAuth, puis protéger index() et analyse().
Le healthcheck /healthz peut rester public pour le reverse proxy.
Aucune authentification n'est intégrée : voir la section « Sécurité » du
README avant toute exposition sur Internet.
"""

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DCMA_SCRIPT = BASE_DIR / "dcma14.py"
MONTECARLO_SCRIPT = BASE_DIR / "montecarlo.py"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
TIMEOUT_SECONDS = int(os.environ.get("ANALYSIS_TIMEOUT", "900"))

# Un seul reverse proxy (NPMplus) est placé devant l'application : x_for=1 fait
# lire l'IP réelle du visiteur dans l'en-tête X-Forwarded-For.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Quota d'analyses par adresse IP et par jour calendaire.
DAILY_ANALYSIS_LIMIT = int(os.environ.get("DAILY_ANALYSIS_LIMIT", "5"))

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


def usage_db_path():
    return Path(os.environ.get("USAGE_DB_PATH", DEFAULT_USAGE_DB))


def usage_connect():
    return sqlite3.connect(str(usage_db_path()), timeout=10)


def init_usage_db():
    """Crée le fichier et la table de comptage s'ils sont absents (idempotent)."""
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


def quota_exceeded_message():
    return (
        f"Vous avez atteint la limite de {DAILY_ANALYSIS_LIMIT} analyses par jour "
        "pour votre adresse IP. Le quota se réinitialise à minuit."
    )


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

MC_LABELS = {
    "deterministic": "Durée déterministe (CPM)",
    "p50": "P50 — médiane simulée",
    "p80": "P80 — 80 % de confiance",
    "p90": "P90 — 90 % de confiance",
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


def run_script(cmd, label):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "UTF-8"

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        app.logger.error("Timeout pendant %s", label)
        raise AnalysisError(
            "L'analyse a dépassé le délai maximal. Réduisez le nombre de simulations ou vérifiez le fichier."
        )
    except FileNotFoundError:
        app.logger.error("Script introuvable : %s", cmd[1] if len(cmd) > 1 else cmd)
        raise AnalysisError("Un script d'analyse est introuvable dans l'application.")
    except Exception:
        app.logger.exception("Erreur d'exécution pendant %s", label)
        raise AnalysisError("Une erreur est survenue pendant l'exécution de l'analyse.")

    if completed.stderr.strip():
        app.logger.warning("%s stderr : %s", label, completed.stderr)

    if completed.returncode != 0:
        app.logger.error(
            "%s a échoué avec le code %s\nstdout=%s\nstderr=%s",
            label,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )
        raise AnalysisError(
            f"L'exécution de {label} a échoué. Vérifiez que le fichier .mpp est valide et lisible."
        )

    stdout = completed.stdout or ""
    if not stdout.strip():
        app.logger.error("%s n'a produit aucune sortie standard", label)
        raise AnalysisError(f"Aucun résultat produit par {label}.")

    # La JVM embarquée écrit systématiquement ce warning Log4j avant le
    # séparateur « ==== » marquant le vrai début de la sortie : on le retire
    # pour ne pas polluer la « Sortie brute » ni le parsing.
    stdout = LOG4J_NOISE.sub("", stdout)

    return stdout


@app.errorhandler(413)
def handle_413(_error):
    return render_template(
        "error.html",
        message="Le fichier dépasse la taille maximale autorisée (20 Mo).",
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
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok"


@app.route("/analyse", methods=["POST"])
def analyse():
    analysis = (request.form.get("analysis") or "dcma").strip()
    if analysis not in {"dcma", "montecarlo", "both"}:
        return render_template("error.html", message="Type d'analyse inconnu."), 400

    ip = client_ip()

    # Refus immédiat, sans lire ni valider le fichier, quand le quota du jour est
    # déjà épuisé. Le contrôle définitif (atomique) est fait par consume_quota().
    if quota_remaining(ip) == 0:
        return render_template(
            "error.html",
            title="Quota quotidien atteint",
            variant="warn",
            message=quota_exceeded_message(),
        ), 429

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

            # Le quota est consommé une seule fois, après validation des
            # fichiers, juste avant de lancer la première analyse.
            if not consume_quota(ip):
                return render_template(
                    "error.html",
                    title="Quota quotidien atteint",
                    variant="warn",
                    message=quota_exceeded_message(),
                ), 429

            results = []

            if analysis in {"dcma", "both"}:
                stdout = run_script(
                    [sys.executable, str(DCMA_SCRIPT), str(mpp_path)],
                    "le diagnostic DCMA-14",
                )
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

                stdout = run_script(cmd, "la simulation Monte Carlo")
                results.append(
                    {
                        "kind": "montecarlo",
                        "title": "Simulation Monte Carlo",
                        "parsed": parse_montecarlo_output(stdout),
                    }
                )

            filename = secure_filename(mpp_file.filename) or "planning.mpp"
            return render_template("result.html", results=results, filename=filename)

    except AnalysisError as exc:
        return render_template("error.html", message=str(exc)), 400

    except Exception:
        app.logger.exception("Erreur inattendue pendant l'analyse")
        return render_template(
            "error.html",
            message="Une erreur inattendue est survenue pendant l'analyse.",
        ), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

