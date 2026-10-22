"""Met à jour les commentaires devenus faux après le relèvement des plafonds."""
from pathlib import Path

remplacements = [
    ("# 768 Mo suffisent pour un planning courant, l'analyse échoue proprement",
     "# 2 Go couvrent un planning très détaillé (relevé le 21/09 : 768 Mo ne"),
    ("# au-delà plutôt que d'épuiser la mémoire du serveur.",
     "# suffisaient pas pour un plan de 100 tâches) ; l'analyse échoue proprement\n"
     "      # au-delà plutôt que d'épuiser la mémoire du serveur."),
    ('# plannings et disposez de la mémoire (ex. "-Xmx1536m -XX:+UseSerialGC").',
     '# plannings et disposez de la mémoire (2 Go ici ; ne jamais dépasser la\n'
     '      # moitié du plafond du conteneur, sinon la JVM est tuée par le noyau).'),
    ("# Le conteneur est plafonné et sacrifiable : en cas de manque de mémoire,",
     "# Conteneur plafonné à 5 Go (pic JVM mesuré : 876 Mo) et sacrifiable : en cas"),
    ("# le noyau tue cette analyse plutôt qu'un service voisin de la machine.",
     "# de manque, le noyau tue cette analyse plutôt qu'un service voisin."),
]

for nom in ("docker-compose.yml", "docker-compose.prod.yml"):
    chemin = Path("/mpp") / nom
    texte = chemin.read_text(encoding="utf-8")
    for avant, apres in remplacements:
        texte = texte.replace(avant, apres)
    chemin.write_text(texte, encoding="utf-8")
    print(f"{nom} : commentaires mis à jour")
