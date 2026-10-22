#!/usr/bin/env python3
"""Sonde : les chaînes du planning sont-elles modifiables en place dans le .mpp ?

Un .mpp est un conteneur OLE dont certaines parties sont compressées : une chaîne
n'est neutralisable sur place que si elle apparaît telle quelle dans les octets du
fichier. Cette sonde relève les chaînes du modèle (titre, auteur, noms de tâches,
de ressources, notes) et indique, pour chacune, sous quelle forme elle est
stockée — sans jamais afficher son contenu.

Usage : python3 sonde_chaines.py /m/fichier.mpp
"""
import sys

import jpype
import mpxj

chemin = sys.argv[1]
with open(chemin, "rb") as f:
    brut = f.read()

mpxj.startJVM()
Lecteur = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
projet = Lecteur().read(chemin)

textes = []


def ajouter(valeur, origine):
    try:
        texte = str(valeur)
    except Exception:
        return
    if texte and texte.lower() not in ("none", "null") and len(texte) >= 3:
        textes.append((texte, origine))


proprietes = projet.getProjectProperties()
for nom in ("getProjectTitle", "getSubject", "getAuthor", "getCompany", "getManager",
            "getComments", "getKeywords", "getCategory", "getDescription", "getName"):
    try:
        ajouter(getattr(proprietes, nom)(), "propriétés." + nom[3:])
    except Exception:
        pass

taches = list(projet.getTasks())
for tache in taches[:12]:
    for nom in ("getName", "getNotes"):
        try:
            ajouter(getattr(tache, nom)(), "tâche." + nom[3:])
        except Exception:
            pass
for ressource in list(projet.getResources())[:12]:
    for nom in ("getName", "getNotes", "getEmailAddress"):
        try:
            ajouter(getattr(ressource, nom)(), "ressource." + nom[3:])
        except Exception:
            pass
for calendrier in list(projet.getCalendars())[:5]:
    try:
        ajouter(calendrier.getName(), "calendrier")
    except Exception:
        pass

uniques = {}
for texte, origine in textes:
    uniques.setdefault(texte, origine)

print(f"chaînes relevées : {len(uniques)} (sur {len(textes)} occurrences)")
print(f"tâches : {len(taches)} | ressources : {len(list(projet.getResources()))}")

stats = {"utf16": 0, "utf8": 0, "les_deux": 0, "absente": 0}
details = []
for texte, origine in uniques.items():
    en_utf16 = texte.encode("utf-16-le")
    en_utf8 = texte.encode("utf-8")
    trouve16 = brut.count(en_utf16)
    trouve8 = brut.count(en_utf8)
    if trouve16 and trouve8:
        stats["les_deux"] += 1
        etat = "utf-16 + utf-8"
    elif trouve16:
        stats["utf16"] += 1
        etat = "utf-16"
    elif trouve8:
        stats["utf8"] += 1
        etat = "utf-8"
    else:
        stats["absente"] += 1
        etat = "ABSENTE (compressée ou encodée autrement)"
    details.append((len(texte), origine, etat, trouve16, trouve8, texte))

for etiquette, cle in (("trouvées en UTF-16 seul", "utf16"), ("trouvées en UTF-8 seul", "utf8"),
                       ("trouvées dans les deux formes", "les_deux"), ("introuvables", "absente")):
    print(f"  {etiquette:28s} : {stats[cle]}")

print("\nrépartition par origine :")
origines = {}
for longueur, origine, etat, _, _, _ in details:
    famille = origine.split(".")[0]
    origines.setdefault(famille, {"localisables": 0, "absentes": 0})
    if etat.startswith("ABSENTE"):
        origines[famille]["absentes"] += 1
    else:
        origines[famille]["localisables"] += 1
for famille, compte in sorted(origines.items()):
    print(f"  {famille:14s} localisables {compte['localisables']:3d} | introuvables {compte['absentes']:3d}")
