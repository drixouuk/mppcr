# Journal des versions

Les versions antérieures à la v1.6.0 sont documentées dans les
[releases GitHub](https://github.com/drixouuk/mppcr/releases).

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
