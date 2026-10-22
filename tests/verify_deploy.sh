#!/bin/bash
# Vérifie que le conteneur de production tourne bien le code du dépôt /mpp.
# Un « docker compose up » silencieux peut ne pas recréer le conteneur : cette
# comparaison d'empreintes est le seul contrôle fiable.
set -uo pipefail

HOST="${HOST:-root@192.168.1.87}"
KEY="${KEY:-$HOME/.ssh/docker_lxc}"
SSHOPT=(-F /dev/null -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)

DIFFS=0
compare() {  # chemin dans /mpp, chemin dans le conteneur
  local repo container
  repo=$(ssh "${SSHOPT[@]}" "$HOST" "sha256sum '/mpp/$1' 2>/dev/null | cut -c1-16")
  container=$(ssh "${SSHOPT[@]}" "$HOST" "docker exec mpp sha256sum '$2' 2>/dev/null | cut -c1-16")
  if [ -n "$repo" ] && [ "$repo" = "$container" ]; then
    printf "  OK   %-26s %s\n" "$1" "$repo"
  else
    printf "  DIFF %-26s dépôt=%s production=%s\n" "$1" "${repo:-absent}" "${container:-absent}"
    DIFFS=$((DIFFS + 1))
  fi
}

echo "=== code du dépôt vs conteneur en production ==="
compare app.py /app/app.py
compare dcma14.py /app/dcma14.py
compare montecarlo.py /app/montecarlo.py
compare requirements.txt /app/requirements.txt
for t in base.html index.html result.html error.html; do
  compare "templates/$t" "/app/templates/$t"
done

echo
echo "=== état du service ==="
ssh "${SSHOPT[@]}" "$HOST" 'docker ps --filter name=^mpp$ --format "  conteneur : {{.Names}} | {{.Status}}"; printf "  /healthz  : "; curl -s http://127.0.0.1:5000/healthz; echo'

echo
if [ "$DIFFS" -eq 0 ]; then
  echo "DÉPLOIEMENT COHÉRENT : le conteneur sert exactement le code du dépôt."
else
  echo "DÉPLOIEMENT INCOHÉRENT : $DIFFS écart(s) — relancer « docker compose up -d --force-recreate »."
  exit 1
fi
