#!/usr/bin/env python3
"""Mesure le Monte Carlo sur un plan volumineux : durée et pic mémoire.

Le plan est généré avec les mêmes caractéristiques que ceux de la section
« Ce qui pèse réellement » du README : tâches enchaînées par des liens
Finish-to-Start, baselines, une tâche sur sept sans lien amont.

Aucune modification n'est faite ici : on mesure avant de décider si
run_simulation() mérite d'être vectorisé.

Usage (dans un conteneur de l'image) : python3 mesure_mc.py 24000 5000
"""
import os
import subprocess
import sys
import time
from pathlib import Path

NOMBRE = int(sys.argv[1]) if len(sys.argv) > 1 else 24000
SIMS = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
CHEMIN = Path(f"/m/plan_{NOMBRE}.xml")


def generer(nb, chemin):
    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Project xmlns="http://schemas.microsoft.com/project">',
        f"<Name>Plan de test {nb} taches</Name>",
        "<StatusDate>2026-10-05T08:00:00</StatusDate>",
        "<MinutesPerDay>480</MinutesPerDay>",
        "<Tasks>",
    ]
    jour, mois = 1, 9
    for i in range(1, nb + 1):
        debut = f"2026-{mois:02d}-{jour:02d}T08:00:00"
        fin = f"2026-{mois:02d}-{jour:02d}T17:00:00"
        pred = ""
        if i > 1 and i % 7:
            pred = (f"<PredecessorLink><PredecessorUID>{i - 1}</PredecessorUID>"
                    f"<Type>1</Type><LinkLag>0</LinkLag></PredecessorLink>")
        lignes.append(
            f"<Task><UID>{i}</UID><ID>{i}</ID><Name>Tache {i}</Name>"
            f"<Duration>PT8H0M0S</Duration><Start>{debut}</Start><Finish>{fin}</Finish>"
            f"<PercentComplete>{100 if i % 9 == 0 else i % 60}</PercentComplete>"
            f"<Summary>0</Summary><Milestone>0</Milestone>"
            f"<Baseline><Number>0</Number><Duration>PT8H0M0S</Duration></Baseline>"
            f"{pred}</Task>")
        jour += 1
        if jour > 28:
            jour, mois = 1, mois + 1
            if mois > 12:
                mois = 1
    lignes.append("</Tasks></Project>")
    chemin.write_text("\n".join(lignes), encoding="utf-8")
    return chemin.stat().st_size


def pic_mo(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as f:
            for ligne in f:
                if ligne.startswith("VmHWM:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


taille = generer(NOMBRE, CHEMIN)
print(f"plan généré : {NOMBRE} tâches, {taille / 1048576:.1f} Mo ({CHEMIN})")

debut = time.monotonic()
proc = subprocess.Popen(
    [sys.executable, "/app/montecarlo.py", str(CHEMIN), "--sims", str(SIMS)],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
pic = 0
while proc.poll() is None:
    pic = max(pic, pic_mo(proc.pid))
    time.sleep(0.3)
sortie, erreur = proc.communicate()
duree = time.monotonic() - debut

print(f"simulations : {SIMS}")
print(f"durée : {duree:.1f} s | pic mémoire : {pic} Mo | code {proc.returncode}")
for ligne in (sortie or "").splitlines():
    if ligne.startswith(("Taches de detail", "Liens de dependance", "Taches gelees",
                         "Duree deterministe", "P50", "P80", "P90")):
        print("  " + ligne.strip())
if proc.returncode != 0:
    print("  stderr :", (erreur or "")[-400:])
