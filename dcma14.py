#!/usr/bin/env python3
"""
Diagnostic qualite DCMA-14 pour plannings MS Project (.mpp, .mspdi/.xml, etc.)
Usage: python3 dcma14.py <fichier1> [fichier2 ...]

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


def is_real_task(t):
    """Exclut les taches recapitulatives (summary) et les jalons pour
    certains controles ou l'on ne veut que les taches de detail."""
    return t is not None and t.getName() is not None and not t.getSummary()


def pct(n, d):
    return round(100.0 * n / d, 2) if d else 0.0


def duration_days(d):
    if d is None:
        return 0.0
    try:
        return float(d.getDuration())
    except Exception:
        return 0.0


def check_logic(tasks):
    """1. Logic — % de taches sans predecesseur ni successeur, en excluant
    la tache de debut de projet (pas de predecesseur attendu) et la tache
    de fin de projet (pas de successeur attendu) -- une seule de chaque,
    identifiees par la date de debut/fin la plus extreme."""
    real = [t for t in tasks if is_real_task(t)]
    if not real:
        return 0.0, 0, 0

    no_pred = [t for t in real if t.getPredecessors() is None or t.getPredecessors().size() == 0]
    no_succ = [t for t in real if t.getSuccessors() is None or t.getSuccessors().size() == 0]

    project_start = min(no_pred, key=lambda t: t.getStart() or 0, default=None) if no_pred else None
    project_end = max(no_succ, key=lambda t: t.getFinish() or 0, default=None) if no_succ else None

    bad_tasks = set()
    for t in no_pred:
        if t is not project_start:
            bad_tasks.add(t.getUniqueID())
    for t in no_succ:
        if t is not project_end:
            bad_tasks.add(t.getUniqueID())

    return pct(len(bad_tasks), len(real)), len(bad_tasks), len(real)


def check_leads_lags(tasks):
    """2. Leads (lag negatif) / 3. Lags (lag positif) sur les relations."""
    total_rel = 0
    leads = 0
    lags = 0
    for t in tasks:
        if t is None or t.getPredecessors() is None:
            continue
        for rel in t.getPredecessors():
            total_rel += 1
            lag = rel.getLag()
            lag_val = duration_days(lag)
            if lag_val < 0:
                leads += 1
            elif lag_val > 0:
                lags += 1
    return (pct(leads, total_rel), leads, total_rel), (pct(lags, total_rel), lags, total_rel)


def check_fs_relationships(tasks):
    """4. Relationship types — % de relations Finish-to-Start."""
    total_rel = 0
    fs = 0
    for t in tasks:
        if t is None or t.getPredecessors() is None:
            continue
        for rel in t.getPredecessors():
            total_rel += 1
            if rel.getType() == RelationType.FINISH_START:
                fs += 1
    return pct(fs, total_rel), fs, total_rel


def check_hard_constraints(tasks):
    """5. Hard constraints — % de taches avec contrainte dure (MSO/MFO/SO/FO)."""
    real = [t for t in tasks if is_real_task(t)]
    if not real:
        return 0.0, 0, 0
    bad = sum(1 for t in real if t.getConstraintType() in HARD_CONSTRAINTS)
    return pct(bad, len(real)), bad, len(real)


def check_high_float(tasks):
    """6. High float — % de taches avec marge totale > 44 jours."""
    real = [t for t in tasks if is_real_task(t)]
    if not real:
        return 0.0, 0, 0
    bad = sum(1 for t in real if duration_days(t.getTotalSlack()) > HIGH_FLOAT_DAYS)
    return pct(bad, len(real)), bad, len(real)


def check_negative_float(tasks):
    """7. Negative float — % de taches avec marge totale negative."""
    real = [t for t in tasks if is_real_task(t)]
    if not real:
        return 0.0, 0, 0
    bad = sum(1 for t in real if duration_days(t.getTotalSlack()) < 0)
    return pct(bad, len(real)), bad, len(real)


def check_high_duration(tasks):
    """8. High duration — % de taches de detail avec duree > 44 jours."""
    real = [t for t in tasks if is_real_task(t) and not t.getMilestone()]
    if not real:
        return 0.0, 0, 0
    bad = sum(1 for t in real if duration_days(t.getDuration()) > HIGH_DURATION_DAYS)
    return pct(bad, len(real)), bad, len(real)


def check_invalid_dates(tasks, status_date):
    """9. Invalid dates — dates prevues avant la date d'etat, ou dates
    reelles apres la date d'etat. Necessite une date d'etat renseignee."""
    if status_date is None:
        return None, 0, 0
    real = [t for t in tasks if is_real_task(t)]
    bad = 0
    for t in real:
        af = t.getActualFinish()
        if af is not None and af.isAfter(status_date):
            bad += 1
            continue
        finish = t.getFinish()
        pct_complete = t.getPercentageComplete()
        incomplete = pct_complete is None or float(pct_complete) < 100.0
        if incomplete and finish is not None and finish.isBefore(status_date):
            bad += 1
    return pct(bad, len(real)), bad, len(real)


def check_resources(tasks):
    """10. Resources — % de taches de detail sans ressource affectee
    (indicatif, pas de seuil DCMA strict)."""
    real = [t for t in tasks if is_real_task(t) and not t.getMilestone()]
    if not real:
        return 0.0, 0, 0
    bad = 0
    for t in real:
        names = t.getResourceNames()
        if names is None or str(names).strip() == "":
            bad += 1
    return pct(bad, len(real)), bad, len(real)


def check_missed_tasks(tasks, status_date):
    """11. Missed tasks — % de taches terminees apres leur date de fin
    de reference (baseline). Necessite une baseline renseignee."""
    real = [t for t in tasks if is_real_task(t)]
    checked = 0
    bad = 0
    for t in real:
        bf = t.getBaselineFinish()
        af = t.getActualFinish()
        if bf is None or af is None:
            continue
        checked += 1
        if af.isAfter(bf):
            bad += 1
    if checked == 0:
        return None, 0, 0
    return pct(bad, checked), bad, checked


def check_bei(tasks, status_date):
    """14. Baseline Execution Index — taches terminees / taches qui
    auraient du l'etre a la date d'etat (selon la baseline)."""
    if status_date is None:
        return None, 0, 0
    real = [t for t in tasks if is_real_task(t)]
    should_be_done = 0
    actually_done = 0
    for t in real:
        bf = t.getBaselineFinish()
        if bf is None or bf.isAfter(status_date):
            continue
        should_be_done += 1
        af = t.getActualFinish()
        if af is not None and not af.isAfter(status_date):
            actually_done += 1
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        run_diagnostic(path)
