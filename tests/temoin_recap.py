#!/usr/bin/env python3
"""Planning témoin avec un lien porté par une tâche RÉCAPITULATIVE.

Un planning ne devrait pas en contenir : les liens doivent relier des tâches de
détail. Ce témoin sert à vérifier que MPPCR le SIGNALE (avertissement) au lieu de
corriger silencieusement le réseau — la phase 2 est enchaînée sur la phase 1 par
un lien de niveau récapitulatif, que la simulation ignore.

Structure :
  Phase 1 — Conception (récap.)      Phase 2 — Réalisation (récap.)
    Analyse                          Développement
    Conception détaillée             Tests
  Lien : Phase 1 -> Phase 2 (récapitulatif, ignoré)
        Analyse -> Conception détaillée -> Développement -> Tests (détail)

Usage : python3 temoin_recap.py [chemin.xml]
"""
import sys
from pathlib import Path

CHEMIN = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/temoin_recap.xml")
lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<Project xmlns="http://schemas.microsoft.com/project">',
          "<Name>MPPCR — témoin liens de récapitulative</Name>",
          "<StatusDate>2026-09-01T08:00:00</StatusDate>",
          "<MinutesPerDay>480</MinutesPerDay>", "<Tasks>"]

compteur = [0]


def tache(nom, preds=(), duree_jours=5, recapitulative=False, niveau=2, numero=""):
    compteur[0] += 1
    uid = compteur[0]
    liens = "".join(f"<PredecessorLink><PredecessorUID>{p}</PredecessorUID><Type>1</Type>"
                    f"<LinkLag>0</LinkLag></PredecessorLink>" for p in preds)
    duree = f"PT{int(duree_jours * 8)}H0M0S"
    lignes.append(
        f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>{nom}</Name>"
        f"<Duration>{duree}</Duration><Start>2026-09-01T08:00:00</Start>"
        "<Finish>2026-09-30T17:00:00</Finish><PercentComplete>0</PercentComplete>"
        f"<Summary>{1 if recapitulative else 0}</Summary><Milestone>0</Milestone>"
        f"<OutlineLevel>{niveau}</OutlineLevel>"
        f"<OutlineNumber>{numero}</OutlineNumber><WBS>{numero}</WBS>"
        f"<Baseline><Number>0</Number><Duration>{duree}</Duration></Baseline>"
        f"{liens}</Task>")
    return uid


phase1 = tache("Phase 1 — Conception", duree_jours=10, recapitulative=True, niveau=1, numero="1")
analyse = tache("Analyse", duree_jours=5, niveau=2, numero="1.1")
conception = tache("Conception détaillée", preds=[analyse], duree_jours=5, niveau=2, numero="1.2")
# LE lien en cause : la récapitulative phase 2 dépend de la récapitulative phase 1.
phase2 = tache("Phase 2 — Réalisation", preds=[phase1], duree_jours=15,
               recapitulative=True, niveau=1, numero="2")
developpement = tache("Développement", preds=[conception], duree_jours=10, niveau=2, numero="2.1")
tache("Tests", preds=[developpement], duree_jours=5, niveau=2, numero="2.2")

lignes += ["</Tasks>", "</Project>", ""]
CHEMIN.write_text("\n".join(lignes), encoding="utf-8")
print(f"{CHEMIN} écrit ({CHEMIN.stat().st_size} octets)")
