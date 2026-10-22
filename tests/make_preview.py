#!/usr/bin/env python3
"""Génère des aperçus HTML autonomes des écrans MPPCR (à ouvrir dans un navigateur).

Les pages sont rendues par Flask avec les sorties réelles des scripts sur le
projet de test MSPDI, pour comparer visuellement avec maquette_mpp.html.

Sortie : /tmp/mppcr-preview/{index,dcma,montecarlo,les-deux,erreur,quota}.html
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

STAGE = Path(__file__).resolve().parent
OUT = Path("/tmp/mppcr-preview")
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
os.environ["USAGE_DB_PATH"] = str(OUT / "usage.db")
os.environ["DAILY_ANALYSIS_LIMIT"] = "5"
sys.path.insert(0, str(STAGE))

import app  # noqa: E402

PROJECT = STAGE / "test_projet.xml"


def run(script, *args):
    proc = subprocess.run([sys.executable, str(STAGE / script), str(PROJECT), *args],
                          capture_output=True, text=True, timeout=600)
    return proc.stdout


dcma = app.parse_dcma_output(app.LOG4J_NOISE.sub("", run("dcma14.py", "--details")))
mc = app.parse_montecarlo_output(app.LOG4J_NOISE.sub("", run("montecarlo.py", "--sims", "500")))

with app.app.test_request_context("/"):
    pages = {
        "index.html": app.render_template("index.html", quota_remaining=4, daily_limit=5,
                                          client_token="a" * 32),
        "dcma.html": app.render_template(
            "result.html", filename="PROJET-X.mpp",
            results=[{"kind": "dcma", "title": "Diagnostic qualité DCMA-14", "parsed": dcma}],
            labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS,
            downloads=app.build_downloads(["csv"], [{"kind": "dcma", "parsed": dcma}], "PROJET-X.mpp")),
        "montecarlo.html": app.render_template(
            "result.html", filename="PROJET-X.mpp",
            results=[{"kind": "montecarlo", "title": "Simulation Monte Carlo", "parsed": mc}],
            labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS),
        "les-deux.html": app.render_template(
            "result.html", filename="PROJET-X.mpp",
            results=[{"kind": "dcma", "title": "Diagnostic qualité DCMA-14", "parsed": dcma},
                     {"kind": "montecarlo", "title": "Simulation Monte Carlo", "parsed": mc}],
            labels=app.DCMA_LABELS, verdicts=app.VERDICT_LABELS,
            downloads=app.build_downloads(
                ["csv", "xlsx"],
                [{"kind": "dcma", "parsed": dcma}, {"kind": "montecarlo", "parsed": mc}],
                "PROJET-X.mpp")),
        "erreur.html": app.render_template(
            "error.html",
            message="L’exécution du diagnostic a échoué. Vérifiez que le fichier .mpp est valide et lisible."),
        "quota.html": app.render_template(
            "error.html", title="Quota quotidien atteint", variant="warn",
            message="Vous avez atteint la limite de 5 analyses par jour pour votre adresse IP. "
                    "Le quota se réinitialise à minuit."),
    }

for name, html in pages.items():
    (OUT / name).write_text(html, encoding="utf-8")
    print(f"  {name:<18} {len(html):>7} octets")
print(f"\nAperçus écrits dans {OUT}")
