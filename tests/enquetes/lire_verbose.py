#!/usr/bin/env python3
"""Lit un .mpp et affiche l erreur complète en cas de rejet."""
import sys
import jpype
import mpxj

chemin = sys.argv[1]
mpxj.startJVM()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
try:
    projet = Lecteur().read(chemin)
    print("lecture OK :", len(list(projet.getTasks())), "taches")
except Exception as erreur:
    print("type :", type(erreur).__name__)
    print("message :", str(erreur)[:1500])
    for attribut in ("stackTrace", "cause", "getCause"):
        try:
            valeur = getattr(erreur, attribut)
            if callable(valeur):
                valeur = valeur()
            print(f"{attribut} :", str(valeur)[:800])
        except Exception:
            pass
