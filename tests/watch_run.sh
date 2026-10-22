#!/bin/bash
# Suit l'exécution du workflow de publication jusqu'à son terme.
TOKEN=$(sed -E 's#https://[^:]+:([^@]+)@github.com#\1#' ~/.git-credentials | head -1)
API="https://api.github.com/repos/drixouuk/mppcr"
H=(-H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json")
RUN=$(curl -sS "${H[@]}" "$API/actions/runs?per_page=1" | python3 -c "import json,sys; print(json.load(sys.stdin)['workflow_runs'][0]['id'])")
echo "run id : $RUN"

for i in $(seq 1 90); do
  line=$(curl -sS "${H[@]}" "$API/actions/runs/$RUN" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"{d['status']}|{d['conclusion']}|{d['created_at']}|{d['updated_at']}\")")
  status=${line%%|*}
  echo "$(date +%H:%M:%S)  $line"
  [ "$status" = "completed" ] && break
  sleep 20
done

echo
echo "=== détail des jobs ==="
curl -sS "${H[@]}" "$API/actions/runs/$RUN/jobs?per_page=50" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for j in d['jobs']:
    print(f\"  {j['name']:<28} {j['status']:<12} {str(j['conclusion']):<10} {j['started_at']} -> {j['completed_at']}\")
    for s in j.get('steps', []):
        if s.get('conclusion') not in (None, 'success', 'skipped'):
            print(f\"      ÉTAPE EN ÉCHEC : {s['name']} ({s['conclusion']})\")
"
echo
echo "=== artefacts et image publiée ==="
curl -sS "${H[@]}" "$API/actions/runs/$RUN/artifacts" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('  artefacts :', d['total_count'])
for a in d.get('artifacts', []): print('   -', a['name'], a['size_in_bytes'], 'octets')
"
curl -sS "${H[@]}" "https://api.github.com/user/packages/container/mppcr/versions" | python3 -c "
import json,sys
d=json.load(sys.stdin)
if isinstance(d, dict) and d.get('message'):
    print('  paquets   :', d['message'])
else:
    for v in d:
        print('  version   :', v['name'], '|', ', '.join(v['metadata']['container']['tags']))
"
