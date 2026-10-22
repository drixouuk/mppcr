#!/usr/bin/env python3
"""Mesure la mémoire d'une analyse DCMA sur un planning volumineux.

Génère un MSPDI de N tâches, lance dcma14.py en relevant le pic de mémoire du
sous-processus (VmHWM) et compare plusieurs limites de tas JVM.

Usage (dans un conteneur de l'image) :
    python3 memtest.py 3000 --sans-limite
    python3 memtest.py 12000 --xmx 768
"""
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path("/app/dcma14.py")
if not SCRIPT.exists():
    SCRIPT = Path(__file__).resolve().parent / "dcma14.py"


def generer(nb_taches, chemin):
    """Planning MSPDI : jalons, liens FS, baselines, quelques contraintes dures."""
    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Project xmlns="http://schemas.microsoft.com/project">',
        "<Name>Planning volumineux de test</Name>",
        "<StatusDate>2026-10-05T08:00:00</StatusDate>",
        "<MinutesPerDay>480</MinutesPerDay>",
        "<Tasks>",
    ]
    jour, mois = 1, 9
    for i in range(1, nb_taches + 1):
        debut = f"2026-{mois:02d}-{jour:02d}T08:00:00"
        fin = f"2026-{mois:02d}-{jour:02d}T17:00:00"
        contrainte = "<ConstraintType>0</ConstraintType>"
        if i % 97 == 0:
            contrainte = "<ConstraintType>2</ConstraintType>"
        pred = ""
        if i > 1:
            pred = (f"<PredecessorLink><PredecessorUID>{i - 1}</PredecessorUID>"
                    f"<Type>1</Type><LinkLag>0</LinkLag></PredecessorLink>")
        # Une tâche sur sept sans lien amont : de quoi alimenter les contrôles.
        if i % 7 == 0:
            pred = ""
        lignes.append(
            f"<Task><UID>{i}</UID><ID>{i}</ID><Name>Tache {i}</Name>"
            f"<Duration>PT8H0M0S</Duration><DurationFormat>7</DurationFormat>"
            f"<Start>{debut}</Start><Finish>{fin}</Finish>"
            f"<PercentComplete>{100 if i % 9 == 0 else i % 60}</PercentComplete>"
            f"<Summary>0</Summary><Milestone>0</Milestone>{contrainte}"
            f"<Baseline><Number>0</Number><Duration>PT8H0M0S</Duration></Baseline>"
            f"{pred}</Task>"
        )
        jour += 1
        if jour > 28:
            jour, mois = 1, mois + 1
            if mois > 12:
                mois = 1
    lignes.append("</Tasks></Project>")
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
    return Path(chemin).stat().st_size


def pic_memoire(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as fichier:
            for ligne in fichier:
                if ligne.startswith("VmHWM:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def lancer(fichier, options=None):
    env = os.environ.copy()
    if options is None:
        env.pop("JAVA_TOOL_OPTIONS", None)
    else:
        env["JAVA_TOOL_OPTIONS"] = options
    debut = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(SCRIPT), fichier],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    pic = 0
    while proc.poll() is None:
        pic = max(pic, pic_memoire(proc.pid))
        time.sleep(0.2)
    sortie, erreur = proc.communicate()
    return {
        "code": proc.returncode,
        "pic_mo": pic,
        "duree_s": round(time.monotonic() - debut, 1),
        "erreur": erreur or "",
        "lignes": len(sortie.splitlines()),
    }


if __name__ == "__main__":
    nb = int(sys.argv[1])
    option = sys.argv[2] if len(sys.argv) > 2 else "cap"
    chemin = f"/tmp/gros_{nb}.xml"
    taille = generer(nb, chemin)
    print(f"planning généré : {nb} tâches, {taille / 1024 / 1024:.2f} Mo")

    if option == "--sans-limite":
        options = None
        etiquette = "sans limite (défaut JVM)"
    elif option == "--sans":
        options = None
        etiquette = "sans limite (défaut JVM)"
    elif option.isdigit():
        options = f"-Xmx{option}m -XX:+ExitOnOutOfMemoryError"
        etiquette = f"-Xmx{option}m"
    else:
        options = os.environ.get("JAVA_TOOL_OPTIONS")
        etiquette = f"limite de l'image ({options})"

    resultat = lancer(chemin, options)
    memoire = ""
    if resultat["code"] != 0 and ("OutOfMemory" in resultat["erreur"]
                                  or "heap space" in resultat["erreur"]):
        memoire = " → ÉCHEC MÉMOIRE PROPRE"
    print(f"  {etiquette:<34} code={resultat['code']:<3} "
          f"pic={resultat['pic_mo']:>5} Mo  durée={resultat['duree_s']:>6} s{memoire}")
    if memoire:
        extrait = [l for l in resultat["erreur"].splitlines()
                   if "OutOfMemory" in l or "heap space" in l]
        print(f"    message JVM : {extrait[0].strip() if extrait else resultat['erreur'][:120]}")
