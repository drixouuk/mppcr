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
| `tests/` | Harnais de vérification et outils de diagnostic (`check_mppcr.py`, `alignment_check.py`, `verify_prod.sh`, mesures mémoire, références `ref_dcma.txt` / `ref_mc.txt`, aperçus HTML) |
| `tests/.mppcr-deps/` | Dépendances du harnais montées en `PYTHONPATH` par `lancer_lxc.sh` — **ignoré**, régénérer par la commande `pip install --target …` du README (§ Contrôles et tests : pyyaml, xlsxwriter, reportlab et ses dépendances, wheels Linux x86_64 / Python 3.11) |
| `rapport_pdf.py` | Rapport PDF (ReportLab) : synthèse et annexes, à partir des sorties analysées par `app.py` |
| `tests/enquetes/` | Scripts d'enquête ponctuels (mémoire, MPXJ, Monte Carlo), hors vérifications de publication |
| `docs/maquettes/` | Maquettes de l'interface : `mppcr-redesign.html` (v2, source **verbatim** de la CSS de `base.html`), `maquette_mpp.html` (v1) |
| `docs/reference/` | Documentation tierce sous droits (Deltek DCMA-14) — **local uniquement**, ignoré |
| `.staging-ancien/` | Ancien staging rapatrié de `homelab/` (clones, bundles) — ignoré, entièrement présent dans l'historique git : supprimable |

## Commandes

- Lancer en local : `docker compose up --build` → <http://localhost:5000> (hors conteneur il faut Java + `pip install -r requirements.txt`, et `USAGE_DB_PATH` pour le compteur).
- Production (CT 121, `docker.lan`, `192.168.1.87`, clé `~/.ssh/docker_lxc`) : conteneur **`mpp`**, image **construite sur place** depuis le dépôt git `/mpp` (`docker-compose.yml`, `MAX_UPLOAD_MB=20`) — et non depuis GHCR. Mettre à jour : sauvegarde `cp -a /mpp /mpp.bak-<horodatage>` + `docker tag mpp-mpp mpp-mpp:<ancienne>-bak-<horodatage>`, puis dans `/mpp` `git fetch --tags && git switch --detach vX.Y.Z && docker compose up -d --build`, puis `verify_prod.sh` (copié sur l'hôte avec `test_projet.xml`, `PROJECT=…`). Réglages propres à la machine dans `/mpp/docker-compose.override.yml` (non versionné). `docker-compose.prod.yml` sert à qui déploie l'image publiée.
- Tests : le harnais tourne **dans l'image de production** (seule à embarquer Java + MPXJ) : `tests/lancer_lxc.sh` copie le code du dépôt et le harnais dans le conteneur `mpp` du CT 121, puis exécute la commande — `tests/lancer_lxc.sh "python3 check_mppcr.py"`, `tests/lancer_lxc.sh "python3 alignment_check.py templates mppcr-redesign.html"` (copier la maquette au besoin). `STAGE=<dossier>` pour tester un autre code.

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
- Plannings de test : `test_projet.xml` (référence), `test_liens.xml`, `test_lags.xml` (décalages % et calendaires), `test_dcma_ecarts.xml` (contrôles 8, 10, 11).
- `tests/test_liens.xml` : planning de test des liens SS/FF, de la contrainte SNLT et du CPLI (deux chemins critiques parallèles en retard de 3 j) ; ses valeurs attendues (CPM 22 j, CPLI 0,85, SNLT 1/7) sont vérifiées par `check_mppcr.py`.
- `TRUSTED_PROXIES` (adresse de NPMplus) ferme le contournement du quota par `X-Forwarded-For` en accès direct au port 5000 — à n'activer qu'après avoir vérifié l'adresse source vue par le conteneur, sinon tous les visiteurs partagent un seul quota.
- `.gitignore` exclut `*.mpp` : seul `tests/planning_ci.mpp` (synthétique) est réintégré par une exception.
- État git au 22/10/2026 : `main` = **v1.15.0** (prévision datée, moteur vectorisé, comparaison, rapport PDF, authentification facultative, ergonomie ; voir `CHANGELOG.md`). Production CT 121 : **v1.15.0** depuis le 23/10/2026, `TRUSTED_PROXIES=192.168.1.141` dans `/mpp/docker-compose.override.yml` ; sauvegardes `/mpp.bak-20261023-085204` et image `mpp-mpp:v1.14.0-bak-20261023-085204`.
