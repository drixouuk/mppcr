#!/usr/bin/env python3
"""
Analyse de risque planning par simulation Monte Carlo.
Usage: python3 montecarlo.py <fichier.mpp|.xml> [--sims 5000] [--opt 0.8] [--pess 1.5]

Principe :
  - Lit le reseau de taches (durees + liens predecesseur/successeur) via MPXJ.
  - Pour chaque tache, genere une distribution PERT (optimiste / probable /
    pessimiste) a partir de la duree planifiee et des facteurs --opt/--pess
    (ou d'une estimation 3-points fournie via un CSV, voir --estimates).
  - N'attribue AUCUNE incertitude au travail deja termine ni aux taches d'un
    autre planning (taches externes) : leur duree est gelee. Elles restent dans
    le reseau, sinon les liens qui les traversent seraient perdus, mais elles ne
    fabriquent plus de dispersion. C'est la difference avec dcma14.py, ou ces
    taches sont au contraire exclues du perimetre des controles.
  - Fait tourner N simulations : a chaque iteration, tire une duree aleatoire
    par tache et recalcule la date de fin de projet par un passage avant
    (forward pass) sur le graphe de dependances (networkx).
  - Restitue P50/P80/P90 de la date de fin, et un indice de criticite par
    tache (% de simulations ou la tache est sur le chemin critique).

Repartition par macro-tache (option --macro-level N) :
  - Le moteur reste identique : on simule toujours au niveau DETAIL, la ou vit la
    vraie structure de dependances. Ce qui change, c'est qu'on conserve a chaque
    iteration la date de fin de chaque sous-arbre au lieu de ne garder que la fin
    globale. La correlation entre lots est donc preservee sans aucune hypothese
    statistique supplementaire a justifier.
  - N est le niveau de plan (getOutlineLevel()) des taches recapitulatives prises
    comme macro-taches : 1 = juste sous la tache de projet racine.
  - Sortie ajoutee APRES le bloc global, uniquement si l'option est fournie : sans
    elle, la sortie est strictement identique a celle des versions precedentes.
  - Les taches de detail qui ne dependent d'aucune recapitulative de ce niveau sont
    regroupees sous « Taches hors lot », pour que la vue soit complete.

Usage:
  python3 montecarlo.py planning.mpp --sims 5000
  python3 montecarlo.py planning.mpp --sims 2000 --macro-level 1

PREREQUIS : le fichier doit avoir des liens predecesseur/successeur exploi-
tables (voir le controle #1 "Logic" du diagnostic DCMA-14 -- dcma14.py).
Sans reseau de dependances, il n'y a rien a simuler : le script s'arrete
avec un message explicite plutot que de produire un resultat trompeur.

Necessite: pip install mpxj jpype1 numpy networkx --break-system-packages
"""
import sys
import csv
import argparse
import numpy as np
import networkx as nx
import mpxj
import jpype

mpxj.startJVM()
UniversalProjectReader = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
TimeUnit = jpype.JClass("org.mpxj.TimeUnit")

# Calendrier du projet, renseigne a la lecture : il ramene les durees en jours
# ouvres (voir duration_days).
PROJECT_CALENDAR = None


def duration_days(d):
    """Duree ramenee en jours ouvres.

    La bibliotheque rend la valeur brute dans l'unite portee par l'objet : « d »
    pour un planning dont les durees sont exprimees en jours, « h » pour un
    planning en heures. Sans conversion, un planning en heures etait simule en
    heures et le resultat affiche en « jours » : dix taches de 40 h donnaient
    ainsi 400 j au lieu de 50 j. On convertit donc via le calendrier du projet,
    avec repli sur la valeur brute si la conversion echoue.
    """
    if d is None:
        return 0.0
    try:
        valeur = float(d.getDuration())
    except Exception:
        return 0.0
    if PROJECT_CALENDAR is not None:
        try:
            return float(d.convertUnits(TimeUnit.DAYS, PROJECT_CALENDAR).getDuration())
        except Exception:
            pass
    return valeur


def est_gelee(t):
    """Tache dont l'incertitude ne doit pas etre simulee : deja terminee
    (% Complete = 100) ou appartenant a un autre planning (tache externe).

    Ces taches restent dans le reseau -- les retirer casserait les liens qui les
    traversent et ferait s'effondrer la duree simulee -- mais leur duree est
    gelee, donc elles n'ajoutent aucune dispersion. L'accesseur getExternalTask()
    rend un booleen ; getExternalProject() rend False et non None pour toutes les
    taches, il ne faut pas s'en servir."""
    try:
        avancement = t.getPercentageComplete()
        if avancement is not None and float(avancement) >= 100.0:
            return True
    except Exception:
        pass
    try:
        return bool(t.getExternalTask())
    except Exception:
        return False


def load_estimates_csv(path):
    """CSV optionnel avec colonnes: task_id,optimistic,most_likely,pessimistic
    (en jours). Permet de remplacer l'approximation par facteurs avec de
    vraies estimations 3-points saisies par les pilotes de lot/composant."""
    estimates = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            estimates[int(row["task_id"])] = (
                float(row["optimistic"]),
                float(row["most_likely"]),
                float(row["pessimistic"]),
            )
    return estimates


def collecter_details(tache, accumulateur):
    """Ajoute recursivement a `accumulateur` les UID des taches de DETAIL (feuilles,
    non recapitulatives) situees sous `tache`."""
    try:
        enfants = tache.getChildTasks()
    except Exception:
        return
    for enfant in (list(enfants) if enfants is not None else []):
        if enfant is None or enfant.getName() is None:
            continue
        try:
            recapitulative = bool(enfant.getSummary())
        except Exception:
            recapitulative = False
        if recapitulative:
            collecter_details(enfant, accumulateur)
        else:
            accumulateur.append(int(enfant.getUniqueID()))


def niveaux_recapitulatifs(proj):
    """Niveaux de plan effectivement presents parmi les taches recapitulatives."""
    niveaux = set()
    for t in proj.getTasks():
        if t is None or t.getName() is None:
            continue
        try:
            if t.getSummary():
                niveaux.add(int(t.getOutlineLevel() or 0))
        except Exception:
            continue
    return sorted(niveaux)


def build_macro_groups(proj, level):
    """Associe chaque tache recapitulative du niveau `level` a la liste des
    UID de ses taches de detail descendantes (feuilles, non-recapitulatives).
    Utilise getChildTasks() recursivement. Rend un dict {uid_macro: (nom, [uid_detail, ...])}."""
    groupes = {}
    for t in proj.getTasks():
        if t is None or t.getName() is None:
            continue
        try:
            if not t.getSummary():
                continue
            niveau = int(t.getOutlineLevel() or 0)
        except Exception:
            continue
        if niveau != level:
            continue
        details = []
        collecter_details(t, details)
        groupes[int(t.getUniqueID())] = (str(t.getName()), details)
    return groupes


def build_graph(proj, opt_factor, pess_factor, estimates):
    """Construit le graphe de dependances. Chaque noeud porte ses 3 parametres
    PERT (o, m, p) en jours. Les jalons (duree 0) restent a 0 dans toutes les
    branches de l'estimation (pas de risque de duree propre a simuler), et les
    taches gelees (achevees ou externes) gardent o = m = p : aucune incertitude
    ne leur est attribuee. Rend (graphe, nombre de liens, nombre de taches gelees).
    """
    G = nx.DiGraph()
    tasks = {int(t.getUniqueID()): t for t in proj.getTasks()
             if t is not None and t.getName() is not None and not t.getSummary()}

    gelees = 0
    for uid, t in tasks.items():
        m = duration_days(t.getDuration())
        if uid in estimates and not est_gelee(t):
            o, m, p = estimates[uid]
        elif t.getMilestone() or m == 0:
            o, m, p = 0.0, 0.0, 0.0
        elif est_gelee(t):
            # Travail deja fait ou d'un autre planning : duree connue, donc
            # aucune dispersion a simuler (une estimation explicite serait un
            # contresens ici : on l'ignore volontairement).
            o, p = m, m
            gelees += 1
        else:
            o, p = m * opt_factor, m * pess_factor
        G.add_node(uid, name=str(t.getName()), o=o, m=m, p=p)

    n_links = 0
    for uid, t in tasks.items():
        preds = t.getPredecessors()
        if preds is None:
            continue
        for rel in preds:
            pred_task = rel.getPredecessorTask()
            if pred_task is None:
                continue
            pred_uid = int(pred_task.getUniqueID())
            if pred_uid in tasks:
                lag = duration_days(rel.getLag())
                G.add_edge(pred_uid, uid, lag=lag)
                n_links += 1

    return G, n_links, gelees


def sample_pert(o, m, p, rng, shape=4.0):
    """Distribution PERT (Beta reparametree), degenere proprement si o=m=p
    (jalon ou tache sans incertitude -> retourne m)."""
    if p <= o:
        return m
    alpha = 1 + shape * (m - o) / (p - o)
    beta = 1 + shape * (p - m) / (p - o)
    return o + rng.beta(alpha, beta) * (p - o)


def early_finish_deterministe(G):
    """Dates de fin au plus tot avec les durees planifiees (most likely), sans aucun
    aleatoire : passage avant standard. Sert au resultat global comme au calcul de
    l'ecart par macro-tache."""
    order = list(nx.topological_sort(G))
    early_finish = {n: 0.0 for n in G.nodes}
    for n in order:
        preds = list(G.predecessors(n))
        early_start = max(
            (early_finish[p] + G[p][n].get("lag", 0.0) for p in preds), default=0.0
        )
        early_finish[n] = early_start + G.nodes[n]["m"]
    return early_finish


def deterministic_duration(G):
    """Duree du chemin critique avec les durees planifiees (most likely),
    sans aucun aleatoire -- passage avant standard, coherent avec la logique
    utilisee dans run_simulation()."""
    early_finish = early_finish_deterministe(G)
    return max(early_finish.values()) if early_finish else 0.0


def run_simulation(G, n_sims, rng, macros=None, seed_report_every=1000):
    """Enchaine n_sims tirages.

    `macros` (facultatif) : dict {uid_macro: (nom, [uid_detail, ...])}. Quand il est
    fourni, on conserve par iteration la date de fin de chaque macro-tache (le max
    des fins de ses taches de detail) et un compteur d'iterations ou AU MOINS UNE de
    ses taches est sur le chemin critique -- une fois par iteration et par macro,
    pas une fois par tache. Le moteur de simulation lui-meme est inchange : la
    correlation entre lots vient de ce qu'on lit les memes tirages, sans hypothese
    statistique supplementaire.
    """
    order = list(nx.topological_sort(G))
    finish_dates = np.zeros(n_sims)
    on_critical = {n: 0 for n in G.nodes}

    fins_macros = {m: np.zeros(n_sims) for m in macros} if macros else {}
    critiques_macros = {m: 0 for m in macros} if macros else {}

    for i in range(n_sims):
        sampled_dur = {
            n: sample_pert(G.nodes[n]["o"], G.nodes[n]["m"], G.nodes[n]["p"], rng)
            for n in G.nodes
        }
        early_start = {n: 0.0 for n in G.nodes}
        early_finish = {n: 0.0 for n in G.nodes}

        for n in order:
            preds = list(G.predecessors(n))
            if preds:
                early_start[n] = max(early_finish[p] + G[p][n].get("lag", 0.0) for p in preds)
            early_finish[n] = early_start[n] + sampled_dur[n]

        project_finish = max(early_finish.values()) if early_finish else 0.0
        finish_dates[i] = project_finish

        # chemin critique de cette iteration : taches sans marge, calcule par
        # un passage arriere (backward pass) standard CPM
        late_finish = {n: project_finish for n in G.nodes}
        for n in reversed(order):
            succs = list(G.successors(n))
            if succs:
                late_finish[n] = min(
                    late_finish[s] - sampled_dur[s] - G[n][s].get("lag", 0.0)
                    for s in succs
                )
        critiques_iteration = set() if macros else None
        for n in G.nodes:
            slack = late_finish[n] - early_finish[n]
            if abs(slack) < 1e-6:
                on_critical[n] += 1
                if critiques_iteration is not None:
                    critiques_iteration.add(n)

        for macro, (_, uids) in (macros or {}).items():
            fins_macros[macro][i] = max(early_finish[u] for u in uids)
            if any(u in critiques_iteration for u in uids):
                critiques_macros[macro] += 1

    if macros:
        return finish_dates, on_critical, fins_macros, critiques_macros
    return finish_dates, on_critical


def afficher_repartition_macros(proj, niveau, macros, fins_macros, critiques_macros,
                               n_sims, deterministe_noeuds):
    """Bloc « Repartition par macro-tache », trie par P90 decroissant.

    L'ecart affiche est celui du P50 de la macro par rapport a sa duree
    deterministe (fin au plus tot de son chemin, durees planifiees), et la
    criticite est la part des simulations ou AU MOINS UNE tache du lot est sur le
    chemin critique -- une iteration compte une fois par lot, pas une fois par
    tache, sinon un lot de 200 taches paraitrait cent fois plus critique qu'un lot
    de 2 taches a structure identique.
    """
    if not macros:
        niveaux = niveaux_recapitulatifs(proj)
        if niveau in niveaux:
            print(f"Les taches recapitulatives de niveau {niveau} ne contiennent aucune "
                  "tache de detail exploitable : rien a repartir par macro-tache.")
        elif niveaux:
            print(f"Aucune tache recapitulative au niveau {niveau} -- "
                  f"niveaux disponibles : {niveaux}.")
        else:
            print("Aucune tache recapitulative dans ce planning : "
                  "--macro-level sans effet.")
        print()
        return

    lignes = []
    for macro, (nom, uids) in macros.items():
        p50, p80, p90 = np.percentile(fins_macros[macro], [50, 80, 90])
        planifie = max(deterministe_noeuds[u] for u in uids)
        criticite = 100.0 * critiques_macros[macro] / n_sims
        lignes.append((p90, nom, p50, p80, p90, p50 - planifie, criticite))
    lignes.sort(key=lambda ligne: -ligne[0])

    print(f"Repartition par macro-tache (niveau {niveau}) :")
    largeur = max(len(ligne[1]) for ligne in lignes)
    for _, nom, p50, p80, p90, ecart, criticite in lignes:
        print(f"  {nom:<{largeur}}  P50: {p50:.1f} j ({ecart:+.1f} j vs planifie)   "
              f"P80: {p80:.1f} j   P90: {p90:.1f} j   Criticite: {criticite:5.1f}%")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--sims", type=int, default=5000, help="Nombre de simulations (defaut 5000)")
    ap.add_argument("--opt", type=float, default=0.8, help="Facteur optimiste (defaut 0.8 = -20%%)")
    ap.add_argument("--pess", type=float, default=1.5, help="Facteur pessimiste (defaut 1.5 = +50%%)")
    ap.add_argument("--estimates", type=str, default=None,
                     help="CSV optionnel task_id,optimistic,most_likely,pessimistic (jours)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--macro-level", type=int, default=None, metavar="N",
                     help="Ajoute la repartition par macro-tache (taches recapitulatives "
                          "de niveau de plan N ; 1 = juste sous la tache de projet). "
                          "Sans cette option, la sortie est inchangee.")
    args = ap.parse_args()

    reader = UniversalProjectReader()
    proj = reader.read(args.path)

    # Calendrier du projet : il sert a ramener les durees en jours ouvres.
    global PROJECT_CALENDAR
    PROJECT_CALENDAR = None
    try:
        PROJECT_CALENDAR = proj.getProjectProperties().getDefaultCalendar()
    except Exception:
        PROJECT_CALENDAR = None

    estimates = load_estimates_csv(args.estimates) if args.estimates else {}
    G, n_links, gelees = build_graph(proj, args.opt, args.pess, estimates)

    print("=" * 72)
    print(f"SIMULATION MONTE CARLO — {args.path}")
    print("=" * 72)
    print(f"Taches de detail : {G.number_of_nodes()}")
    print(f"Liens de dependance : {n_links}")
    print(f"Taches gelees (achevees ou externes) : {gelees} "
          "-- duree connue, aucune incertitude simulee")

    # Correspondance macro -> details, si une repartition par lot est demandee.
    # On ne garde que les details presents dans le graphe, et l'on regroupe sous
    # « Taches hors lot » ceux qui ne dependent d'aucune recapitulative du niveau
    # demande : la vue reste ainsi complete.
    macros = None
    hors_lot = []
    if args.macro_level is not None:
        groupes = build_macro_groups(proj, args.macro_level)
        macros = {}
        couverts = set()
        for uid, (nom, details) in groupes.items():
            presents = [u for u in details if u in G.nodes]
            if presents:
                macros[uid] = (nom, presents)
                couverts.update(presents)
        hos_lot = [n for n in G.nodes if n not in couverts]
        if not macros:
            # Aucune recapitulative a ce niveau : message dedie, sans fabriquer un
            # groupe « hors lot » qui contiendrait tout le planning.
            macros = None
        elif hos_lot:
            macros["hors_lot"] = ("Taches hors lot", hos_lot)

    if n_links == 0:
        print()
        print("ARRET : aucun lien predecesseur/successeur exploitable dans ce fichier.")
        print("Sans reseau de dependances, il n'y a pas de chemin critique a simuler --")
        print("chaque tache serait independante, ce qui donnerait un resultat trompeur.")
        print("Renseignez d'abord les liens entre taches (voir le controle #1 'Logic'")
        print("du diagnostic dcma14.py), puis relancez cette simulation.")
        return

    if not nx.is_directed_acyclic_graph(G):
        cycle = nx.find_cycle(G)
        print()
        print(f"ARRET : le reseau contient une boucle de dependances : {cycle}")
        print("Corrigez la boucle avant de simuler (un planning ne peut pas etre circulaire).")
        return

    rng = np.random.default_rng(args.seed)
    if macros:
        finish_dates, on_critical, fins_macros, critiques_macros = run_simulation(
            G, args.sims, rng, macros=macros)
    else:
        finish_dates, on_critical = run_simulation(G, args.sims, rng)

    p50, p80, p90 = np.percentile(finish_dates, [50, 80, 90])
    deterministic = deterministic_duration(G)

    print(f"Simulations : {args.sims}")
    print()
    print(f"Duree deterministe (chemin critique, durees planifiees) : {deterministic:.1f} j")
    print(f"P50 (mediane simulee)  : {p50:.1f} j  ({p50 - deterministic:+.1f} j vs planifie)")
    print(f"P80                    : {p80:.1f} j  ({p80 - deterministic:+.1f} j vs planifie)")
    print(f"P90                    : {p90:.1f} j  ({p90 - deterministic:+.1f} j vs planifie)")
    print()
    print("Indice de criticite par tache (top 10 -- % de simulations sur le chemin critique) :")
    ranked = sorted(on_critical.items(), key=lambda kv: -kv[1])[:10]
    name_w = max(len(G.nodes[n]["name"]) for n, _ in ranked) + 2
    for uid, count in ranked:
        name = G.nodes[uid]["name"]
        print(f"  {name:<{name_w}} {100.0 * count / args.sims:5.1f}%")
    print()

    if args.macro_level is not None:
        afficher_repartition_macros(
            proj, args.macro_level, macros,
            fins_macros if macros else None, critiques_macros if macros else None,
            args.sims, early_finish_deterministe(G))


if __name__ == "__main__":
    main()
