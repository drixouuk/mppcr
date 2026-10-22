#!/bin/bash
# Smoke test HTTP de l'image MPPCR (conteneur jetable).
# Couvre : santé, healthcheck, formulaire, analyse DCMA+Monte Carlo, score de
# conformité, exports CSV/Excel, quota, concurrence (gthread) et nettoyage.
#
#   IMAGE=mpp-mpp:latest PORT=5001 LIMIT=3 bash smoke.sh
set -uo pipefail

IMAGE="${IMAGE:-mpp-mpp:latest}"
NAME="${NAME:-mppcr-smoke}"
PORT="${PORT:-5001}"
LIMIT="${LIMIT:-3}"
WORK=/tmp/mppcr-smoke
PROJECT="${PROJECT:-/tmp/mppcr-check-stage/test_projet.xml}"
FAILS=0
COUNT=0

ok() { COUNT=$((COUNT + 1)); echo "  OK   $1"; }
ko() { COUNT=$((COUNT + 1)); FAILS=$((FAILS + 1)); echo "  FAIL $1 ${2:-}"; }
check() { if [ "$1" = "1" ]; then ok "$2"; else ko "$2" "${3:-}"; fi; }
res() { if [ "$1" -eq 0 ]; then echo 1; else echo 0; fi; }

# Extrait un fichier encodé dans la page (lien data:base64) vers un fichier.
extract_data() {
  python3 - "$1" "$2" "$3" <<'PYEOF'
import base64, re, sys
html = open(sys.argv[1], encoding="utf-8").read()
motif = r'href="data:' + re.escape(sys.argv[2]) + r';base64,([^"]+)"'
trouve = re.search(motif, html)
if not trouve:
    sys.exit(1)
open(sys.argv[3], "wb").write(base64.b64decode(trouve.group(1)))
PYEOF
}

docker rm -f "$NAME" >/dev/null 2>&1
rm -rf "$WORK"; mkdir -p "$WORK"
cp "$PROJECT" "$WORK/planning.mpp" || { echo "projet de test introuvable : $PROJECT"; exit 1; }

echo "=== démarrage de $NAME ($IMAGE) sur :$PORT (limite $LIMIT) ==="
docker run -d --name "$NAME" -p "$PORT:5000" \
  -e DAILY_ANALYSIS_LIMIT="$LIMIT" -e MAX_CONCURRENT_ANALYSES=2 \
  -e MAX_UPLOAD_MB="${MAX_UPLOAD_MB:-1}" "$IMAGE" >/dev/null

for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null | grep -q '^ok$'; then
    echo "  /healthz OK après ${i} tentative(s)"; break
  fi
  [ "$i" -eq 40 ] && { echo "  /healthz indisponible"; docker logs "$NAME"; exit 1; }
  sleep 2
done

echo
echo "=== 1. healthcheck de l'image et modèle de workers ==="
for i in $(seq 1 24); do
  st=$(docker inspect --format '{{.State.Health.Status}}' "$NAME")
  [ "$st" = "healthy" ] && break
  [ "$i" -eq 24 ] && { echo "  status=$st"; docker logs "$NAME"; }
  sleep 5
done
check "$([ "$st" = healthy ] && echo 1 || echo 0)" "HEALTHCHECK de l'image = healthy" "status=$st"
docker logs "$NAME" 2>&1 | grep -q 'Using worker: gthread'
check "$(res $?)" "Gunicorn en workers gthread" "worker class != gthread"
docker exec "$NAME" test -f /app/data/usage.db
check "$(res $?)" "usage.db créé dans /app/data"

echo
echo "=== 2. formulaire ==="
curl -fsS "http://127.0.0.1:$PORT/" -o "$WORK/index.html"
check "$(res $?)" "GET / = 200"
grep -q 'class="brand-mark">M<' "$WORK/index.html"; check "$(res $?)" "marque MPPCR"
grep -q "$LIMIT analyses restantes aujourd’hui pour votre adresse IP" "$WORK/index.html"
check "$(res $?)" "quota affiché"
grep -q 'class="tabs"' "$WORK/index.html"; check "$(res $?)" "barre d'onglets"
check "$([ "$(grep -o 'class="tab is-off"' "$WORK/index.html" | wc -l)" = "2" ] && echo 1 || echo 0)" \
  "onglets résultats inertes sur le formulaire"
check "$([ "$(grep -o 'name="analysis" id=' "$WORK/index.html" | wc -l)" = "3" ] && echo 1 || echo 0)" \
  "3 choix d'analyse"
check "$([ "$(grep -o 'name="export"' "$WORK/index.html" | wc -l)" = "2" ] && echo 1 || echo 0)" \
  "exports facultatifs CSV et Excel"
grep -q 'Export des résultats (facultatif)' "$WORK/index.html"
check "$(res $?)" "exports dans un volet repliable"
grep -q 'name="client_token"' "$WORK/index.html"; check "$(res $?)" "jeton d'annulation présent"
grep -q 'id="waiting" hidden' "$WORK/index.html"; check "$(res $?)" "page d'attente masquée sans JS"
grep -q 'id="cancel-btn"' "$WORK/index.html"; check "$(res $?)" "bouton d'arrêt présent"
grep -q 'class="drop"' "$WORK/index.html"; check "$(res $?)" "zone de dépôt pointillée"
grep -q "${MAX_UPLOAD_MB:-1} Mo max" "$WORK/index.html"; check "$(res $?)" "limite de taille annoncée"
grep -q 'peuvent être refusés' "$WORK/index.html"; check "$(res $?)" "avertissement plannings volumineux"
check "$([ "$(grep -c 'googleapis\|cdn\.' "$WORK/index.html")" = "0" ] && echo 1 || echo 0)" \
  "aucun CDN ni police hébergée"

echo
echo "=== 3. analyse réelle : DCMA + Monte Carlo, format page ==="
code=$(curl -sS -o "$WORK/result.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 203.0.113.1" \
  -F "analysis=both" -F "format=page" -F "sims=500" \
  -F "mpp_file=@$WORK/planning.mpp;type=application/octet-stream" \
  "http://127.0.0.1:$PORT/analyse")
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "POST /analyse = 200" "code=$code"
if [ "$code" = "200" ]; then
  grep -q 'class="card card-pad score"' "$WORK/result.html"; check "$(res $?)" "score de conformité affiché"
  grep -q 'Score de conformité DCMA' "$WORK/result.html"; check "$(res $?)" "libellé du score"
  grep -q 'Comment ce score est calculé' "$WORK/result.html"; check "$(res $?)" "barème v1 dépliable"
  grep -q 'class="score-fill' "$WORK/result.html"; check "$(res $?)" "jauge du score"
  grep -q 'Incertitude P50 → P90' "$WORK/result.html"; check "$(res $?)" "dispersion Monte Carlo"
  grep -q 'class="file-chip"' "$WORK/result.html"; check "$(res $?)" "puce du fichier"
  grep -q 'href="#dcma"' "$WORK/result.html"; check "$(res $?)" "onglet ancré DCMA"
  grep -q 'href="#montecarlo"' "$WORK/result.html"; check "$(res $?)" "onglet ancré Monte Carlo"
  grep -q 'class="kpi kpi-total"' "$WORK/result.html"; check "$(res $?)" "cartes KPI"
  grep -q 'Tâches concernées (N°)' "$WORK/result.html"; check "$(res $?)" "colonne des tâches concernées"
  tr -d '\n' < "$WORK/result.html" | grep -qE 'class="ids">[[:space:]]*[0-9]'
  check "$(res $?)" "numéros de tâche affichés"
  grep -q 'class="dur is-rec"' "$WORK/result.html"; check "$(res $?)" "carte P80 mise en avant"
  grep -q 'class="scale-mark' "$WORK/result.html"; check "$(res $?)" "échelle des percentiles"
  grep -q 'crit-fill cf-' "$WORK/result.html"; check "$(res $?)" "barres de criticité"
  grep -q 'class="decision"' "$WORK/result.html"; check "$(res $?)" "encart décisionnel"
  grep -q '<details class="raw">' "$WORK/result.html"; check "$(res $?)" "sortie brute repliable"
  check "$([ "$(grep -c Log4j "$WORK/result.html")" = "0" ] && echo 1 || echo 0)" "aucune trace Log4j"
fi

echo
echo "=== 3b. fichier au-delà de la limite de taille ==="
head -c 2000000 /dev/urandom > "$WORK/volumineux.mpp"
code=$(curl -sS -o "$WORK/trop-gros.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 203.0.113.9" \
  -F "analysis=dcma" -F "mpp_file=@$WORK/volumineux.mpp" "http://127.0.0.1:$PORT/analyse")
check "$([ "$code" = "413" ] && echo 1 || echo 0)" "refus 413 pour un fichier trop gros" "code=$code"
grep -q 'Fichier trop volumineux' "$WORK/trop-gros.html"; check "$(res $?)" "titre du message"
grep -q 'Mo reçus' "$WORK/trop-gros.html"; check "$(res $?)" "taille reçue rappelée"
grep -q 'extrait du planning' "$WORK/trop-gros.html"; check "$(res $?)" "conseil d'extrait"
grep -q 'OOM\|oom_score\|mem_limit' "$WORK/trop-gros.html"; check "$([ $? -ne 0 ] && echo 1 || echo 0)" "message sans jargon technique"

echo
echo "=== 4. export CSV demandé en plus de la page ==="
code=$(curl -sS -o "$WORK/result-csv.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 203.0.113.2" \
  -F "analysis=dcma" -F "export=csv" \
  -F "mpp_file=@$WORK/planning.mpp" "http://127.0.0.1:$PORT/analyse")
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "POST avec export CSV = 200 (page)" "code=$code"
grep -q 'Exports disponibles' "$WORK/result-csv.html"; check "$(res $?)" "bloc « Exports disponibles »"
grep -q 'Détail des contrôles' "$WORK/result-csv.html"; check "$(res $?)" "résultats web toujours affichés"
grep -q 'download="MPPCR_planning_' "$WORK/result-csv.html"; check "$(res $?)" "nom de fichier daté"
extract_data "$WORK/result-csv.html" "text/csv" "$WORK/export.csv"
check "$(res $?)" "lien de téléchargement CSV exploitable"
grep -q 'SCORE DE CONFORMITÉ' "$WORK/export.csv"; check "$(res $?)" "section score dans le CSV"
grep -q 'Logic (tâches sans lien amont/aval)' "$WORK/export.csv"; check "$(res $?)" "intitulés accentués"
grep -q 'Tâches concernées (N° MS Project)' "$WORK/export.csv"; check "$(res $?)" "colonne des tâches dans le CSV"
head -c 3 "$WORK/export.csv" | grep -q $'\xef\xbb\xbf'; check "$(res $?)" "BOM UTF-8"

echo
echo "=== 5. export Excel demandé en plus de la page ==="
code=$(curl -sS -o "$WORK/result-xlsx.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 203.0.113.3" \
  -F "analysis=both" -F "export=xlsx" -F "sims=500" \
  -F "mpp_file=@$WORK/planning.mpp" "http://127.0.0.1:$PORT/analyse")
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "POST avec export Excel = 200 (page)" "code=$code"
grep -q 'Classeur Excel' "$WORK/result-xlsx.html"; check "$(res $?)" "lien Excel proposé"
extract_data "$WORK/result-xlsx.html" "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" "$WORK/export.xlsx"
check "$(res $?)" "lien de téléchargement Excel exploitable"
check "$([ "$(head -c 2 "$WORK/export.xlsx")" = "PK" ] && echo 1 || echo 0)" "signature ZIP"
size=$(stat -c %s "$WORK/export.xlsx" 2>/dev/null || echo 0)
check "$([ "$size" -gt 5000 ] && echo 1 || echo 0)" "classeur non vide ($size octets)"
docker run --rm -v "$WORK:/w" --entrypoint python3 "$IMAGE" -c "
import sys, zipfile
with zipfile.ZipFile('/w/export.xlsx') as z:
    wb = z.read('xl/workbook.xml').decode('utf-8')
sys.exit(0 if 'Contrôles DCMA-14' in wb and 'Monte Carlo' in wb else 1)"
check "$(res $?)" "deux feuilles (DCMA + Monte Carlo)"

echo
echo "=== 5b. arrêt d'une analyse ==="
jeton=$(python3 -c "import secrets; print(secrets.token_hex(16))")
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/annuler/$jeton")
check "$([ "$code" = "404" ] && echo 1 || echo 0)" "arrêt sans analyse en cours -> 404" "code=$code"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/annuler/zzz")
check "$([ "$code" = "400" ] && echo 1 || echo 0)" "jeton invalide -> 400" "code=$code"
code=$(curl -sS -o "$WORK/annuler.html" -w '%{http_code}' "http://127.0.0.1:$PORT/annuler/$jeton")
grep -q 'Aucune analyse à interrompre' "$WORK/annuler.html"
check "$(res $?)" "lien direct -> page explicative"

echo "=== 6. quota : épuisement puis refus ==="
for i in $(seq 1 "$LIMIT"); do
  code=$(curl -sS -o /dev/null -w '%{http_code}' \
    -F "analysis=dcma" -F "format=page" \
    -F "mpp_file=@$WORK/planning.mpp" "http://127.0.0.1:$PORT/analyse")
  echo "  analyse #$i -> HTTP $code"
done
code=$(curl -sS -o "$WORK/quota.html" -w '%{http_code}' \
  -F "analysis=dcma" -F "format=page" \
  -F "mpp_file=@$WORK/planning.mpp" "http://127.0.0.1:$PORT/analyse")
check "$([ "$code" = "429" ] && echo 1 || echo 0)" "analyse #$((LIMIT + 1)) refusée = 429" "code=$code"
grep -q 'Quota quotidien atteint' "$WORK/quota.html"; check "$(res $?)" "page quota"
grep -q 'c-warn' "$WORK/quota.html"; check "$(res $?)" "callout ambre"

echo
echo "=== 7. concurrence : /healthz reste servi pendant les analyses ==="
for i in 1 2 3; do
  curl -sS -o /dev/null -H "X-Forwarded-For: 203.0.113.$((100 + i))" \
    -F "analysis=montecarlo" -F "format=page" -F "sims=4000" \
    -F "mpp_file=@$WORK/planning.mpp" "http://127.0.0.1:$PORT/analyse" &
done
sleep 4
debut=$(date +%s%N)
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/healthz")
duree=$(( ($(date +%s%N) - debut) / 1000000 ))
wait
check "$([ "$code" = "200" ] && echo 1 || echo 0)" \
  "/healthz répond pendant 3 analyses simultanées (${code} en ${duree} ms)"
check "$([ "$duree" -lt 3000 ] && echo 1 || echo 0)" "réponse rapide sous charge (${duree} ms)"
docker logs "$NAME" 2>&1 | grep -qi 'traceback'
check "$([ $? -ne 0 ] && echo 1 || echo 0)" "aucune trace d'erreur dans les logs"

echo
echo "=== 8. fichiers temporaires nettoyés ==="
n=$(docker exec "$NAME" sh -c 'ls /tmp 2>/dev/null | grep -c mppweb_ || true')
check "$([ "$n" = "0" ] && echo 1 || echo 0)" "aucun répertoire mppweb_* résiduel" "n=$n"

echo
echo "=== 9. état final ==="
docker inspect --format '  user={{.Config.User}} healthcheck={{if .Config.Healthcheck}}oui{{else}}non{{end}}' "$NAME"
docker exec "$NAME" sh -c 'ls -ld /app/data'

echo
echo "======================================================================"
if [ "$FAILS" -eq 0 ]; then
  echo "SMOKE TEST: $COUNT/$COUNT OK"
else
  echo "SMOKE TEST: $((COUNT - FAILS))/$COUNT OK — $FAILS échec(s)"
fi
echo "======================================================================"
docker rm -f "$NAME" >/dev/null 2>&1
rm -rf "$WORK"
exit "$FAILS"
