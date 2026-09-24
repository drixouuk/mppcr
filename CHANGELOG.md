# Journal des versions

Les versions antérieures à la v1.6.0 sont documentées dans les
[releases GitHub](https://github.com/drixouuk/mppcr/releases).

## v1.14.0 — branche `dev/montecarlo-macro` (non publiée)

### Ajouté
- **Répartition du risque par lot dans l'interface** (l'option
  `montecarlo.py --macro-level N` existait depuis la v1.12.0 mais n'était
  accessible qu'en ligne de commande) :
  - formulaire : champ « Répartition du risque par lot (facultatif) » dans les
    options Monte Carlo, avec la liste fixe **niveau 1, 2 ou 3** (aucune saisie
    libre) ;
  - page de résultat : tableau **lot / P50 / P80 / P90 / écart vs planifié /
    criticité**, trié par P90 décroissant, plus le seau « Tâches hors lot » ;
  - exports CSV (section « RÉPARTITION DU RISQUE PAR LOT ») et Excel (bloc dans
    la feuille « Monte Carlo »).
- **Avertissement sur les liens portés par une tâche récapitulative** : un
  planning ne devrait pas en contenir (les liens relient des tâches de détail).
  Comme la simulation ne retient que les détails, ces liens sont ignorés ; le
  script les **compte et les cite** (`Liens sur taches recapitulatives : N -- ex. :
  …`) et la page affiche un bandeau d'avertissement, sans aucun correctif
  silencieux du réseau. **L'avertissement n'apparaît qu'avec la répartition par
  lot** : la sortie par défaut du script reste strictement identique, la
  référence figée des tests reste donc valable.
- Quand le niveau demandé n'existe pas dans le planning, la page explique
  pourquoi (niveaux présents) au lieu d'afficher un tableau vide.

### Mesuré
- Sur le plan témoin à 4 programmes chaînés : 5 groupes (4 programmes + hors
  lot), tri par P90 décroissant, aucun lien de récapitulative signalé.
- Sur le témoin à liens de récapitulative : 1 lien détecté et cité
  (`Phase 1 — Conception -> Phase 2 — Réalisation`), et la répartition montre
  bien les deux phases en parallèle — la cause est désormais écrite à l'écran.

## v1.13.0

### Modifié
- **Interface reprise intégralement d'après la maquette v2**
  (`mppcr-redesign.html`) : sa feuille de style est reprise **verbatim** dans
  `templates/base.html`, complétée par un second bloc qui ne contient que les
  ajouts imposés par le rendu serveur (affichage de la vue unique de la page,
  bandes du score, page d'attente, styles d'impression).
  - en-tête : marque, version de l'application, fil d'étapes
    « 1 Nouvelle analyse › 2 Diagnostic DCMA-14 › 3 Monte Carlo » ;
  - formulaire : trois étapes numérotées (fichier, analyse, options), zone de
    dépôt, panneau d'appel à l'action collant avec récapitulatif vivant et quota
    du jour ;
  - résultat : héros de score (bande, barre, repères 55/70/85, formule), cinq
    cartes KPI, bandeaux d'alerte, sous-navigation DCMA / Monte Carlo, priorités
    en volets dépliables (action + numéros de tâches en pastilles), tableau des
    contrôles à cinq colonnes, cartes P50/P80/P90, axe des percentiles,
    indice de criticité par tâche.
- Le tableau « Détail des contrôles » **perd la colonne « Tâches concernées »** :
  la maquette v2 place ces numéros dans les priorités correspondantes, sous
  forme de pastilles. Les exports CSV et Excel conservent la colonne complète,
  sans troncature.
- Options Monte Carlo : le nombre de simulations et les facteurs PERT passent en
  **listes déroulantes** (1 000 / 5 000 / 10 000 et « 0,8 / 1,5 » / « 0,7 / 1,8 »).
  Le script continue de recevoir `--opt` / `--pess`, et le serveur accepte
  toujours les champs `opt` et `pess` séparés pour les appels directs.
- Date d'état affichée au format français (`05/10/2026 08:00`) au lieu de la
  valeur ISO du fichier, et écarts Monte Carlo (lecture décisionnelle,
  incertitude P50 → P90) écrits avec la virgule décimale — y compris dans les
  exports CSV et Excel.
- Le volet « Comment ce score est calculé » nomme les contrôles hors barème au
  pluriel (10 et 12), au lieu d'une phrase au singulier.

### Ajouté
- `LICENSE` : **GPL-3.0** (déposée sur GitHub, synchronisée dans le dépôt) et
  section « Licence » du README.

### Vérifié
- Harnais fonctionnel : **293 vérifications** (dont l'exécution réelle des deux
  scripts). Contrôles d'alignement sur la maquette v2 : **188 vérifications**,
  dont la comparaison caractère par caractère de la feuille de style
  (21 833 octets identiques) et l'absence de tout contenu de démonstration.
- Effet des options du formulaire, mesuré sur le plan témoin (7 tâches,
  5 000 simulations) :
  - facteurs 0,8 / 1,5 → P50 **48,6 j**, P90 52,8 j ;
  - facteurs 0,7 / 1,8 → P50 **49,8 j**, P90 56,2 j ;
  - estimation 3 points explicite du lot Développement (1 j au lieu de 20 j) →
    déterministe 47,0 j → **28,0 j**, P50 28,8 j.

## v1.12.0

### Ajouté
- `montecarlo.py` : option **`--macro-level N`** — répartition du risque par lot
  WBS, `N` étant le niveau de plan (`getOutlineLevel()`) des tâches récapitulatives
  à traiter comme macro-tâches. Le moteur de simulation est **inchangé** (on simule
  toujours au niveau détail) : on conserve simplement, à chaque itération, la date
  de fin de chaque sous-arbre, d'où une corrélation entre lots qui ne demande
  aucune hypothèse statistique supplémentaire. Sortie triée par P90 décroissant,
  avec l'écart du P50 par rapport à la durée planifiée du lot et une criticité
  comptée **une fois par lot et par itération** (et non par tâche).
- Les tâches de détail hors de toute récapitulative du niveau demandé sont
  regroupées sous « Tâches hors lot » ; un niveau inexistant est signalé avec la
  liste des niveaux disponibles.
- README : section « Répartition par macro-tâche » (architecture), avec exemple
  d'usage et limites.

### Mesuré
- Sur un plan témoin à 4 programmes chaînés (29 tâches) : A 62,6 j → B 211,4 j →
  C 234,5 j → D **330,9 j**, contre une fin globale de **330,9 j** — le dernier lot
  porte bien la fin de projet, chaque lot finit après son prédécesseur.
- Surcoût de l'option **non mesurable** : 17 s avec et sans, sur 3 000 tâches ×
  1 000 simulations.
- Sans l'option, la sortie reste **strictement identique** (référence figée
  comparée à chaque exécution des tests).

### Constat (défaut préexistant, non corrigé)
- Les liens portés par une tâche **récapitulative** ne sont pas propagés à ses
  tâches de détail : le graphe ne contient que les détails. Un planning qui enchaîne
  ses lots par des liens de niveau récapitulatif est donc simulé avec des lots
  démarrant trop tôt — la vue par macro-tâche le rend immédiatement visible (un lot
  peut finir avant son prédécesseur). À traiter sur décision, en déplaçant les liens
  sur les tâches de détail ou en propageant les liens de récapitulatif dans
  `build_graph()`.

## v1.11.1

### Modifié
- `dcma14.py` : les seuils du contrôle 13 (CPLI, attendu entre 0,95 et 1,44) et du
  contrôle 14 (BEI, attendu au-dessus de 0,95) rejoignent `THRESHOLDS` avec les
  huit autres seuils, au lieu d'être écrits en clair dans `run_diagnostic()`.
  **Aucun changement de comportement** : les valeurs sont identiques, la sortie de
  référence des tests reste valable.
- `dcma14.py` : le périmètre du contrôle 4 est explicité dans sa docstring. La
  boucle porte sur `is_remaining(t)` et non sur la liste brute des tâches : les
  liens des tâches récapitulatives, des jalons, des tâches achevées et des tâches
  externes ne comptent pas. Ce n'est **pas un oubli** mais la règle du référentiel,
  appliquée depuis la v1.7.0 (même périmètre que les contrôles 2 et 3).

### Mesuré
- Monte Carlo sur un plan de 24 000 tâches (9,3 Mo, 20 571 liens), 5 000 simulations
  — le défaut du formulaire : **≈ 636 s**, pour un pic mémoire de **280 Mo**. Le coût
  est linéaire : 100 simulations en 18,6 s, 300 en 43,8 s, soit **0,126 s par
  simulation** plus ~6 s fixes (JVM, lecture, construction du graphe). C'est donc
  **au-delà du `ANALYSIS_TIMEOUT` de 300 s** : l'application coupe l'analyse sur un
  plan de cette taille, proprement, mais elle la coupe.
  Le coût mémoire, lui, est modeste : ce n'est pas là qu'est la limite.
  `run_simulation()` est **laissé en pur Python** : la vectorisation numpy est
  possible (traiter les tâches par niveaux topologiques) mais reste à décider, le
  cas ne se présentant pas sur les plannings courants (~500 tâches → 5 000
  simulations en 15-20 s, mesuré à 3,4 s pour 1 000 simulations sur un plan réel de
  548 tâches).

## v1.11.0

### Ajouté
- Version déployée visible partout : fichier `VERSION` embarqué dans l'image,
  affiché en pied de page, écrit dans les exports CSV, journalisé au démarrage
  (`MPPCR vX.Y.Z — démarrage`) et lisible par `docker exec mpp cat /app/VERSION`.
- La CI refuse une étiquette dont la valeur diffère de `VERSION`, et le smoke test
  vérifie que la version est exposée puis affichée.

### Corrigé
- Avertissement de fiabilité du Monte Carlo : il reprend la valeur **réelle** du
  contrôle 1 du diagnostic au lieu d'un ratio de liens par tâche, qui portait sur
  une autre population (toutes les tâches, achevées comprises, et des relations au
  lieu de tâches). Un planning à 88 % de liens par tâche peut légitimement afficher
  34 % de tâches restantes sans logique. En Monte Carlo seul, l'application ne se
  prononce plus sur la logique et renvoie vers le diagnostic.

## v1.10.0

### Corrigé
- Monte Carlo : les tâches **achevées** et **externes** ne reçoivent plus
  d'incertitude (durée gelée, `o = m = p`). Elles restent dans le réseau — les
  retirer casserait les liens qui les traversent — mais la dispersion ne provient
  plus du passé : sur un plan témoin, P50 passe de **+20,1 j** à **+1,0 j**.
- Monte Carlo : durées converties en **jours ouvrés** via le calendrier du projet,
  comme dans `dcma14.py`. Sur un planning dont les durées sont en heures, dix
  tâches de 40 h affichaient 400 j au lieu de 50 j.

## v1.9.0

### Modifié
- Les **tâches externes** (appartenant à un autre planning) sortent du périmètre de
  tous les contrôles : leurs liens vivent dans un fichier que l'analyse ne charge
  pas. Elles restent comptées dans le diagnostic (`DIAG|TOTAL`, `externes=`).

### Corrigé
- Piège `getExternalProject()` : cette méthode rend `False` — et non `None` — pour
  **toutes** les tâches. Un test `is not None` sur sa valeur les excluait toutes et
  affichait `0/0` sur chaque contrôle. La détection s'appuie désormais sur
  `getExternalTask()`.

## v1.8.1

### Corrigé
- `dcma14.py` : les durées sont converties en jours ouvrés (`Duration.convertUnits`
  avec le calendrier du projet). Les seuils de 44 jours des contrôles 6 et 8 étaient
  comparés à des **heures** sur les plannings exprimés en heures : toute tâche
  dépassant 44 heures était signalée à tort.
- `check_logic()` ne s'appuie plus sur `.size()` des collections Java mais sur
  `len(list(...))`, insensible à la configuration de JPype.

## v1.8.0

### Ajouté
- Diagnostic technique des tâches signalées par le contrôle 1 : `dcma14.py
  --details` ajoute des compteurs (`preds`, `succs`, avancement, jalon, tâche
  externe, active, sous-projet) sans aucun nom de tâche. L'application les
  journalise, ce qui permet de diagnostiquer un planning sans le transmettre.

## v1.7.0

### Modifié
- Périmètre DCMA-14 conforme : les contrôles **2 à 10** ne portent plus que sur les
  **tâches restantes**, comme le prescrit le référentiel (« … / nombre de tâches non
  terminées »). Les contrôles 11 (Missed tasks) et 14 (BEI) portent au contraire sur
  les tâches terminées.

## v1.6.0

### Modifié
- Le contrôle 1 (Logic) exclut les **jalons** et les **tâches achevées** : on
  n'attend plus de lien aval sur une tâche terminée. Sur un plan témoin de 13 tâches
  dont 8 achevées, le contrôle passe de 7,69 % (1/13) à 0 % (0/5).

### Corrigé
- Le contrôle de non-régression compare désormais **réellement** la sortie du
  diagnostic à la référence figée, au lieu de vérifier sa seule présence.
