#!/bin/bash
# Vérification fonctionnelle de la production (conteneur mpp, port 5000).
# Couvre : score, exports facultatifs téléchargeables, attente, arrêt, concurrence.
set -uo pipefail

BASE="${BASE:-http://127.0.0.1:5000}"
PROJECT="${PROJECT:-/tmp/mppcr-check-stage/test_projet.xml}"
WORK=/tmp/mppcr-prodcheck
FAILS=0
COUNT=0

# La fixture peut avoir été effacée entre deux exécutions : sans ce repli, tous
# les envois échouaient en amont (curl sans fichier) et le rapport n'affichait
# que des échecs incompréhensibles.
if [ ! -f "$PROJECT" ]; then
  for candidat in /tmp/mesure/test_projet.xml /tmp/harness/test_projet.xml \
                  "$(dirname "$0")/test_projet.xml"; do
    if [ -f "$candidat" ]; then PROJECT="$candidat"; break; fi
  done
fi
if [ ! -f "$PROJECT" ]; then
  echo "fixture introuvable (test_projet.xml) : renseigner PROJECT=…"
  exit 1
fi
echo "fixture utilisée : $PROJECT"

ok() { COUNT=$((COUNT + 1)); echo "  OK   $1"; }
ko() { COUNT=$((COUNT + 1)); FAILS=$((FAILS + 1)); echo "  FAIL $1 ${2:-}"; }
check() { if [ "$1" = "1" ]; then ok "$2"; else ko "$2" "${3:-}"; fi; }
res() { if [ "$1" -eq 0 ]; then echo 1; else echo 0; fi; }

extract_data() {
  python3 - "$1" "$2" "$3" <<'PYEOF'
import base64, re, sys
html = open(sys.argv[1], encoding="utf-8").read()
trouve = re.search(r'href="data:' + re.escape(sys.argv[2]) + r';base64,([^"]+)"', html)
if not trouve:
    sys.exit(1)
open(sys.argv[3], "wb").write(base64.b64decode(trouve.group(1)))
PYEOF
}

rm -rf "$WORK"; mkdir -p "$WORK"
cp "$PROJECT" "$WORK/planning.mpp"

echo "=== 1. service et code servi ==="
printf "  /healthz : "; curl -s "$BASE/healthz"; echo
st=$(docker inspect --format '{{.State.Health.Status}}' mpp)
check "$([ "$st" = healthy ] && echo 1 || echo 0)" "conteneur healthy" "status=$st"
# Filtrer un FICHIER, jamais un tube : avec « set -o pipefail », grep -q qui sort
# dès la première correspondance fait échouer « docker logs » en SIGPIPE (141).
docker logs mpp > "$WORK/logs.txt" 2>&1
grep -q 'Using worker: gthread' "$WORK/logs.txt"
check "$(res $?)" "workers gthread actifs"

echo
echo "=== 2. formulaire : exports facultatifs, attente, arrêt ==="
curl -fsS "$BASE/" -o "$WORK/index.html"
check "$(res $?)" "GET / = 200"
check "$([ "$(grep -o 'name="export" id=' "$WORK/index.html" | wc -l)" = "2" ] && echo 1 || echo 0)" \
  "deux exports proposés (CSV, Excel)"
grep -q 'class="stepper"' "$WORK/index.html"; check "$(res $?)" "fil d'étapes de la maquette"
grep -qE 'class="version-tag">v[0-9]+\.[0-9]+\.[0-9]+[^<]*<' "$WORK/index.html"
check "$(res $?)" "version affichée dans l'en-tête"
grep -q 'class="dropzone"' "$WORK/index.html"; check "$(res $?)" "zone de dépôt du fichier"
grep -q '<select id="sims" name="sims">' "$WORK/index.html"; check "$(res $?)" "nombre de simulations en liste"
grep -q '<select id="factors" name="factors">' "$WORK/index.html"
check "$(res $?)" "facteurs PERT en liste déroulante"
grep -q 'name="estimates_file"' "$WORK/index.html"; check "$(res $?)" "estimations 3 points (CSV)"
grep -qE '<select id="macro" name="macro_level">' "$WORK/index.html"
check "$(res $?)" "répartition par lot (liste déroulante)"
check "$([ "$(grep -oE '<option value="[123]">' "$WORK/index.html" | wc -l)" = "3" ] && echo 1 || echo 0)" \
  "trois niveaux proposés (1 à 3)"
grep -q 'Export des résultats' "$WORK/index.html" && grep -q '(facultatif)' "$WORK/index.html"
check "$(res $?)" "exports dans un volet repliable"
check "$([ "$(grep -o 'name="format"' "$WORK/index.html" | wc -l)" = "0" ] && echo 1 || echo 0)" \
  "la page web n'est plus un format à choisir"
grep -q 'name="client_token"' "$WORK/index.html"; check "$(res $?)" "jeton d'annulation"
grep -q 'id="waiting" hidden' "$WORK/index.html"; check "$(res $?)" "page d'attente masquée"
grep -q 'id="cancel-btn"' "$WORK/index.html"; check "$(res $?)" "bouton d'arrêt"

echo
echo "=== 3. score de conformité sur une analyse réelle ==="
code=$(curl -sS -o "$WORK/result.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 198.20.0.1" \
  -F "analysis=both" -F "sims=500" -F "mpp_file=@$WORK/planning.mpp" "$BASE/analyse")
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "POST page = 200" "code=$code"
score=$(tr -d '\n' < "$WORK/result.html" | grep -oE 'class="score-num">[0-9]+' | grep -oE '[0-9]+')
echo "  score obtenu : ${score}/100"
check "$([ -n "$score" ] && echo 1 || echo 0)" "score chiffré affiché" "score='$score'"
grep -q 'Comment ce score est calculé (barème v1)' "$WORK/result.html"; check "$(res $?)" "barème v1"
grep -q 'Incertitude P50 → P90' "$WORK/result.html"; check "$(res $?)" "dispersion Monte Carlo"
grep -q 'class="card score-hero"' "$WORK/result.html"; check "$(res $?)" "héros de score (maquette v2)"
grep -q 'class="band-badge' "$WORK/result.html"; check "$(res $?)" "bande de verdict du score"
check "$([ "$(grep -o 'class="kpi' "$WORK/result.html" | wc -l)" = "6" ] && echo 1 || echo 0)" \
  "cinq cartes KPI (plus le conteneur)" "n=$(grep -o 'class=.kpi' "$WORK/result.html" | wc -l)"
grep -q 'class="subnav subnav-fixe"' "$WORK/result.html"; check "$(res $?)" "sous-navigation fixe des sections"
grep -q '<details class="prio"' "$WORK/result.html"; check "$(res $?)" "priorités en volets dépliables"
grep -q 'class="card table-card"' "$WORK/result.html"; check "$(res $?)" "tableau des contrôles (maquette v2)"
grep -q 'class="dur-card reco"' "$WORK/result.html"; check "$(res $?)" "P80 mis en avant"
grep -q 'class="card axis-card"' "$WORK/result.html"; check "$(res $?)" "axe des percentiles"
grep -q 'id="print-btn"' "$WORK/result.html"; check "$(res $?)" "bouton Imprimer la page"
grep -q 'Tâches concernées (numéros MS Project)' "$WORK/result.html"
check "$(res $?)" "tâches concernées listées dans les priorités"
tr -d '\n' < "$WORK/result.html" | grep -qE 'class="task-id">[0-9]+<'
check "$(res $?)" "numéros de tâche (N° MS Project) affichés"
ids=$(tr -d '\n' < "$WORK/result.html" | grep -oE 'class="task-id">[0-9]+' | grep -oE '[0-9]+' | head -3 | tr '\n' ' ')
echo "  extrait des N° affichés : ${ids}"
check "$([ "$(grep -c 'download="MPPCR_' "$WORK/result.html")" = "0" ] && echo 1 || echo 0)" \
  "aucun export quand rien n'est coché"

echo
echo "=== 3b. répartition du risque par lot (niveau 1) ==="
code=$(curl -sS -o "$WORK/lot.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 198.20.0.11" \
  -F "analysis=montecarlo" -F "sims=500" -F "macro_level=1" \
  -F "mpp_file=@$WORK/planning.mpp" "$BASE/analyse")
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "analyse avec répartition par lot = 200" "code=$code"
grep -q 'Répartition du risque par lot' "$WORK/lot.html"
check "$(res $?)" "tableau par lot affiché"
grep -q 'Niveau 1 ·' "$WORK/lot.html"; check "$(res $?)" "niveau et nombre de lots rappelés"
grep -q 'Écart P50 vs planifié' "$WORK/lot.html"; check "$(res $?)" "colonnes du tableau par lot"
grep -q 'class="card table-card"' "$WORK/lot.html"; check "$(res $?)" "composants de la maquette réutilisés"
code=$(curl -sS -o "$WORK/lot-refus.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 198.20.0.12" \
  -F "analysis=montecarlo" -F "macro_level=4" \
  -F "mpp_file=@$WORK/planning.mpp" "$BASE/analyse")
check "$([ "$code" = "400" ] && echo 1 || echo 0)" "niveau hors liste refusé = 400" "code=$code"
grep -q 'compris entre 1 et 3' "$WORK/lot-refus.html"
check "$(res $?)" "message de refus explicite"

echo
echo "=== 4. exports CSV et Excel demandés en plus ==="
code=$(curl -sS -o "$WORK/rc.html" -w '%{http_code}' \
  -H "X-Forwarded-For: 198.20.0.2" \
  -F "analysis=dcma" -F "export=csv" -F "export=xlsx" \
  -F "mpp_file=@$WORK/planning.mpp" "$BASE/analyse")
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "POST avec deux exports = 200 (page)" "code=$code"
check "$([ "$(grep -o 'download="MPPCR_planning_' "$WORK/rc.html" | wc -l)" = "2" ] && echo 1 || echo 0)" \
  "deux boutons de téléchargement"
grep -q 'Détail des contrôles' "$WORK/rc.html"; check "$(res $?)" "résultats web toujours affichés"
extract_data "$WORK/rc.html" "text/csv" "$WORK/export.csv"
check "$(res $?)" "CSV téléchargeable"
grep -q 'Tâches concernées (N° MS Project)' "$WORK/export.csv"; check "$(res $?)" "colonne des tâches dans le CSV"
grep -q 'SCORE DE CONFORMITÉ' "$WORK/export.csv"; check "$(res $?)" "score dans le CSV"
extract_data "$WORK/rc.html" "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" "$WORK/export.xlsx"
check "$(res $?)" "Excel téléchargeable"
check "$([ "$(head -c 2 "$WORK/export.xlsx")" = "PK" ] && echo 1 || echo 0)" "classeur Excel valide"
echo "  CSV : $(stat -c %s "$WORK/export.csv") octets | XLSX : $(stat -c %s "$WORK/export.xlsx") octets"

echo
echo "=== 5. arrêt d'analyse ==="
jeton=$(python3 -c "import secrets; print(secrets.token_hex(16))")
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/annuler/$jeton")
check "$([ "$code" = "404" ] && echo 1 || echo 0)" "arrêt sans analyse en cours -> 404" "code=$code"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/annuler/zzz")
check "$([ "$code" = "400" ] && echo 1 || echo 0)" "jeton invalide -> 400" "code=$code"

echo
echo "=== 5b. garde-fous mémoire ==="
lim=$(docker inspect --format '{{.HostConfig.Memory}}' mpp)
check "$([ "$lim" -gt 0 ] && echo 1 || echo 0)" "conteneur plafonné en mémoire ($((lim / 1024 / 1024)) Mo)"
adj=$(docker inspect --format '{{.HostConfig.OomScoreAdj}}' mpp)
check "$([ "$adj" -ge 0 ] && echo 1 || echo 0)" "priorité de sacrifice définie (oom_score_adj=$adj)"
heap=$(docker exec mpp java -XX:+PrintFlagsFinal -version 2>/dev/null | grep -oE "^ *size_t MaxHeapSize *= *[0-9]+" | grep -oE "[0-9]+$")
echo "  tas JVM maximal : $((heap / 1024 / 1024)) Mo"
check "$([ -n "$heap" ] && [ "$heap" -le 2147483648 ] && echo 1 || echo 0)" \
  "tas JVM borné (1,5 Go attendu, 2 Go maximum)" "heap=$heap"
# Le refus se teste à partir de la limite RÉELLEMENT configurée dans le conteneur
# (elle vaut 20 Mo en production ici, 5 Mo par défaut dans l'image).
limite_mo=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' mpp \
  | grep '^MAX_UPLOAD_MB=' | cut -d= -f2)
limite_mo=${limite_mo:-5}
taille_mo=$((limite_mo + 3))
head -c $((taille_mo * 1000000)) /dev/urandom > "$WORK/trop-gros.mpp"
code=$(curl -sS -o "$WORK/tg.html" -w '%{http_code}' -H "X-Forwarded-For: 198.20.9.1" \
  -F "analysis=dcma" -F "mpp_file=@$WORK/trop-gros.mpp" "$BASE/analyse")
check "$([ "$code" = "413" ] && echo 1 || echo 0)" \
  "fichier de ${taille_mo} Mo refusé pour une limite de ${limite_mo} Mo = 413" "code=$code"
grep -q 'Fichier trop volumineux' "$WORK/tg.html"; check "$(res $?)" "titre du message de refus"
grep -qi "mémoire allouée à l" "$WORK/tg.html"; check "$(res $?)" "mémoire allouée rappelée"
# La valeur annoncée doit être celle réellement appliquée au conteneur.
grep -q "$((heap / 1048576)) Mo" "$WORK/tg.html"; check "$(res $?)" \
  "valeur de mémoire annoncée cohérente ($((heap / 1048576)) Mo)"
# Le message de manque de mémoire doit évoquer la piste d'un fichier corrompu
# (il ne se déclenche qu'après une vraie exhaustion : on vérifie le code servi).
docker exec mpp grep -q "peut aussi être corrompu" /app/app.py; check "$(res $?)" \
  "piste du fichier corrompu présente dans le code servi"

echo
echo "=== 6. concurrence : /healthz servi pendant trois analyses ==="
for i in 1 2 3; do
  curl -sS -o /dev/null -H "X-Forwarded-For: 198.20.1.$i" \
    -F "analysis=montecarlo" -F "sims=4000" \
    -F "mpp_file=@$WORK/planning.mpp" "$BASE/analyse" &
done
sleep 4
debut=$(date +%s%N)
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/healthz")
duree=$(( ($(date +%s%N) - debut) / 1000000 ))
wait
check "$([ "$code" = "200" ] && echo 1 || echo 0)" "/healthz répond sous charge (HTTP $code, ${duree} ms)"
docker inspect --format '{{.State.Health.Status}}' mpp | grep -q healthy
check "$(res $?)" "conteneur resté healthy après la charge"

echo
echo "=== 7. état interne ==="
n=$(docker exec mpp python3 -c "
import sqlite3
c = sqlite3.connect('/app/data/usage.db')
print(c.execute('SELECT COUNT(*) FROM running').fetchone()[0])")
check "$([ "$n" = "0" ] && echo 1 || echo 0)" "aucun créneau d'analyse résiduel" "n=$n"
m=$(docker exec mpp sh -c 'ls /tmp 2>/dev/null | grep -c mppweb_ || true')
check "$([ "$m" = "0" ] && echo 1 || echo 0)" "aucun répertoire temporaire résiduel" "n=$m"

rm -rf "$WORK"
echo
echo "======================================================================"
if [ "$FAILS" -eq 0 ]; then echo "PRODUCTION: $COUNT/$COUNT OK"; else echo "PRODUCTION: $((COUNT - FAILS))/$COUNT OK — $FAILS échec(s)"; fi
echo "======================================================================"
exit "$FAILS"
