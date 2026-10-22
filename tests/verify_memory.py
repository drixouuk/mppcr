#!/usr/bin/env python3
"""Vérifie les messages liés à la mémoire sur l'application qui tourne.

    python3 verify_memory.py http://127.0.0.1:5005 /tmp/gros_12000.xml /tmp/gros_500.xml
"""
import re
import sys
import urllib.request
import uuid

BASE = sys.argv[1]
FICHIER_GROS = sys.argv[2]
FICHIER_PETIT = sys.argv[3]
FAILS = []
COUNT = [0]


def check(label, ok, detail=""):
    COUNT[0] += 1
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(label)


def texte(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def televerser(chemin, ip, nom="planning.mpp"):
    b = uuid.uuid4().hex
    entete = (
        f'--{b}\r\nContent-Disposition: form-data; name="analysis"\r\n\r\ndcma\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="mpp_file"; filename="{nom}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    corps = entete + open(chemin, "rb").read() + f"\r\n--{b}--\r\n".encode()
    requete = urllib.request.Request(
        BASE + "/analyse", data=corps,
        headers={"Content-Type": f"multipart/form-data; boundary={b}",
                 "X-Forwarded-For": ip})
    try:
        with urllib.request.urlopen(requete, timeout=300) as reponse:
            return reponse.status, reponse.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.read().decode("utf-8", "replace")


print("=== 1. formulaire : la limite annoncée est la bonne ===")
with urllib.request.urlopen(BASE + "/", timeout=30) as reponse:
    accueil = reponse.read().decode("utf-8")
limite = re.search(r"\.mpp uniquement · (\d+) Mo max", accueil)
check("limite affichée dans la zone de dépôt", limite is not None,
      "mention absente")
check("avertissement sur les plannings volumineux",
      "demandent beaucoup" in accueil and "peuvent être refusés" in accueil)

print("\n=== 2. fichier au-delà de la limite ===")
code, page = televerser(FICHIER_GROS, "198.21.1.1", "gros.mpp")
check("refus 413", code == 413, f"={code}")
contenu = texte(page)
check("titre « Fichier trop volumineux »", "Fichier trop volumineux" in contenu)
check("taille reçue et limite rappelées",
      "Mo reçus" in contenu and "Mo." in contenu and ("2 Mo" in contenu or "5 Mo" in contenu),
      f"={contenu[:200]}")
check("conseil d'export d'un extrait", "extrait du planning" in contenu)

print("\n=== 3. petite taille mais mémoire insuffisante ===")
code, page = televerser(FICHIER_PETIT, "198.21.1.2", "petit.mpp")
contenu = texte(page)
if "trop volumineux pour la mémoire" in contenu:
    check("message mémoire dédié", True)
    check("mémoire allouée annoncée", "Mo alloués à l'analyse" in contenu, f"={contenu[:160]}")
    check("marche à suivre", "Réduisez le périmètre analysé" in contenu)
else:
    # avec une limite JVM raisonnable, l'analyse doit simplement réussir
    check("analyse réussie avec la limite mémoire de l'image", code == 200,
          f"code={code}")

print("\n=== 4. journal applicatif ===")
print("  (voir les journaux du conteneur pour le pic mémoire relevé)")

print("\n" + "=" * 60)
if FAILS:
    print(f"{COUNT[0] - len(FAILS)}/{COUNT[0]} OK — échecs : {', '.join(FAILS)}")
    sys.exit(1)
print(f"{COUNT[0]}/{COUNT[0]} OK")
