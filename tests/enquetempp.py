#!/usr/bin/env python3
"""Enquête sur un .mpp qui fait exploser la lecture : que dit le fichier de lui-même ?

Deux lectures très légères, qui n'ont pas besoin de déplier les données datées :
  - les propriétés du projet (dates de début/fin, date d'état, calendrier) ;
  - la surface complète des options de lecture de MPXJ, pour savoir s'il existe un
    moyen officiel de ne pas construire ces données.

Usage : python3 enquetempp.py /m/fichier.mpp
"""
import sys

import jpype
import mpxj

chemin = sys.argv[1]
mpxj.startJVM()

MPPReader = jpype.JClass("org.mpxj.mpp.MPPReader")


def pic_mo():
    with open("/proc/self/status", encoding="ascii", errors="replace") as f:
        for ligne in f:
            if ligne.startswith("VmHWM:"):
                return int(ligne.split()[1]) // 1024
    return 0


print(f"=== {chemin} ===", flush=True)

print("\n--- propriétés seules (lecture légère) ---", flush=True)
try:
    lecteur = MPPReader()
    lecteur.setReadPropertiesOnly(True)
    projet = lecteur.read(chemin)
    if projet is None:
        print("  lecteur : rien reconnu")
    else:
        proprietes = projet.getProjectProperties()
        for nom in ("getStartDate", "getFinishDate", "getStatusDate", "getName",
                    "getDefaultStartTime", "getDefaultEndTime", "getMinutesPerDay",
                    "getMinutesPerWeek", "getDaysPerMonth", "getProjectTitle",
                    "getAuthor", "getLastSaved"):
            try:
                valeur = getattr(proprietes, nom)()
                print(f"  {nom[3:]:22s} : {valeur}")
            except Exception as erreur:
                print(f"  {nom[3:]:22s} : (illisible : {str(erreur)[:60]})")
        print(f"  tâches lues : {len(list(projet.getTasks()))}")
    print(f"  pic mémoire : {pic_mo()} Mo", flush=True)
except Exception as erreur:
    print(f"  ÉCHEC : {str(erreur)[:300]} (pic {pic_mo()} Mo)", flush=True)

print("\n--- options de lecture disponibles dans MPXJ ---", flush=True)
for classe in ("org.mpxj.mpp.MPPReader", "org.mpxj.reader.ProjectReader",
               "org.mpxj.ProjectConfig", "org.mpxj.mpp.TimephasedDataFactory",
               "org.mpxj.TimephasedWork"):
    try:
        c = jpype.JClass(classe)
        methodes = sorted({str(m.getName()) for m in c.class_.getMethods()})
        print(f"  {classe.split('.')[-1]} : {', '.join(methodes[:28])}")
    except Exception as erreur:
        print(f"  {classe} : illisible ({str(erreur)[:60]})")
