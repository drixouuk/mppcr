#!/usr/bin/env python3
"""Mesure l'effet des choix du formulaire v2 sur la fixture (facteurs PERT, CSV)."""
import subprocess
import sys
from pathlib import Path

STAGE = Path(__file__).resolve().parent
FIXTURE = STAGE / "test_projet.xml"
estimations = STAGE / "estimations_temoin.csv"
estimations.write_text("task_id,optimistic,most_likely,pessimistic\n5,1,1,1\n", encoding="utf-8")


def p50(*arguments):
    sortie = subprocess.run(
        [sys.executable, str(STAGE / "montecarlo.py"), str(FIXTURE), "--sims", "5000",
         *arguments],
        cwd=str(STAGE), capture_output=True, text=True, timeout=600).stdout
    lignes = [l for l in sortie.splitlines() if l.startswith(("P50", "P90", "Duree deterministe"))]
    return " | ".join(" ".join(l.split()) for l in lignes)


print("facteurs 0,8/1,5 :", p50("--opt", "0.8", "--pess", "1.5"))
print("facteurs 0,7/1,8 :", p50("--opt", "0.7", "--pess", "1.8"))
print("sans estimations :", p50())
print("avec estimations :", p50("--estimates", str(estimations)))
