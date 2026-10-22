#!/usr/bin/env python3
"""Le lecteur restitue-t-il les liens dans les deux sens, en .mpp et en XML ?

Compare, pour le MÊME projet fourni dans les deux formats, ce que la bibliothèque
rend comme prédécesseurs et comme successeurs. Un écart systématique sur les
successeurs désignerait une limite de lecture du format binaire, et non un défaut
du contrôle 1 (qui ne peut signaler que ce que le lecteur voit).

Usage : python3 comparer_liens.py /m/un.mpp /m/un.xml
"""
import sys

import jpype
import mpxj

mpxj.startJVM()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")


def compte(valeur):
    if valeur is None:
        return 0
    try:
        return len(list(valeur))
    except TypeError:
        return 0


def analyser(chemin):
    projet = Lecteur().read(chemin)
    taches = [t for t in projet.getTasks()
              if t.getName() is not None and not t.getSummary()]
    total_preds = total_succs = 0
    avec_preds = avec_succs = 0
    paires = []
    for tache in taches:
        preds = compte(tache.getPredecessors())
        succs = compte(tache.getSuccessors())
        total_preds += preds
        total_succs += succs
        avec_preds += 1 if preds else 0
        avec_succs += 1 if succs else 0
        paires.append((str(tache.getName())[:18], preds, succs))
    return taches, total_preds, total_succs, avec_preds, avec_succs, paires


for chemin in sys.argv[1:]:
    taches, total_preds, total_succs, avec_preds, avec_succs, paires = analyser(chemin)
    print(f"### {chemin.split('/')[-1]}")
    print(f"    tâches de détail : {len(taches)}")
    print(f"    prédécesseurs : {total_preds} liens, {avec_preds} tâches en ont")
    print(f"    successeurs   : {total_succs} liens, {avec_succs} tâches en ont")
    print(f"    premières tâches (nom, preds, succs) : {paires[:6]}")
    print()
