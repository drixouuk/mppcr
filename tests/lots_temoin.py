#!/usr/bin/env python3
"""Planning témoin à plusieurs lots (pour tester la répartition par macro-tâche).

Structure :
  Programme A (5 tâches)  ->  Programme B (8 tâches, un sous-lot de niveau 2)
      ->  Programme C (3 tâches)  et  Programme D (6 tâches)  ->  jalon final hors lot

Les lots sont de tailles et de durées différentes, et les dépendances traversent
les lots (A -> B -> C/D, puis le jalon), ce qui permet de vérifier que la
répartition par lot reste cohérente avec le résultat global.

Usage : python3 lots_temoin.py /m/lots.xml
"""
import sys
from pathlib import Path

CHEMIN = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lots.xml")
lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<Project xmlns="http://schemas.microsoft.com/project">',
          "<Name>Planning a plusieurs lots</Name>",
          "<StatusDate>2026-10-05T08:00:00</StatusDate>",
          "<MinutesPerDay>480</MinutesPerDay>", "<Tasks>"]

compteur = [0]
# Numérotation de plan : MS Project (et MPXJ) reconstituent la hiérarchie à partir
# de <OutlineLevel>/<OutlineNumber>. Sans ces balises, tous les lots passent pour
# des tâches de détail et la fonctionnalité de répartition n'a rien à regrouper.
compteurs = {1: 0, 2: 0}


def tache(nom, preds=(), duree_jours=5, recapitulative=False, avancement=0, jalon=False,
          niveau=3):
    compteur[0] += 1
    uid = compteur[0]
    if niveau == 1:
        compteurs[1] += 1
        compteurs[2] = 0
        numero = f"{compteurs[1]}"
    elif niveau == 2:
        compteurs[2] += 1
        numero = f"{compteurs[1]}.{compteurs[2]}"
    else:
        niveau = max(niveau, 2)
        compteurs[2] += 1
        numero = f"{compteurs[1]}.{compteurs[2]}"
    liens = "".join(f"<PredecessorLink><PredecessorUID>{p}</PredecessorUID><Type>1</Type>"
                    f"<LinkLag>0</LinkLag></PredecessorLink>" for p in preds)
    duree = "PT0H0M0S" if jalon else f"PT{int(duree_jours * 8)}H0M0S"
    lignes.append(
        f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>{nom}</Name>"
        f"<Duration>{duree}</Duration><Start>2026-09-01T08:00:00</Start>"
        "<Finish>2026-09-30T17:00:00</Finish>"
        f"<PercentComplete>{avancement}</PercentComplete>"
        f"<Summary>{1 if recapitulative else 0}</Summary>"
        f"<Milestone>{1 if jalon else 0}</Milestone>"
        f"<OutlineLevel>{niveau}</OutlineLevel>"
        f"<OutlineNumber>{numero}</OutlineNumber><WBS>{numero}</WBS>"
        f"<Baseline><Number>0</Number><Duration>{duree}</Duration></Baseline>"
        f"{liens}</Task>")
    return uid


# Programme A : 5 tâches, dont 2 achevées (incertitude gelée)
tache("Programme A — Etudes", recapitulative=True, niveau=1)
a = tache("A1 Cadrage", duree_jours=8, avancement=100)
a = tache("A2 Analyse", preds=(a,), duree_jours=12, avancement=100)
a = tache("A3 Conception", preds=(a,), duree_jours=25)
a = tache("A4 Revue", preds=(a,), duree_jours=6)
a = tache("A5 Validation", preds=(a,), duree_jours=10)

# Programme B : 8 tâches, avec un sous-lot de niveau 2
tache("Programme B — Realisation", recapitulative=True, niveau=1)
tache("B — sous-lot composants", recapitulative=True, niveau=2)
b1 = tache("B1 Composant 1", preds=(a,), duree_jours=40)
b1 = tache("B2 Composant 2", preds=(b1,), duree_jours=55)
b2 = tache("B3 Integration", preds=(b1,), duree_jours=20)
b2 = tache("B4 Tests unitaires", preds=(b2,), duree_jours=15)
b3 = tache("B5 Recette interne", preds=(b2,), duree_jours=12)
tache("B — sous-lot deploiement", preds=(b3,), recapitulative=True, niveau=2)
b4 = tache("B6 Preparation", duree_jours=18)
b4 = tache("B7 Deploiement", preds=(b4,), duree_jours=9)
b4 = tache("B8 Stabilisation", preds=(b4,), duree_jours=14)

# Programme C : 3 tâches, courte mais tendue
tache("Programme C — Formation", recapitulative=True, niveau=1)
c = tache("C1 Support de formation", preds=(b2,), duree_jours=22)
c = tache("C2 Sessions", preds=(c,), duree_jours=7)
c = tache("C3 Evaluation", preds=(c,), duree_jours=5)

# Programme D : 6 tâches, alimentée par B et C
tache("Programme D — Mise en service", recapitulative=True, niveau=1)
d = tache("D1 Preparation site", preds=(b4, c), duree_jours=16)
d = tache("D2 Installation", preds=(d,), duree_jours=30)
d = tache("D3 Parametrage", preds=(d,), duree_jours=18)
d = tache("D4 Tests de mise en service", preds=(d,), duree_jours=12)
d = tache("D5 Documentation", preds=(d,), duree_jours=9)
d = tache("D6 Transfert de competences", preds=(d,), duree_jours=7)

# Jalon final, hors lot (au niveau 1, sans récapitulative au-dessus)
tache("Jalon — reception du projet", preds=(d,), jalon=True, niveau=1)

lignes.append("</Tasks></Project>")
CHEMIN.write_text("\n".join(lignes), encoding="utf-8")
print(f"{CHEMIN} écrit : {CHEMIN.stat().st_size} octets, {compteur[0]} tâches "
      "(4 programmes de niveau 1, 1 sous-lot de niveau 2, 1 jalon hors lot)")
