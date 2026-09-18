# MPPCR — MS Project Check & Risk

Analyse qualité et analyse de risque de plannings MS Project (`.mpp`), exposées
dans une interface web simple.

Deux outils :

- **Diagnostic DCMA-14** — 14 contrôles qualité de planning (logique, leads,
  lags, contraintes dures, marges, dates invalides, CPLI, BEI…), chacun avec son
  seuil, sa cible et une action corrective recommandée. Un verdict par contrôle
  et une synthèse globale.
- **Simulation Monte Carlo** — distribution PERT sur les durées des tâches,
  propagation par le graphe de dépendances, restitution de P50 / P80 / P90, de
  la contingence à prévoir par rapport au chemin critique déterministe (CPM) et
  d'un indice de criticité par tâche (part des simulations où la tâche se
  trouve sur le chemin critique).
- **Score de conformité** — une note sur 100 (barème MPPCR versionné) qui
  synthétise les contrôles en tenant compte des données manquantes, plutôt qu'un
  décompte brut de verdicts.
- **Exports** — résultat CSV ou Excel demandés en plus de l'affichage, ou PDF
  via l'impression du navigateur.

Le projet est défini comme son propre PID (pas de portefeuille multi-projets),
en français, sans authentification (voir [Sécurité](#sécurité--avertissement)).

## Démarrage rapide

```bash
docker run -d -p 5000:5000 -e DAILY_ANALYSIS_LIMIT=5 ghcr.io/drixouuk/mppcr:latest
```

L'application est alors disponible sur <http://localhost:5000>.

L'image est publiée en multi-architecture (`linux/amd64` et `linux/arm64`) sur
GitHub Container Registry par le workflow
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml).

## Utilisation avec docker-compose

`docker-compose.prod.yml` pinne une version explicite plutôt que `:latest` :

```bash
docker compose -f docker-compose.prod.yml up -d
```

Le fichier est volontairement sans volume : les plannings analysés vivent dans
un répertoire temporaire, et le compteur d'usage est interne au conteneur
(réinitialisé à chaque recréation de l'image — comportement voulu).

## Configuration du reverse proxy

L'application se place derrière un reverse proxy qui gère TLS. Exemple Nginx
(Nginx Proxy Manager) :

```nginx
location / {
    proxy_pass http://192.168.1.87:5000;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Le formulaire accepte des plannings jusqu'à 20 Mo.
    client_max_body_size 25m;

    # Cohérent avec ANALYSIS_TIMEOUT=900 : Monte Carlo peut être long.
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
}
```

`X-Forwarded-For` est nécessaire : l'application lit l'IP réelle du visiteur via
`ProxyFix` (un seul proxy devant l'application, `x_for=1`).

## Variables d'environnement

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ANALYSIS_TIMEOUT` | `900` | Délai maximal, en secondes, accordé à chaque script d'analyse avant abandon. |
| `DAILY_ANALYSIS_LIMIT` | `5` | Nombre d'analyses autorisées par adresse IP et par jour calendaire. |
| `MAX_CONCURRENT_ANALYSES` | `2` | Nombre d'analyses lourdes menées de front, tous visiteurs confondus (au moins 1). |

`USAGE_DB_PATH` existe uniquement pour lancer l'application hors conteneur :
les compteurs sont écrits par défaut dans `/app/data/usage.db`.

## Score de conformité

La page de résultat et les exports affichent un **score sur 100** (`barème v1`) :

- seuls les **contrôles évaluables** sont notés. Le contrôle 12 (CPTest) est une
  vérification manuelle jamais mesurable ici, le 10 (ressources) un indicateur
  sans seuil : les deux sont **hors barème**. Un contrôle N/A faute de données ne
  compte ni comme conforme ni comme faute ;
- **tous les contrôles notés pèsent le même poids** :
  `score = 100 × conformes ÷ évalués` ;
- le score est **plafonné par la complétude** : 75 si la date d'état du projet ou
  la baseline manque, 60 si les deux manquent. Sans ce plafond, un planning vide
  obtiendrait 100/100 faute de contrôle mesurable ;
- bandes : 85 et plus *conforme* · 70 à 84 *acceptable* · 55 à 69 *fragile* ·
  moins de 55 *insuffisant*.

La méthode DCMA-14 ne définit aucun score composite : celui-ci est une convention
MPPCR, **versionnée** (affichée sur la page) pour rester comparable d'une analyse
à l'autre. Le barème est détaillé dans un bloc repliable sur la page de résultat.

## Exports

Le résultat est **toujours affiché dans le navigateur**. Les exports sont
*facultatifs et cumulables* : ils se demandent au dépôt du formulaire, dans le
volet « Export des résultats (facultatif) », et apparaissent ensuite en boutons
de téléchargement sur la page de résultat.

| Export | Contenu |
| --- | --- |
| **CSV** | séparateur `;`, BOM UTF-8 (Excel FR) : score, synthèse, contrôles, priorités, Monte Carlo, criticité, sorties brutes |
| **Excel** | une feuille par analyse lancée (« Contrôles DCMA-14 », « Monte Carlo »), verdicts colorés, score en tête |

Les fichiers sont produits dans la même requête que l'affichage et **encodés dans
la page elle-même** (`data:`) : aucune écriture côté serveur, aucune requête
supplémentaire, aucun JavaScript, et un simple clic suffit à télécharger. Un même
lancement peut demander les deux exports : il ne décompte qu'**une** analyse du
quota.

Pour un PDF, utiliser le style d'impression déjà embarqué : **Imprimer →
Enregistrer au format PDF** depuis la page de résultat.

## Attente et arrêt d'une analyse

Le calcul dure de quelques secondes à plusieurs minutes (démarrage de la JVM,
puis simulation). Deux aides, en amélioration progressive : sans JavaScript, la
soumission du formulaire se comporte exactement comme avant.

- **Attente** : à l'envoi du formulaire, une page d'attente affiche le temps
  écoulé, les étapes prévues (diagnostic DCMA-14, simulation Monte Carlo) et un
  rappel si l'analyse dépasse la minute. Elle disparaît d'elle-même dès que le
  résultat arrive.
- **Arrêt** : le bouton « Arrêter l'analyse » transmet une demande d'annulation
  (jeton aléatoire de 32 caractères, lié à l'adresse IP qui a lancé l'analyse).
  Le script en cours est interrompu, JVM comprise, et **aucune analyse n'est
  décomptée** du quota.

## Limitation d'usage et concurrence

Sans système de comptes, chaque adresse IP dispose de `DAILY_ANALYSIS_LIMIT`
analyses par jour calendaire. Le compteur est stocké dans un fichier SQLite
(`/app/data/usage.db`) partagé par les workers Gunicorn — un compteur en mémoire
serait incohérent entre les workers. Au-delà du quota, le formulaire refuse la
requête avec une page explicite, sans lancer d'analyse. Le formulaire affiche le
nombre d'analyses restantes pour l'IP courante.

Le quota n'est décompté **qu'après une analyse réussie** : un fichier illisible,
un dépassement de délai, une attente sur le sémaphore ou une analyse arrêtée par
le visiteur ne consomment rien. Le contrôle effectué à l'entrée de la route reste une barrière
simple : une rafale de requêtes simultanées d'une même IP peut dépasser le quota
d'au plus `MAX_CONCURRENT_ANALYSES` analyses.

Les analyses lourdes sont bornées par `MAX_CONCURRENT_ANALYSES`, compteur partagé
via SQLite entre tous les workers et threads. Au-delà, la requête reçoit une page
« Analyse en cours » (HTTP 503 + `Retry-After`) et **ne consomme pas de quota**.
Gunicorn tourne en `gthread` (2 workers × 4 threads) : une analyse occupe un
thread, mais `subprocess.run()` relâche le GIL, si bien que le formulaire et
`/healthz` restent servis par les autres threads du même worker pendant le calcul
du sous-processus JVM.

## Confidentialité

Les fichiers `.mpp` et les CSV d'estimations sont traités dans un répertoire
temporaire, supprimé automatiquement à la fin de chaque requête. Aucun planning
n'est conservé après l'analyse. Le seul élément persistant est le compteur
d'usage par IP (nombre d'analyses par jour), sans lien avec le contenu analysé.

## Sécurité — avertissement

> **L'application n'a aucune authentification intégrée.**
>
> Toute personne pouvant joindre l'application peut soumettre un planning et
> consommer du CPU. **Ne jamais l'exposer directement sur Internet.** En amont,
> mettre en place au minimum une authentification basique au niveau du reverse
> proxy, un accès VPN, ou une restriction réseau (liste d'IP autorisées) —
> WebAuthn, NPMplus Access List ou équivalent.
>
> La limitation d'usage (`DAILY_ANALYSIS_LIMIT`) est un garde-fou anti-abus
> simple, pas une protection : elle est contournable par changement d'adresse IP
> et n'authentifie personne.
>
> Le contrôle d'accès par comptes utilisateurs est volontairement hors périmètre
> de cette version.

Le endpoint `/healthz` reste public, comme attendu par un reverse proxy ou un
orchestrateur.

## Architecture

```
app.py            Interface Flask : parsing des sorties des scripts, cibles DCMA,
                  commentaires, KPI Monte Carlo, priorités d'action, quota d'usage.
dcma14.py         Contrôle qualité DCMA-14 (script CLI d'origine, appelé en subprocess).
montecarlo.py     Simulation Monte Carlo (script CLI d'origine, appelé en subprocess).
templates/        Interface web server-rendue (Jinja2), CSS sans framework, sans JS requis.
Dockerfile        python:3.11-slim + JRE headless (MPXJ/jpype1) + Gunicorn (2 workers gthread × 4 threads).
```

`dcma14.py` et `montecarlo.py` ne sont jamais modifiés : ils sont exécutés en
subprocess par `app.py`, qui met en forme leurs sorties. Ils utilisent `mpxj` et
`jpype1` pour lire les `.mpp` via une JVM embarquée.

## Publication de l'image

Le workflow se déclenche sur push vers `main` et sur les tags `v*` :

1. `test` — build natif sur `linux/amd64` (`ubuntu-24.04`) et `linux/arm64`
   (`ubuntu-24.04-arm`), puis smoke test : `/healthz`, page d'accueil, présence
   de `/app/data/usage.db`, statut `healthy` du `HEALTHCHECK`.
2. `publish` — build et push par architecture (par digest), avec cache GitHub
   Actions.
3. `merge` — assemblage du manifeste multi-arch et application des tags :
   `latest` (push sur `main`), `vX.Y.Z`, `vX.Y`, `vX` (tags semver),
   `sha-xxxxxxx` (systématique).
4. `release` — création automatique d'une GitHub Release sur tag `v*`.

Les runners ARM64 natifs sont gratuits pour les dépôts publics. Si le dépôt
devient privé sans runners ARM, remplacer `ubuntu-24.04-arm` par `ubuntu-24.04`
et ajouter l'étape d'émulation :

```yaml
      - name: Set up QEMU
        uses: docker/setup-qemu-action@v4
```

Le premier push crée le paquet GHCR : vérifier sa visibilité (Package Settings →
Change visibility) — un paquet privé empêche le `docker run` sans authentification.

## Licence

À définir par l'auteur du dépôt.
