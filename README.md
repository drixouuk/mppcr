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

## Interface

Trois écrans, sans JavaScript obligatoire ni ressource externe (aucune police
téléchargée, aucune CDN) :

1. **Analyser un planning** — trois étapes numérotées : le fichier (zone de
   dépôt), le type d'analyse (DCMA-14, Monte Carlo, ou les deux), les options.
   Un panneau latéral rappelle le fichier choisi, l'analyse, les exports et la
   répartition par lot demandés, affiche le quota du jour et porte le bouton de
   lancement.
2. **Résultat** — un en-tête (fichier, date d'état, faits marquants, boutons
   d'export et d'impression), le **score de conformité** avec sa bande et ses
   repères, cinq compteurs de contrôles, un bandeau d'alerte quand un point est
   bloquant, puis les **priorités d'amélioration** (volet dépliable par écart :
   action recommandée et numéros de tâches concernés) et le **détail des
   14 contrôles**. La simulation Monte Carlo suit dans la même page :
   P50 / P80 / P90, axe des percentiles, lecture décisionnelle, indice de
   criticité par tâche et, si elle a été demandée, répartition du risque par lot
   WBS (voir [Répartition par macro-tâche](#répartition-par-macro-tâche-monte-carlo)).
3. **Erreur** — fichier refusé, quota atteint, analyse interrompue ou échec de
   lecture, avec le motif et le retour au formulaire.

Le fil d'étapes de l'en-tête situe en permanence l'écran courant, et la version
déployée y est affichée. La mise en forme suit la maquette `mppcr-redesign.html`
(v2) : sa feuille de style est reprise **verbatim** dans `templates/base.html`,
un second bloc ne portant que les ajouts propres au rendu serveur — un contrôle
automatisé compare les deux fichiers (voir [Contrôles et tests](#contrôles-et-tests)).

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

    # Le formulaire accepte des plannings jusqu'à MAX_UPLOAD_MB (5 Mo par défaut).
    client_max_body_size 25m;

    # Une requête lance au plus deux scripts (diagnostic puis simulation), chacun
    # borné par ANALYSIS_TIMEOUT=300 : 700 s couvre le pire cas légitime.
    proxy_read_timeout 700s;
    proxy_send_timeout 700s;
}
```

`X-Forwarded-For` est nécessaire : l'application lit l'IP réelle du visiteur via
`ProxyFix` (un seul proxy devant l'application, `x_for=1`).

## Variables d'environnement

| Variable | Défaut (compose) | Rôle |
| --- | --- | --- |
| `ANALYSIS_TIMEOUT` | `300` | Délai maximal, en secondes, accordé à chaque script d'analyse avant abandon. |
| `DAILY_ANALYSIS_LIMIT` | `5` | Nombre d'analyses autorisées par adresse IP et par jour calendaire. |
| `MAX_CONCURRENT_ANALYSES` | `2` | Nombre d'analyses lourdes menées de front, tous visiteurs confondus (au moins 1). |
| `MAX_UPLOAD_MB` | `20` | Taille maximale du planning accepté, en Mo (5 Mo par défaut dans l'image, 20 Mo dans le compose de production). Au-delà, refus avec un message explicite. |
| `JAVA_TOOL_OPTIONS` | `-Xmx1536m …` | Mémoire allouée à la JVM d'analyse (voir « Mémoire et plannings volumineux »). |

`USAGE_DB_PATH` existe uniquement pour lancer l'application hors conteneur :
les compteurs sont écrits par défaut dans `/app/data/usage.db`.

## Contrôles DCMA-14

Le périmètre de chaque contrôle suit les définitions du référentiel, qui précisent
la population sur laquelle la métrique est calculée (source :
[DCMA 14-Point Assessment Metric Descriptions, Deltek Acumen](https://api.deltek.com/Product/Acumen/8.3/GA/DCMA%2014%20Point%20Assessment%20Metric%20Descriptions.html)).

**Tâches restantes.** Les contrôles **1 à 10** ne portent que sur les tâches **non
terminées** (`% Complete < 100`) : leurs formules sont toutes de la forme
« … / nombre de tâches non terminées ». Les jalons restent en outre exclus des
contrôles 1, 8 et 10, où les notions de lien aval, de durée ou de ressource n'ont
pas de sens. Les contrôles **11 (Missed tasks)** et **14 (BEI)** portent au
contraire sur les tâches **terminées** — ils n'utilisent pas ce filtre — tout comme
le 13 (CPLI), calculé sur le chemin critique, et le 12 (CPTest), vérification
manuelle.

| # | Contrôle | Population | Formule du référentiel |
| --- | --- | --- | --- |
| 1 | Logic | tâches restantes, hors jalons | tâches sans lien ÷ tâches non terminées |
| 2 | Leads | liens des tâches restantes | liens avec lead ÷ liens |
| 3 | Lags | liens des tâches restantes | liens avec lag ÷ liens |
| 4 | Relations | liens des tâches restantes | liens FS ÷ liens |
| 5 | Contraintes dures | tâches restantes | tâches non terminées avec contrainte dure ÷ tâches non terminées |
| 6 | Marge > 44 j | tâches restantes | idem |
| 7 | Marge négative | tâches restantes | idem |
| 8 | Durée > 44 j | tâches restantes, hors jalons | idem |
| 9 | Dates invalides | tâches restantes | dates réelles futures ou dates prévues passées, sur les tâches non terminées |
| 10 | Sans ressource | tâches restantes, hors jalons | idem |
| 11 | Missed tasks | tâches terminées | terminées en retard ÷ échéances de baseline passées |
| 12 | CPTest | — | vérification manuelle |
| 13 | CPLI | chemin critique | (durée du chemin critique + marge totale) ÷ durée du chemin critique |
| 14 | BEI | tout le planning | terminées ÷ à terminer selon la baseline |

**Tâches d'un autre planning.** Les tâches marquées comme **externes**
(`getExternalTask()`, liens inter-projets) sont **hors périmètre de tous les
contrôles** : leurs liens amont et aval vivent dans un autre fichier, que l'analyse
ne charge pas. Les inclure revenait à leur imputer des défauts de logique
invérifiables ici — sur un planning réel, 16 des 44 signalements du contrôle 1
étaient des tâches d'un autre projet. Elles restent comptées dans le diagnostic
`DIAG|TOTAL` sous `externes=`, pour que l'information ne soit pas perdue.

> Attention à l'accesseur : `getExternalTask()` rend un booléen utilisable
> directement, alors que `getExternalProject()` rend `False` — et non `None` —
> **y compris pour les tâches ordinaires**. Un test `is not None` sur cette valeur
> exclurait toutes les tâches et afficherait `0/0` sur chaque contrôle.

> **Changements de comportement.** *v1.6.0* : le contrôle 1 exclut les jalons et
> les tâches achevées. *v1.7.0* : les contrôles 2 à 10 sont alignés sur le même
> périmètre, comme le prescrit le référentiel. Les pourcentages, les décomptes et
> donc le score de conformité changent sur tout planning comportant de l'historique :
> sur un plan témoin de 13 tâches dont 8 achevées, le dénominateur des contrôles 2 à
> 10 passe de 11-13 à 5-6 tâches, et le contrôle 9 (dates invalides) de 30,77 % à
> 66,67 % — un historique « propre » diluait la mesure. Les contrôles 11 à 14 sont
> inchangés. La référence figée des tests est régénérée à chaque changement de
> périmètre, et le contrôle de non-régression la compare réellement à une exécution.

**Écarts connus** par rapport au texte du référentiel, non corrigés à ce stade car
ils touchent la *définition* des métriques et non leur population : le contrôle 8
utilise la durée planifiée au lieu de la **durée de baseline** et n'applique pas la
condition de « période de planification détaillée ou rolling wave » ; le contrôle 11
compte au dénominateur les tâches ayant une date réelle de fin plutôt que celles
dont l'échéance de baseline est passée ; le contrôle 10 n'applique pas la condition
« durée supérieure à zéro avec charge en heures ou en devise affectée ». À traiter
sur décision explicite, mesure avant/après à l'appui.

## Simulation Monte Carlo

La seconde analyse est une **simulation de risque planning** : pour chaque tâche,
une distribution PERT (optimiste / probable / pessimiste) est construite à partir
de la durée planifiée et des facteurs `--opt` / `--pess` (0,8 et 1,5 par défaut,
0,7 et 1,8 pour un jeu plus prudent — le formulaire propose ces deux jeux),
ou de vraies estimations 3-points fournies dans un CSV (`--estimates`,
`task_id,optimistic,most_likely,pessimistic` en jours). N tirages sont ensuite
enchaînés par un passage avant sur le réseau de dépendances, et le script rend
**P50 / P80 / P90** de la durée, plus un **indice de criticité par tâche** (part
des simulations où la tâche est sur le chemin critique).

**Population simulée**, différente de celle des contrôles DCMA :

| Catégorie | Traitement | Pourquoi |
| --- | --- | --- |
| Tâche récapitulative | exclue | elle doublerait ses tâches de détail |
| Jalon, durée nulle | à 0 dans toutes les branches | pas de risque de durée propre |
| **Tâche achevée** | **durée gelée** (o = m = p) | son avenir est connu : lui attribuer ±20 % / +50 % fabriquait de la dispersion à partir du passé |
| **Tâche externe** | **durée gelée** | le risque d'un autre planning n'est pas le nôtre à modéliser |

Ces deux dernières catégories **restent dans le réseau** — les retirer casserait
les liens qui les traversent et ferait s'effondrer la durée simulée — mais elles
n'ajoutent aucune incertitude. C'est l'inverse du choix fait pour le diagnostic,
où elles sont au contraire **exclues** du périmètre. Le script annonce le nombre
de tâches gelées dans sa sortie.

Les durées sont converties en **jours ouvrés** via le calendrier du projet, comme
dans `dcma14.py` : sans cela, un planning dont les durées sont exprimées en heures
était simulé en heures puis affiché en « jours » (dix tâches de 40 h donnaient
400 j au lieu de 50 j).

Deux garde-fous : sans aucun lien exploitable, la simulation s'arrête avec un
message explicite plutôt que de traiter chaque tâche comme indépendante ; et un
réseau contenant une boucle de dépendances est refusé.

**Coût mesuré.** `run_simulation()` est en Python pur : le temps croît comme
`simulations × tâches`. Mesures sur un plan de 24 000 tâches (20 571 liens) :
100 simulations en 18,6 s, 300 en 43,8 s, soit **0,126 s par simulation** plus
environ 6 s fixes (démarrage JVM, lecture, construction du graphe) — donc
**≈ 636 s pour les 5 000 simulations du formulaire**, au-delà du
`ANALYSIS_TIMEOUT` de 300 s. Le pic mémoire reste modeste (280 Mo) : la limite est
de **temps**, pas de mémoire. Ordres de grandeur : ~500 tâches → 15-20 s ;
~10 000 tâches → ~4,5 min ; au-delà, l'analyse est coupée par le délai maximal.
La vectorisation numpy (traitement par niveaux topologiques) est identifiée comme
la parade si des plannings de cette taille deviennent courants.

**Fiabilité affichée.** Quand le diagnostic DCMA-14 accompagne la simulation dans
la même analyse, l'avertissement de fiabilité reprend la **valeur réelle du
contrôle 1** — « 33,7 % des tâches restantes (26/77) n'ont pas de logique amont ou
aval, au-dessus des 5 % attendus » — et aucun avertissement n'apparaît si ce
contrôle est conforme. Lorsque le Monte Carlo est lancé seul, il ne se prononce pas
sur la logique : il renvoie vers le diagnostic.

C'est une correction de cohérence : la version précédente comparait un « ratio de
liens par tâche » (calculé sur **toutes** les tâches de détail, achevées comprises,
et sur des **relations**) au seuil de 5 % du contrôle 1 (calculé sur les seules
tâches **restantes**, en nombre de tâches). Un planning à 88 % de liens par tâche
peut parfaitement afficher 34 % de tâches restantes sans logique : les deux
mesures sont justes, mais elles ne comparent pas la même chose. Le ratio reste
affiché comme information de forme du réseau, sous l'étiquette « liens par tâche,
tous statuts confondus ».

### Évolutions envisagées (non implémentées)

1. **Reste-à-faire et fin prévisionnelle par lot** : la répartition par lot est
   livrée (voir [Répartition par macro-tâche](#répartition-par-macro-tâche-monte-carlo)),
   mais elle exprime encore des **durées**, pas des dates : ni le reste-à-faire ni
   la fin prévisionnelle ne sont calculés par lot. Les briques existent
   (`ProjectCalendar.getDate()` pour convertir des jours ouvrés en date réelle, en
   sautant week-ends et jours fériés) ; c'est la même question de fond que le point
   suivant.
2. **Bascule en « durée restante / fin prévisionnelle »** : aujourd'hui la
   simulation affiche la durée **totale depuis le jour 0**, passé compris, et
   n'utilise ni la date d'état ni `Task.getRemainingDuration()`. Pour un planning
   en cours, on attend plutôt le **reste-à-faire** et une **date de fin
   prévisionnelle** ancrée sur la date d'état. C'est un changement de
   signification, pas un correctif, et il repose sur un CPM du travail restant
   **simplifié** : toute tâche simulée est supposée pouvoir démarrer à la date
   d'état (les dates réelles déjà passées ne sont pas rejouées, et il n'y a pas de
   nivellement des ressources).

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

## Tâches concernées

Chaque **priorité d'amélioration** de la page de résultat liste les numéros de
tâche (colonne N° de MS Project) en écart pour le contrôle concerné, sous forme
de pastilles : jusqu'à 60 numéros, puis « +N autres ». Le tableau « Détail des
contrôles » ne porte plus cette information (la maquette v2 l'a déplacée dans
les priorités, là où l'action est décrite) ; les exports CSV et Excel conservent
la **liste complète**, sans troncature.

Ces numéros s'appuient sur l'option `--details` de `dcma14.py`, ajoutée pour
l'occasion : elle n'ajoute qu'un bloc technique à la fin de la sortie, après la
ligne « Resume ». **Sans cette option, la sortie du script est strictement
identique** à celle des versions précédentes — une référence figée est comparée
réellement à chaque exécution des tests (elle n'est régénérée que lorsqu'un
contrôle change de comportement de façon délibérée, comme le contrôle 1 en
v1.6.0). Les contrôles sans écart affichent un tiret, et le contrôle 12
(vérification manuelle) n'a pas de liste.

Le même bloc porte, depuis la v1.8.0, des **compteurs de diagnostic** pour le
contrôle 1 : une ligne par tâche signalée, sans aucun nom de tâche —
`DIAG|1|<n°>|preds=<n>|succs=<n>|pct=<n>|jalon=<0/1>|externe=<0/1>|active=<0/1>|sous_projet=<0/1>`
— et une ligne de synthèse `DIAG|TOTAL|taches=…|restantes=…|liens=…|externes=…`.
L'interface web ne lit que les lignes `N|…` et ignore ce bloc, mais elle le
**journalise**. Il distingue un planning où les liens manquent réellement d'un
planning dont la **lecture** des liens échoue (tâches externes, inactives ou
sous-projets) — sans avoir à transmettre le planning : ce sont les compteurs, vus
par la bibliothèque de lecture, qui parlent.

## Exports

Le résultat est **toujours affiché dans le navigateur**. Les exports sont
*facultatifs et cumulables* : ils se demandent au dépôt du formulaire, dans le
volet « Export des résultats (facultatif) », et apparaissent ensuite en boutons
dans l'en-tête de la page de résultat. Un export non demandé y reste visible
(avec l'indication du volet à cocher) mais inerte, à côté du bouton
**Imprimer / PDF**.

| Export | Contenu |
| --- | --- |
| **CSV** | séparateur `;`, BOM UTF-8 (Excel FR) : score, synthèse, contrôles, priorités, Monte Carlo, criticité, répartition par lot si demandée, sorties brutes |
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

## Mémoire et plannings volumineux

La lecture d'un `.mpp` se fait dans une JVM embarquée (MPXJ + POI) : c'est de loin
le poste le plus gourmand, et il croît vite avec le nombre de tâches.

**Le piège corrigé en v1.4.** Dans un conteneur LXC, `/proc/meminfo` peut afficher
la mémoire de l'**hôte** (32 Go dans notre cas) au lieu de celle allouée au
conteneur (8 Go, partagés avec d'autres services). La JVM se dimensionnait donc
sur 32 Go et se donnait **7,8 Go de tas** : un planning volumineux pouvait épuiser
la mémoire de la machine et faire tuer des processus voisins par le noyau. Trois
garde-fous ont été ajoutés :

| Garde-fou | Effet |
| --- | --- |
| `JAVA_TOOL_OPTIONS=-Xmx1536m …` (image) | borne le tas de chaque analyse : de 7,8 Go à 1,5 Go |
| `MAX_UPLOAD_MB` (20 en production, 5 par défaut) | refuse les fichiers trop gros par un message explicite, avant lecture |
| `mem_limit: 5g` + `oom_score_adj: 500` (compose) | le conteneur ne peut pas affamer ses voisins, et c'est lui que le noyau tue en premier |
| `ANALYSIS_TIMEOUT=300` | un fichier pathologique est coupé au bout de 5 minutes, jamais la journée |

Quand la mémoire allouée ne suffit pas, l'analyse s'arrête proprement et le
visiteur reçoit un message dédié — « Le planning est trop volumineux pour la
mémoire disponible (1536 Mo alloués à l'analyse). Réduisez le périmètre analysé… Le
fichier peut aussi être corrompu : dans ce cas, ouvrez-le dans MS Project et
recopiez son contenu dans un nouveau fichier .mpp. » — sans consommer d'analyse de
son quota. Le **pic de mémoire** de chaque analyse est écrit dans les journaux, ce
qui permet de calibrer les limites sur des fichiers réels plutôt qu'à l'aveugle.

Cette dernière phrase n'est pas théorique : un planning corrompu **s'ouvre
normalement dans MS Project** tout en étant illisible pour la bibliothèque de
lecture, qui épuise alors n'importe quelle mémoire disponible. Le seul remède
connu est de recopier le contenu du planning dans un fichier neuf.

**Régressions de la bibliothèque de lecture.** MPXJ 16.7.0 introduit une
allocation non bornée dans `TimephasedDataFactory` sur certains `.mpp` : le
diagnostic échoue en `OutOfMemoryError` **quelle que soit la mémoire allouée**,
y compris 6 Go, sur un fichier de moins de 2 Mo — et le vidage de tas ne montre
alors que des objets fabriqués en boucle, pas les données du planning. La version
16.6.0 lit ces mêmes fichiers en 2 s et 117 Mo. Le pin est donc volontairement
maintenu à **16.6.0** (voir la note dans `requirements.txt` et `joniles/mpxj#932`,
fermée sans correctif faute de fichier de reproduction) : ne remonter la
dépendance qu'après avoir vérifié la version suivante sur un fichier réel.

**Pour accepter de plus gros plannings**, si la machine en a les moyens :

```bash
docker run -d -p 5000:5000 \
  -e MAX_UPLOAD_MB=15 \
  -e MAX_CONCURRENT_ANALYSES=1 \
  -e JAVA_TOOL_OPTIONS="-Xmx3g -XX:MaxMetaspaceSize=256m -XX:+UseSerialGC -XX:+ExitOnOutOfMemoryError" \
  --memory 6g ghcr.io/drixouuk/mppcr:latest
```

Deux règles : le tas ne doit **jamais dépasser la moitié du plafond du conteneur**
(sinon c'est le noyau qui tue la JVM, et le visiteur reçoit une erreur technique au
lieu d'un diagnostic), et chaque analyse simultanée consomme sa propre JVM — garder
`MAX_CONCURRENT_ANALYSES` à 1 si la machine n'a pas la mémoire pour deux.

### Ce qui pèse réellement (mesures du 21/09)

Le nombre de tâches et la taille du fichier sont de mauvais indicateurs. Mesures
faites avec la JVM de l'image, pic de mémoire résident du sous-processus :

| Modèle analysé | Fichier | Pic mémoire | Durée |
| --- | --- | --- | --- |
| 19 tâches (`.mpp` binaire réel) | 393 Ko | 111 Mo | 1,0 s |
| 1 tâche, calendriers très détaillés (`.mpp` binaire réel) | 11,8 Mo | 118 Mo | 1,4 s |
| 100 tâches, 900 liens, 10¹⁰ chemins possibles (MSPDI) | 111 Ko | 129 Mo | 1,4 s |
| 100 tâches avec cycle, auto-dépendance ou tâches externes (MSPDI) | 31-35 Ko | 118-123 Mo | 1,4-2,0 s |
| 24 000 tâches + liens + baselines (MSPDI) | 11,8 Mo | 225 Mo | 5,0 s |
| 100 000 entrées d'avancement daté (MSPDI) | 16,4 Mo | 201 Mo | 4,0 s |
| **1 000 000 d'entrées d'avancement daté** (MSPDI) | 160 Mo | **792 Mo** | **16,0 s** |

Résultat contre-intuitif : ce n'est ni la taille du fichier, ni le nombre de tâches,
ni la densité des liens qui pèsent, mais le **volume de données datées**
(`TimephasedData`, l'avancement saisi période par période). Un plan de 100 tâches
suivi au jour le jour pendant des années dépasse 1 Go là où un plan de 24 000 tâches
non suivi se contente de 225 Mo : c'est ce profil qui a motivé le passage de 768 Mo
à 2 Go de tas.

Repères pour dimensionner : ~110 Mo incompressibles (JVM + POI + Python), puis
environ 5 Ko par tâche et 0,7 Ko par entrée datée. Les durées restent modestes
(quelques secondes) jusqu'à plusieurs centaines de milliers d'entrées ; au-delà,
compter une quinzaine de secondes. À très fort volume, ce n'est plus la mémoire mais
le **temps de calcul** qui devient le facteur limitant — d'où `ANALYSIS_TIMEOUT`.

**Côté hôte**, la correction la plus durable est d'installer `lxcfs` sur l'hôte
Proxmox et de l'activer pour le conteneur, afin que `/proc/meminfo` reflète la
mémoire réellement allouée : toute JVM (et tout outil qui se dimensionne sur
`/proc`) cesse alors de surestimer la machine.

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
dcma14.py         Contrôle qualité DCMA-14 (appelé en subprocess, option --details
                  pour la liste des N° de tâches en écart).
montecarlo.py     Simulation Monte Carlo (script CLI d'origine, appelé en subprocess).
templates/        Interface web server-rendue (Jinja2), CSS sans framework, sans JS requis.
Dockerfile        python:3.11-slim + JRE headless (MPXJ/jpype1) + Gunicorn (2 workers gthread × 4 threads).
```

`dcma14.py` et `montecarlo.py` ne sont jamais modifiés : ils sont exécutés en
subprocess par `app.py`, qui met en forme leurs sorties. Ils utilisent `mpxj` et
`jpype1` pour lire les `.mpp` via une JVM embarquée.

### Répartition par macro-tâche (Monte Carlo)

`montecarlo.py` simule toujours au niveau **détail**, là où vit la vraie structure
de dépendances, mais il peut restituer le risque **par lot WBS**.

**Dans l'interface** : volet « Options avancées Monte Carlo » du formulaire, champ
**« Répartition du risque par lot (facultatif) »** — la liste fixe `Aucune`,
`Niveau 1`, `Niveau 2`, `Niveau 3`. Le résultat s'ajoute sous l'indice de
criticité, sous forme d'un tableau **lot / P50 / P80 / P90 / écart P50 vs planifié
/ criticité**, et rejoint les exports CSV et Excel.

**En ligne de commande** :

```bash
# Répartition par programme (tâches récapitulatives de niveau 1)
python3 montecarlo.py planning.mpp --sims 5000 --macro-level 1

# Par sous-lot (niveau 2)
python3 montecarlo.py planning.mpp --sims 5000 --macro-level 2
```

`N` est le niveau de plan (`getOutlineLevel()`) des récapitulatives à traiter comme
macro-tâches : 1 correspond aux lots juste sous la tâche de projet. Sortie ajoutée
après le bloc global, triée par P90 décroissant :

```
Repartition par macro-tache (niveau 1) :
  Programme D — Mise en service  P50: 330.9 j (+13.9 j vs planifie)   P80: 341.1 j   P90: 346.8 j   Criticite: 100.0%
  Programme C — Formation        P50: 234.5 j (+9.5 j vs planifie)    P80: 243.7 j   P90: 248.2 j   Criticite: 100.0%
```

- **Sans l'option, la sortie est strictement identique** à celle des versions
  précédentes : l'option est opt-in, comme `--details` dans `dcma14.py`, et la
  référence figée des tests reste valable.
- **Le moteur de simulation ne change pas** : on conserve simplement, à chaque
  itération, la date de fin de chaque sous-arbre au lieu de ne garder que la fin
  globale. La corrélation entre lots vient de ce qu'ils partagent les mêmes
  tirages, sans hypothèse statistique supplémentaire.
- La **criticité** d'un lot est la part des simulations où *au moins une* de ses
  tâches est sur le chemin critique : une itération compte **une fois par lot**,
  pas une fois par tâche — sinon un lot de 200 tâches paraîtrait cent fois plus
  critique qu'un lot de 2 tâches à structure identique.
- Les tâches de détail qui ne dépendent d'aucune récapitulative du niveau demandé
  sont regroupées sous **« Tâches hors lot »**, pour que la vue reste complète.
- Si le niveau demandé n'existe pas, le script le dit et liste les niveaux
  disponibles (`Aucune tache recapitulative au niveau 9 -- niveaux disponibles : [1]`),
  et la page l'explique au lieu d'afficher un tableau vide.
- **Limite connue** : les liens portés par une tâche **récapitulative** ne sont
  pas propagés à ses tâches de détail, puisque le graphe ne contient que les
  détails — détail ci-dessous.

#### Liens portés par une tâche récapitulative

Un planning **ne doit pas** en contenir : les liens relient des tâches de détail.
Comme la simulation ne retient que ces dernières, un tel lien est **ignoré** — un
lot peut alors finir avant son prédécesseur. MPPCR ne corrige pas le réseau : il
**signale** le cas, avec le nombre de liens et des exemples
(`Phase 1 — Conception -> Phase 2 — Réalisation`), dans un bandeau d'avertissement
au-dessus du tableau par lot.

L'avertissement n'est produit **qu'avec la répartition par lot** (c'est la vue où
le phénomène se voit) : sans l'option, la sortie du script est inchangée, au
caractère près, et la référence figée des tests reste valable.

## Contrôles et tests

Trois vérifications, à lancer avant toute publication :

| Vérification | Portée | Volume |
| --- | --- | --- |
| `check_mppcr.py` | parsing des sorties, rendu des écrans, quota et concurrence, exports, mémoire, répartition par lot, non-régression des sorties de référence | 320 |
| `alignment_check.py` | feuille de style de la maquette reprise **verbatim**, jetons, classes, structure des écrans | 190 |
| `verify_prod.sh` | production réelle : conteneur, HTTP, répartition par lot, exports, refus, charge, absence de résidu | 62 |

Les deux premières tournent **dans l'image de production** (seul environnement qui
embarque Flask, JPype, MPXJ et Java) : `lancer_lxc.sh` y copie le code de staging
et le harnais, puis exécute la commande demandée. `verify_prod.sh` interroge le
conteneur en fonctionnement sur `http://127.0.0.1:5000`.

Les sorties de `dcma14.py` et `montecarlo.py` sont comparées **pour de vrai** aux
références figées (`ref_dcma.txt`, `ref_mc.txt`) : elles ne sont régénérées que
lorsqu'un contrôle change de comportement de façon délibérée.

## Journal des versions

Les correctifs et les changements de comportement sont consignés dans
[`CHANGELOG.md`](CHANGELOG.md), avec les effets mesurés à l'appui. Le fichier
`VERSION` — voir « Quelle version tourne ? » — indique la version effectivement
déployée.

## Publication de l'image

Le workflow se déclenche sur push vers `main` et sur les tags `v*` :

1. `test` — build natif sur `linux/amd64` (`ubuntu-24.04`) et `linux/arm64`
   (`ubuntu-24.04-arm`), puis smoke test : `/healthz`, page d'accueil, présence
   de `/app/data/usage.db`, version exposée par l'image et affichée sur la page,
   statut `healthy` du `HEALTHCHECK`. Sur un tag, le job vérifie d'abord que le
   fichier **`VERSION`** correspond bien à l'étiquette poussée : une version
   oubliée fait échouer la publication au lieu de produire une image menteuse.
2. `publish` — build et push par architecture (par digest), avec cache GitHub
   Actions.
3. `merge` — assemblage du manifeste multi-arch et application des tags :
   `latest` (push sur `main`), `vX.Y.Z`, `vX.Y`, `vX` (tags semver),
   `sha-xxxxxxx` (systématique).
4. `release` — création automatique d'une GitHub Release sur tag `v*`.

### Quelle version tourne ?

Le fichier **`VERSION`** (racine du dépôt) est embarqué dans l'image et fait foi.
La version est :

- **affichée dans l'en-tête** de l'application, sur toutes les pages, ainsi
  qu'en pied de page ;
- écrite dans les **exports CSV**, à côté de la date ;
- **journalisée au démarrage** (`MPPCR vX.Y.Z — démarrage`), donc lisible par
  `docker logs` ;
- lisible dans l'image : `docker exec mpp cat /app/VERSION`.

Pour publier une version : mettre `VERSION` à jour dans le commit, puis pousser
l'étiquette correspondante — un décalage entre les deux est refusé par la CI.

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

**GNU General Public License, version 3 (GPL-3.0)** — voir le fichier
[`LICENSE`](LICENSE).

Copyright (C) 2026 l'auteur du dépôt MPPCR.

Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
modifier selon les termes de la licence GPL-3.0 publiée par la Free Software
Foundation, dans sa version 3 ou, à votre choix, toute version ultérieure. Il est
distribué dans l'espoir qu'il sera utile, mais **sans aucune garantie** ; voir la
licence pour les détails. Toute redistribution d'une version modifiée doit
conserver la même licence et rendre le code source disponible.
