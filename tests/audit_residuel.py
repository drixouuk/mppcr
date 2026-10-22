#!/usr/bin/env python3
"""Audit de ce qui reste comme texte lisible dans un .mpp assaini.

On relève toutes les suites de caractères imprimables (formes UTF-16 et ASCII) et
on les classe : ce qui ressemble à du texte naturel (mots) doit avoir disparu,
seuls les noms techniques du format doivent subsister.

Usage : python3 audit_residuel.py fichier.mpp
"""
import re
import sys

chemin = sys.argv[1]
brut = open(chemin, "rb").read()

# Noms techniques du format OLE et de MS Project : légitimes, sans valeur métier.
TECHNIQUE = re.compile(
    r"^(Root Entry|CompObj|Props|Props9|Props12|Props14|SummaryInformation|DocumentSummaryInformation"
    r"|MsoDataStore|CVba|CV_iew|CTable|CEdl|TBknd[A-Za-z0-9]+|Var2Data|VarMeta|FixedData|FixedMeta"
    r"|Fixed2Data|Fixed2Meta|FixedMeta|VBA|PROJECT|PROJECTwm|dir|Module[0-9]+|__SRP_[0-9]+"
    r"|_VBA_PROJECT|Contents|ThisDocument|Microsoft|MSProject|Standard|Item"
    r"|Janvier|Fevrier|Mars|Avril|Mai|Juin|Juillet|Aout|Septembre|Octobre|Novembre|Decembre"
    r"|Lundi|Mardi|Mercredi|Jeudi|Vendredi|Samedi|Dimanche"
    r"|January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    r"|EUR|USD|MAD|HT|Hr|day|week|month|year)[^a-zA-Z0-9]*$")

suites = set()

# UTF-16 (minuscules/capitales alternées avec des zéros) : on repère les paires.
for encodage, motif in (("utf-16-le", rb"(?:[\x20-\x7e]\x00){5,}"), ("ascii", rb"[\x20-\x7e]{5,}")):
    for trouve in re.finditer(motif, brut):
        texte = trouve.group(0).decode(encodage, "replace")
        suites.add(texte)

mots = []
techniques = []
for texte in suites:
    if TECHNIQUE.match(texte):
        techniques.append(texte)
        continue
    voyelles = sum(1 for c in texte.lower() if c in "aeiouyàéèêëîïôöûü")
    lettres = sum(1 for c in texte if c.isalpha())
    if lettres >= 5 and voyelles >= 2 and " " not in texte.strip()[:1]:
        mots.append(texte)

print(f"fichier : {chemin} ({len(brut)} octets)")
print(f"suites imprimables distinctes : {len(suites)}")
print(f"  dont noms techniques reconnus : {len(techniques)}")
print(f"  dont suites ressemblant à des mots : {len(mots)}")
for texte in sorted(mots, key=len, reverse=True)[:12]:
    print(f"    « {texte[:60]} » ({len(texte)} caractères)")
