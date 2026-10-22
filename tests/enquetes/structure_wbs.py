#!/usr/bin/env python3
"""Affiche la structure WBS d'un planning : niveaux, tâches récapitulatives, feuilles.

Usage : python3 structure_wbs.py /m/fichier.xml
"""
import sys
import jpype
import mpxj

mpxj.startJVM()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")

for chemin in sys.argv[1:]:
    projet = Lecteur().read(chemin)
    print(f"### {chemin.split('/')[-1]}")
    niveaux = {}
    for t in projet.getTasks():
        if t is None or t.getName() is None:
            continue
        try:
            niveau = int(t.getOutlineLevel() or 0)
        except Exception:
            niveau = -1
        recap = bool(t.getSummary())
        niveaux.setdefault(niveau, {"recap": 0, "detail": 0})
        niveaux[niveau]["recap" if recap else "detail"] += 1
        enfants = t.getChildTasks()
        nb_enfants = len(list(enfants)) if enfants is not None else 0
        if recap and niveau <= 2:
            print(f"    niveau {niveau}  RECAP  {str(t.getName())[:40]:40s} {nb_enfants} enfant(s)  "
                  f"#{int(t.getUniqueID())}")
    print(f"    niveaux : " + " | ".join(
        f"{n}: {v['recap']} recap / {v['detail']} détail" for n, v in sorted(niveaux.items())))
    print()
