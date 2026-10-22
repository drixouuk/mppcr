#!/usr/bin/env python3
"""Produit un .mpp de reproduction générique : deuxième version, exhaustive.

Leçon de la première tentative : énumérer les champs à la main ne suffit pas.
MPXJ rend par exemple les notes *converties* en texte brut alors que le fichier
stocke du RTF, si bien que le texte d'origine survivait dans le binaire. On
collecte donc TOUTES les valeurs texte du modèle par réflexion (tous les
accesseurs sans argument qui rendent une chaîne, sur le projet, chaque tâche,
chaque ressource, chaque affectation, chaque calendrier), puis on applique des
règles génériques sur tout ce qui reste lisible : blocs RTF, URL, chemins réseau,
adresses électroniques et suites de mots.

Trois mécanismes, sans jamais reconstruire le conteneur OLE :
  1. remplacement en place des valeurs texte du modèle, à longueur identique ;
  2. remplacement générique des suites lisibles non techniques ;
  3. vidage des flux du projet VBA (code source des macros) par réécriture de
     leur entrée de répertoire OLE.

Usage : python3 assainir_mpp.py source.mpp destination.mpp [--audit]
"""
import hashlib
import re
import struct
import sys

import jpype
import mpxj

source, destination = sys.argv[1], sys.argv[2]
brut = bytearray(open(source, "rb").read())
avant = len(brut)

mpxj.startJVM()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
projet = Lecteur().read(source)


def valeurs_texte(objet):
    """Toutes les chaînes exposées par un objet du modèle, via introspection."""
    trouvailles = []
    try:
        methodes = objet.getClass().getMethods()
    except Exception:
        return trouvailles
    for methode in methodes:
        try:
            nom = str(methode.getName())
            if not nom.startswith("get") or nom in ("getClass",):
                continue
            if methode.getParameterCount() != 0:
                continue
            if str(methode.getReturnType().getName()) != "java.lang.String":
                continue
            valeur = getattr(objet, nom)()
        except Exception:
            continue
        if valeur:
            texte = str(valeur)
            if len(texte) >= 3:
                trouvailles.append(texte)
    return trouvailles


chaines = []
chaines += valeurs_texte(projet.getProjectProperties())
for tache in projet.getTasks():
    chaines += valeurs_texte(tache)
for ressource in projet.getResources():
    chaines += valeurs_texte(ressource)
for affectation in projet.getResourceAssignments():
    chaines += valeurs_texte(affectation)
for calendrier in projet.getCalendars():
    chaines += valeurs_texte(calendrier)

print(f"valeurs texte relevées dans le modèle : {len(chaines)} "
      f"({len(set(chaines))} distinctes)")


def texte_generique(longueur, base):
    if longueur <= len(base):
        return base[:longueur]
    return (base + "x" * longueur)[:longueur]


remplaces = 0
for rang, texte in enumerate(sorted(set(chaines), key=len, reverse=True)):
    base = f"Element{rang:04d}"
    for encodage in ("utf-16-le", "utf-8"):
        octets = texte.encode(encodage)
        depart = 0
        while True:
            position = brut.find(octets, depart)
            if position < 0:
                break
            depart = position + 1
            if encodage == "utf-16-le":
                neuf = texte_generique(len(texte), base).encode("utf-16-le")
            else:
                neuf = texte_generique(len(octets), base).encode("utf-8")[:len(octets)]
                neuf = neuf.ljust(len(octets), b"x")
            brut[position:position + len(octets)] = neuf
            remplaces += 1

# ------------------------------------------------- règles génériques sur les suites
MOTIF_UUID = re.compile(r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}")
MOTIF_TECHNIQUE = re.compile(
    r"^(Root Entry|CompObj|Props[0-9]*|SummaryInformation|DocumentSummaryInformation"
    r"|MsoDataStore|CVba|CV_iew|CTable|CEdl|TBknd[A-Za-z0-9]+|Var2Data|VarMeta"
    r"|Fixed[0-9]?Data|Fixed[0-9]?Meta|VBA|PROJECT|PROJECTwm|dir|Module[0-9]+"
    r"|__SRP_[0-9]+|_VBA_PROJECT|Item|Microsoft|MSProject|Standard"
    r"|Janvier|Fevrier|Mars|Avril|Mai|Juin|Juillet|Aout|Septembre|Octobre|Novembre|Decembre"
    r"|Lundi|Mardi|Mercredi|Jeudi|Vendredi|Samedi|Dimanche"
    r"|January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    r"|Suite|Word|Excel|PowerPoint|Visio|Outlook|Access)[^a-zA-Z0-9]*$")
MOTIF_BALISE = re.compile(r"[<>{}]")
MOTIF_RTF = re.compile(r"\\(rtf|pard|fs[0-9]|f[0-9]|ansi|widctlpar|deflang)")
MOTIF_URL = re.compile(r"(https?://|www\.|ftp://|\\\\[a-zA-Z0-9]|[A-Za-z]:\\\\)")
MOTIF_COURRIEL = re.compile(r"[A-Za-z0-9._%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def suites_lisibles(donnees, encodage, motif):
    for trouve in re.finditer(motif, donnees):
        yield trouve.start(), trouve.group(0).decode(encodage, "replace")


generiques = 0
for encodage, motif in (("utf-16-le", rb"(?:[\x20-\x7e]\x00){6,}"), ("ascii", rb"[\x20-\x7e]{6,}")):
    remplacements = []
    for position, texte in suites_lisibles(bytes(brut), encodage, motif):
        if MOTIF_TECHNIQUE.match(texte) or MOTIF_UUID.match(texte):
            continue
        generique = False
        if MOTIF_URL.search(texte) or MOTIF_COURRIEL.search(texte):
            generique = True
        elif MOTIF_RTF.search(texte):
            generique = True
        elif not MOTIF_BALISE.search(texte):
            # Ni balise ni code : si cela ressemble à des mots, on neutralise.
            lettres = sum(1 for c in texte if c.isalpha())
            if lettres >= 5 and (" " in texte or lettres == len(texte)):
                generique = True
        if generique:
            remplacements.append((position, len(texte), encodage))
    for position, longueur, forme in remplacements:
        if forme == "utf-16-le":
            neuf = ("Element" + "x" * longueur)[:longueur].encode("utf-16-le")
        else:
            neuf = ("Element" + "x" * longueur).encode("utf-8")[:longueur].ljust(longueur, b"x")
        brut[position:position + len(neuf)] = neuf
        generiques += 1

# ------------------------------------------------------------ vidage du projet VBA
FIN_DE_CHAINE = 0xFFFFFFFE
flux_a_vider = ("Module1", "Module2", "Module3", "Module4", "Module5",
                "__SRP_0", "__SRP_1", "__SRP_2", "__SRP_3", "__SRP_4", "__SRP_5",
                "_VBA_PROJECT", "dir", "PROJECT", "PROJECTwm")
cibles = {nom.upper() for nom in flux_a_vider}
vides = []
repertoire = []
for offset in range(0, max(0, len(brut) - 128), 4):
    longueur_nom = struct.unpack_from("<H", brut, offset + 64)[0]
    if longueur_nom < 4 or longueur_nom > 64 or longueur_nom % 2:
        continue
    try:
        nom = brut[offset:offset + longueur_nom - 2].decode("utf-16-le")
    except UnicodeDecodeError:
        continue
    if not nom:
        continue
    if any(ord(c) > 126 or (ord(c) < 32 and not (rang == 0 and ord(c) == 5))
           for rang, c in enumerate(nom)):
        continue
    type_objet = brut[offset + 66]
    if type_objet not in (1, 2, 5):
        continue
    taille = struct.unpack_from("<Q", brut, offset + 120)[0]
    repertoire.append((offset, nom, type_objet, taille))
for offset, nom, type_objet, taille in repertoire:
    if type_objet == 2 and nom.upper() in cibles and taille > 0:
        struct.pack_into("<I", brut, offset + 116, FIN_DE_CHAINE)
        struct.pack_into("<Q", brut, offset + 120, 0)
        vides.append((nom, taille))

open(destination, "wb").write(brut)

print(f"source  : {source} ({avant} octets)")
print(f"sortie  : {destination} ({len(brut)} octets)")
print(f"valeurs du modèle remplacées : {remplaces} occurrence(s)")
print(f"suites génériques neutralisées : {generiques}")
print(f"flux vidés : {len(vides)} ({sum(t for _, t in vides)} octets)")
print(f"entrées de répertoire OLE reconnues : {len(repertoire)}")
print("empreinte sha256 :", hashlib.sha256(bytes(brut)).hexdigest())
