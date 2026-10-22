#!/usr/bin/env python3
"""Reconstruit un .mpp sans certains flux internes, puis tente de le lire.

Sert à isoler la partie du fichier qui fait exploser le lecteur : on retire un
flux (ou une famille de flux), on réécrit le conteneur OLE avec POI, puis on lit
le résultat. Si la lecture redevient normale, le coupable est identifié — et le
nettoyage peut devenir une étape de l'application.

Usage :
    python3 nettoyer_flux.py source.mpp copie.mpp "CV_iew"
    python3 nettoyer_flux.py source.mpp copie.mpp ""        # copie témoin
"""
import os
import sys
import time

import jpype
import mpxj

source, destination, exclusion = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "")

mpxj.startJVM()
File = jpype.JClass("java.io.File")
POIFS = jpype.JClass("org.apache.poi.poifs.filesystem.POIFSFileSystem")
FileOutputStream = jpype.JClass("java.io.FileOutputStream")
DIS = jpype.JClass("org.apache.poi.poifs.filesystem.DocumentInputStream")

origine = POIFS(File(source))
copie = POIFS()
compteur = {"flux": 0, "retires": 0, "octets": 0}


def copier(entree, parent, chemin):
    nom = str(entree.getName())
    complet = f"{chemin}/{nom}" if chemin else nom
    if entree.isDirectoryEntry():
        if exclusion and exclusion in complet:
            compteur["retires"] += 1
            return
        cible = parent.createDirectory(nom)
        enfants = entree.getEntries()
        while enfants.hasNext():
            copier(enfants.next(), cible, complet)
        return
    if exclusion and exclusion in complet:
        compteur["retires"] += 1
        return
    flux = DIS(entree)
    parent.createDocument(nom, flux)
    compteur["flux"] += 1
    compteur["octets"] += int(entree.getSize())


copier(origine.getRoot(), copie, "")
sortie = FileOutputStream(destination)
copie.writeFilesystem(sortie)
sortie.close()

print(f"copie : {compteur['flux']} flux conservés ({compteur['octets']} octets), "
      f"{compteur['retires']} retirés" + (f" (filtre « {exclusion} »)" if exclusion else " (témoin)"))
print(f"taille obtenue : {os.path.getsize(destination)} octets")

mpxj.startJVM() if False else None
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
debut = time.monotonic()
try:
    projet = Lecteur().read(destination)
    taches = len(list(projet.getTasks()))
    pic = 0
    with open("/proc/self/status", encoding="ascii", errors="replace") as f:
        for ligne in f:
            if ligne.startswith("VmHWM:"):
                pic = int(ligne.split()[1]) // 1024
    print(f"LECTURE OK en {time.monotonic() - debut:.1f} s : {taches} tâches, pic {pic} Mo")
except Exception as erreur:
    pic = 0
    with open("/proc/self/status", encoding="ascii", errors="replace") as f:
        for ligne in f:
            if ligne.startswith("VmHWM:"):
                pic = int(ligne.split()[1]) // 1024
    print(f"LECTURE ÉCHOUÉE en {time.monotonic() - debut:.1f} s (pic {pic} Mo) : {str(erreur)[:200]}")
