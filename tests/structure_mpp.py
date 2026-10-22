#!/usr/bin/env python3
"""Inspecte la structure interne d'un .mpp (conteneur OLE) sans le charger.

But : voir si un flux interne est démesuré par rapport au fichier — le signe d'une
structure anormale, qui expliquerait qu'un fichier de 1,9 Mo ne puisse pas être lu
même avec 4 Go de tas.

Usage : python3 structure_mpp.py /m/fichier.mpp
"""
import glob
import os
import sys
import time

import jpype
import mpxj

chemin = sys.argv[1]
mpxj.startJVM()
print(f"=== {os.path.basename(chemin)} : {os.path.getsize(chemin)} octets ===", flush=True)

Fichier = jpype.JClass("java.io.File")
POIFS = jpype.JClass("org.apache.poi.poifs.filesystem.POIFSFileSystem")

taille_fichier = os.path.getsize(chemin)
systeme = POIFS(Fichier(chemin))
racine = systeme.getRoot()
lignes = []


def parcourir(entree, prefixe=""):
    nom = str(entree.getName())
    complet = (prefixe + "/" + nom) if prefixe else nom
    if entree.isDirectoryEntry():
        enfants = entree.getEntries()
        while enfants.hasNext():
            parcourir(enfants.next(), complet)
        return
    lignes.append((int(entree.getSize()), complet))


parcourir(racine)
lignes.sort(reverse=True)
total = sum(t for t, _ in lignes)
print(f"{'taille':>12} {'part':>7}  flux")
for taille, nom in lignes[:20]:
    print(f"{taille:>12} {taille / taille_fichier * 100:6.1f} %  {nom}")
print(f"\nflux listés : {len(lignes)} | total déclaré : {total} octets "
      f"({total / taille_fichier:.2f} x la taille du fichier)")
print(f"pic mémoire du processus : ", end="", flush=True)
with open("/proc/self/status", encoding="ascii", errors="replace") as f:
    for ligne in f:
        if ligne.startswith("VmHWM:"):
            print(f"{int(ligne.split()[1]) // 1024} Mo")
            break
