#!/usr/bin/env python3
"""
Analyse de risque planning par simulation Monte Carlo.
Usage: python3 montecarlo.py <fichier.mpp|.xml> [--sims 5000] [--opt 0.8] [--pess 1.5]

Principe :
  - Lit le reseau de taches (durees + liens predecesseur/successeur) via MPXJ.
  - Pour chaque tache, genere une distribution PERT (optimiste / probable /
    pessimiste) a partir de la duree planifiee et des facteurs --opt/--pess
    (ou d'une estimation 3-points fournie via un CSV, voir --estimates).
  - Fait tourner N simulations : a chaque iteration, tire une duree aleatoire
    par tache et recalcule la date de fin de projet par un passage avant
    (forward pass) sur le graphe de dependances (networkx).
  - Restitue P50/P80/P90 de la date de fin, et un indice de criticite par
    tache (% de simulations ou la tache est sur le chemin critique).

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


def duration_days(d):
    if d is None:
        return 0.0
    try:
        return float(d.getDuration())
    except Exception:
        return 0.0


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


def build_graph(proj, opt_factor, pess_factor, estimates):
    """Construit le graphe de dependances. Chaque noeud porte ses 3 parametres
    PERT (o, m, p) en jours. Les jalons (duree 0) restent a 0 dans toutes les
    branches de l'estimation (pas de risque de duree propre a simuler)."""
    G = nx.DiGraph()
    tasks = {int(t.getUniqueID()): t for t in proj.getTasks()
             if t is not None and t.getName() is not None and not t.getSummary()}

    for uid, t in tasks.items():
        m = duration_days(t.getDuration())
        if uid in estimates:
            o, m, p = estimates[uid]
        elif t.getMilestone() or m == 0:
            o, m, p = 0.0, 0.0, 0.0
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

    return G, n_links


def sample_pert(o, m, p, rng, shape=4.0):
    """Distribution PERT (Beta reparametree), degenere proprement si o=m=p
    (jalon ou tache sans incertitude -> retourne m)."""
    if p <= o:
        return m
    alpha = 1 + shape * (m - o) / (p - o)
    beta = 1 + shape * (p - m) / (p - o)
    return o + rng.beta(alpha, beta) * (p - o)


def deterministic_duration(G):
    """Duree du chemin critique avec les durees planifiees (most likely),
    sans aucun aleatoire -- passage avant standard, coherent avec la logique
    utilisee dans run_simulation()."""
    order = list(nx.topological_sort(G))
    early_finish = {n: 0.0 for n in G.nodes}
    for n in order:
        preds = list(G.predecessors(n))
        early_start = max(
            (early_finish[p] + G[p][n].get("lag", 0.0) for p in preds), default=0.0
        )
        early_finish[n] = early_start + G.nodes[n]["m"]
    return max(early_finish.values()) if early_finish else 0.0


def run_simulation(G, n_sims, rng, seed_report_every=1000):
    order = list(nx.topological_sort(G))
    finish_dates = np.zeros(n_sims)
    on_critical = {n: 0 for n in G.nodes}

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
        for n in G.nodes:
            slack = late_finish[n] - early_finish[n]
            if abs(slack) < 1e-6:
                on_critical[n] += 1

    return finish_dates, on_critical


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--sims", type=int, default=5000, help="Nombre de simulations (defaut 5000)")
    ap.add_argument("--opt", type=float, default=0.8, help="Facteur optimiste (defaut 0.8 = -20%%)")
    ap.add_argument("--pess", type=float, default=1.5, help="Facteur pessimiste (defaut 1.5 = +50%%)")
    ap.add_argument("--estimates", type=str, default=None,
                     help="CSV optionnel task_id,optimistic,most_likely,pessimistic (jours)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    reader = UniversalProjectReader()
    proj = reader.read(args.path)

    estimates = load_estimates_csv(args.estimates) if args.estimates else {}
    G, n_links = build_graph(proj, args.opt, args.pess, estimates)

    print("=" * 72)
    print(f"SIMULATION MONTE CARLO — {args.path}")
    print("=" * 72)
    print(f"Taches de detail : {G.number_of_nodes()}")
    print(f"Liens de dependance : {n_links}")

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


if __name__ == "__main__":
    main()
