#!/usr/bin/env python3
"""Enveloppe de capacité mesurée de bout en bout, via l'application elle-même.

Démarre une instance de test de l'application (port et mémoire paramétrables),
puis envoie une série de fichiers .mpp en HTTP comme le ferait un visiteur :
on relève le code HTTP, la durée, le verdict et le pic mémoire annoncé dans les
journaux de l'application.

Usage (sur le LXC, en root) :
    python3 enveloppe_app.py [--port 5051] [--jvm "-Xmx2g"] [--conteneur 5g] fichier.mpp ...
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

IMAGE = "mpp-mpp:latest"
NOM = "mpp-enveloppe"

JAVA_DE_BASE = ("-XX:MaxMetaspaceSize=192m -XX:+UseSerialGC "
                "-XX:ActiveProcessorCount=1 -XX:+ExitOnOutOfMemoryError")


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def journaux():
    resultat = docker("logs", NOM)
    return (resultat.stdout or "") + (resultat.stderr or "")


def demarrer(port, jvm, conteneur, upload_mb, simultanees):
    docker("rm", "-f", NOM)
    options = f"{jvm} {JAVA_DE_BASE}".strip()
    commande = [
        "run", "-d", "--name", NOM, "--memory", conteneur, "--memory-swap", conteneur,
        "-p", f"127.0.0.1:{port}:5000",
        "-e", f"JAVA_TOOL_OPTIONS={options}",
        "-e", f"MAX_UPLOAD_MB={upload_mb}",
        "-e", f"MAX_CONCURRENT_ANALYSES={simultanees}",
        IMAGE,
    ]
    resultat = docker(*commande)
    if resultat.returncode != 0:
        sys.exit(f"démarrage impossible : {resultat.stderr.strip()[:400]}")
    for _ in range(60):
        etat = docker("inspect", "-f", "{{.State.Health.Status}}", NOM).stdout.strip()
        if etat == "healthy":
            return True
        time.sleep(1)
    print("  (avertissement : conteneur non « healthy » après 60 s)")
    return False


def envoyer(port, fichier):
    taille = fichier.stat().st_size
    debut = time.monotonic()
    # L'application n'accepte que les noms en .mpp : on renomme à l'envoi, comme
    # le fait un visiteur qui dépose un planning exporté.
    resultat = subprocess.run(
        ["curl", "-sS", "-o", "/tmp/enveloppe_reponse.html", "-w", "%{http_code}",
         "--max-time", "1200", "-F", f"mpp_file=@{fichier};filename=planning.mpp",
         "-F", "analysis=dcma", f"http://127.0.0.1:{port}/analyse"],
        capture_output=True, text=True,
    )
    duree = time.monotonic() - debut
    code = resultat.stdout.strip()
    page = Path("/tmp/enveloppe_reponse.html").read_text(errors="replace") if Path("/tmp/enveloppe_reponse.html").exists() else ""
    return taille, code, duree, page


def verdict(page):
    """Extrait l'information utile de la page rendue."""
    texte = re.sub(r"<[^>]+>", " ", page)
    texte = re.sub(r"\s+", " ", texte)
    score = re.search(r"(\d+)\s*/\s*100", texte)
    ecarts = re.search(r"(\d+)\s+contr[oô]le\(s\)\s+(?:en [eé]cart|[aà] corriger)", texte, re.I)
    trop = "trop volumineux" in texte.lower()
    memoire = "mémoire disponible" in texte.lower()
    if score:
        return f"score {score.group(1)}/100" + (f", {ecarts.group(1)} écart(s)" if ecarts else "")
    if trop:
        return "refusé : fichier trop volumineux"
    if memoire:
        return "refusé : mémoire insuffisante"
    return "autre page (" + texte.strip()[:80] + ")"


def mesures_recentes(depuis):
    """Durée et pic mémoire annoncés par l'application pour chaque analyse."""
    trouves = []
    for ligne in journaux().splitlines():
        correspondance = re.search(
            r"terminé en ([\d.]+) s \(pic mémoire du sous-processus : (\d+) Mo\)", ligne)
        if correspondance:
            trouves.append((float(correspondance.group(1)), int(correspondance.group(2))))
    return trouves[len(depuis):] if depuis else trouves


def main():
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--port", type=int, default=5051)
    parseur.add_argument("--jvm", default="-Xmx2g")
    parseur.add_argument("--conteneur", default="5g")
    parseur.add_argument("--upload-mb", type=int, default=20)
    parseur.add_argument("--simultanees", type=int, default=1)
    parseur.add_argument("--garder", action="store_true", help="ne pas arrêter le conteneur de test")
    parseur.add_argument("fichiers", nargs="+")
    arguments = parseur.parse_args()

    print(f"=== instance de test : {arguments.jvm} / conteneur {arguments.conteneur} / "
          f"upload {arguments.upload_mb} Mo / {arguments.simultanees} analyse(s) simultanée(s) ===")
    demarrer(arguments.port, arguments.jvm, arguments.conteneur, arguments.upload_mb,
             arguments.simultanees)
    deja = mesures_recentes(0)
    resultats = []

    for chemin in arguments.fichiers:
        fichier = Path(chemin)
        if not fichier.exists():
            print(f"  {chemin} : introuvable")
            continue
        taille, code, duree, page = envoyer(arguments.port, fichier)
        nouvelles = mesures_recentes(deja)
        deja = mesures_recentes(0)
        duree_analyse, pic = (nouvelles[-1] if nouvelles else (None, None))
        print(f"  {fichier.name:44s} {taille / 1048576:6.2f} Mo  http={code}  "
              f"total={duree:6.1f} s  analyse={(f'{duree_analyse:.1f} s' if duree_analyse else '—'):>7s}  "
              f"pic={(f'{pic} Mo' if pic else '—'):>7s}  {verdict(page)}")
        resultats.append({
            "fichier": fichier.name, "octets": taille, "http": code,
            "duree_totale": round(duree, 1), "duree_analyse": duree_analyse,
            "pic_mo": pic, "verdict": verdict(page),
        })

    Path("/tmp/enveloppe_resultats.json").write_text(json.dumps(resultats, indent=2))
    print("\nrésultats JSON : /tmp/enveloppe_resultats.json")

    if not arguments.garder:
        docker("rm", "-f", NOM)
        print("conteneur de test supprimé")


if __name__ == "__main__":
    main()
