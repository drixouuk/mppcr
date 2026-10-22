#!/usr/bin/env python3
"""Balayage mémoire sur un vrai .mpp : quel tas JVM est nécessaire ?

Pour chaque valeur de -Xmx demandée, lance dcma14.py sur le fichier, relève le
pic de mémoire du sous-processus (VmHWM) et indique succès ou échec mémoire.

Usage : python3 mesure_tas.py RL.mpp 256 384 512 768 1024 1536
"""
import os
import subprocess
import sys
import time
from pathlib import Path

fichier = Path(sys.argv[1]).resolve()
valeurs = list(sys.argv[2:])

# Les scripts d'analyse vivent dans /app dans l'image, ou à côté de ce fichier
# lorsqu'on l'exécute depuis un répertoire de travail.
DCMA = Path("/app/dcma14.py") if Path("/app/dcma14.py").exists() else Path(__file__).parent / "dcma14.py"
MC = Path("/app/montecarlo.py") if Path("/app/montecarlo.py").exists() else Path(__file__).parent / "montecarlo.py"


def pic_mo(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as f:
            for ligne in f:
                if ligne.startswith("VmHWM:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def taches():
    """Nombre de tâches de détail, lu dans la sortie de montecarlo.py."""
    p = subprocess.run([sys.executable, str(MC), str(fichier), "--sims", "10"],
                       capture_output=True, text=True, timeout=900)
    for ligne in p.stdout.splitlines():
        if ligne.startswith("Taches de detail") or ligne.startswith("Liens de dependance"):
            print("   ", ligne.strip())
    if p.returncode != 0:
        print("    (lecture impossible :", (p.stderr or "").strip()[:160], ")")
    return p.returncode


print(f"=== {fichier.name} : {fichier.stat().st_size} octets ===")
taches()
print()

def options(valeur):
    """« image » = options de l'image, sinon -Xmx<n>m seul (ou avec :gc)."""
    if valeur == "image":
        return os.environ.get("JAVA_TOOL_OPTIONS", "")
    if valeur.endswith(":gc"):
        return (f"-Xmx{int(valeur[:-3])}m -XX:MaxMetaspaceSize=192m "
                "-XX:+UseSerialGC -XX:ActiveProcessorCount=1 -XX:+ExitOnOutOfMemoryError")
    if valeur.endswith(":exit"):
        return f"-Xmx{int(valeur[:-5])}m -XX:+ExitOnOutOfMemoryError"
    return f"-Xmx{int(valeur)}m"


for xmx in valeurs:
    libelle = str(xmx)
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin",
           "JAVA_TOOL_OPTIONS": options(xmx),
           "PYTHONIOENCODING": "UTF-8"}
    debut = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(DCMA), str(fichier), "--details"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, env=env)
    pic = 0
    while proc.poll() is None:
        pic = max(pic, pic_mo(proc.pid))
        time.sleep(0.2)
    _, erreur = proc.communicate()
    duree = time.monotonic() - debut
    manque = proc.returncode != 0 and any(
        m in (erreur or "").lower() for m in ("outofmemory", "heap space", "too small maximum heap"))
    etat = "ÉCHEC MÉMOIRE" if manque else ("OK" if proc.returncode == 0 else f"échec {proc.returncode}")
    print(f"  {libelle:<18} {etat:<13} pic={pic:>5} Mo  durée={duree:>6.1f} s   [{options(xmx)[:52]}]")
