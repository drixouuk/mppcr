#!/usr/bin/env python3
"""Recense les éléments de travail datés incohérents (début postérieur à la fin).

MPXJ signale ces éléments via l'API Log4j, qui n'émet rien sans moteur de
journalisation : on ajoute log4j-core au classpath pour voir les messages, puis on
compte les affectations concernées et on relève les dates en cause (données de
planning non affichées : uniquement des horodatages).

Usage : python3 inventaire_inversions.py /m/fichier.mpp
"""
import sys

import jpype

jpype.addClassPath("/m/log4j-core.jar")
import mpxj  # noqa: E402

chemin = sys.argv[1]
mpxj.startJVM()

Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
projet = Lecteur().read(chemin)
taches = list(projet.getTasks())
affectations = list(projet.getResourceAssignments())
print(f"lecture terminée : {len(taches)} tâches, {len(affectations)} affectations", flush=True)

inversions, exemples, lues = 0, [], 0
for affectation in affectations:
    for methode in ("getTimephasedWork", "getTimephasedCost",
                    "getTimephasedBaselineWork", "getTimephasedBaselineCost"):
        try:
            donnees = getattr(affectation, methode)()
        except Exception:
            continue
        if donnees is None:
            continue
        for element in donnees:
            try:
                debut, fin = element.getStart(), element.getFinish()
            except Exception:
                continue
            lues += 1
            if debut is not None and fin is not None and debut.isAfter(fin):
                inversions += 1
                if len(exemples) < 3:
                    exemples.append((str(debut), str(fin)))
print(f"entrées datées lues : {lues}")
print(f"entrées inversées restantes : {inversions}")
for debut, fin in exemples:
    print(f"   exemple : début {debut} > fin {fin}")
