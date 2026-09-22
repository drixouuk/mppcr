#!/usr/bin/env python3
"""
Diagnostic qualite DCMA-14 pour plannings MS Project (.mpp, .mspdi/.xml, etc.)
Usage: python3 dcma14.py <fichier1> [fichier2 ...] [--details]

L'option --details ajoute, apres la ligne « Resume », un bloc technique listant
les numeros de tache (colonne N° de MS Project) concernes par chaque controle :
une ligne « numero_controle|id,id,id », ou « - » si aucune tache n'est en ecart.
Sans cette option, la sortie est identique a celle des versions precedentes.

Implemente les 14 controles de la methodologie DCMA (Defense Contract
Management Agency), standard de reference pour l'audit de qualite de
planning en environnement aero/defense.

Necessite: pip install mpxj jpype1 --break-system-packages
"""
import sys
import mpxj
import jpype

mpxj.startJVM()

UniversalProjectReader = jpype.JClass("org.mpxj.reader.UniversalProjectReader")
RelationType = jpype.JClass("org.mpxj.RelationType")
ConstraintType = jpype.JClass("org.mpxj.ConstraintType")
TimeUnit = jpype.JClass("org.mpxj.TimeUnit")

# Calendrier du projet, renseigne a la lecture : il sert a ramener les durees en
# jours ouvres (voir duration_days).
PROJECT_CALENDAR = None

# Seuils de tolerance standards DCMA-14
THRESHOLDS = {
    "logic": 5.0,          # % de taches sans predecesseur/successeur
    "leads": 0.0,           # % de relations avec lead (lag negatif)
    "lags": 5.0,            # % de relations avec lag positif
    "fs_relationships": 90.0,  # % minimum de relations Finish-to-Start
    "hard_constraints": 5.0,   # % de taches avec contrainte dure
    "high_float": 5.0,      # % de taches avec marge totale > 44j
    "negative_float": 0.0,  # % de taches avec marge negative
    "high_duration": 5.0,   # % de taches (non-recap) avec duree > 44j
}
HIGH_FLOAT_DAYS = 44
HIGH_DURATION_DAYS = 44

HARD_CONSTRAINTS = {
    ConstraintType.MUST_START_ON,
    ConstraintType.MUST_FINISH_ON,
    ConstraintType.START_ON,
    ConstraintType.FINISH_ON,
}

# --- Detail des taches concernees (option --details) -----------------------
# Chaque controle peut enregistrer ici les numeros de tache (colonne N° de
# MS Project) des taches en ecart. La collecte est desactivee par defaut : sans
# l'option --details, le script produit exactement la meme sortie qu'avant.
COLLECT_DETAILS = False
TASK_DETAILS = {}
# Compteurs techniques du controle 1 (option --details uniquement) : ils servent a
# distinguer un planning ou les liens manquent vraiment d'un planning dont la
# lecture des liens echoue (taches externes, inactives, sous-projets). Aucun nom
# de tache n'y figure : uniquement des nombres.
TASK_DIAG = {}


def task_id(t):
    """N° de la tache (colonne ID de MS Project), avec repli sur l'UID."""
    if t is None:
        return None
    for accesseur in ("getID", "getUniqueID"):
        try:
            valeur = getattr(t, accesseur)()
        except Exception:
            continue
        if valeur is not None:
            return int(valeur)
    return None


def record_detail(numero, tasks):
    """Enregistre les taches en ecart d'un controle (silencieux si non demande)."""
    if not COLLECT_DETAILS:
        return
    identifiants = sorted({tid for tid in (task_id(t) for t in (tasks or []))
                           if tid is not None})
    TASK_DETAILS[numero] = identifiants


def diagnostic_tache(t):
    """Compteurs techniques d'une tache, pour le diagnostic du controle 1.

    Rend un dictionnaire de nombres seulement : nombre de predecesseurs et de
    successeurs tels que le LECTEUR les restitue, avancement, jalon, tache
    externe, tache active, tache representant un sous-projet.
    """
    def nombre(accesseur):
        try:
            valeur = getattr(t, accesseur)()
        except Exception:
            return -1
        if valeur is None:
            return -1
        try:
            return int(valeur)
        except (TypeError, ValueError):
            return 1 if valeur else 0

    def compte(accesseur):
        try:
            valeur = getattr(t, accesseur)()
        except Exception:
            return -1
        if valeur is None:
            return 0
        try:
            return len(list(valeur))
        except TypeError:
            return -1

    def renseigne(accesseur):
        """1 si l'accesseur rend une valeur exploitable, 0 sinon."""
        try:
            valeur = getattr(t, accesseur)()
        except Exception:
            return 0
        return 1 if valeur else 0

    return {
        "id": task_id(t) if task_id(t) is not None else -1,
        "preds": compte("getPredecessors"),
        "succs": compte("getSuccessors"),
        "pct": nombre("getPercentageComplete"),
        "jalon": nombre("getMilestone"),
        "externe": renseigne("getExternalTask"),
        "active": nombre("getActive"),
        "sous_projet": renseigne("getSubprojectFile"),
    }


def print_details():
    """Bloc « N|id,id » lu par l'interface web, un controle par ligne."""
    print("=" * 72)
    print("DETAILS_TACHES (numeros MS Project des taches en ecart)")
    print("=" * 72)
    for numero in sorted(TASK_DETAILS):
        identifiants = TASK_DETAILS[numero]
        print(f"{numero}|{','.join(str(i) for i in identifiants) if identifiants else '-'}")
    print()

    # Diagnostic du controle 1 (Logic) : pourquoi ces taches sont-elles sans lien ?
    # L'interface web ne lit que les lignes « N|… » ci-dessus et ignore ce bloc.
    lignes = TASK_DIAG.get(1) or []
    total = TASK_DIAG.get("TOTAL")
    if lignes or total:
        print("=" * 72)
        print("DIAG_LECTURE (compteurs techniques du controle 1, aucun nom de tache)")
        print("=" * 72)
        if total:
            print(f"DIAG|TOTAL|taches={total['taches']}|restantes={total['restantes']}"
                  f"|liens={total['liens']}|externes={total['externes']}")
        for tache in lignes:
            print(f"DIAG|1|{tache['id']}|preds={tache['preds']}|succs={tache['succs']}"
                  f"|pct={tache['pct']}|jalon={tache['jalon']}|externe={tache['externe']}"
                  f"|active={tache['active']}|sous_projet={tache['sous_projet']}")
        print()


def is_real_task(t):
    """Exclut les taches recapitulatives (summary) et les jalons pour
    certains controles ou l'on ne veut que les taches de detail."""
    return t is not None and t.getName() is not None and not t.getSummary()


def is_complete(t):
    """Tache marquee achevee (% Complete = 100)."""
    pct_complete = t.getPercentageComplete()
    return pct_complete is not None and float(pct_complete) >= 100.0


def is_remaining(t):
    """Tache restante au sens du referentiel DCMA : tache de detail non achevee.

    Les controles 1 a 10 ne portent que sur les taches restantes — leurs formules
    sont toutes de la forme « ... / nombre de taches non terminees » :
      1 Logic       : taches sans lien amont/aval parmi les taches non terminees
      2/3 Leads/Lags: liens avec lead (lag negatif) ou lag, parmi les taches non terminees
      4 Relations   : repartition des types de liens des taches non terminees
      5 Contraintes : contraintes dures parmi les taches non terminees
      6/7 Marges    : marge > 44 j ou < 0 parmi les taches non terminees
      8 Duree       : durees excessives parmi les taches non terminees
      9 Dates       : dates reelles futures ou dates prevues passees des taches non terminees
      10 Ressources : taches de detail non terminees sans ressource
    Les controles 11 (Missed tasks) et 14 (BEI) portent au contraire sur les taches
    terminees : ils ne doivent surtout pas utiliser ce filtre."""
    return is_real_task(t) and not is_complete(t)


def pct(n, d):
    return round(100.0 * n / d, 2) if d else 0.0


def duration_days(d):
    """Duree ramenee en jours ouvres.

    La bibliotheque rend la valeur brute dans l'unite portee par l'objet :
    « d » pour un planning dont les durees sont exprimees en jours, « h » pour un
    planning dont les durees sont en heures. Comparer cette valeur telle quelle a
    un seuil exprime en jours (44) faussait les controles 6 et 8 : un planning en
    heures voyait ainsi signalees toutes les taches depassant 44 HEURES, soit
    environ 5,5 jours. On convertit donc en jours ouvres via le calendrier du
    projet ; faute de calendrier, on retombe sur la valeur brute.
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


def check_logic(tasks):
    """1. Logic — % de taches sans predecesseur ni successeur, en excluant
    la tache de debut de projet (pas de predecesseur attendu) et la tache
    de fin de projet (pas de successeur attendu) -- une seule de chaque,
    identifiees par la date de debut/fin la plus extreme.

    Conformement a la pratique DCMA, le controle ne porte que sur les taches
    RESTANTES : les taches achevees (% Complete = 100) et les jalons sortent du
    numerateur comme du denominateur, puisqu'on n'attend plus de lien aval sur
    une tache terminee. C'est deja le choix de check_high_duration()."""
    real = [t for t in tasks if is_real_task(t) and not t.getMilestone() and not is_complete(t)]
    if not real:
        return 0.0, 0, 0

    # On compte les liens par leur longueur plutot que par size() : JPype peut
    # rendre une liste Python selon la configuration, et les deux formes sont
    # alors equivalentes.
    no_pred = [t for t in real if len(list(t.getPredecessors() or [])) == 0]
    no_succ = [t for t in real if len(list(t.getSuccessors() or [])) == 0]

    project_start = min(no_pred, key=lambda t: t.getStart() or 0, default=None) if no_pred else None
    project_end = max(no_succ, key=lambda t: t.getFinish() or 0, default=None) if no_succ else None

    bad_tasks = set()
    for t in no_pred:
        if t is not project_start:
            bad_tasks.add(t.getUniqueID())
    for t in no_succ:
        if t is not project_end:
            bad_tasks.add(t.getUniqueID())

    record_detail(1, [t for t in real if t.getUniqueID() in bad_tasks])
    if COLLECT_DETAILS:
        # Compteurs techniques : permettent de savoir si le lecteur restitue bien
        # les liens de ces taches (voir print_details).
        TASK_DIAG[1] = [diagnostic_tache(t) for t in real if t.getUniqueID() in bad_tasks]
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_leads_lags(tasks):
    """2. Leads (lag negatif) / 3. Lags (lag positif) sur les relations.

    Perimetre DCMA : les liens des taches RESTANTES. Formule :
    (# de liens avec lead / # de liens) x 100."""
    total_rel = 0
    leads = 0
    lags = 0
    lead_tasks = []
    lag_tasks = []
    for t in tasks:
        if t is None or not is_remaining(t) or t.getPredecessors() is None:
            continue
        for rel in t.getPredecessors():
            total_rel += 1
            lag = rel.getLag()
            lag_val = duration_days(lag)
            if lag_val < 0:
                leads += 1
                lead_tasks.append(t)
            elif lag_val > 0:
                lags += 1
                lag_tasks.append(t)
    record_detail(2, lead_tasks)
    record_detail(3, lag_tasks)
    return (pct(leads, total_rel), leads, total_rel), (pct(lags, total_rel), lags, total_rel)


def check_fs_relationships(tasks):
    """4. Relationship types — % de relations Finish-to-Start.

    Perimetre DCMA : les liens des taches RESTANTES. Formule :
    (# de liens FS / # de liens) x 100."""
    total_rel = 0
    fs = 0
    non_fs_tasks = []
    for t in tasks:
        if t is None or not is_remaining(t) or t.getPredecessors() is None:
            continue
        for rel in t.getPredecessors():
            total_rel += 1
            if rel.getType() == RelationType.FINISH_START:
                fs += 1
            else:
                non_fs_tasks.append(t)
    record_detail(4, non_fs_tasks)
    return pct(fs, total_rel), fs, total_rel


def check_hard_constraints(tasks):
    """5. Hard constraints — % de taches avec contrainte dure (MSO/MFO/SO/FO).

    Perimetre DCMA : taches RESTANTES. Formule :
    (# de taches non terminees avec contrainte dure / # de taches non terminees) x 100."""
    real = [t for t in tasks if is_remaining(t)]
    if not real:
        return 0.0, 0, 0
    bad_tasks = [t for t in real if t.getConstraintType() in HARD_CONSTRAINTS]
    record_detail(5, bad_tasks)
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_high_float(tasks):
    """6. High float — % de taches avec marge totale > 44 jours.

    Perimetre DCMA : taches RESTANTES."""
    real = [t for t in tasks if is_remaining(t)]
    if not real:
        return 0.0, 0, 0
    bad_tasks = [t for t in real if duration_days(t.getTotalSlack()) > HIGH_FLOAT_DAYS]
    record_detail(6, bad_tasks)
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_negative_float(tasks):
    """7. Negative float — % de taches avec marge totale negative.

    Perimetre DCMA : taches RESTANTES."""
    real = [t for t in tasks if is_remaining(t)]
    if not real:
        return 0.0, 0, 0
    bad_tasks = [t for t in real if duration_days(t.getTotalSlack()) < 0]
    record_detail(7, bad_tasks)
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_high_duration(tasks):
    """8. High duration — % de taches de detail avec duree > 44 jours.

    Perimetre DCMA : taches RESTANTES, hors jalons (un jalon n'a pas de duree)."""
    real = [t for t in tasks if is_remaining(t) and not t.getMilestone()]
    if not real:
        return 0.0, 0, 0
    bad_tasks = [t for t in real if duration_days(t.getDuration()) > HIGH_DURATION_DAYS]
    record_detail(8, bad_tasks)
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_invalid_dates(tasks, status_date):
    """9. Invalid dates — Perimetre DCMA : taches RESTANTES, dont on verifie
    qu'elles n'ont ni date reelle posterieure a la date d'etat, ni date prevue
    anterieure a cette date. Necessite une date d'etat renseignee."""
    if status_date is None:
        return None, 0, 0
    real = [t for t in tasks if is_remaining(t)]
    bad_tasks = []
    for t in real:
        af = t.getActualFinish()
        if af is not None and af.isAfter(status_date):
            bad_tasks.append(t)
            continue
        finish = t.getFinish()
        if finish is not None and finish.isBefore(status_date):
            bad_tasks.append(t)
    record_detail(9, bad_tasks)
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_resources(tasks):
    """10. Resources — % de taches de detail sans ressource affectee
    (indicatif, pas de seuil DCMA strict).

    Perimetre DCMA : taches RESTANTES, hors jalons."""
    real = [t for t in tasks if is_remaining(t) and not t.getMilestone()]
    if not real:
        return 0.0, 0, 0
    bad_tasks = []
    for t in real:
        names = t.getResourceNames()
        if names is None or str(names).strip() == "":
            bad_tasks.append(t)
    record_detail(10, bad_tasks)
    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_missed_tasks(tasks, status_date):
    """11. Missed tasks — % de taches terminees apres leur date de fin
    de reference (baseline). Necessite une baseline renseignee."""
    real = [t for t in tasks if is_real_task(t)]
    checked = 0
    bad_tasks = []
    for t in real:
        bf = t.getBaselineFinish()
        af = t.getActualFinish()
        if bf is None or af is None:
            continue
        checked += 1
        if af.isAfter(bf):
            bad_tasks.append(t)
    record_detail(11, bad_tasks)
    if checked == 0:
        return None, 0, 0
    return pct(len(bad_tasks), checked), len(bad_tasks), checked


def check_bei(tasks, status_date):
    """14. Baseline Execution Index — taches terminees / taches qui
    auraient du l'etre a la date d'etat (selon la baseline)."""
    if status_date is None:
        return None, 0, 0
    real = [t for t in tasks if is_real_task(t)]
    should_be_done = 0
    actually_done = 0
    late_tasks = []
    for t in real:
        bf = t.getBaselineFinish()
        if bf is None or bf.isAfter(status_date):
            continue
        should_be_done += 1
        af = t.getActualFinish()
        if af is not None and not af.isAfter(status_date):
            actually_done += 1
        else:
            late_tasks.append(t)
    record_detail(14, late_tasks)
    if should_be_done == 0:
        return None, actually_done, should_be_done
    return round(actually_done / should_be_done, 2), actually_done, should_be_done


def check_cpli(tasks):
    """13. Critical Path Length Index — approxime ici par
    (duree du chemin critique + marge totale de la tache la plus tardive
    du chemin critique) / duree du chemin critique. Version simplifiee :
    necessite un chemin critique identifiable (getCritical())."""
    critical = [t for t in tasks if t is not None and t.getName() is not None and t.getCritical()]
    if not critical:
        return None, 0
    # duree du chemin critique = somme des durees des taches critiques (approximation)
    cp_length = sum(duration_days(t.getDuration()) for t in critical if not t.getSummary())
    if cp_length == 0:
        return None, len(critical)
    max_float = max((duration_days(t.getTotalSlack()) for t in critical), default=0.0)
    cpli = round((cp_length + max_float) / cp_length, 2)
    record_detail(13, critical)
    return cpli, len(critical)


def verdict(value, threshold, lower_is_better=True):
    if value is None:
        return "N/A"
    if lower_is_better:
        return "OK" if value <= threshold else "A CORRIGER"
    else:
        return "OK" if value >= threshold else "A CORRIGER"


def run_diagnostic(path):
    reader = UniversalProjectReader()
    proj = reader.read(path)
    tasks = list(proj.getTasks())
    status_date = proj.getProjectProperties().getStatusDate()

    # Calendrier du projet : indispensable pour ramener les durees en jours ouvres
    # (les fichiers en heures sinon). Voir duration_days.
    global PROJECT_CALENDAR
    PROJECT_CALENDAR = None
    try:
        PROJECT_CALENDAR = proj.getProjectProperties().getDefaultCalendar()
    except Exception:
        PROJECT_CALENDAR = None

    if COLLECT_DETAILS:
        TASK_DETAILS.clear()
        TASK_DIAG.clear()
        # Vue d'ensemble : le lecteur voit-il des liens dans ce planning ?
        liens = 0
        externes = 0
        for t in tasks:
            try:
                preds = t.getPredecessors()
                liens += len(list(preds)) if preds is not None else 0
            except Exception:
                pass
            try:
                if t.getExternalTask():
                    externes += 1
            except Exception:
                pass
        TASK_DIAG["TOTAL"] = {
            "taches": len(tasks),
            "restantes": len([t for t in tasks if is_remaining(t)]),
            "liens": liens,
            "externes": externes,
        }

    print("=" * 72)
    print(f"DIAGNOSTIC DCMA-14 — {path}")
    if status_date is not None:
        print(f"Date d'etat du projet : {status_date}")
    else:
        print("Date d'etat du projet : NON RENSEIGNEE (plusieurs controles seront N/A)")
    print("=" * 72)

    rows = []

    v, bad, total = check_logic(tasks)
    rows.append(("1. Logic (taches sans lien amont/aval)", f"{v}%", f"{bad}/{total}",
                  verdict(v, THRESHOLDS["logic"])))

    (v_lead, n_lead, t_lead), (v_lag, n_lag, t_lag) = check_leads_lags(tasks)
    rows.append(("2. Leads (lag negatif)", f"{v_lead}%", f"{n_lead}/{t_lead}",
                  verdict(v_lead, THRESHOLDS["leads"])))
    rows.append(("3. Lags (lag positif)", f"{v_lag}%", f"{n_lag}/{t_lag}",
                  verdict(v_lag, THRESHOLDS["lags"])))

    v, n, t = check_fs_relationships(tasks)
    rows.append(("4. Relations Finish-to-Start", f"{v}%", f"{n}/{t}",
                  verdict(v, THRESHOLDS["fs_relationships"], lower_is_better=False)))

    v, n, t = check_hard_constraints(tasks)
    rows.append(("5. Contraintes dures", f"{v}%", f"{n}/{t}",
                  verdict(v, THRESHOLDS["hard_constraints"])))

    v, n, t = check_high_float(tasks)
    rows.append((f"6. Marge totale excessive (>{HIGH_FLOAT_DAYS}j)", f"{v}%", f"{n}/{t}",
                  verdict(v, THRESHOLDS["high_float"])))

    v, n, t = check_negative_float(tasks)
    rows.append(("7. Marge totale negative", f"{v}%", f"{n}/{t}",
                  verdict(v, THRESHOLDS["negative_float"])))

    v, n, t = check_high_duration(tasks)
    rows.append((f"8. Duree excessive (>{HIGH_DURATION_DAYS}j)", f"{v}%", f"{n}/{t}",
                  verdict(v, THRESHOLDS["high_duration"])))

    v, n, t = check_invalid_dates(tasks, status_date)
    rows.append(("9. Dates invalides (vs date d'etat)",
                  f"{v}%" if v is not None else "N/A", f"{n}/{t}" if v is not None else "-",
                  verdict(v, 0.0) if v is not None else "N/A (date d'etat manquante)"))

    v, n, t = check_resources(tasks)
    rows.append(("10. Taches de detail sans ressource", f"{v}%", f"{n}/{t}", "INFO"))

    v, n, t = check_missed_tasks(tasks, status_date)
    rows.append(("11. Taches terminees en retard vs baseline",
                  f"{v}%" if v is not None else "N/A", f"{n}/{t}" if v is not None else "-",
                  verdict(v, 5.0) if v is not None else "N/A (baseline manquante)"))

    rows.append(("12. Test chemin critique (CPTest)", "-", "-",
                  "N/A (verification manuelle requise)"))

    cpli, n_crit = check_cpli(tasks)
    rows.append(("13. Critical Path Length Index (CPLI)",
                  f"{cpli}" if cpli is not None else "N/A", f"{n_crit} taches critiques",
                  ("OK" if cpli is not None and 0.95 <= cpli <= 1.44 else
                   ("N/A (aucune tache marquee critique)" if cpli is None else "A CORRIGER"))))

    bei, done, should = check_bei(tasks, status_date)
    rows.append(("14. Baseline Execution Index (BEI)",
                  f"{bei}" if bei is not None else "N/A", f"{done}/{should}",
                  ("OK" if bei is not None and bei >= 0.95 else
                   ("N/A (baseline/date d'etat manquante)" if bei is None else "A CORRIGER"))))

    name_w = max(len(r[0]) for r in rows) + 2
    for name, val, detail, verd in rows:
        print(f"{name:<{name_w}} {val:>10}  {detail:<15}  {verd}")

    print()
    n_issues = sum(1 for r in rows if r[3] == "A CORRIGER")
    n_na = sum(1 for r in rows if "N/A" in r[3])
    print(f"Resume : {n_issues} controle(s) en ecart, {n_na} non applicable(s) faute de donnees, "
          f"{len(rows) - n_issues - n_na} OK.")
    print()

    # Bloc technique ajoute uniquement avec --details : il suit la ligne
    # « Resume », que les lecteurs existants utilisent comme fin de sortie.
    if COLLECT_DETAILS:
        print_details()


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if "--details" in arguments:
        COLLECT_DETAILS = True
        arguments = [a for a in arguments if a != "--details"]
    if not arguments:
        print(__doc__)
        sys.exit(1)
    for path in arguments:
        run_diagnostic(path)
