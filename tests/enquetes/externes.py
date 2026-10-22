#!/usr/bin/env python3
"""Piste « liens externes / dépendances cycliques » : est-ce que ça fait exploser l'analyse ?

Trois réseaux minuscules (100 tâches) mais pathologiques, là où mes tests
précédents étaient tous des graphes acycliques bien propres :

  A. cycle : la chaîne principale revient sur elle-même (10 -> 20 -> ... -> 90 -> 10) ;
  B. auto-dépendance : une tâche se déclare son propre prédécesseur ;
  C. tâches EXTERNES : des tâches marquées comme appartenant à un autre projet
     (lien inter-projets), avec des liens vers elles — le cas décrit par
     l'utilisateur, où un seul .mpp est chargé et les cibles sont introuvables.

Usage (dans un conteneur de l'image) :
    python3 externes.py /m
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DCMA = Path("/app/dcma14.py")
DOSSIER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")
LIMITE = 90
TACHES = 100


def tache(uid, preds, externe=False, projet=None):
    liens = "".join(f"<PredecessorLink><PredecessorUID>{p}</PredecessorUID><Type>1</Type>"
                    f"<LinkLag>0</LinkLag></PredecessorLink>" for p in preds)
    externe_xml = ""
    if externe:
        externe_xml = "<ExternalTask>1</ExternalTask>"
        if projet:
            externe_xml += f"<ExternalTaskProject>{projet}</ExternalTaskProject>"
    return (f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>Tache {uid}</Name>"
            "<Duration>PT8H0M0S</Duration><Start>2026-09-01T08:00:00</Start>"
            "<Finish>2026-09-01T17:00:00</Finish><Summary>0</Summary><Milestone>0</Milestone>"
            f"{externe_xml}{liens}</Task>")


def entete(nom):
    return ['<?xml version="1.0" encoding="UTF-8"?>',
            '<Project xmlns="http://schemas.microsoft.com/project">',
            f"<Name>{nom}</Name>",
            "<StatusDate>2026-10-05T08:00:00</StatusDate>",
            "<MinutesPerDay>480</MinutesPerDay>", "<Tasks>"]


def ecrire(chemin, lignes):
    lignes = lignes + ["</Tasks></Project>"]
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
    return Path(chemin).stat().st_size


def cas_cycle(chemin):
    lignes = entete("Cycle de dependances")
    for i in range(1, TACHES + 1):
        preds = [i - 1] if i > 1 else []
        if i == 10:          # la chaîne revient sur elle-même : 10 <- 100 <- 99 <- …
            preds = [TACHES]
        lignes.append(tache(i, preds))
    return ecrire(chemin, lignes)


def cas_auto(chemin):
    lignes = entete("Auto dependance")
    for i in range(1, TACHES + 1):
        preds = [i] if i == 50 else ([i - 1] if i > 1 else [])
        lignes.append(tache(i, preds))
    return ecrire(chemin, lignes)


def cas_externe(chemin):
    """50 tâches externes (autre projet) + 50 tâches locales qui en dépendent."""
    lignes = entete("Liens inter-projets")
    for i in range(1, 51):
        lignes.append(tache(i, [], externe=True, projet="Autre_Planning.mpp"))
    for i in range(51, TACHES + 1):
        lignes.append(tache(i, [i - 50, i - 1 if i > 51 else 1]))
    return ecrire(chemin, lignes)


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
                            env=env, cwd="/app", start_new_session=True)
    pic = 0
    while proc.poll() is None:
        pic = max(pic, pic_mo(proc.pid))
        if time.monotonic() - debut > LIMITE:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()
            return f"INTERROMPU >{LIMITE} s", pic, time.monotonic() - debut, "", ""
        time.sleep(0.2)
    sortie, erreur = proc.communicate()
    memoire = "OutOfMemoryError" in sortie or "OutOfMemoryError" in (erreur or "")
    etat = "OK" if proc.returncode == 0 else ("ÉCHEC MÉMOIRE" if memoire else f"échec {proc.returncode}")
    extrait = ""
    if proc.returncode != 0:
        extrait = ((erreur or "").strip() or (sortie or "").strip())[-200:]
    return etat, pic, time.monotonic() - debut, extrait, sortie[-200:] if proc.returncode == 0 else ""


print(f"=== réseaux pathologiques de {TACHES} tâches (limite {LIMITE} s par essai) ===", flush=True)
for etiquette, generateur in (("A. cycle", cas_cycle), ("B. auto-dépendance", cas_auto),
                              ("C. tâches externes", cas_externe)):
    chemin = DOSSIER / f"patho_{etiquette.split('.')[0].strip().lower()}.xml"
    taille = generateur(chemin)
    print(f"### {etiquette} — {taille} octets ###", flush=True)
    for xmx in (768, 2048):
        etat, pic, duree, extrait, fin = lancer(chemin, xmx)
        print(f"    -Xmx{xmx:<5}m {etat:<16s} pic={pic:>5} Mo  durée={duree:>6.1f} s", flush=True)
        if extrait:
            print(f"        message : {extrait}", flush=True)
        if fin:
            derniere = [l for l in fin.splitlines() if l.strip()]
            if derniere:
                print(f"        dernière ligne : {derniere[-1][:150]}", flush=True)
print("=== fin ===", flush=True)
