#!/usr/bin/env python3
"""Enveloppe mémoire en fonction de la TAILLE DU MODÈLE (pas du fichier).

Génère des plans MSPDI à N tâches (deux séries : squelette léger, puis
affectations avec données échelonnées, qui pèsent lourd dans les vrais
plannings) et relève, pour chaque tas JVM testé, la réussite et le pic de
mémoire du sous-processus.

Usage (dans un conteneur de l'image, avec JAVA_TOOL_OPTIONS) :
    python3 enveloppe_taches.py /m sortie.txt
"""
import os
import subprocess
import sys
import time
from pathlib import Path

SORTIE = Path(sys.argv[2]) if len(sys.argv) > 2 else None
DOSSIER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")
DCMA = Path("/app/dcma14.py")


def journal(message):
    print(message, flush=True)
    if SORTIE:
        with SORTIE.open("a", encoding="utf-8") as f:
            f.write(message + "\n")


def generer(nb, chemin, avec_affectations):
    """Plan MSPDI : tâches, liens FS, baselines ; option affectations échelonnées."""
    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Project xmlns="http://schemas.microsoft.com/project">',
        f"<Name>Plan de test {nb} taches</Name>",
        "<StatusDate>2026-10-05T08:00:00</StatusDate>",
        "<MinutesPerDay>480</MinutesPerDay>",
    ]
    if avec_affectations:
        lignes.append("<Resources>")
        for r in range(1, 41):
            lignes.append(
                f"<Resource><UID>{r}</UID><ID>{r}</ID><Name>Ressource {r}</Name>"
                f"<Type>0</Type><MaxUnits>1</MaxUnits><StandardRate>35</StandardRate>"
                "</Resource>")
        lignes.append("</Resources>")
    lignes.append("<Tasks>")

    jour, mois, annee = 1, 9, 2026
    for i in range(1, nb + 1):
        debut = f"{annee}-{mois:02d}-{jour:02d}T08:00:00"
        fin = f"{annee}-{mois:02d}-{jour:02d}T17:00:00"
        contrainte = "<ConstraintType>2</ConstraintType>" if i % 97 == 0 else "<ConstraintType>0</ConstraintType>"
        pred = ""
        if i > 1 and i % 7:
            pred = (f"<PredecessorLink><PredecessorUID>{i - 1}</PredecessorUID>"
                    f"<Type>1</Type><LinkLag>0</LinkLag></PredecessorLink>")
        resume = 1 if i % 250 == 0 else 0
        corps = (
            f"<Task><UID>{i}</UID><ID>{i}</ID><Name>Tache {i} du planning</Name>"
            f"<Duration>PT8H0M0S</Duration><DurationFormat>7</DurationFormat>"
            f"<Start>{debut}</Start><Finish>{fin}</Finish>"
            f"<PercentComplete>{100 if i % 9 == 0 else i % 60}</PercentComplete>"
            f"<Summary>{resume}</Summary><Milestone>0</Milestone>"
            f"<OutlineLevel>{1 + (i % 3)}</OutlineLevel>{contrainte}"
            f"<Baseline><Number>0</Number><Duration>PT8H0M0S</Duration></Baseline>"
            f"{pred}</Task>")
        lignes.append(corps)
        jour += 1
        if jour > 28:
            jour, mois = 1, mois + 1
            if mois > 12:
                mois, annee = 1, annee + 1
    lignes.append("</Tasks>")

    if avec_affectations:
        lignes.append("<Assignments>")
        uid = 0
        for i in range(1, nb + 1):
            if i % 250 == 0:
                continue
            for k in range(2):
                uid += 1
                res = (i + k * 7) % 40 + 1
                echelonne = ""
                if k == 0:
                    # Données échelonnées : ce que produisent les vraies saisies
                    # d'avancement dans MS Project.
                    for semaine in range(3):
                        echelonne += (
                            "<TimephasedData><Type>1</Type>"
                            f"<UID>{uid}</UID><Start>2026-09-0{1 + semaine}T08:00:00</Start>"
                            f"<Finish>2026-09-0{2 + semaine}T17:00:00</Finish>"
                            "<Unit>2</Unit><Value>PT0H0M0S</Value></TimephasedData>")
                lignes.append(
                    f"<Assignment><UID>{uid}</UID><TaskUID>{i}</TaskUID>"
                    f"<ResourceUID>{res}</ResourceUID><Units>1</Units>"
                    f"<Work>PT16H0M0S</Work><Start>2026-09-01T08:00:00</Start>"
                    f"<Finish>2026-09-03T17:00:00</Finish>{echelonne}</Assignment>")
        lignes.append("</Assignments>")
    lignes.append("</Project>")
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
    return Path(chemin).stat().st_size


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
    options = " ".join(p for p in base.split() if not p.startswith("-Xmx"))
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONIOENCODING": "UTF-8",
           "JAVA_TOOL_OPTIONS": f"-Xmx{xmx}m {options}".strip()}
    debut = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(DCMA), str(fichier), "--details"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env,
                            cwd="/app" if Path("/app").exists() else None)
    pic = 0
    while proc.poll() is None:
        pic = max(pic, pic_mo(proc.pid))
        time.sleep(0.2)
    sortie, erreur = proc.communicate()
    duree = time.monotonic() - debut
    memoire = "OutOfMemoryError" in sortie or "OutOfMemoryError" in (erreur or "")
    if proc.returncode != 0:
        etat = "ÉCHEC MÉMOIRE" if memoire else f"échec {proc.returncode}"
    else:
        etat = "OK"
    return etat, pic, duree


def serie(titre, effectifs, avec_affectations, tas):
    journal("")
    journal(f"### {titre} ###")
    for nb in effectifs:
        chemin = DOSSIER / f"plan_{nb}{'_aff' if avec_affectations else ''}.xml"
        taille = generer(nb, chemin, avec_affectations)
        journal(f"  {nb:>6} tâches  fichier {taille / 1048576:6.2f} Mo")
        for xmx in tas:
            etat, pic, duree = lancer(chemin, xmx)
            journal(f"         -Xmx{xmx:<5}m {etat:<14s} pic={pic:>5} Mo  durée={duree:>6.1f} s")
            if etat == "OK" and xmx == tas[0]:
                pass


journal(f"=== enveloppe mémoire par taille de modèle — {time.strftime('%Y-%m-%d %H:%M')} ===")
journal(f"options de base : {os.environ.get('JAVA_TOOL_OPTIONS', '(aucune)')}")

serie("A) squelette : tâches + liens + baselines",
      [2000, 6000, 12000, 24000], False, [768, 2048])
serie("B) plan complet : + 40 ressources et affectations échelonnées",
      [500, 1500, 3000, 6000], True, [768, 2048])

journal("")
journal("=== fin ===")
