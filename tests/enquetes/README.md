# Scripts d'enquête

Outils ponctuels écrits pendant des diagnostics passés : ils ne font **pas**
partie des vérifications à lancer avant une publication (voir `tests/`), mais
documentent comment un problème a été mesuré et peuvent resservir.

| Thème | Scripts |
|---|---|
| Mémoire et plannings volumineux (septembre 2026) | `analyse_hprof.py`, `diagnostic_mpp.py`, `echelonne.py`, `enquetempp.py`, `enveloppe_app.py`, `enveloppe_taches.py`, `etapes.py`, `lire_options.py`, `lire_verbose.py`, `memtest.py`, `mesure_tas.py`, `nettoyer_flux.py`, `structure_mpp.py`, `inventaire_inversions.py`, `verify_memory.py` |
| Fichiers de reproduction anonymisés | `assainir_mpp.py`, `audit_residuel.py`, `sonde_chaines.py` |
| Lecture MPXJ (liens, unités, structure) | `comparer_liens.py`, `externes.py`, `unites_duree.py`, `structure_wbs.py`, `verif_logic.py` |
| Monte Carlo et répartition par lot | `audit_montecarlo.py`, `debug_macro.py`, `mesure_mc.py`, `mesure_options.py` |
| Interface et déploiement | `e2e_real.py`, `lire_resultat.py`, `gen_base.py`, `fix_commentaires.py`, `verify_deploy.sh`, `watch_all.sh`, `watch_run.sh` |
| Version antérieure de référence | `apres_dcma14.py` (copie de `dcma14.py` d'une étape intermédiaire) |

Ils sont envoyés avec le harnais par `tests/lancer_lxc.sh` (dossier
`/tmp/harness/enquetes`, code de l'application dans `PYTHONPATH`) :

```bash
tests/lancer_lxc.sh "python3 enquetes/structure_wbs.py test_projet.xml"
```

Certains ont été écrits pour une version précédente du code ou de la maquette :
les relire avant de les relancer.
