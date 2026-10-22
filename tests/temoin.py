#!/usr/bin/env python3
"""Planning témoin : de l'historique (tâches achevées) et des jalons.

Sert à mesurer l'effet du patch du contrôle 1 (DCMA-14) : un planning réaliste
contient beaucoup de tâches terminées, dont on n'attend plus de lien aval.

Usage : python3 temoin.py /m/temoin.xml
"""
import sys
from pathlib import Path

lignes = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<Project xmlns="http://schemas.microsoft.com/project">',
    "<Name>Planning temoin avec historique</Name>",
    "<StatusDate>2026-10-05T08:00:00</StatusDate>",
    "<MinutesPerDay>480</MinutesPerDay>",
    "<Tasks>",
]


def tache(uid, nom, debut, fin, avancement, jalon=False, preds=()):
    liens = "".join(f"<PredecessorLink><PredecessorUID>{p}</PredecessorUID><Type>1</Type>"
                    f"<LinkLag>0</LinkLag></PredecessorLink>" for p in preds)
    return (f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>{nom}</Name>"
            f"<Duration>{'PT0H0M0S' if jalon else 'PT40H0M0S'}</Duration>"
            f"<Start>{debut}</Start><Finish>{fin}</Finish>"
            f"<PercentComplete>{avancement}</PercentComplete>"
            "<Summary>0</Summary>"
            f"<Milestone>{1 if jalon else 0}</Milestone>"
            "<Baseline><Number>0</Number><Duration>PT40H0M0S</Duration></Baseline>"
            f"{liens}</Task>")


# Un jalou de départ, six tâches terminées, quatre en cours, une isolée, un jalou final.
lignes.append(tache(1, "Lancement", "2026-06-01T08:00:00", "2026-06-01T08:00:00", 100, jalon=True))
lignes.append(tache(2, "Etudes", "2026-06-01T08:00:00", "2026-06-05T17:00:00", 100, preds=(1,)))
lignes.append(tache(3, "Appel d'offres", "2026-06-08T08:00:00", "2026-06-12T17:00:00", 100, preds=(2,)))
lignes.append(tache(4, "Commande", "2026-06-15T08:00:00", "2026-06-19T17:00:00", 100, preds=(3,)))
lignes.append(tache(5, "Livraison", "2026-06-22T08:00:00", "2026-06-26T17:00:00", 100, preds=(4,)))
lignes.append(tache(6, "Installation", "2026-06-29T08:00:00", "2026-07-03T17:00:00", 100, preds=(5,)))
lignes.append(tache(7, "Recette partielle", "2026-07-06T08:00:00", "2026-07-10T17:00:00", 100, preds=(6,)))
lignes.append(tache(8, "Formation", "2026-09-01T08:00:00", "2026-09-05T17:00:00", 60, preds=(7,)))
lignes.append(tache(9, "Documentation", "2026-09-08T08:00:00", "2026-09-12T17:00:00", 40, preds=(8,)))
lignes.append(tache(10, "Mise en service", "2026-09-15T08:00:00", "2026-09-19T17:00:00", 20, preds=(9,)))
lignes.append(tache(11, "Reprise de donnees", "2026-09-22T08:00:00", "2026-09-26T17:00:00", 20, preds=(10,)))
lignes.append(tache(12, "Tache isolee sans lien", "2026-10-01T08:00:00", "2026-10-05T17:00:00", 10))
lignes.append(tache(13, "Cloture", "2026-10-06T08:00:00", "2026-10-06T08:00:00", 0, jalon=True, preds=(11,)))

lignes.append("</Tasks></Project>")
chemin = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/temoin.xml")
chemin.write_text("\n".join(lignes), encoding="utf-8")
print(f"{chemin} écrit : {chemin.stat().st_size} octets, 13 tâches "
      "(8 avec 100 %, 2 jalons, 1 isolée non achevée)")
