#!/usr/bin/env python3
"""Que simule réellement le Monte Carlo ? Recense les catégories de tâches du graphe.

Reproduit la construction du graphe de montecarlo.py pour compter, parmi les
tâches simulées : achevées, externes, jalons — c'est-à-dire celles auxquelles une
incertitude ne devrait pas être attribuée.

Usage : python3 audit_montecarlo.py /m/fichier.mpp
"""
import sys

import jpype
import mpxj

mpxj.startJVM()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")

chemin = sys.argv[1]
projet = Lecteur().read(chemin)

taches = [t for t in projet.getTasks()
          if t is not None and t.getName() is not None and not t.getSummary()]

categories = {"normale": [], "achevee": [], "externe": [], "jalon": []}
for t in taches:
    try:
        pct = t.getPercentageComplete()
        achevee = pct is not None and float(pct) >= 100.0
    except Exception:
        achevee = False
    try:
        externe = bool(t.getExternalTask())
    except Exception:
        externe = False
    try:
        jalon = bool(t.getMilestone())
    except Exception:
        jalon = False
    if achevee:
        categories["achevee"].append(t)
    elif externe:
        categories["externe"].append(t)
    elif jalon:
        categories["jalon"].append(t)
    else:
        categories["normale"].append(t)

print(f"=== {chemin.split('/')[-1]} ===")
print(f"tâches du graphe Monte Carlo : {len(taches)}")
for nom, liste in categories.items():
    print(f"  {nom:10s} : {len(liste):4d}")
print()

# % d'avancement des tâches « achevées » et durée restante quand elle existe
if categories["achevee"]:
    print("  Détail des tâches achevées (aujourd'hui simulées avec une incertitude) :")
    for t in categories["achevee"][:6]:
        try:
            restante = t.getRemainingDuration()
            restante = "?" if restante is None else f"{float(restante.getDuration()):.1f} {restante.getUnits()}"
        except Exception:
            restante = "?"
        print(f"    {str(t.getName())[:34]:34s} durée planifiée "
              f"{float(t.getDuration().getDuration()):.1f} {t.getDuration().getUnits()} | "
              f"restante {restante}")
