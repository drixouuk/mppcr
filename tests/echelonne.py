#!/usr/bin/env python3
"""Mécanisme : est-ce le VOLUME DE DONNÉES DATÉES qui fait exploser la mémoire ?

On garde le nombre de tâches ridiculement petit (100, comme le fichier réel qui a
échoué) mais on augmente le nombre d'entrées échelonnées par affectation —
l'avancement saisi période par période dans MS Project. C'est le seul poste qui
peut rendre un .mpp de 2 Mo très lourd en mémoire.

Usage (dans un conteneur de l'image) :
    python3 echelonne.py /m
"""
import os
import subprocess
import sys
import time
from pathlib import Path

DCMA = Path("/app/dcma14.py")
DOSSIER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")
TACHES = 100


def generer(entrees_par_affectation, chemin):
    """100 tâches, 100 affectations, N entrées échelonnées chacune."""
    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Project xmlns="http://schemas.microsoft.com/project">',
        "<Name>Plan avec avancement echelonne</Name>",
        "<StatusDate>2026-10-05T08:00:00</StatusDate>",
        "<MinutesPerDay>480</MinutesPerDay>",
        "<Resources>",
        "<Resource><UID>1</UID><ID>1</ID><Name>Equipe</Name><Type>0</Type><MaxUnits>1</MaxUnits></Resource>",
        "</Resources>",
        "<Tasks>",
    ]
    for i in range(1, TACHES + 1):
        lignes.append(
            f"<Task><UID>{i}</UID><ID>{i}</ID><Name>Tache {i}</Name>"
            "<Duration>PT8H0M0S</Duration><Start>2026-09-01T08:00:00</Start>"
            "<Finish>2026-09-01T17:00:00</Finish><Summary>0</Summary><Milestone>0</Milestone>"
            + (f"<PredecessorLink><PredecessorUID>{i-1}</PredecessorUID><Type>1</Type>"
               "<LinkLag>0</LinkLag></PredecessorLink>" if i > 1 else "")
            + "</Task>")
    lignes.append("</Tasks>")
    lignes.append("<Assignments>")
    for i in range(1, TACHES + 1):
        echelonne = "".join(
            "<TimephasedData><Type>1</Type>"
            f"<UID>{i}</UID><Start>2026-09-01T08:00:00</Start>"
            "<Finish>2026-09-01T17:00:00</Finish><Unit>2</Unit><Value>PT8H0M0S</Value>"
            "</TimephasedData>" for _ in range(entrees_par_affectation))
        lignes.append(
            f"<Assignment><UID>{i}</UID><TaskUID>{i}</TaskUID><ResourceUID>1</ResourceUID>"
            f"<Units>1</Units><Work>PT8H0M0S</Work>{echelonne}</Assignment>")
    lignes.append("</Assignments></Project>")
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
    return Path(chemin).stat().st_size, TACHES * entrees_par_affectation


def pic_mo(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as f:
            for ligne in f:
                if ligne.startswith("VmHWM:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def lancer(fichier, xmx):
    base = os.environ.get("JAVA_TOOL_OPTIONS", "")
    reste = " ".join(p for p in base.split() if not p.startswith("-Xmx"))
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONIOENCODING": "UTF-8",
           "JAVA_TOOL_OPTIONS": f"-Xmx{xmx}m {reste}".strip()}
    debut = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(DCMA), str(fichier), "--details"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=env, cwd="/app")
    pic = 0
    while proc.poll() is None:
        pic = max(pic, pic_mo(proc.pid))
        time.sleep(0.2)
    sortie, erreur = proc.communicate()
    memoire = "OutOfMemoryError" in sortie or "OutOfMemoryError" in (erreur or "")
    etat = "OK" if proc.returncode == 0 else ("ÉCHEC MÉMOIRE" if memoire else f"échec {proc.returncode}")
    return etat, pic, time.monotonic() - debut, (erreur or "").strip()[:160], sortie[-300:] if memoire else ""


print("=== 100 tâches, avancement daté croissant ===", flush=True)
for entrees in (100, 1000, 2000):
    chemin = DOSSIER / f"echelonne_{entrees}.xml"
    taille, total = generer(entrees, chemin)
    print(f"  {entrees:>5} entrées/affectation = {total:>8} entrées datées, "
          f"fichier {taille/1048576:6.2f} Mo", flush=True)
    for xmx in (768, 2048):
        etat, pic, duree, erreur, extrait = lancer(chemin, xmx)
        print(f"        -Xmx{xmx:<5}m {etat:<14s} pic={pic:>5} Mo  durée={duree:>6.1f} s", flush=True)
        if erreur:
            print(f"            stderr : {erreur}", flush=True)
        if extrait:
            print(f"            stdout : {extrait[-160:]}", flush=True)
print("=== fin ===", flush=True)
