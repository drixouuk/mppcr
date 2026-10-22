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
    (forward pass) sur le graphe de dependances (networkx), en respectant le
    type de chaque lien (FS, SS, FF, SF) et son decalage.
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
  - Les liens portes par une tache RECAPITULATIVE sont signales (ligne « Liens sur
    taches recapitulatives : N »). Un planning ne devrait pas en contenir : comme
    la simulation ne retient que les taches de detail, ces liens sont ignores et
    un lot peut finir avant son predecesseur. Aucun correctif n'est applique,
    c'est un avertissement ; il n'apparait qu'avec l'option, donc la sortie par
    defaut reste inchangee.

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
import io
import math
import argparse
import numpy as np
import networkx as nx
import mpxj
import jpype

mpxj.startJVM()
UniversalProjectReader = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
TimeUnit = jpype.JClass("org.mpxj.TimeUnit")
RelationType = jpype.JClass("org.mpxj.RelationType")
Duration = jpype.JClass("org.mpxj.Duration")

# Calendrier du projet, renseigne a la lecture : il ramene les durees en jours
# ouvres (voir duration_days).
PROJECT_CALENDAR = None

# Jours ouvres par semaine du projet (MinutesPerWeek / MinutesPerDay), renseigne
# a la lecture : il ramene les unites calendaires en jours ouvres. 5 par defaut.
JOURS_OUVRES_SEMAINE = 5.0

# Unites « ecoulees » (calendaires) de MPXJ, exprimees en jours calendaires.
# MPXJ les convertit en temps de travail a 24 h par jour : 2 jours calendaires
# devenaient 6 jours ouvres (48 h / 8 h). On les ramene au prorata de la semaine
# de travail : 2 jours calendaires = 2 x 5/7 = 1,43 jour ouvre.
UNITES_ECOULEES = {
    "ELAPSED_MINUTES": 1 / 1440, "ELAPSED_HOURS": 1 / 24, "ELAPSED_DAYS": 1.0,
    "ELAPSED_WEEKS": 7.0, "ELAPSED_MONTHS": 30.0, "ELAPSED_YEARS": 365.0,
}
# Un decalage en pourcentage n'est pas une duree : c'est une part de la duree du
# PREDECESSEUR (convention MS Project). Voir lag_days.
UNITES_POURCENT = {"PERCENT", "ELAPSED_PERCENT"}


def nom_unite(d):
    """Nom de l'unite MPXJ d'une duree (« DAYS », « ELAPSED_DAYS », « PERCENT »...)."""
    try:
        return str(d.getUnits().name())
    except Exception:
        return ""


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
    unite = nom_unite(d)
    if unite in UNITES_ECOULEES:
        return valeur * UNITES_ECOULEES[unite] * JOURS_OUVRES_SEMAINE / 7.0
    if unite in UNITES_POURCENT:
        return 0.0
    if PROJECT_CALENDAR is not None:
        try:
            return float(d.convertUnits(TimeUnit.DAYS, PROJECT_CALENDAR).getDuration())
        except Exception:
            pass
    return valeur

def lag_days(rel):
    """Decalage d'un lien, en jours ouvres.

    Un decalage en % est la part correspondante de la duree du predecesseur :
    50 % sur une tache de 10 j = 5 j. Il etait converti comme une duree, ce qui
    donnait une valeur sans rapport (1,04 j pour « 500 % »)."""
    lag = rel.getLag()
    if lag is None:
        return 0.0
    if nom_unite(lag) in UNITES_POURCENT:
        try:
            part = float(lag.getDuration()) / 100.0
        except Exception:
            return 0.0
        pred = rel.getPredecessorTask()
        return part * duration_days(pred.getDuration()) if pred is not None else 0.0
    return duration_days(lag)



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


# Marqueur ecrit sur stderr quand le CSV d'estimations est refuse : l'interface
# web le reconnait et affiche le motif au lieu d'un echec generique.
MARQUEUR_ESTIMATIONS = "ESTIMATIONS_INVALIDES:"
COLONNES_ESTIMATIONS = ("task_id", "optimistic", "most_likely", "pessimistic")


class EstimationsInvalides(ValueError):
    pass


def load_estimates_csv(path):
    """CSV optionnel avec colonnes: task_id,optimistic,most_likely,pessimistic
    (en jours). Permet de remplacer l'approximation par facteurs avec de
    vraies estimations 3-points saisies par les pilotes de lot/composant.

    Accepte la virgule ou le point-virgule comme separateur (Excel FR exporte au
    point-virgule), la virgule decimale et un BOM UTF-8. Chaque ligne doit
    verifier 0 <= optimiste <= probable <= pessimiste : sinon la loi PERT n'est
    pas definie (parametre beta negatif) et la simulation plantait sans motif.
    Leve EstimationsInvalides avec un message lisible.
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            contenu = f.read()
    except UnicodeDecodeError:
        raise EstimationsInvalides("le fichier doit etre encode en UTF-8")
    premiere = contenu.splitlines()[0] if contenu.strip() else ""
    separateur = ";" if premiere.count(";") > premiere.count(",") else ","
    lecteur = csv.DictReader(io.StringIO(contenu), delimiter=separateur)
    entetes = {(nom or "").strip().lower() for nom in (lecteur.fieldnames or [])}
    manquantes = [c for c in COLONNES_ESTIMATIONS if c not in entetes]
    if manquantes:
        raise EstimationsInvalides(
            f"colonne(s) absente(s) : {', '.join(manquantes)} "
            f"(attendu : {','.join(COLONNES_ESTIMATIONS)})")

    estimates = {}
    for numero, brut in enumerate(lecteur, start=2):
        ligne = {(cle or "").strip().lower(): (valeur or "").strip()
                 for cle, valeur in brut.items() if cle is not None}
        if not any(ligne.values()):
            continue
        try:
            uid = int(ligne["task_id"])
            o, m, p = (float(ligne[c].replace(",", ".")) for c in COLONNES_ESTIMATIONS[1:])
        except (ValueError, AttributeError):
            raise EstimationsInvalides(f"ligne {numero} : valeur non numerique")
        if not all(math.isfinite(x) for x in (o, m, p)) or not 0 <= o <= m <= p:
            raise EstimationsInvalides(
                f"ligne {numero} (tache {uid}) : il faut 0 <= optimistic <= most_likely "
                f"<= pessimistic (lu : {o:g} / {m:g} / {p:g})")
        estimates[uid] = (o, m, p)
    return estimates


def type_lien(relation):
    """Type du lien : FS (fin -> debut), SS, FF ou SF. FS par defaut."""
    try:
        genre = relation.getType()
    except Exception:
        return "FS"
    if genre == RelationType.START_START:
        return "SS"
    if genre == RelationType.FINISH_FINISH:
        return "FF"
    if genre == RelationType.START_FINISH:
        return "SF"
    return "FS"


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


def est_achevee(t):
    try:
        avancement = t.getPercentageComplete()
        return avancement is not None and float(avancement) >= 100.0
    except Exception:
        return False


def duree_restante(t):
    """Duree restante d'une tache (jours ouvres) : 0 si achevee, duree restante
    MPXJ si elle est commencee, duree planifiee sinon."""
    if est_achevee(t):
        return 0.0
    try:
        reste = t.getRemainingDuration()
        if reste is not None:
            return duration_days(reste)
    except Exception:
        pass
    return duration_days(t.getDuration())


def build_graph(proj, opt_factor, pess_factor, estimates, reste_a_faire=False):
    """Construit le graphe de dependances. Chaque noeud porte ses 3 parametres
    PERT (o, m, p) en jours. Les jalons (duree 0) restent a 0 dans toutes les
    branches de l'estimation (pas de risque de duree propre a simuler), et les
    taches gelees (achevees ou externes) gardent o = m = p : aucune incertitude
    ne leur est attribuee. Rend (graphe, nombre de liens, nombre de taches gelees).

    `reste_a_faire` : reseau du travail RESTANT, date 0 = date d'etat. Une tache
    achevee vaut 0 j et ses liens aval sont retires (son successeur n'attend plus
    rien) ; une tache commencee ne porte que sa duree restante, incertitude
    comprise ; une estimation 3 points s'applique a la tache entiere et n'est donc
    pas reprise pour une tache deja commencee. Toute tache peut demarrer des la
    date d'etat : les dates reelles passees ne sont pas rejouees.
    """
    G = nx.DiGraph()
    tasks = {int(t.getUniqueID()): t for t in proj.getTasks()
             if t is not None and t.getName() is not None and not t.getSummary()}

    gelees = 0
    for uid, t in tasks.items():
        m = duration_days(t.getDuration())
        if reste_a_faire:
            entiere = m
            m = duree_restante(t)
            if est_achevee(t):
                G.add_node(uid, name=str(t.getName()), o=0.0, m=0.0, p=0.0)
                continue
            if m < entiere and uid in estimates:
                estimates = {k: v for k, v in estimates.items() if k != uid}
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
                if reste_a_faire and est_achevee(tasks[pred_uid]):
                    continue
                lag = lag_days(rel)
                G.add_edge(pred_uid, uid, lag=lag, type=type_lien(rel))
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


def passage_avant(G, order, durees):
    """Dates au plus tot (debut, fin) selon le type de chaque lien.

    FS : le successeur debute apres la FIN du predecesseur (+ decalage) ;
    SS : il debute apres son DEBUT ; FF : il finit apres sa FIN ; SF : il finit
    apres son DEBUT. Traiter tous les liens comme FS -- ce que faisait la
    simulation jusqu'ici -- serialisait des taches paralleles : deux taches de
    10 j liees debut-debut donnaient 20 j au lieu de 10. Aucune tache ne debute
    avant le debut du projet (date 0).
    """
    debut, fin = {}, {}
    for n in order:
        duree = durees[n]
        au_plus_tot = 0.0
        for p in G.predecessors(n):
            arc = G[p][n]
            lag = arc.get("lag", 0.0)
            genre = arc.get("type", "FS")
            if genre == "SS":
                borne = debut[p] + lag
            elif genre == "FF":
                borne = fin[p] + lag - duree
            elif genre == "SF":
                borne = debut[p] + lag - duree
            else:
                borne = fin[p] + lag
            if borne > au_plus_tot:
                au_plus_tot = borne
        debut[n] = au_plus_tot
        fin[n] = au_plus_tot + duree
    return debut, fin


def passage_arriere(G, order, durees, fin_projet):
    """Dates de fin au plus tard, symetriques de passage_avant()."""
    fin_tard = {}
    for n in reversed(order):
        duree = durees[n]
        au_plus_tard = fin_projet
        for s in G.successors(n):
            arc = G[n][s]
            lag = arc.get("lag", 0.0)
            genre = arc.get("type", "FS")
            if genre == "SS":
                borne = fin_tard[s] - durees[s] - lag + duree
            elif genre == "FF":
                borne = fin_tard[s] - lag
            elif genre == "SF":
                borne = fin_tard[s] - lag + duree
            else:
                borne = fin_tard[s] - durees[s] - lag
            if borne < au_plus_tard:
                au_plus_tard = borne
        fin_tard[n] = au_plus_tard
    return fin_tard


def early_finish_deterministe(G):
    """Dates de fin au plus tot avec les durees planifiees (most likely), sans aucun
    aleatoire : passage avant standard. Sert au resultat global comme au calcul de
    l'ecart par macro-tache."""
    order = list(nx.topological_sort(G))
    _, early_finish = passage_avant(G, order, {n: G.nodes[n]["m"] for n in G.nodes})
    return early_finish


def deterministic_duration(G):
    """Duree du chemin critique avec les durees planifiees (most likely),
    sans aucun aleatoire -- passage avant standard, coherent avec la logique
    utilisee dans run_simulation()."""
    early_finish = early_finish_deterministe(G)
    return max(early_finish.values()) if early_finish else 0.0


# Taille des blocs de tirages : on simule BLOC tirages a la fois, sous forme de
# matrices (taches x tirages). Le produit est plafonne pour borner la memoire
# (4 matrices de float64 : ~130 Mo a 4 millions d'elements).
ELEMENTS_PAR_BLOC = 4_000_000
CODE_TYPE = {"FS": 0, "SS": 1, "FF": 2, "SF": 3}


class Reseau:
    """Graphe pre-indexe dans l'ordre topologique, pour le moteur vectorise."""

    def __init__(self, G):
        self.ordre = list(nx.topological_sort(G))
        self.index = {n: i for i, n in enumerate(self.ordre)}
        self.o = np.array([G.nodes[n]["o"] for n in self.ordre], dtype=float)
        self.m = np.array([G.nodes[n]["m"] for n in self.ordre], dtype=float)
        self.p = np.array([G.nodes[n]["p"] for n in self.ordre], dtype=float)
        self.preds = [[(self.index[a], G[a][n].get("lag", 0.0), CODE_TYPE[G[a][n].get("type", "FS")])
                       for a in G.predecessors(n)] for n in self.ordre]
        self.succs = [[(self.index[s], G[n][s].get("lag", 0.0), CODE_TYPE[G[n][s].get("type", "FS")])
                       for s in G.successors(n)] for n in self.ordre]

    def __len__(self):
        return len(self.ordre)


def tirer_durees(res, nb, rng, correlation=0.0, groupes=None):
    """Matrice (taches x nb) de durees PERT.

    Avec `correlation` > 0, les durees des taches d'un meme groupe (lot) sont
    correlees par leurs RANGS (methode d'Iman-Conover) : on tire des normales
    correlees z = sqrt(r) * Z_lot + sqrt(1 - r) * e, puis on range les durees
    deja tirees de chaque tache dans l'ordre de ses z. Les lois de chaque tache
    sont donc exactement conservees ; seule leur dependance change. Sans cela, les
    derives des taches d'un lot se compensent et le risque est sous-estime.
    """
    o, m, p = res.o, res.m, res.p
    variable = p > o
    durees = np.repeat(m[:, None], nb, axis=1)
    if variable.any():
        ov, pv = o[variable], p[variable]
        mv = np.clip(m[variable], ov, pv)
        alpha = 1 + 4.0 * (mv - ov) / (pv - ov)
        beta = 1 + 4.0 * (pv - mv) / (pv - ov)
        durees[variable] = ov[:, None] + rng.beta(
            alpha[:, None], beta[:, None], size=(int(variable.sum()), nb)) * (pv - ov)[:, None]
    if correlation > 0 and groupes is not None and nb > 1:
        nb_groupes = int(groupes.max()) + 1
        communs = rng.standard_normal((nb_groupes, nb))
        z = (np.sqrt(correlation) * communs[groupes]
             + np.sqrt(1 - correlation) * rng.standard_normal((len(res), nb)))
        rangs = np.argsort(np.argsort(z, axis=1), axis=1)
        durees = np.take_along_axis(np.sort(durees, axis=1), rangs, axis=1)
    return durees


def passe_avant_bloc(res, durees):
    """Dates au plus tot de tout un bloc de tirages (meme regles que passage_avant)."""
    debut = np.zeros_like(durees)
    fin = np.empty_like(durees)
    for i in range(len(res)):
        au_plus_tot = debut[i]
        for j, lag, genre in res.preds[i]:
            if genre == 0:
                borne = fin[j] + lag
            elif genre == 1:
                borne = debut[j] + lag
            elif genre == 2:
                borne = fin[j] + lag - durees[i]
            else:
                borne = debut[j] + lag - durees[i]
            np.maximum(au_plus_tot, borne, out=au_plus_tot)
        fin[i] = au_plus_tot + durees[i]
    return debut, fin


def passe_arriere_bloc(res, durees, fin_projet):
    """Dates de fin au plus tard d'un bloc (memes regles que passage_arriere)."""
    fin_tard = np.empty_like(durees)
    for i in reversed(range(len(res))):
        au_plus_tard = fin_tard[i]
        au_plus_tard[:] = fin_projet
        for s, lag, genre in res.succs[i]:
            if genre == 0:
                borne = fin_tard[s] - durees[s] - lag
            elif genre == 1:
                borne = fin_tard[s] - durees[s] - lag + durees[i]
            elif genre == 2:
                borne = fin_tard[s] - lag
            else:
                borne = fin_tard[s] - lag + durees[i]
            np.minimum(au_plus_tard, borne, out=au_plus_tard)
    return fin_tard


def simuler(G, n_sims, rng, macros=None, correlation=0.0, groupes=None):
    """Enchaine n_sims tirages, par blocs vectorises.

    Rend un dict : `fins` (date de fin de chaque tirage), `critiques` (nombre de
    tirages ou chaque tache est critique), `sensibilite` (correlation de Pearson
    entre la duree tiree de chaque tache et la fin du projet), et, si `macros` est
    fourni, `fins_macros` / `critiques_macros` comme run_simulation().

    `groupes` : {uid: identifiant de lot} pour la correlation intra-lot.

    Le traitement par blocs remplace la boucle tirage par tirage : un plan de
    24 000 taches demandait 0,126 s par tirage (636 s pour 5 000), au-dela du delai
    maximal ; les operations portent desormais sur des vecteurs de tirages.
    """
    res = Reseau(G)
    n = len(res)
    fins = np.zeros(n_sims)
    critiques = np.zeros(n, dtype=np.int64)
    somme_x = np.zeros(n)
    somme_xx = np.zeros(n)
    somme_xy = np.zeros(n)
    somme_y = somme_yy = 0.0

    indices_groupes = None
    if groupes:
        codes = {}
        indices_groupes = np.array(
            [codes.setdefault(groupes.get(u, "hors lot"), len(codes)) for u in res.ordre])

    macros = macros or {}
    lignes_macros = {mac: np.array([res.index[u] for u in uids]) for mac, (_, uids) in macros.items()}
    fins_macros = {mac: np.zeros(n_sims) for mac in macros}
    critiques_macros = {mac: 0 for mac in macros}

    taille = max(1, min(n_sims, ELEMENTS_PAR_BLOC // max(n, 1)))
    for depart in range(0, n_sims, taille):
        nb = min(taille, n_sims - depart)
        durees = tirer_durees(res, nb, rng, correlation, indices_groupes)
        _, fin = passe_avant_bloc(res, durees)
        fin_projet = fin.max(axis=0) if n else np.zeros(nb)
        fins[depart:depart + nb] = fin_projet
        critique = np.abs(passe_arriere_bloc(res, durees, fin_projet) - fin) < 1e-6
        critiques += critique.sum(axis=1)

        somme_x += durees.sum(axis=1)
        somme_xx += (durees * durees).sum(axis=1)
        somme_xy += durees @ fin_projet
        somme_y += float(fin_projet.sum())
        somme_yy += float(fin_projet @ fin_projet)

        for mac, lignes in lignes_macros.items():
            fins_macros[mac][depart:depart + nb] = fin[lignes].max(axis=0)
            critiques_macros[mac] += int(critique[lignes].any(axis=0).sum())

    cov = somme_xy / n_sims - (somme_x / n_sims) * (somme_y / n_sims)
    var_x = somme_xx / n_sims - (somme_x / n_sims) ** 2
    var_y = somme_yy / n_sims - (somme_y / n_sims) ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where((var_x > 1e-12) & (var_y > 1e-12), cov / np.sqrt(var_x * var_y), 0.0)

    return {
        "fins": fins,
        "critiques": {u: int(critiques[i]) for i, u in enumerate(res.ordre)},
        "sensibilite": {u: float(corr[i]) for i, u in enumerate(res.ordre)},
        "fins_macros": fins_macros,
        "critiques_macros": critiques_macros,
    }


def run_simulation(G, n_sims, rng, macros=None, seed_report_every=1000):
    """Interface historique de simuler() : (fins, criticite) et, si `macros` est
    fourni, (fins, criticite, fins par macro, criticite par macro)."""
    resultat = simuler(G, n_sims, rng, macros=macros)
    if macros:
        return (resultat["fins"], resultat["critiques"],
                resultat["fins_macros"], resultat["critiques_macros"])
    return resultat["fins"], resultat["critiques"]


def groupes_par_lot(proj, G):
    """{uid de tache de detail: lot} au niveau de plan 1 ; les taches hors lot
    forment un groupe a part."""
    groupes = {}
    for uid_lot, (_, details) in build_macro_groups(proj, 1).items():
        for u in details:
            if u in G.nodes:
                groupes[u] = uid_lot
    return groupes


def date_apres(date_etat, jours):
    """Date atteinte `jours` jours ouvres apres la date d'etat (calendrier du
    projet), au format AAAA-MM-JJ. Repli : prorata calendaire de la semaine."""
    if PROJECT_CALENDAR is not None:
        try:
            cible = PROJECT_CALENDAR.getDate(date_etat, Duration.getInstance(float(jours), TimeUnit.DAYS))
            if cible is not None:
                return str(cible)[:10]
        except Exception:
            pass
    from datetime import date, timedelta
    try:
        depart = date.fromisoformat(str(date_etat)[:10])
    except ValueError:
        return "?"
    return (depart + timedelta(days=jours * 7.0 / JOURS_OUVRES_SEMAINE)).isoformat()


def afficher_prevision(proj, args, estimates):
    """Bloc « Prevision depuis la date d'etat » (option --prevision).

    Simulation du travail RESTANT (voir build_graph(reste_a_faire=True)) : reste a
    faire planifie, P50 / P80 / P90 du reste a faire, et les dates de fin
    correspondantes sur le calendrier du projet (week-ends et feries sautes).
    """
    date_etat = proj.getProjectProperties().getStatusDate()
    if date_etat is None:
        print("Prevision depuis la date d'etat : date d'etat absente -- prevision impossible.")
        print()
        return
    G, _, _ = build_graph(proj, args.opt, args.pess, estimates, reste_a_faire=True)
    if G.number_of_nodes() == 0 or not nx.is_directed_acyclic_graph(G):
        return
    rng = np.random.default_rng(args.seed + 1)
    resultat = simuler(G, args.sims, rng, correlation=args.correlation,
                       groupes=groupes_par_lot(proj, G) if args.correlation else None)
    planifie = deterministic_duration(G)
    print(f"Prevision depuis la date d'etat ({str(date_etat)[:10]}) :")
    print(f"  Reste a faire planifie : {planifie:.1f} j -> fin {date_apres(date_etat, planifie)}")
    for libelle, valeur in zip(("P50", "P80", "P90"), np.percentile(resultat["fins"], [50, 80, 90])):
        print(f"  {libelle} : {valeur:.1f} j -> fin {date_apres(date_etat, valeur)}")
    print()


def afficher_sensibilite(G, sensibilite, nombre=10):
    """Bloc « Sensibilite » (option --sensibilite) : les taches dont la duree tiree
    est la plus correlee a la fin du projet. C'est le graphe « tornado » : une
    tache toujours critique mais courte et peu incertaine pese peu."""
    classees = sorted(((u, c) for u, c in sensibilite.items() if abs(c) > 1e-9),
                      key=lambda kv: -abs(kv[1]))[:nombre]
    print(f"Sensibilite (correlation duree de tache / fin de projet, top {nombre}) :")
    if not classees:
        print("  aucune tache n'a d'incertitude simulee")
    else:
        largeur = max(len(G.nodes[u]["name"]) for u, _ in classees) + 2
        for u, c in classees:
            print(f"  {G.nodes[u]['name']:<{largeur}} {c:+.2f}")
    print()


def afficher_distribution(fins, classes):
    """Bloc « Distribution » (option --histogramme N) : effectif par classe de
    duree simulee, pour l'histogramme et la courbe en S."""
    effectifs, bornes = np.histogram(fins, bins=classes)
    print(f"Distribution de la duree simulee ({classes} classes) :")
    for i, effectif in enumerate(effectifs):
        print(f"  {bornes[i]:.2f} - {bornes[i + 1]:.2f} : {int(effectif)}")
    print()


def liens_recapitulatifs(proj):
    """Repere les liens de dependance portes par une tache RECAPITULATIVE.

    Un planning ne devrait pas en contenir : les liens relient des taches de
    detail. Comme build_graph() ne retient que ces dernieres, ces liens sont
    IGNORES dans la simulation -- le reseau est simule comme s'ils n'existaient
    pas. On les compte pour le DIRE, sans rien corriger : c'est un avertissement
    affiche avec la repartition par macro-tache, la seule vue ou le phenomene se
    voit (un lot peut finir avant son predecesseur).

    Rend (nombre_de_liens, ["Predecesseur -> Successeur", ...] limite a 3).
    """
    total = 0
    exemples = []
    for tache in proj.getTasks():
        if tache is None or tache.getName() is None:
            continue
        relations = list(tache.getPredecessors() or [])
        if not relations:
            continue
        for relation in relations:
            predecesseur = relation.getPredecessorTask()
            if predecesseur is None:
                continue
            if tache.getSummary() or predecesseur.getSummary():
                total += 1
                if len(exemples) < 3:
                    exemples.append(f"{predecesseur.getName()} -> {tache.getName()}")
    return total, exemples


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
    nb_recap, exemples = liens_recapitulatifs(proj)
    if nb_recap:
        detail = f" -- ex. : {' ; '.join(exemples)}" if exemples else ""
        print(f"Liens sur taches recapitulatives : {nb_recap} "
              f"(ignores dans la simulation){detail}")
        print("  Un planning ne devrait pas en contenir : ces liens ne relient pas des")
        print("  taches de detail. Les lots sont donc simules sans eux, ce qui peut")
        print("  donner a un lot une duree trop courte devant son predecesseur.")
        print()

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
    ap.add_argument("--correlation", type=float, default=0.0, metavar="R",
                     help="Correlation des durees entre taches d'un meme lot de niveau 1 "
                          "(0 a 0.9, defaut 0 = taches independantes).")
    ap.add_argument("--sensibilite", action="store_true",
                     help="Ajoute le classement des taches par sensibilite (tornado).")
    ap.add_argument("--histogramme", type=int, default=0, metavar="N",
                     help="Ajoute la distribution de la duree simulee en N classes.")
    ap.add_argument("--prevision", action="store_true",
                     help="Ajoute le reste a faire et les dates de fin depuis la date d'etat.")
    args = ap.parse_args()
    if not 0.0 <= args.correlation <= 0.9:
        ap.error("--correlation doit etre comprise entre 0 et 0.9")
    if args.histogramme and not 2 <= args.histogramme <= 100:
        ap.error("--histogramme doit etre compris entre 2 et 100")

    reader = UniversalProjectReader()
    proj = reader.read(args.path)

    # Calendrier du projet : il sert a ramener les durees en jours ouvres.
    global PROJECT_CALENDAR
    PROJECT_CALENDAR = None
    try:
        PROJECT_CALENDAR = proj.getProjectProperties().getDefaultCalendar()
    except Exception:
        PROJECT_CALENDAR = None

    global JOURS_OUVRES_SEMAINE
    JOURS_OUVRES_SEMAINE = 5.0
    try:
        proprietes = proj.getProjectProperties()
        jours = float(proprietes.getMinutesPerWeek()) / float(proprietes.getMinutesPerDay())
        if 0 < jours <= 7:
            JOURS_OUVRES_SEMAINE = jours
    except Exception:
        pass

    try:
        estimates = load_estimates_csv(args.estimates) if args.estimates else {}
    except EstimationsInvalides as exc:
        print(f"{MARQUEUR_ESTIMATIONS} {exc}", file=sys.stderr)
        sys.exit(2)
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
    if args.macro_level is not None:
        groupes = build_macro_groups(proj, args.macro_level)
        macros = {}
        couverts = set()
        for uid, (nom, details) in groupes.items():
            presents = [u for u in details if u in G.nodes]
            if presents:
                macros[uid] = (nom, presents)
                couverts.update(presents)
        hors_lot = [n for n in G.nodes if n not in couverts]
        if not macros:
            # Aucune recapitulative a ce niveau : message dedie, sans fabriquer un
            # groupe « hors lot » qui contiendrait tout le planning.
            macros = None
        elif hors_lot:
            macros["hors_lot"] = ("Taches hors lot", hors_lot)

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
    resultat = simuler(G, args.sims, rng, macros=macros, correlation=args.correlation,
                       groupes=groupes_par_lot(proj, G) if args.correlation else None)
    finish_dates, on_critical = resultat["fins"], resultat["critiques"]
    fins_macros, critiques_macros = resultat["fins_macros"], resultat["critiques_macros"]

    p50, p80, p90 = np.percentile(finish_dates, [50, 80, 90])
    deterministic = deterministic_duration(G)

    print(f"Simulations : {args.sims}")
    if args.correlation:
        print(f"Correlation intra-lot : {args.correlation:.2f} (lots de niveau 1)")
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

    if args.sensibilite:
        afficher_sensibilite(G, resultat["sensibilite"])
    if args.histogramme:
        afficher_distribution(finish_dates, args.histogramme)
    if args.prevision:
        afficher_prevision(proj, args, estimates)

    if args.macro_level is not None:
        afficher_repartition_macros(
            proj, args.macro_level, macros,
            fins_macros if macros else None, critiques_macros if macros else None,
            args.sims, early_finish_deterministe(G))


if __name__ == "__main__":
    main()
