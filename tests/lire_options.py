#!/usr/bin/env python3
"""Essaie les options de lecture MPXJ sur un .mpp qui explose la mémoire.

  raw  : MPPReader + setUseRawTimephasedData(true)  -> ne déplie pas les données datées
  pres : MPPReader + setReadPresentationData(false) -> ignore vues et tables
  both : les deux
  ref  : UniversalProjectReader (comportement actuel de l'application)

Usage : python3 lire_options.py /m/fichier.mpp raw
"""
import os
import sys
import time

import jpype
import mpxj

chemin, mode = sys.argv[1], sys.argv[2]
mpxj.startJVM()

MPPReader = jpype.JClass("org.mpxj.mpp.MPPReader")
Universal = jpype.JClass("org.mpxj.reader.UniversalProjectReader")


def pic_mo():
    with open("/proc/self/status", encoding="ascii", errors="replace") as f:
        for ligne in f:
            if ligne.startswith("VmHWM:"):
                return int(ligne.split()[1]) // 1024
    return 0


if mode == "ref":
    lecteur = Universal()
else:
    lecteur = MPPReader()
    if mode in ("raw", "both"):
        lecteur.setUseRawTimephasedData(True)
    if mode in ("pres", "both"):
        lecteur.setReadPresentationData(False)

debut = time.monotonic()
try:
    projet = lecteur.read(chemin)
    if projet is None:
        print(f"{mode:5s} : lecteur n'a rien reconnu")
        sys.exit(1)
    taches = len(list(projet.getTasks()))
    liens = len(list(projet.getTaskLinks()))
    affectations = len(list(projet.getResourceAssignments()))
    echelonne = 0
    for affectation in projet.getResourceAssignments():
        for methode in ("getTimephasedWork", "getTimephasedCost"):
            try:
                donnees = getattr(affectation, methode)()
                if donnees is not None:
                    echelonne += len(list(donnees))
                    break
            except Exception:
                continue
    print(f"{mode:5s} : OK en {time.monotonic() - debut:5.1f} s | pic {pic_mo():>5} Mo | "
          f"{taches} tâches, {liens} liens, {affectations} affectations, "
          f"{echelonne} entrées datées")
except Exception as erreur:
    print(f"{mode:5s} : ÉCHEC en {time.monotonic() - debut:5.1f} s | pic {pic_mo():>5} Mo | "
          f"{str(erreur)[:160]}")
    sys.exit(1)
