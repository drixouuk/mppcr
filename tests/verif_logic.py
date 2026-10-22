#!/usr/bin/env python3
"""Vérifie la règle de signalement du contrôle 1 (Logic) sur des cas construits.

Trois plans :
  A. toutes les tâches ont prédécesseur ET successeur  -> aucun signalement attendu
  B. deux tâches réellement orphelines (l'une sans amont, l'autre sans aval)
     placées au milieu du réseau                            -> exactement 2 signalements
  C. une tâche liée à une tâche EXTERNE (autre projet) des deux côtés
                                                          -> à observer
Le script lit la ligne « DETAILS_TACHES » produite par dcma14.py --details et
affiche, contrôle par contrôle, les numéros signalés.

Usage (dans un conteneur de l'image) : python3 verif_logic.py /m
"""
import re
import subprocess
import sys
from pathlib import Path

DOSSIER = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp")
DCMA = "/app/dcma14.py"


def tache(uid, nom, preds=(), externe=False, projet=None, avancement=20):
    liens = "".join(f"<PredecessorLink><PredecessorUID>{p}</PredecessorUID><Type>1</Type>"
                    f"<LinkLag>0</LinkLag></PredecessorLink>" for p in preds)
    ext = "<ExternalTask>1</ExternalTask>" + (
        f"<ExternalTaskProject>{projet}</ExternalTaskProject>" if projet else "") if externe else ""
    return (f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>T{uid}</Name>"
            "<Duration>PT8H0M0S</Duration><Start>2026-09-01T08:00:00</Start>"
            "<Finish>2026-09-01T17:00:00</Finish>"
            f"<PercentComplete>{avancement}</PercentComplete>"
            "<Summary>0</Summary><Milestone>0</Milestone>"
            f"<Baseline><Number>0</Number><Duration>PT8H0M0S</Duration></Baseline>"
            f"{ext}{liens}</Task>")


def ecrire(chemin, taches, titre):
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<Project xmlns="http://schemas.microsoft.com/project">',
              f"<Name>{titre}</Name>",
              "<StatusDate>2026-10-05T08:00:00</StatusDate>",
              "<MinutesPerDay>480</MinutesPerDay>", "<Tasks>"]
    lignes += taches
    lignes.append("</Tasks></Project>")
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
    return Path(chemin).stat().st_size


plans = {}

# A : chaîne complète, chaque tâche a un amont et un aval (sauf les extrémités,
# volontairement tolérées par le contrôle).
plans["A_tout_lie"] = [tache(i, f"T{i}", preds=([i - 1] if i > 1 else [])) for i in range(1, 7)]

# B : la chaîne A, plus une tâche sans amont et une sans aval au MILIEU du réseau.
taches_b = [tache(i, f"T{i}", preds=([i - 1] if i > 1 else [])) for i in range(1, 7)]
taches_b.append(tache(7, "T7", preds=()))                      # orpheline amont
taches_b.append(tache(8, "T8", preds=(3,)))                    # sans aval, en plein milieu
plans["B_deux_orphelines"] = taches_b

# C : tâche 4 liée à une tâche EXTERNE en amont, et l'externe 9 dépend de la 4
# (donc 4 a, dans MS Project, un prédécesseur et un successeur externes).
plans["C_liens_externes"] = [
    tache(1, "T1", preds=()),
    tache(2, "T2", preds=(1,)),
    tache(3, "T3", preds=(2,)),
    tache(9, "T9_externe", preds=(4,), externe=True, projet="Autre_Projet.mpp"),
    tache(4, "T4", preds=(9,)),
    tache(5, "T5", preds=(4,)),
]

attendus = {"A_tout_lie": 0, "B_deux_orphelines": 2, "C_liens_externes": None}

for nom, taches in plans.items():
    chemin = DOSSIER / f"{nom}.xml"
    taille = ecrire(chemin, taches, nom)
    execution = subprocess.run([sys.executable, DCMA, str(chemin), "--details"],
                               capture_output=True, text=True, timeout=300)
    sortie = execution.stdout or ""
    ligne_logic = next((l for l in sortie.splitlines() if l.startswith("1. ")), "(absente)")
    detail = ""
    capture = False
    for ligne in sortie.splitlines():
        if "DETAILS_TACHES" in ligne:
            capture = True
            continue
        if capture and ligne.startswith("1|"):
            detail = ligne.strip()
    print(f"### {nom} ({taille} octets, {len(taches)} tâches)")
    print(f"    {ligne_logic.strip()}")
    print(f"    contrôle 1, tâches signalées : {detail or '1|-'}")
    if attendus[nom] is not None:
        nombre = 0 if detail in ("", "1|-") else len(detail.split("|", 1)[1].split(","))
        verdict = "conforme à l'attendu" if nombre == attendus[nom] else "INATTENDU"
        print(f"    attendu {attendus[nom]} signalement(s) -> {verdict}")
    print()
