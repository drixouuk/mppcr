#!/usr/bin/env python3
"""Isole l'étape exacte qui fait exploser la mémoire.

  A : read() seul, puis nombre de tâches
  B : + parcours des tâches et des liens
  C : + parcours des affectations et de leur travail (sans données datées)
  D : + appel explicite aux données datées (getTimephasedWork)

Chaque étape annonce sa durée et le pic mémoire du processus : la première qui
explose désigne le déclencheur.

Usage : python3 etapes.py /m/fichier.mpp A|B|C|D
"""
import sys
import time

import jpype
import mpxj

chemin, etape = sys.argv[1], sys.argv[2]
mpxj.startJVM()


def pic_mo():
    with open("/proc/self/status", encoding="ascii", errors="replace") as f:
        for ligne in f:
            if ligne.startswith("VmHWM:"):
                return int(ligne.split()[1]) // 1024
    return 0


def journal(message):
    print(f"  [{time.monotonic() - debut:6.1f} s] {message} (pic {pic_mo()} Mo)", flush=True)


debut = time.monotonic()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
projet = Lecteur().read(chemin)
journal(f"A. lecture : {len(list(projet.getTasks()))} tâches")

if etape in ("B", "C", "D"):
    liens = list(projet.getTaskLinks())
    ressources = list(projet.getResources())
    for tache in projet.getTasks():
        tache.getName(), tache.getDuration(), tache.getStart(), tache.getFinish()
    journal(f"B. tâches et liens : {len(liens)} liens, {len(ressources)} ressources")

if etape in ("C", "D"):
    affectations = list(projet.getResourceAssignments())
    travail = 0
    for affectation in affectations:
        for accesseur in ("getWork", "getRemainingWork", "getCost", "getUnits",
                          "getStart", "getFinish"):
            try:
                getattr(affectation, accesseur)()
            except Exception:
                pass
        travail += 1
    journal(f"C. affectations : {travail} parcourues")
    for tache in projet.getTasks():
        for accesseur in ("getWork", "getRemainingWork", "getCost", "getBaselineWork"):
            try:
                getattr(tache, accesseur)()
            except Exception:
                pass
    journal("C. travail des tâches lu")

if etape == "D":
    total = 0
    for affectation in projet.getResourceAssignments():
        for methode in ("getTimephasedWork", "getTimephasedCost", "getTimephasedBaselineWork"):
            try:
                donnees = getattr(affectation, methode)()
                if donnees is not None:
                    total += len(list(donnees))
            except Exception:
                pass
    journal(f"D. données datées : {total} entrées")

journal("fin")
