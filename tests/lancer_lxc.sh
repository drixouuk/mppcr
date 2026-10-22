#!/bin/bash
# Envoie le code de staging et le harnais dans le conteneur de production, puis
# y exécute une commande. Les vérifications tournent DANS l'image de production :
# c'est le seul environnement qui embarque Flask, JPype, MPXJ et Java.
#
#   ./lancer_lxc.sh                                  # copie seulement
#   ./lancer_lxc.sh "python3 make_preview.py"         # aperçus HTML
#   ./lancer_lxc.sh "python3 check_mppcr.py"          # harnais complet
#   ./lancer_lxc.sh "python3 alignment_check.py templates mppcr-redesign.html"
#
# Tous les scripts et fichiers de test de tests/ (*.py, *.xml, *.txt, *.mpp) et
# la maquette v2 sont envoyés à la racine de /tmp/harness, à côté du code ; les
# scripts d'enquête dans /tmp/harness/enquetes (code de l'application dans le
# PYTHONPATH : « python3 enquetes/structure_wbs.py test_projet.xml »).
#   ./lancer_lxc.sh "sh -c 'cat /tmp/mppcr-preview/les-deux.html'" | head
set -euo pipefail

HOST="${HOST:-root@192.168.1.87}"
KEY="${KEY:-$HOME/.ssh/docker_lxc}"
CHECK="$(cd "$(dirname "$0")" && pwd)"
STAGE="${STAGE:-$(cd "$CHECK/.." && pwd)}"
DEPS="$CHECK/.mppcr-deps"
SSHOPT=(-F /dev/null -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)

# /tmp ne survit pas entre deux commandes locales : l'archive est refaite ici.
ARCHIVE="${ARCHIVE:-/tmp/mppcr-harness.tgz}"
tar czf "$ARCHIVE" \
    -C "$STAGE" app.py dcma14.py montecarlo.py rapport_pdf.py VERSION templates Dockerfile \
                requirements.txt docker-compose.yml docker-compose.prod.yml \
                .gitignore .github README.md CHANGELOG.md \
    -C "$STAGE/docs/maquettes" mppcr-redesign.html \
    -C "$CHECK" $(cd "$CHECK" && ls -- *.py *.xml *.txt *.mpp 2>/dev/null) enquetes \
    -C "$(dirname "$DEPS")" "$(basename "$DEPS")"

scp "${SSHOPT[@]}" "$ARCHIVE" "$HOST:/tmp/mppcr-harness.tgz"
ssh "${SSHOPT[@]}" "$HOST" '
  docker exec mpp rm -rf /tmp/harness
  docker exec mpp mkdir -p /tmp/harness
  docker cp /tmp/mppcr-harness.tgz mpp:/tmp/harness/archive.tgz
  docker exec mpp tar xzf /tmp/harness/archive.tgz -C /tmp/harness'

if [ "$#" -gt 0 ]; then
  ssh "${SSHOPT[@]}" "$HOST" "docker exec -w /tmp/harness -e PYTHONPATH=/tmp/harness/.mppcr-deps:/tmp/harness mpp $*"
fi
