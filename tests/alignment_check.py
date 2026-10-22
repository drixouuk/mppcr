#!/usr/bin/env python3
"""Vérifie l'alignement des templates Jinja2 sur la maquette mppcr-redesign.html (v2).

Contrôles :
  1. le premier bloc <style> de base.html est la copie VERBATIM de la CSS de la
     maquette (et non une réécriture qui lui ressemble) ;
  2. aucun jeton CSS n'est inventé hors maquette ;
  3. toute classe utilisée dans les templates existe dans la CSS de la maquette,
     ou figure dans la liste explicite des ajouts propres au rendu serveur ;
  4. les composants structurants de la maquette sont présents dans base.html ;
  5. les écrans reprennent la structure de la maquette (formulaire, résultat,
     erreur), sans contenu de démonstration ni bascule de vue par boutons radio.

Usage : python3 alignment_check.py <dossier_templates> <chemin_maquette>
"""
import re
import sys
from pathlib import Path

TEMPLATES = Path(sys.argv[1] if len(sys.argv) > 1 else "templates")
MOCKUP = Path(sys.argv[2] if len(sys.argv) > 2 else "mppcr-redesign.html")

# Classes absentes de la maquette, ajoutées par le rendu serveur :
#   - la page d'attente et le bouton d'arrêt de l'analyse en cours (JavaScript) ;
#   - l'état « quota épuisé » de la pastille du panneau d'appel à l'action.
# Les bandes de score (bd-*) ne figurent pas ici : elles n'apparaissent que via
# des expressions Jinja (`{{ s.band_css }}`), donc jamais comme classe littérale.
ADDED = {
    "overlay", "overlay-card", "overlay-spin", "overlay-title", "overlay-step",
    "is-empty",
    # v1.15 : graphiques de distribution rendus côté serveur (SVG).
    "charts-grid", "chart-card", "chart", "chart-grid", "chart-axis", "chart-bar",
    "chart-col", "chart-hit", "chart-mark", "chart-line", "chart-area", "chart-dot",
    "chart-pt", "chart-table",
    # v1.15 : synthèse, sous-navigation fixe, dates sous les cartes, dépôt compact.
    "synth", "synth-item", "synth-lab", "synth-val", "synth-sub", "synth-act",
    "subnav-fixe", "d-date", "dz-compact", "crit-group",
}

FAILS, COUNT = [], [0]


def check(label, ok, detail=""):
    COUNT[0] += 1
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(label)


def styles(text):
    """Blocs <style> d'un document, dans l'ordre."""
    return re.findall(r"<style>(.*?)</style>", text, re.S)


def classes(css):
    out = set()
    for sel in re.findall(r"([^{}]+)\{", css):
        for c in re.findall(r"\.([a-zA-Z][a-zA-Z0-9_-]*)", sel):
            out.add(c)
    return out


def tokens(css):
    """Jetons déclarés dans la règle :root (hors commentaires)."""
    bloc = re.search(r":root\{(.*?)\}", css, re.S)
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;}]+)", bloc.group(1) if bloc else ""))


def used_classes(text):
    out = set()
    for attr in re.findall(r'class="([^"]*)"', text):
        # Neutralise les expressions Jinja avant de découper les classes.
        attr = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr, flags=re.S)
        for tok in attr.split():
            # Une classe interrompue par une expression Jinja (« alert-{{ … }} »)
            # est tronquée : elle n'est pas jugée ici.
            if tok.endswith("-"):
                continue
            if not re.search(r"[{}%:'\"(),.]", tok):
                out.add(tok)
    return out


mockup_html = MOCKUP.read_text(encoding="utf-8")
base_html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
idx = (TEMPLATES / "index.html").read_text(encoding="utf-8")
res = (TEMPLATES / "result.html").read_text(encoding="utf-8")
err = (TEMPLATES / "error.html").read_text(encoding="utf-8")

mock_styles = styles(mockup_html)
base_styles = styles(base_html)

print("=== 1. CSS de la maquette reprise à l'identique ===")
check("la maquette contient un bloc <style>", len(mock_styles) == 1, f"={len(mock_styles)}")
check("base.html contient la CSS maquette puis les ajouts du rendu serveur",
      len(base_styles) == 2, f"={len(base_styles)}")
identical = bool(base_styles) and base_styles[0].strip("\n") == mock_styles[0].strip("\n")
check("le premier bloc <style> est la copie verbatim de la CSS de la maquette",
      identical,
      f"{len(base_styles[0]) if base_styles else 0} octets contre "
      f"{len(mock_styles[0]) if mock_styles else 0}")
mock_css = mock_styles[0] if mock_styles else ""
base_css = base_styles[0] if base_styles else ""

print("\n=== 2. Jetons CSS ===")
mock_tok, base_tok = tokens(mock_css), tokens(base_css)
missing = {k: v for k, v in mock_tok.items() if base_tok.get(k) != v}
check(f"les {len(mock_tok)} jetons de la maquette sont repris à l'identique",
      not missing, f"écarts : {missing}")
extra = {k: v for k, v in base_tok.items() if k not in mock_tok}
check("aucun jeton inventé hors maquette", not extra, f"en trop : {extra}")

print("\n=== 3. Classes CSS ===")
mock_cls = classes(mock_css)
allowed = mock_cls | ADDED
used = set()
for text in (base_html, idx, res, err):
    used |= used_classes(text)
unknown = sorted(used - allowed)
check("toutes les classes utilisées viennent de la maquette (ou sont documentées)",
      not unknown, f"non documentées : {unknown}")
check("les ajouts du rendu serveur sont réellement absents de la maquette",
      not (ADDED & mock_cls), f"déjà dans la maquette : {sorted(ADDED & mock_cls)}")

print("\n=== 4. Composants de la maquette ===")
for cls in ["top", "top-inner", "brand", "brand-mark", "brand-name", "brand-sub",
            "version-tag", "stepper", "step", "step-sep", "view", "container",
            "page-head", "form-grid", "card", "card-pad", "step-label", "step-num",
            "dropzone", "dz-icon", "dz-title", "dz-hint", "dz-file", "privacy-note",
            "type-grid", "type-card", "type-title", "radio", "type-desc",
            "opt", "opt-body", "check-row", "lbl", "sub", "field", "field-row",
            "cta-panel", "cta-summary", "btn", "btn-primary", "btn-ghost", "btn-sm",
            "quota", "dot", "confid",
            "result-head", "back-link", "file-chip", "ext", "meta-chip", "meta-chips",
            "head-actions", "score-hero", "score-hero-inner", "score-left", "k-label",
            "score-num", "band-badge", "bdot", "score-right", "score-bar-wrap",
            "score-bar", "score-fill", "score-ticks", "tick", "score-formula",
            "legend", "li", "lg",
            "kpis", "kpi", "k-ok", "k-ko", "k-na", "k-info", "k-val", "k-sub",
            "alert", "alert-danger", "alert-warn", "alert-info", "a-icon", "alert-action",
            "sec-title", "count", "right", "subnav",
            "prio", "prio-rank", "pr-ko", "pr-na", "pr-warn", "prio-name", "prio-metric",
            "prio-chev", "prio-body", "reco", "task-ids", "task-id",
            "badge", "b-ok", "b-ko", "b-na", "b-info",
            "table-card", "table-scroll", "ctrl", "ctrl-name", "ctrl-result",
            "ctrl-target", "ctrl-action", "frac", "row-ko",
            "mc-durations", "dur-card", "d-label", "d-val", "d-delta", "d-ref", "d-up",
            "d-dn", "ref", "reco", "axis-card", "axis", "axis-fill", "marker", "m-val",
            "m-lab", "m-cpm", "m-p80", "axis-caption", "mc-meta",
            "callout", "c-icon", "crit-list", "crit-row", "crit-id", "crit-bar",
            "crit-fill", "hot", "crit-val", "crit-note",
            "mono", "small", "muted", "foot-inner", "sep"]:
    check(f"classe « {cls} » reprise de la maquette",
          cls in base_css or cls in ADDED, "absente de base.html")

print("\n=== 5. Structure des écrans ===")
# Le contenu de démonstration de la maquette vit aussi dans des commentaires
# CSS : seules les parties balisées (après le dernier <style>) sont comparées.
markup = "".join(texte.split("</style>")[-1] for texte in (base_html, idx, res, err))
check("maquette : pas de contenu de démonstration recopié dans les templates",
      not any(x in markup for x in
              ("CapteursNew3", "T-0149", "v2.0.0-maquette", "1 437,2", "PROJET-X")))
check("maquette : bascule de vue par boutons radio non reprise",
      'name="tab"' not in markup and 'id="view-toggle"' not in markup
      and 'id="tab-form"' not in markup)

check("en-tête : marque, version et fil d'étapes",
      'class="brand"' in base_html and 'class="brand-mark">M<' in base_html
      and 'class="version-tag"' in base_html and 'class="stepper"' in base_html)
check("en-tête : version réelle de l'application (jamais codée en dur)",
      "{{ app_version }}" in base_html and "v1." not in base_html.split("</style>")[1])
check("pied de page : version, mention de non-conservation et légende",
      "Aucun planning conservé après analyse" in base_html
      and "MPPCR</b>" in base_html and "foot-inner" in base_html
      and base_html.count('class="lg"') == 3)

check("formulaire : zone de dépôt avec input natif superposé",
      'class="dropzone"' in idx and 'type="file" id="mpp_file"' in idx
      and "required" in idx)
check("formulaire : trois cartes de type d'analyse de la maquette",
      idx.count('class="type-card"') == 3
      and "Diagnostic qualité DCMA-14" in idx and "Simulation Monte Carlo" in idx
      and "Qualité DCMA puis risque planning. Recommandé." in idx)
# La maquette proposait deux cases (CSV, Excel) et renvoyait à l'impression pour
# le PDF ; depuis la v1.15, un rapport PDF construit côté serveur est une
# troisième case, du même composant (check-row).
check("formulaire : volet « Export des résultats (facultatif) » avec trois cases",
      "Export des résultats" in idx and idx.count('name="export" id=') == 3
      and 'value="csv"' in idx and 'value="xlsx"' in idx and 'value="pdf"' in idx)
check("formulaire : volet des options Monte Carlo repliable",
      idx.count('<details class="opt">') == 2 and "Options avancées Monte Carlo" in idx)
check("formulaire : nombre de simulations en liste déroulante (1 000 / 5 000 / 10 000)",
      '<select id="sims" name="sims">' in idx
      and all(f'value="{v}"' in idx for v in (1000, 5000, 10000))
      and '<option value="5000" selected>' in idx)
check("formulaire : facteurs PERT en liste déroulante (0,8/1,5 et 0,7/1,8)",
      '<select id="factors" name="factors">' in idx
      and 'value="0.8/1.5"' in idx and 'value="0.7/1.8"' in idx
      and '<option value="0.8/1.5" selected>' in idx)
check("formulaire : estimations 3 points en CSV facultatif",
      'id="est" name="estimates_file"' in idx and 'accept=".csv"' in idx
      and "task_id,optimistic,most_likely,pessimistic" in idx)
check("formulaire : panneau d'appel à l'action collant",
      'class="cta-panel"' in idx and 'class="cta-summary"' in idx
      and 'class="btn btn-primary"' in idx and 'type="submit"' in idx)
check("formulaire : jeton d'annulation et page d'attente masquée",
      'name="client_token"' in idx and 'id="waiting" hidden' in idx
      and 'id="cancel-btn"' in idx)

check("résultat : en-tête avec retour, puce fichier, date d'état et faits",
      'class="result-head"' in res and 'class="back-link"' in res
      and 'class="file-chip"' in res and 'class="ext"' in res
      and 'class="meta-chip"' in res and 'class="meta-chips"' in res)
check("résultat : actions d'en-tête (exports + impression)",
      'class="head-actions"' in res and 'id="print-btn"' in res
      and "Imprimer la page" in res and 'download="{{ d.filename }}"' in res)
check("résultat : héros de score de la maquette",
      'class="card score-hero"' in res and "score-hero-inner" in res
      and 'class="score-left' in res and 'class="score-num"' in res
      and 'class="band-badge' in res and 'class="score-bar"' in res
      and 'class="score-ticks"' in res and 'class="score-formula"' in res)
check("résultat : repères et bandes du barème v1",
      res.count('class="tick"') == 3 and "55–69 fragile" in res and "≥ 85 conforme" in res)
check("résultat : méthode de calcul du score en volet dépliable",
      'id="methode-score"' in res and "Comment ce score est calculé" in res)
check("résultat : cinq cartes KPI de la maquette",
      all(f'class="kpi {k}"' in res for k in ("k-ok", "k-ko", "k-na", "k-info"))
      and 'class="kpis"' in res and res.count('class="k-sub"') == 5)
check("résultat : bandeaux d'alerte de la maquette",
      'class="alert alert-danger"' in res and 'class="alert alert-warn"' in res
      and 'class="alert alert-info"' in res and 'class="alert-action"' in res)
check("résultat : sous-navigation par ancres (diag / mc)",
      'class="subnav' in res and "('diag', " in res and "('mc', " in res
      and 'href="#{{ ancre }}"' in res)
check("résultat : priorités en volets dépliables de la maquette",
      '<details class="prio"' in res and 'class="prio-rank' in res
      and 'class="prio-name"' in res and 'class="prio-metric"' in res
      and 'class="prio-chev"' in res and 'class="prio-body"' in res
      and 'class="reco"' in res and 'class="task-ids"' in res
      and 'class="badge {{ item.css' in res)
check("résultat : tableau des contrôles à cinq colonnes (maquette v2)",
      'class="card table-card"' in res and 'class="table-scroll"' in res
      and 'class="ctrl"' in res and 'class="ctrl-name"' in res
      and 'class="ctrl-result"' in res and 'class="ctrl-target"' in res
      and 'class="ctrl-action"' in res and 'class="row-ko"' in res
      and "Tâches concernées (N°)" not in res)
check("résultat : cartes de durée Monte Carlo (dont P80 mis en avant)",
      'class="mc-durations"' in res and 'class="dur-card' in res
      and "ref" in res and "reco" in res and 'class="d-delta' in res)
check("résultat : axe des percentiles avec repères",
      'class="card axis-card"' in res and 'class="axis"' in res
      and 'class="axis-fill"' in res and 'class="marker' in res
      and 'class="axis-caption"' in res and 'class="mc-meta"' in res)
check("résultat : lecture décisionnelle en encart",
      'class="callout"' in res and "Lecture décisionnelle" in res
      and 'class="c-icon"' in res)
check("résultat : indice de criticité par tâche",
      'class="card crit-list"' in res and 'class="crit-row"' in res
      and 'class="crit-id"' in res and 'class="crit-bar"' in res
      and 'class="crit-fill' in res and 'class="crit-val"' in res
      and 'class="crit-note"' in res)
check("résultat : sortie brute conservée en volet replié (rendu serveur)",
      "Sortie brute" in res and "<pre>{{ p.raw }}</pre>" in res)
check("résultat : le fil d'étapes reflète l'analyse demandée",
      "has_dcma" in res and "has_mc" in res and 'class="step active"' in res
      and "' done' if has_dcma" in res and "%} aria-current" in res)
check("résultat : garde-fou de clic accidentel, en amélioration progressive",
      "window.confirm" in res and "ev.preventDefault()" in res
      and 'data-quit' in res)

check("formulaire : champ « Répartition du risque par lot » (niveau 1 à 3)",
      '<select id="macro" name="macro_level">' in idx
      and all(f'value="{n}"' in idx for n in (1, 2, 3))
      and "Aucune — résultat global seulement" in idx)
check("résultat : tableau par lot rendu avec les composants de la maquette",
      "Répartition du risque par lot" in res
      and 'class="card table-card"' in res and 'class="ctrl-name"' in res
      and "Écart P50 vs planifié" in res and "Criticité" in res)
check("erreur : bandeau d'alerte et action de retour",
      'class="alert alert-' in err and 'class="btn btn-primary"' in err
      and "Analyser un planning" in err)
check("aucune police ni ressource externe",
      "googleapis" not in base_html
      and "http://" not in mock_css.replace("http://www.w3.org", "")
      and "http://" not in base_css.replace("http://www.w3.org", ""))

print("\n" + "=" * 62)
print(f"{COUNT[0] - len(FAILS)}/{COUNT[0]} vérifications d'alignement OK")
if FAILS:
    print("ÉCHECS : " + ", ".join(FAILS))
    sys.exit(1)
print("Alignement maquette v2 : conforme.")
