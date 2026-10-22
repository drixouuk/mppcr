#!/usr/bin/env python3
"""Test de bout en bout : vrais scripts + vrai fichier projet + templates.

Exécute dcma14.py et montecarlo.py sur un projet MSPDI de test, puis injecte
leurs sorties réelles dans les fonctions de parsing et dans result.html.

À lancer DANS un conteneur construit depuis le Dockerfile :
    python3 /stage/e2e_real.py
"""
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import date
import subprocess
import sys
from pathlib import Path

STAGE = Path(__file__).resolve().parent
WORK = Path("/tmp/e2e_mppcr")
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
os.environ["USAGE_DB_PATH"] = str(WORK / "usage.db")
os.environ["DAILY_ANALYSIS_LIMIT"] = "20"
os.environ["MAX_CONCURRENT_ANALYSES"] = "1"
os.environ["MAX_UPLOAD_MB"] = "1"
sys.path.insert(0, str(STAGE))

import app  # noqa: E402

PROJECT = STAGE / "test_projet.xml"
FAILS = []
COUNT = [0]


def check(label, ok, detail=""):
    COUNT[0] += 1
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(label)


def run(script, *args):
    proc = subprocess.run([sys.executable, str(STAGE / script), str(PROJECT), *args],
                          capture_output=True, text=True, timeout=600)
    print(f"\n----- {script} (code {proc.returncode}) -----")
    print(proc.stdout)
    if proc.stderr.strip():
        print(f"[stderr] {proc.stderr[:2000]}")
    return proc


print("=" * 70)
print("ÉTAPE 1 — dcma14.py sur un vrai fichier projet (MSPDI lu par MPXJ)")
print("=" * 70)
proc = run("dcma14.py")
check("dcma14.py se termine sans erreur", proc.returncode == 0, f"code={proc.returncode}")

raw = proc.stdout
# Récupère la VRAIE ligne Log4j émise par la JVM embarquée : elle n'apparaît
# que sur le chemin de lecture MPP binaire (POI + log4j-api), pas sur MSPDI.
probe = subprocess.run(
    [sys.executable, "-c",
     "import mpxj, jpype; mpxj.startJVM(); "
     "jpype.JClass('org.apache.logging.log4j.LogManager').getLogger('probe')"],
    capture_output=True, text=True, timeout=300)
real_noise = probe.stdout
print(f"\n----- ligne Log4j réelle capturée (stdout) -----\n{real_noise!r}")
check("ligne Log4j réelle capturée sur stdout",
      "Log4j API could not find a logging provider." in real_noise, f"={real_noise!r}")
check("ligne Log4j absente de stderr", "Log4j" not in probe.stderr)

noise = real_noise or "2026-09-15T08:49:36.442228766Z main ERROR Log4j API could not find a logging provider.\n"
filtered = app.LOG4J_NOISE.sub("", noise + raw)
dcma = app.parse_dcma_output(filtered)
dcma_without_noise = app.parse_dcma_output(raw)
check("le filtre ne change pas le parsing",
      dcma["rows"] == dcma_without_noise["rows"] and dcma["kpi"] == dcma_without_noise["kpi"]
      and dcma["summary"] == dcma_without_noise["summary"],
      f"={dcma['kpi']} vs {dcma_without_noise['kpi']}")
check("ligne Log4j retirée de la sortie brute", "Log4j" not in dcma["raw"])
check("sortie utile conservée à l'identique",
      dcma["raw"] == raw, f"={dcma['raw'][:60]!r}")
check("au moins 10 contrôles analysés", dcma["kpi"]["total"] >= 10, f"={dcma['kpi']}")
check("statut global calculé", bool(dcma["status_label"]), f"={dcma['status_label']}")
check("au moins un contrôle à corriger (tâche orpheline)", dcma["kpi"]["ko"] >= 1, f"={dcma['kpi']}")
check("priorités non vides", len(dcma["priorities"]) >= 1, f"={len(dcma['priorities'])}")
check("contrôle Logic détecte la tâche sans lien",
      any(r["num"] == 1 and r["kind"] == "ko" for r in dcma["rows"]),
      f"={[ (r['num'], r['kind'], r['value']) for r in dcma['rows'][:4] ]}")

with app.app.test_request_context("/"):
    html = app.render_template(
        "result.html",
        results=[{"kind": "dcma", "title": "Diagnostic qualité DCMA-14", "parsed": dcma}],
        filename="test_projet.xml",
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS,
    )
check("DCMA rendu : badge de statut issu du vrai script",
      dcma["status_label"] in html)
check("DCMA rendu : 5 cartes KPI", html.count('class="kpi kpi-') == 5)
n_rows = html.count('<div class="ctrl-name">')
check("DCMA rendu : table complète",
      n_rows == len(dcma["rows"]), f"={n_rows}/{len(dcma['rows'])}")
check("DCMA rendu : pas de gabarit non rendu", "{{" not in html)

print("\n" + "=" * 70)
print("ÉTAPE 2 — montecarlo.py sur le même fichier projet")
print("=" * 70)
proc_mc = run("montecarlo.py", "--sims", "500")
check("montecarlo.py se termine sans erreur", proc_mc.returncode == 0, f"code={proc_mc.returncode}")

mc = app.parse_montecarlo_output(app.LOG4J_NOISE.sub("", proc_mc.stdout))
check("simulation non arrêtée (liens présents)", not mc["stop_message"], f"={mc['stop_message']}")
check("4 métriques de durée", len(mc["metrics"]) == 4, f"={[m['key'] for m in mc['metrics']]}")
check("P50 <= P80 <= P90",
      [m["value_num"] for m in mc["metrics"]][1] <= [m["value_num"] for m in mc["metrics"]][2]
      <= [m["value_num"] for m in mc["metrics"]][3],
      f"={[m['value_num'] for m in mc['metrics']]}")
check("criticité non vide", len(mc["criticality"]) >= 1, f"={len(mc['criticality'])}")
check("réseau interprété", mc["network"] is not None, f"={mc['network']}")
check("lecture décisionnelle produite", bool(mc["decision"]), f"={mc['decision'][:80]}")

with app.app.test_request_context("/"):
    html_mc = app.render_template(
        "result.html",
        results=[{"kind": "montecarlo", "title": "Simulation Monte Carlo", "parsed": mc}],
        filename="test_projet.xml",
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS,
    )
check("MC rendu : 4 cartes de durée", len(__import__("re").findall(r'<div class="dur(?:\s[^"]*)?"', html_mc)) == 4)
check("MC rendu : carte P80 mise en avant", 'class="dur is-rec"' in html_mc)
check("MC rendu : barres de criticité", html_mc.count('class="crit-fill') == len(mc["criticality"]))
check("MC rendu : échelle percentiles calculée sur les valeurs réelles",
      html_mc.count('class="scale-mark') == 4)
tbl, raw = html.split('<details class="raw"', 1)
check("DCMA rendu : intitulés accentués depuis la maquette dans le tableau",
      "Logic (tâches sans lien amont/aval)" in tbl and "Logic (taches" not in tbl)
check("DCMA rendu : sortie brute laissée verbatim (non accentuée)",
      "Logic (taches sans lien amont/aval)" in raw)
check("MC rendu : pas de gabarit non rendu", "{{" not in html_mc)

print("\n" + "=" * 70)
print("ÉTAPE 3 — page résultat combinée (DCMA + Monte Carlo)")
print("=" * 70)
with app.app.test_request_context("/"):
    html_both = app.render_template(
        "result.html",
        results=[
            {"kind": "dcma", "title": "Diagnostic qualité DCMA-14", "parsed": dcma},
            {"kind": "montecarlo", "title": "Simulation Monte Carlo", "parsed": mc},
        ],
        filename="test_projet.xml",
        labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS,
    )
n_sec = html_both.count('<div class="result" id="')
check("deux sections rendues", n_sec == 2, f"={n_sec}")
head = re.sub(r'<details class="raw".*?</details>', "", html_both, flags=re.S)  # hors sortie brute
n_chip = head.count('<span class="file-chip">')
n_name = head.count("test_projet.xml")
check("nom du fichier affiché dans une puce par section",
      n_chip == 2 and n_name == 3, f"chip={n_chip} nom={n_name}")
check("onglets : les deux analyses -> trois onglets actifs, aucun inerte",
      html_both.count('class="tab is-off"') == 0 and 'href="#dcma"' in html_both
      and 'href="#montecarlo"' in html_both and 'href="/"' in html_both)
check("onglets : ancres de section présentes",
      'id="dcma"' in html_both and 'id="montecarlo"' in html_both)
check("les deux titres d'analyse sont présents",
      "Diagnostic qualité DCMA-14" in html_both and "Simulation Monte Carlo" in html_both)
check("pas de gabarit non rendu", "{{" not in html_both)

print("\n" + "=" * 70)
print("ÉTAPE 4 — fencepost : run_script filtre bien la sortie d'un sous-processus")
print("=" * 70)
out = app.run_script([sys.executable, "-c",
                      f"import sys; sys.stdout.write({noise!r} + '=' * 10 + '\\nDIAGNOSTIC DCMA-14\\n')"],
                     "le test de filtrage")
check("run_script a retiré la ligne Log4j", "Log4j" not in out, f"={out!r}")
check("run_script conserve la sortie utile", "DIAGNOSTIC DCMA-14" in out, f"={out!r}")


print("\n" + "=" * 70)
print("ÉTAPE 4b — sortie par défaut de dcma14.py inchangée (référence figée)")
print("=" * 70)
reference = STAGE / "ref_dcma.txt"
if reference.exists():
    # même invocation que celle qui a produit la référence : chemin relatif,
    # car le nom du fichier apparaît dans l'en-tête de la sortie
    sans_option = subprocess.run([sys.executable, "dcma14.py", PROJECT.name],
                                 cwd=str(STAGE), capture_output=True, text=True,
                                 timeout=600).stdout
    check("sortie sans --details identique à la référence figée",
          sans_option == reference.read_text(encoding="utf-8"))
    avec_option = subprocess.run([sys.executable, "dcma14.py", PROJECT.name, "--details"],
                                 cwd=str(STAGE), capture_output=True, text=True,
                                 timeout=600).stdout
    check("--details ajoute le bloc sans toucher au reste",
          avec_option.startswith(sans_option.rstrip("\n")) and "DETAILS_TACHES" in avec_option)
    ids = app.parse_task_details(avec_option)
    check("bloc de détail exploitable", ids.get(1) == [8], f"={ids}")
    check("le bloc couvre les contrôles en écart",
          set(ids).issuperset({1, 5, 9, 10, 11, 14}), f"={sorted(ids)}")
else:
    check("référence de sortie présente", False, f"={reference}")

print("\n" + "=" * 70)
print("ÉTAPE 5 — route /analyse de bout en bout : page, CSV, Excel, quota, sémaphore")
print("=" * 70)


def quota_count(ip):
    with closing(sqlite3.connect(os.environ["USAGE_DB_PATH"])) as conn:
        row = conn.execute("SELECT count FROM usage WHERE ip = ? AND date = ?",
                           (ip, date.today().isoformat())).fetchone()
        return int(row[0]) if row else 0


client = app.app.test_client()


def post(ip, exports=(), analysis="both", sims="500", token=None):
    data = {"analysis": analysis, "sims": sims, "opt": "0.8", "pess": "1.5",
            "mpp_file": (open(PROJECT, "rb"), "PROJET-X.mpp")}
    if token:
        data["client_token"] = token
    for fmt in exports:
        data.setdefault("export", [])
        data["export"] = list(data["export"]) + [fmt]
    return client.post("/analyse", data=data, content_type="multipart/form-data",
                       headers={"X-Forwarded-For": ip})


ip_page = "203.0.113.10"
avant = quota_count(ip_page)
r = post(ip_page, "page")
html_route = r.get_data(as_text=True)
check("POST page -> 200", r.status_code == 200, f"={r.status_code}")
check("colonne « Tâches concernées (N°) » alimentée par le script",
      "Tâches concernées (N°)" in html_route and ">8<" in html_route.replace(" ", ""),
      "tâche 8 absente")
check("score affiché sur la page réelle",
      'class="card card-pad score"' in html_route and "Score de conformité DCMA" in html_route)
check("barème dépliable présent", "Comment ce score est calculé" in html_route)
check("dispersion Monte Carlo à côté du score", "Incertitude P50 → P90" in html_route)
check("quota décompté après une analyse réussie", quota_count(ip_page) == avant + 1,
      f"={quota_count(ip_page)}")
check("page : pas de trace Log4j",
      "Log4j" not in html_route.split('<details class="raw"')[0])

ip_csv = "203.0.113.11"
r = post(ip_csv, exports=("csv",), analysis="dcma")
page_csv = r.get_data(as_text=True)
check("POST avec export CSV -> page 200", r.status_code == 200, f"={r.status_code}")
check("CSV : bloc d'export dans la page",
      "Exports disponibles" in page_csv and 'download="MPPCR_PROJET-X_' in page_csv)
check("CSV : page de résultat toujours affichée",
      "CONTRÔLES DCMA-14" in page_csv.replace("Contrôles DCMA-14", "CONTRÔLES DCMA-14")
      or "Détail des contrôles" in page_csv)
lien = re.search(r'href="data:text/csv;base64,([^"]+)"', page_csv)
check("CSV : lien de téléchargement autonome", lien is not None)
if lien:
    import base64 as _b64
    csv_txt = _b64.b64decode(lien.group(1)).decode("utf-8-sig")
    check("CSV téléchargé : contenu du rapport",
          "CONTRÔLES DCMA-14" in csv_txt and "SCORE DE CONFORMITÉ" in csv_txt
          and "Logic (tâches sans lien amont/aval)" in csv_txt)
    check("CSV téléchargé : BOM UTF-8",
          _b64.b64decode(lien.group(1)).startswith(b"\xef\xbb\xbf"))
check("CSV : quota décompté une seule fois", quota_count(ip_csv) == 1, f"={quota_count(ip_csv)}")

ip_xlsx = "203.0.113.12"
r = post(ip_xlsx, exports=("xlsx",))
page_xlsx = r.get_data(as_text=True)
check("POST avec export Excel -> page 200", r.status_code == 200, f"={r.status_code}")
lien_x = re.search(r'href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,([^"]+)"',
                   page_xlsx)
check("XLSX : lien de téléchargement autonome", lien_x is not None)
if lien_x:
    import base64 as _b64
    import io as _io
    import zipfile as _zip
    classeur = _b64.b64decode(lien_x.group(1))
    check("XLSX : signature ZIP", classeur[:2] == b"PK")
    check("XLSX : taille plausible", len(classeur) > 5000, f"={len(classeur)}")
    with _zip.ZipFile(_io.BytesIO(classeur)) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
    check("XLSX : deux feuilles (DCMA + Monte Carlo)",
          "Contrôles DCMA-14" in wb and "Monte Carlo" in wb)

ip_both = "203.0.113.16"
r = post(ip_both, exports=("csv", "xlsx"))
page_both = r.get_data(as_text=True)
check("deux exports demandés -> deux liens",
      page_both.count('download="MPPCR_PROJET-X_') == 2, f"={page_both.count('download=')}")
check("une seule analyse décomptée pour deux exports", quota_count(ip_both) == 1,
      f"={quota_count(ip_both)}")

ip_none = "203.0.113.17"
r = post(ip_none, exports=())
check("aucun export coché -> page sans bloc d'export",
      "Exports disponibles" not in r.get_data(as_text=True))

ip_busy = "203.0.113.13"
slot = app.acquire_analysis_slot(ip_busy)
r = post(ip_busy, "page", analysis="dcma")
check("sémaphore plein -> 503", r.status_code == 503, f"={r.status_code}")
check("503 : Retry-After", r.headers.get("Retry-After") == "30",
      f"={r.headers.get('Retry-After')}")
check("503 : page dédiée", "Analyse en cours" in r.get_data(as_text=True))
check("503 : quota intact", quota_count(ip_busy) == 0, f"={quota_count(ip_busy)}")
app.release_analysis_slot(slot)
r = post(ip_busy, "page", analysis="dcma")
check("créneau libéré -> analyse de nouveau acceptée", r.status_code == 200, f"={r.status_code}")

ip_bad = "203.0.113.14"
r = client.post("/analyse",
                data={"analysis": "dcma", "format": "page",
                      "mpp_file": (_io.BytesIO(b"pas un mpp"), "planning.mpp")},
                content_type="multipart/form-data",
                headers={"X-Forwarded-For": ip_bad})
check("fichier illisible -> 400", r.status_code == 400, f"={r.status_code}")
check("échec -> aucun quota consommé", quota_count(ip_bad) == 0, f"={quota_count(ip_bad)}")

# --- limites de taille et messages mémoire (chemin réel) -----------------
print("\n" + "=" * 70)
print("ÉTAPE 6 — protections mémoire : taille et manque de mémoire")
print("=" * 70)
gros = _io.BytesIO(b"0" * (2 * 1024 * 1024))
r = client.post("/analyse",
                data={"analysis": "dcma",
                      "mpp_file": (gros, "volumineux.mpp")},
                content_type="multipart/form-data",
                headers={"X-Forwarded-For": "203.0.113.30"})
page_gros = r.get_data(as_text=True)
check("fichier au-delà de la limite -> 413", r.status_code == 413, f"={r.status_code}")
check("message « Fichier trop volumineux »", "Fichier trop volumineux" in page_gros)
check("taille reçue et limite rappelées",
      "Mo reçus" in page_gros and "1 Mo" in page_gros)
check("quota non consommé pour un refus de taille", quota_count("203.0.113.30") == 0)

os.environ["JAVA_TOOL_OPTIONS"] = "-Xmx1m"
r = post("203.0.113.31", analysis="dcma")
page_memoire = r.get_data(as_text=True)
check("mémoire JVM insuffisante -> message dédié",
      "trop volumineux pour la mémoire" in page_memoire, f"={page_memoire[:80]}")
check("mémoire allouée annoncée", "1 Mo alloués" in page_memoire)
check("quota non consommé pour un échec mémoire", quota_count("203.0.113.31") == 0)
os.environ.pop("JAVA_TOOL_OPTIONS", None)

r = client.post("/analyse", data={"analysis": "dcma", "export": "pdf"},
                headers={"X-Forwarded-For": "203.0.113.15"})
check("export inconnu ignoré (page rendue sans lien)", r.status_code == 400 or "Exports disponibles" not in r.get_data(as_text=True), f"={r.status_code}")

# --- annulation en cours d'analyse, par la route -------------------------
import threading as _threading
import time as _time
import secrets as _secrets

ip_cancel = "203.0.113.18"
jeton = _secrets.token_hex(16)
etat = {}


def _post_long():
    try:
        etat["reponse"] = post(ip_cancel, analysis="montecarlo", sims="50000", token=jeton)
    except BaseException as exc:  # noqa: BLE001
        etat["erreur"] = exc


fil = _threading.Thread(target=_post_long, daemon=True)
fil.start()
_time.sleep(1.5)
reponse = client.post(f"/annuler/{jeton}", headers={"X-Forwarded-For": ip_cancel})
check("annulation : demande acceptée (204)", reponse.status_code == 204, f"={reponse.status_code}")
fil.join(timeout=60)
corps = etat.get("reponse").get_data(as_text=True) if etat.get("reponse") is not None else ""
check("annulation : page « Analyse interrompue »", "Analyse interrompue" in corps,
      f"={corps[:120]}")
check("annulation : quota non décompté", quota_count(ip_cancel) == 0, f"={quota_count(ip_cancel)}")
with closing(sqlite3.connect(os.environ["USAGE_DB_PATH"])) as conn:
    restants = conn.execute("SELECT COUNT(*) FROM running").fetchone()[0]
check("annulation : créneau d'analyse libéré", restants == 0, f"={restants}")

r = client.post("/annuler/" + _secrets.token_hex(16), headers={"X-Forwarded-For": ip_cancel})
check("annulation d'une analyse inexistante -> 404", r.status_code == 404, f"={r.status_code}")
r = client.post("/annuler/zzz", headers={"X-Forwarded-For": ip_cancel})
check("jeton invalide -> 400", r.status_code == 400, f"={r.status_code}")
r = client.get("/annuler/" + jeton, headers={"X-Forwarded-For": ip_cancel})
check("lien direct d'annulation -> page explicative", r.status_code == 404
      and "Aucune analyse à interrompre" in r.get_data(as_text=True), f"={r.status_code}")

print("\n" + "=" * 70)
print(f"{COUNT[0] - len(FAILS)}/{COUNT[0]} vérifications OK")
if FAILS:
    print("ÉCHECS : " + ", ".join(FAILS))
    sys.exit(1)
print("Bout en bout : tout est vert.")
