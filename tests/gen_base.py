#!/usr/bin/env python3
"""Genere templates/base.html : CSS de la maquette repris VERBATIM + coquille serveur.

Usage : python3 gen_base.py <maquette.html> <templates/base.html>
"""
import re
import sys
from pathlib import Path

mockup = Path(sys.argv[1]).read_text(encoding="utf-8")
dest = Path(sys.argv[2])
css_verbatim = re.search(r"<style>\n(.*?)\n</style>", mockup, re.S).group(1)

SERVEUR_CSS = """<style>
/* ============================================================
   Rendu serveur — ajouts nécessaires, hors maquette
   ------------------------------------------------------------
   La maquette est une page unique dont les deux vues sont basculées
   par deux boutons radio (main .view{display:none} + :checked) : ici
   chaque écran est une page à part entière, sans JavaScript.
   ============================================================ */
main .view{display:block}
[hidden]{display:none !important}

/* Zone de dépôt : l'input natif couvre la zone en pointillés (clic,
   clavier et glisser-déposer natifs, sans JavaScript). */
.dropzone{position:relative;display:block}
.dropzone input[type=file]{
  position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;
}

/* Bandes du score de conformité : la maquette ne montre que le cas
   « fragile » (60/100) ; les trois autres bandes reprennent les couleurs
   des jetons et des repères du score. */
.score-left .k-label{
  font-size:11.5px;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;color:#93370D;
}
.score-left.bd-ok .k-label{color:#067647}
.score-left.bd-mid .k-label{color:#4D7C0F}
.score-left.bd-ko .k-label{color:#B42318}
.score-left.bd-ok{background:linear-gradient(160deg,#ECFDF3 0%,#F6FEF9 100%)}
.score-left.bd-mid{background:linear-gradient(160deg,#F7FEE7 0%,#ECFDF3 100%)}
.score-left.bd-ko{background:linear-gradient(160deg,#FEF3F2 0%,#FEE4E2 100%)}
.score-fill.bd-ok{background:linear-gradient(90deg,#84CC16,#12B76A)}
.score-fill.bd-mid{background:linear-gradient(90deg,#F79009,#84CC16)}
.score-fill.bd-ko{background:linear-gradient(90deg,#F04438,#D92D20)}
.band-badge.bd-ok{background:rgba(18,183,106,.12);color:#067647}
.band-badge.bd-ok .bdot{background:var(--ok)}
.band-badge.bd-mid{background:rgba(132,204,22,.16);color:#4D7C0F}
.band-badge.bd-mid .bdot{background:#84CC16}
.band-badge.bd-ko{background:rgba(240,68,56,.12);color:#B42318}
.band-badge.bd-ko .bdot{background:var(--ko)}

/* Criticité : la maquette montre des identifiants courts (T-0149) ; le rendu
   serveur affiche le nom des tâches, tronqué s'il dépasse la colonne. */
.crit-row{grid-template-columns:200px 1fr 62px}
.crit-id{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:640px){
  .crit-row{grid-template-columns:120px 1fr 54px}
}

/* Sortie brute repliée (diagnostic) : composant de la maquette, contenu
   technique propre au rendu serveur. */
.opt-body pre{
  font-family:var(--mono);font-size:11.5px;line-height:1.5;color:var(--text-2);
  white-space:pre-wrap;overflow-x:auto;max-height:340px;margin-top:6px;
}

/* Page d'attente de l'analyse (affichée par le JavaScript au moment de
   l'envoi) et bouton d'arrêt. */
.overlay{
  position:fixed;inset:0;z-index:100;padding:24px;
  background:rgba(16,24,40,.55);
  display:flex;align-items:center;justify-content:center;
}
.overlay-card{max-width:460px;width:100%;text-align:center}
.overlay-spin{
  width:34px;height:34px;border-radius:50%;margin:0 auto 12px;
  border:3px solid var(--border);border-top-color:var(--primary);
  animation:overlay-spin .8s linear infinite;
}
@keyframes overlay-spin{to{transform:rotate(360deg)}}
.overlay-title{font-weight:700;font-size:16px;margin-bottom:6px}
.overlay-step{font-size:12.5px;color:var(--text-3);margin-top:4px}
.is-empty .dot{background:var(--ko);box-shadow:0 0 0 3px var(--ko-bg)}

/* Éléments <a> portant une classe prévue pour un <label> ou un <button>. */
.back-link{text-decoration:none}
.alert-action{text-decoration:none}

/* Impression : la page de résultat doit rester lisible sur papier
   (bouton « Imprimer / PDF » de la maquette). */
@media print{
  header.top,.subnav,.head-actions,.no-print{display:none !important}
  body{background:#fff}
  footer{margin-top:24px}
  .card,.score-hero,.dur-card,.kpi{box-shadow:none}
  .table-scroll{overflow:visible}
  .card,.table-card,.crit-list,.dur-card{break-inside:avoid}
}
</style>"""

body = """<!doctype html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>{% block title %}MPPCR — MS Project Check &amp; Risk{% endblock %}</title>
<style>
__CSS__
</style>
__SERVEUR__
</head>
<body>

<!-- ================= HEADER ================= -->
<header class="top">
  <div class="container top-inner">
    <a class="brand" href="{{ url_for('index') }}"{% block brand_guard %}{% endblock %}>
      <span class="brand-mark">M</span>
      <span>
        <span class="brand-name">MPPCR</span><br>
        <span class="brand-sub">MS Project Check &amp; Risk</span>
      </span>
    </a>
    <span class="version-tag">{% if app_version %}{{ app_version }}{% else %}version inconnue{% endif %}</span>

    <nav class="stepper" aria-label="Étapes de l’analyse">
      {% block stepper %}
        <span class="step active" aria-current="step"><span class="n">1</span><span class="s-lab">Nouvelle analyse</span></span>
        <span class="step-sep">&rsaquo;</span>
        <span class="step" title="Lancez une analyse pour obtenir ce résultat"><span class="n">2</span><span class="s-lab">Diagnostic DCMA-14</span></span>
        <span class="step-sep">&rsaquo;</span>
        <span class="step" title="Lancez une analyse pour obtenir ce résultat"><span class="n">3</span><span class="s-lab">Monte Carlo</span></span>
      {% endblock %}
    </nav>
  </div>
</header>

<main>
  {% block content %}{% endblock %}
</main>

<footer>
  <div class="container foot-inner">
    <span><b style="color:var(--text-2)">MPPCR</b>{% if app_version %} {{ app_version }}{% endif %} · MS Project Check &amp; Risk</span>
    <span class="sep">·</span>
    <span>Aucun planning conservé après analyse</span>
    <span class="right">
      <span class="legend">
        <span class="li"><span class="lg" style="background:var(--ok)"></span>Conforme</span>
        <span class="li"><span class="lg" style="background:var(--ko)"></span>À corriger</span>
        <span class="li"><span class="lg" style="background:var(--warn)"></span>Attention</span>
      </span>
    </span>
  </div>
</footer>

</body>
</html>
"""

dest.write_text(body.replace("__CSS__", css_verbatim).replace("__SERVEUR__", SERVEUR_CSS),
                encoding="utf-8")
print(f"{dest} écrit")
