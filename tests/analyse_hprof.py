#!/usr/bin/env python3
"""Histogramme des objets d'un vidage de tas Java (format HPROF), en flux.

Sert à nommer ce qui explose réellement : on compte les instances par classe dans
un fichier .hprof sans le charger en mémoire.

Usage : python3 analyse_hprof.py dump.hprof [nombre]
"""
import struct
import sys
from collections import Counter

chemin = sys.argv[1]
combien = int(sys.argv[2]) if len(sys.argv) > 2 else 15

with open(chemin, "rb") as f:
    entete = b""
    while not entete.endswith(b"\x00"):
        entete += f.read(1)
    taille_id = struct.unpack(">I", f.read(4))[0]
    f.read(8)  # horodatage

    print(f"format : {entete[:-1].decode('ascii', 'replace')} | identifiants sur {taille_id} octets")

    def lire_id():
        return struct.unpack(">Q" if taille_id == 8 else ">I", f.read(taille_id))[0]

    chaines = {}
    classes = {}
    instances = Counter()
    octets_par_classe = Counter()
    total = 0

    while True:
        tag = f.read(1)
        if not tag:
            break
        f.read(4)  # horodatage
        longueur = struct.unpack(">I", f.read(4))[0]
        corps = f.read(longueur)

        if tag == b"\x01":  # chaîne
            identifiant = struct.unpack(">Q" if taille_id == 8 else ">I", corps[:taille_id])[0]
            chaines[identifiant] = corps[taille_id:].decode("utf-8", "replace")
        elif tag == b"\x02":  # chargement de classe
            decalage = 4 + taille_id + 4
            id_classe = struct.unpack(">Q" if taille_id == 8 else ">I",
                                      corps[4:4 + taille_id])[0]
            id_nom = struct.unpack(">Q" if taille_id == 8 else ">I",
                                   corps[decalage:decalage + taille_id])[0]
            classes[id_classe] = id_nom
        elif tag in (b"\x0c", b"\x1c"):  # vidage de tas (JDK 8) ou segment (JDK 9+)
            position = 0
            while position < len(corps):
                sous_tag = corps[position]
                position += 1
                if sous_tag == 0x21:  # instance
                    id_objet = int.from_bytes(corps[position:position + taille_id], "big")
                    position += taille_id
                    position += 4  # numéro de pile
                    id_classe = int.from_bytes(corps[position:position + taille_id], "big")
                    position += taille_id
                    taille = int.from_bytes(corps[position:position + 4], "big")
                    position += 4 + taille
                    instances[id_classe] += 1
                    octets_par_classe[id_classe] += taille + 16
                    total += 1
                elif sous_tag == 0x22:  # tableau d'objets
                    position += taille_id + 4
                    nombre = int.from_bytes(corps[position:position + 4], "big")
                    position += 4 + nombre * taille_id
                elif sous_tag in (0x23, 0x24):  # tableaux primitifs et tableaux de tableaux
                    position += taille_id + 4 + 4
                    nombre = int.from_bytes(corps[position:position + 4], "big")
                    position += 4
                    type_element = corps[position]
                    position += 1
                    tailles = {4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
                    position += nombre * tailles.get(type_element, 1)
                elif sous_tag == 0x01:  # racine
                    position += taille_id + 8
                elif sous_tag == 0x02:
                    position += taille_id + 4 + 4
                elif sous_tag == 0x03:
                    position += taille_id + 4 + 4
                elif sous_tag == 0x04:
                    position += taille_id + 4
                elif sous_tag == 0x05:
                    position += taille_id
                elif sous_tag == 0x06:
                    position += taille_id + 4
                elif sous_tag == 0x07:
                    position += taille_id
                elif sous_tag == 0x08:
                    position += taille_id + 4 + 4
                elif sous_tag in (0x89, 0x8a, 0x8b, 0x8c, 0x8d, 0x8e, 0x90, 0xc3):
                    break  # structures non nécessaires ici : on arrête ce segment
                else:
                    break

    print(f"instances comptées : {total}\n")
    print(f"{'instances':>12} {'Mo estimés':>11}  classe")
    for id_classe, nombre in instances.most_common(combien):
        nom = chaines.get(classes.get(id_classe, 0), "?")
        print(f"{nombre:>12} {octets_par_classe[id_classe] / 1048576:>10.1f} Mo  {nom}")
