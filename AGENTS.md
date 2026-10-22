# AGENTS.md — mppcr

**MPPCR — MS Project Check & Risk** : application Flask qui analyse un planning `.mpp` (diagnostic qualité **DCMA-14**, simulation **Monte Carlo**, score de conformité sur 100, exports CSV/Excel).
Dépôt distant : `https://github.com/drixouuk/mppcr` (branche `main`). Image publiée : `ghcr.io/drixouuk/mppcr`.

Documentation de référence : [`README.md`](README.md) (contrôles, score, mémoire, sécurité) et [`CHANGELOG.md`](CHANGELOG.md) (effets mesurés de chaque version).

## Où est quoi

| Chemin | Contenu |
|---|---|
| `app.py` | Interface Flask : routes `/`, `/analyse`, `/annuler/<jeton>`, `/healthz` ; parsing des sorties des scripts, score, priorités, exports, quota par IP et verrou de concurrence (SQLite `usage.db`) |
| `dcma14.py` | 14 contrôles DCMA, CLI : `python3 dcma14.py <fichier> [--details]` |
| `montecarlo.py` | Simulation PERT, CLI : `python3 montecarlo.py <fichier> --sims N --opt 0.8 --pess 1.5 [--macro-level N]` |
| `templates/` | Jinja2 rendu serveur ; `base.html` contient la feuille de style de la maquette v2 **reprise verbatim** |
| `VERSION` | Version affichée par l'appli et vérifiée par la CI (`vX.Y.Z`) |
| `Dockerfile` | `python:3.11-slim` + JRE headless (MPXJ/JPype) + Gunicorn 2 workers gthread × 4 threads |
| `docker-compose.yml` / `docker-compose.prod.yml` | Build local / image GHCR **épinglée** (jamais `:latest`) |
| `.github/workflows/docker-publish.yml` | Build amd64 + arm64, smoke test, push GHCR, release sur tag `v*` |

## Commandes

- Lancer en local : `docker compose up --build` → <http://localhost:5000> (hors conteneur il faut Java + `pip install -r requirements.txt`, et `USAGE_DB_PATH` pour le compteur).
- Production : `docker compose -f docker-compose.prod.yml up -d` sur le CT 121 (`docker.lan`, `192.168.1.87`, clé `~/.ssh/docker_lxc`).
- Tests : le harnais vit **hors du dépôt**, dans `~/myprojects/homelab/.mppcr-check/` (`check_mppcr.py`, `alignment_check.py`, `verify_prod.sh`, références `ref_dcma.txt` / `ref_mc.txt`). Il tourne **dans l'image de production** via `lancer_lxc.sh`, qui copie par défaut le code de `homelab/.mppcr-staging/` : pour tester ce clone, `STAGE=~/myprojects/mppcr ./lancer_lxc.sh "python3 check_mppcr.py"`.

## Publier une version

1. Mettre `VERSION` et `CHANGELOG.md` à jour dans le même commit.
2. Pousser sur `main` (→ image `latest` + `sha-xxxxxxx`), puis l'étiquette : `git tag vX.Y.Z && git push origin vX.Y.Z`. La CI **refuse** un tag qui ne correspond pas à `VERSION`.
3. Épingler la nouvelle version dans `docker-compose.prod.yml`.

## Pièges

- **`dcma14.py` et `montecarlo.py` sont appelés en subprocess** : `app.py` parse leur sortie texte. Tout changement de format casse le parsing et les références figées — ne régénérer `ref_*.txt` que pour un changement de comportement délibéré.
- **Ne pas monter `mpxj` au-delà de 16.6.0** (régression OOM de la 16.7.0, voir `requirements.txt`). Versions figées sur l'image de référence, Python 3.11.
- **Tas JVM borné** (`JAVA_TOOL_OPTIONS=-Xmx1536m …`) : sans limite, la JVM se dimensionne sur la RAM de l'hôte vue depuis le LXC. Garder `MAX_CONCURRENT_ANALYSES × Xmx` sous `mem_limit` (5 Go).
- Le **smoke test CI** fait des `grep` sur le HTML (libellés, `id`, classes) : modifier un template impose de vérifier `docker-publish.yml`.
- Derrière un reverse proxy : `X-Forwarded-For` obligatoire (`ProxyFix x_for=1`) sinon le quota par IP compte le proxy ; `proxy_read_timeout` ≥ 700 s.
- **Jamais commité** : `*.mpp`, `*.mspdi`, `*.csv` (plannings clients), `.env*`.
- État git au 22/10/2026 : `main` = v1.13.0 ; branche `origin/dev/montecarlo-macro` (3 commits d'avance, répartition du risque par lot) = contenu actuel de `homelab/.mppcr-staging/` (`VERSION` `v1.14.0-dev`).
