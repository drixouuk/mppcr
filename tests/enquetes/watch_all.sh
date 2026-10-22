#!/bin/bash
# Attend la fin de toutes les exécutions en cours du dépôt, puis fait le bilan.
TOKEN=$(sed -E 's#https://[^:]+:([^@]+)@github.com#\1#' ~/.git-credentials | head -1)
API="https://api.github.com/repos/drixouuk/mppcr"
H=(-H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json")

for i in $(seq 1 90); do
  actifs=$(curl -sS "${H[@]}" "$API/actions/runs?per_page=6" | python3 -c "
import json,sys
d=json.load(sys.stdin)
runs=d['workflow_runs']
print(sum(1 for r in runs if r['status'] != 'completed'))
for r in runs[:4]:
    if r['status'] != 'completed':
        print('   en cours :', r['head_sha'][:8], r['event'], r['status'], file=sys.stderr)
")
  echo "$(date +%H:%M:%S)  exécutions actives : $actifs"
  [ "$actifs" = "0" ] && break
  sleep 20
done

echo
echo "=== exécutions ==="
curl -sS "${H[@]}" "$API/actions/runs?per_page=6" | python3 -c "
import json,sys
for r in json.load(sys.stdin)['workflow_runs']:
    ref = r.get('head_branch') or r['head_sha'][:8]
    print(f\"  #{r['run_number']} {r['event']:<5} {ref:<10} {r['status']}/{r['conclusion']}\")
    for j in r.get('pull_requests', []):
        pass
"
echo
echo "=== jobs de la dernière exécution sur tag ==="
RUN=$(curl -sS "${H[@]}" "$API/actions/runs?event=push&per_page=10" | python3 -c "
import json,sys
for r in json.load(sys.stdin)['workflow_runs']:
    if r['head_branch'] and r['head_branch'].startswith('v1.0.1'):
        print(r['id']); break
")
if [ -n "$RUN" ]; then
  curl -sS "${H[@]}" "$API/actions/runs/$RUN/jobs" | python3 -c "
import json,sys
for j in json.load(sys.stdin)['jobs']:
    print(f\"  {j['name']:<26} {j['status']:<11} {str(j['conclusion'])}\")
"
else
  echo "  (exécution de tag non trouvée)"
fi

echo
echo "=== releases ==="
curl -sS "${H[@]}" "$API/releases" | python3 -c "
import json,sys
for r in json.load(sys.stdin):
    print(f\"  {r['tag_name']} | {r['published_at']} | {r['html_url']}\")
"
echo
echo "=== tags du paquet GHCR ==="
curl -sS "${H[@]}" "https://api.github.com/user/packages/container/mppcr/versions" | python3 -c "
import json,sys
tags=[]
for v in json.load(sys.stdin):
    tags += v['metadata']['container']['tags']
for t in sorted(set(tags)):
    print('  ', t)
"
echo
echo "=== tags du dépôt ==="
curl -sS "${H[@]}" "$API/tags" | python3 -c "
import json,sys
for t in json.load(sys.stdin):
    print(f\"   {t['name']} -> {t['commit']['sha'][:8]}\")
"
