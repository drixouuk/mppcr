#!/usr/bin/env python3
"""Diagnostic d'un .mpp qui épuise la mémoire : que contient-il, et où va la mémoire ?

Mesure séparément la LECTURE (MPXJ/POI) et l'analyse, en relevant le pic de
mémoire résident du processus — la JVM tourne dans le même processus (JPype), donc
VmHWM couvre les deux.

Usage (dans un conteneur de l'image, options Java de l'application) :
    python3 diagnostic_mpp.py /m/fichier.mpp
"""
import glob
import os
import sys
import time

import jpype
import mpxj


def pic_mo():
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            for ligne in f:
                if ligne.startswith("VmHWM:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def compte(objet, methodes):
    """Essaie plusieurs noms d'accesseur et rend (nom retenu, nombre)."""
    for methode in methodes:
        try:
            valeur = getattr(objet, methode)()
        except Exception:
            continue
        if valeur is None:
            continue
        try:
            return methode, len(list(valeur))
        except Exception:
            try:
                return methode, int(valeur)
            except Exception:
                return methode, -1
    return "aucun", -1


chemin = sys.argv[1]
print(f"=== {os.path.basename(chemin)} : {os.path.getsize(chemin)} octets ===", flush=True)
print(f"    options Java : {os.environ.get('JAVA_TOOL_OPTIONS', '(aucune)')}", flush=True)

debut = time.monotonic()
mpxj.startJVM()
print(f"    démarrage JVM : {time.monotonic() - debut:.1f} s, pic {pic_mo()} Mo", flush=True)

Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")

debut = time.monotonic()
projet = Lecteur().read(chemin)
lecture = time.monotonic() - debut
print(f"    LECTURE seule : {lecture:.1f} s, pic {pic_mo()} Mo", flush=True)

for etiquette, methodes in (
    ("tâches", ("getTasks",)),
    ("liens", ("getTaskLinks", "getRelations")),
    ("ressources", ("getResources",)),
    ("affectations", ("getResourceAssignments", "getAssignments")),
    ("calendriers", ("getCalendars",)),
    ("sous-projets", ("getSubProjects",)),
    ("champs personnalisés", ("getCustomFields",)),
):
    nom, nombre = compte(projet, methodes)
    print(f"    {etiquette:22s} {nombre:>10}   ({nom})", flush=True)

# Le poste suspect : les données datées (avancement saisi période par période).
total_aff, max_aff, total_tache = 0, 0, 0
accesseur_aff = "aucun"
for affectation in projet.getResourceAssignments():
    for methode in ("getTimephasedWork", "getTimephasedCost", "getTimephasedData"):
        try:
            donnees = getattr(affectation, methode)()
            if donnees is None:
                continue
            taille = len(list(donnees))
            accesseur_aff = methode
            total_aff += taille
            max_aff = max(max_aff, taille)
            break
        except Exception:
            continue
accesseur_tache = "aucun"
for tache in projet.getTasks():
    for methode in ("getTimephasedWork", "getTimephasedCost", "getTimephasedData"):
        try:
            donnees = getattr(tache, methode)()
            if donnees is None:
                continue
            total_tache += len(list(donnees))
            accesseur_tache = methode
            break
        except Exception:
            continue
print(f"    données datées affectations {total_aff:>10}  ({accesseur_aff}, max {max_aff}/affectation)",
      flush=True)
print(f"    données datées tâches       {total_tache:>10}  ({accesseur_tache})", flush=True)
print(f"    TOTAL entrées datées        {total_aff + total_tache:>10}", flush=True)
print(f"    pic après inventaire : {pic_mo()} Mo", flush=True)
