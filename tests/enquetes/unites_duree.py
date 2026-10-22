#!/usr/bin/env python3
"""Que renvoie MPXJ pour une durée, et dans quelle unité ?

Enjeu : les contrôles 6 et 8 comparent une valeur à un seuil exprimé en JOURS.
Si la bibliothèque rend des heures (ou des minutes), la comparaison est fausse.
On affiche donc, pour chaque tâche : la valeur brute, son unité, et la valeur
convertie en jours ouvrés via le calendrier du projet.

Usage : python3 unites_duree.py /m/fichier.mpp
"""
import sys

import jpype
import mpxj

chemin = sys.argv[1]
mpxj.startJVM()

Fichier = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
Unite = jpype.JClass("org.mpxj.TimeUnit")

# Outils disponibles pour la conversion
Duree = jpype.JClass("org.mpxj.Duration")
print("=== méthodes utiles de Duration ===")
methodes = sorted({str(m.getName()) for m in Duree.class_.getMethods()})
print("  ", ", ".join(m for m in methodes if any(x in m.lower() for x in
      ("unit", "convert", "day", "hour", "duration"))) [:600])

projet = Fichier().read(chemin)
proprietes = projet.getProjectProperties()

calendrier = None
for accesseur in ("getDefaultCalendar", "getCalendar"):
    try:
        calendrier = getattr(proprietes, accesseur)()
        if calendrier is not None:
            print(f"=== calendrier obtenu par {accesseur}() : {calendrier.getName()} ===")
            break
    except Exception:
        continue
if calendrier is None:
    try:
        calendriers = list(projet.getCalendars())
        calendrier = calendriers[0] if calendriers else None
        print("=== calendrier : premier de la liste ===")
    except Exception as erreur:
        print("  (aucun calendrier exploitable :", str(erreur)[:80], ")")

for nom in ("getMinutesPerDay", "getMinutesPerWeek", "getDaysPerMonth",
            "getDefaultDurationUnits", "getDurationFormat"):
    try:
        print(f"  {nom[3:]:22s} : {getattr(proprietes, nom)()}")
    except Exception:
        pass

print()
print(f"{'tâche':22s} {'durée brute':>12} {'unité':>8} {'en jours':>10}  {'marge brute':>12} {'unité':>8}")
for tache in list(projet.getTasks())[:12]:
    duree = tache.getDuration()
    marge = tache.getTotalSlack()
    if duree is None:
        continue
    try:
        brut = float(duree.getDuration())
    except Exception:
        brut = "?"
    try:
        unite = str(duree.getUnits())
    except Exception:
        unite = "?"
    jours = "?"
    if calendrier is not None:
        try:
            jours = round(float(duree.convertUnits(Unite.DAYS, calendrier).getDuration()), 2)
        except Exception as erreur:
            jours = f"échec: {str(erreur)[:30]}"
    marge_brute = marge_unite = "?"
    if marge is not None:
        try:
            marge_brute = float(marge.getDuration())
            marge_unite = str(marge.getUnits())
        except Exception:
            pass
    print(f"{str(tache.getName())[:22]:22s} {brut!s:>12} {unite:>8} {jours!s:>10}  {marge_brute!s:>12} {marge_unite:>8}")
