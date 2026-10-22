#!/usr/bin/env python3
"""Extrait le tableau des contrôles et le score d'une page de résultat MPPCR."""
import re
import sys

html = open(sys.argv[1], encoding="utf-8", errors="replace").read()


def texte(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


lignes = {}
for ligne in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
    contenu = texte(ligne)
    correspondance = re.match(r"^(\d+)\.\s*(.+)$", contenu)
    if correspondance:
        lignes[int(correspondance.group(1))] = contenu

for numero in sorted(lignes):
    print(f"  {lignes[numero][:98]}")

score = re.search(r'class="score-value">\s*([0-9]+)', html)
print("  score :", (score.group(1) + "/100") if score else "(absent)")
resume = re.search(r"Resume\s*:\s*([^<]{0,80})", texte(html))
print("  resume :", resume.group(1).strip() if resume else "(absent)")
