#!/usr/bin/env python3
"""
Rapport PDF de MPPCR : une page de synthèse, à joindre tel quel à un comité,
puis les annexes (détail des contrôles, tâches concernées, criticité,
sensibilité, distribution, comparaison).

Le rapport est construit à partir des mêmes structures que la page de résultat
(sorties analysées par app.py) : il ne relance aucun calcul. Les libellés et la
version sont passés par l'appelant, ce module ne dépend pas d'app.py.

Police : Helvetica (aucun fichier de police à embarquer). Son jeu WinAnsi ne
contient ni « ≤ » ni « ≥ » ni flèches : ces caractères sont transcrits.
"""

import io
import re
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# Jetons de la maquette v2 (templates/base.html).
TEXTE = colors.HexColor("#101828")
TEXTE_2 = colors.HexColor("#475467")
TEXTE_3 = colors.HexColor("#667085")
BORDURE = colors.HexColor("#E4E7EC")
SURFACE_2 = colors.HexColor("#F9FAFB")
PRIMAIRE = colors.HexColor("#2E5BFF")
OK = colors.HexColor("#12B76A")
KO = colors.HexColor("#F04438")
ATTENTION = colors.HexColor("#DC6803")
NA = colors.HexColor("#475467")
COULEUR_VERDICT = {"ok": OK, "ko": KO, "na": NA, "info": PRIMAIRE}
COULEUR_BANDE = {"b-ok": OK, "b-info": PRIMAIRE, "b-warn": ATTENTION, "b-ko": KO, "b-na": NA}

# Caractères absents du jeu WinAnsi de Helvetica.
TRANSCRIPTION = str.maketrans({
    "≤": "<=", "≥": ">=", "→": "->", "←": "<-", "≈": "~", "×": "x", "−": "-",
    " ": " ", " ": " ",
})

STYLES = {
    "titre": ParagraphStyle("titre", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=TEXTE),
    "sous_titre": ParagraphStyle("sous_titre", fontName="Helvetica", fontSize=9, leading=12, textColor=TEXTE_3),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=TEXTE,
                         spaceBefore=10, spaceAfter=5),
    "texte": ParagraphStyle("texte", fontName="Helvetica", fontSize=9, leading=12, textColor=TEXTE_2,
                            alignment=TA_LEFT),
    "petit": ParagraphStyle("petit", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=TEXTE_3),
    "cellule": ParagraphStyle("cellule", fontName="Helvetica", fontSize=8, leading=10, textColor=TEXTE),
    "cellule_gras": ParagraphStyle("cellule_gras", fontName="Helvetica-Bold", fontSize=8, leading=10,
                                   textColor=TEXTE),
    "chiffre": ParagraphStyle("chiffre", fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=TEXTE),
    "etiquette": ParagraphStyle("etiquette", fontName="Helvetica-Bold", fontSize=7, leading=9,
                                textColor=TEXTE_3),
}


def t(texte):
    """Texte prêt pour un Paragraph : transcrit et échappé (noms de tâches)."""
    return escape(str(texte if texte is not None else "").translate(TRANSCRIPTION))


def virgule(texte):
    """Décimales à la française dans un texte venant des scripts (« 33.33% »)."""
    return re.sub(r"(\d)\.(\d)", r"\1,\2", str(texte if texte is not None else ""))


def fr(valeur, decimales=1):
    if valeur is None:
        return "—"
    return f"{valeur:.{decimales}f}".replace(".", ",")


def p(texte, style="texte"):
    return Paragraph(texte, STYLES[style])


def tableau(lignes, largeurs, entete=True, zebre=True):
    table = Table(lignes, colWidths=largeurs, repeatRows=1 if entete else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), TEXTE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDURE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if entete:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
                  ("TEXTCOLOR", (0, 0), (-1, 0), TEXTE_3),
                  ("BACKGROUND", (0, 0), (-1, 0), SURFACE_2)]
    if zebre:
        style += [("ROWBACKGROUNDS", (0, 1 if entete else 0), (-1, -1), [colors.white, SURFACE_2])]
    table.setStyle(TableStyle(style))
    return table


def tuiles(elements, largeur_totale):
    """Rangée de tuiles « étiquette / valeur / complément » (cartes de la maquette)."""
    cellules = []
    for etiquette, valeur, complement, couleur in elements:
        cellules.append([
            p(t(etiquette).upper(), "etiquette"),
            Paragraph(f'<font color="{(couleur or TEXTE).hexval().replace("0x", "#")}">{valeur}</font>',
                      STYLES["chiffre"]),
            p(t(complement), "petit"),
        ])
    largeur = largeur_totale / max(len(cellules), 1)
    table = Table([cellules], colWidths=[largeur] * len(cellules))
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDURE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDURE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def histogramme(classes, metriques, largeur, hauteur=48 * mm):
    """Histogramme des durées simulées (colonnes en couleur primaire, repères P50 /
    P80 / P90), même lecture que le graphique de la page."""
    dessin = Drawing(largeur, hauteur)
    total = sum(c[2] for c in classes) or 1
    gauche, bas, haut = 30, 18, hauteur - 8
    utile = largeur - gauche - 6
    x_min, x_max = classes[0][0], classes[-1][1]
    effectif_max = max(c[2] for c in classes) or 1
    pas = utile / len(classes)
    for fraction in (0.5, 1.0):
        y = bas + fraction * (haut - bas)
        dessin.add(Line(gauche, y, gauche + utile, y, strokeColor=BORDURE, strokeWidth=0.5))
        dessin.add(String(gauche - 4, y - 3, str(int(effectif_max * fraction)), fontName="Helvetica",
                          fontSize=6.5, fillColor=TEXTE_3, textAnchor="end"))
    for i, (_, _, effectif) in enumerate(classes):
        h = effectif / effectif_max * (haut - bas)
        dessin.add(Rect(gauche + i * pas + 1, bas, pas - 2, h, fillColor=PRIMAIRE, strokeColor=None))
    dessin.add(Line(gauche, bas, gauche + utile, bas, strokeColor=TEXTE_3, strokeWidth=0.5))
    for valeur in (x_min, (x_min + x_max) / 2, x_max):
        x = gauche + (valeur - x_min) / (x_max - x_min) * utile
        dessin.add(String(x, bas - 10, f"{fr(valeur)} j", fontName="Helvetica", fontSize=6.5,
                          fillColor=TEXTE_3, textAnchor="middle"))
    for rang, cle in enumerate(("p50", "p80", "p90")):
        valeur = metriques.get(cle)
        if valeur is None or not x_min <= valeur <= x_max:
            continue
        x = gauche + (valeur - x_min) / (x_max - x_min) * utile
        dessin.add(Line(x, bas, x, haut, strokeColor=TEXTE_3, strokeWidth=0.6))
        dessin.add(String(x + 2, haut - 7 - rang * 8, cle.upper(), fontName="Helvetica-Bold", fontSize=6.5,
                          fillColor=TEXTE_2))
    dessin.add(String(gauche + utile, haut + 1, f"{total} tirages", fontName="Helvetica", fontSize=6.5,
                      fillColor=TEXTE_3, textAnchor="end"))
    return dessin


def construire(results, filename, comparaison=None, contexte=None):
    """PDF (octets) du rapport. `contexte` : labels, verdicts, version, date,
    actions (libellés de contrôle, de verdict, version de l'application, date
    du rapport)."""
    contexte = contexte or {}
    libelles = contexte.get("labels") or {}
    verdicts = contexte.get("verdicts") or {}
    version = contexte.get("version") or ""
    date_rapport = contexte.get("date") or ""
    dcma = next((r["parsed"] for r in results if r.get("kind") == "dcma"), None)
    mc = next((r["parsed"] for r in results if r.get("kind") == "montecarlo"), None)

    tampon = io.BytesIO()
    marge = 16 * mm
    doc = SimpleDocTemplate(tampon, pagesize=A4, leftMargin=marge, rightMargin=marge,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"MPPCR — {filename}", author="MPPCR")
    largeur = A4[0] - 2 * marge
    flux = []

    # ------------------------------------------------------------ synthèse
    flux.append(p("Rapport d'analyse du planning", "titre"))
    details = [t(filename), f"rapport du {t(date_rapport)}", f"MPPCR {t(version)}"]
    if dcma and dcma.get("status_date"):
        details.insert(1, f"date d'état : {t(dcma['status_date'])}")
    flux.append(p(" · ".join(details), "sous_titre"))
    flux.append(Spacer(1, 8))

    if dcma and dcma.get("score"):
        score = dcma["score"]
        valeur = "—" if score.get("score") is None else f"{score['score']}/100"
        plafond = (f"plafonné à {score['cap']} : {', '.join(score.get('missing') or [])} non renseigné"
                   if score.get("capped") else f"{score.get('conformes')} conformes sur {score.get('evaluated')} évalués")
        kpi = dcma.get("kpi") or {}
        flux.append(tuiles([
            ("Score de conformité", valeur, f"{score.get('label', '')} — {plafond}",
             COULEUR_BANDE.get(score.get("badge_css"), TEXTE)),
            ("Conformes", str(kpi.get("ok", 0)), "cibles atteintes", OK),
            ("À corriger", str(kpi.get("ko", 0)), "contrôles en écart", KO),
            ("Non évaluables", str(kpi.get("na", 0)), "données manquantes", NA),
        ], largeur))
        if dcma.get("overall_comment"):
            flux.append(Spacer(1, 6))
            flux.append(p(t(dcma["overall_comment"])))
        priorites = (dcma.get("priorities") or [])[:5]
        if priorites:
            flux.append(p("Priorités d'amélioration", "h2"))
            lignes = [["#", "Contrôle", "Résultat", "Action recommandée"]]
            for rang, item in enumerate(priorites, start=1):
                lignes.append([
                    str(rang),
                    p(t(libelles.get(item.get("num"), item.get("name"))), "cellule_gras"),
                    p(t(virgule(f"{item.get('value', '')} ({verdicts.get(item.get('kind'), '')})")), "cellule"),
                    p(t(item.get("comment", "")), "cellule"),
                ])
            flux.append(tableau(lignes, [8 * mm, 52 * mm, 30 * mm, largeur - 90 * mm]))

    if mc and not mc.get("stop_message") and mc.get("metrics"):
        flux.append(p("Risque planning (Monte Carlo)", "h2"))
        metriques = {m["key"]: m.get("value_num") for m in mc["metrics"]}
        cpm = metriques.get("deterministic")
        tuiles_mc = [("Déterministe (CPM)", f"{fr(cpm)} j", "référence, pas un engagement", TEXTE)]
        for cle, libelle, couleur in (("p50", "P50 — médiane", TEXTE), ("p80", "P80 — objectif", OK),
                                      ("p90", "P90 — prudent", TEXTE)):
            valeur = metriques.get(cle)
            if valeur is not None:
                ecart = f"{valeur - cpm:+.1f} j vs CPM".replace(".", ",") if cpm is not None else ""
                tuiles_mc.append((libelle, f"{fr(valeur)} j", ecart, couleur))
        flux.append(tuiles(tuiles_mc, largeur))
        prevision = (mc.get("forecast") or {}).get("rows") or []
        if prevision:
            flux.append(Spacer(1, 6))
            noms = {"plan": "Planifiée", "p50": "P50", "p80": "P80", "p90": "P90"}
            flux.append(p("<b>Fin prévisionnelle depuis la date d'état :</b> " + " · ".join(
                f"{noms.get(r['key'], r['key'])} {t(r['date_fr'])} ({fr(r['days'])} j restants)"
                for r in prevision)))
        if mc.get("decision"):
            flux.append(Spacer(1, 4))
            flux.append(p("<b>Lecture décisionnelle.</b> " + t(mc["decision"])))
        if mc.get("network_warning"):
            flux.append(Spacer(1, 4))
            flux.append(p(f'<font color="#DC6803"><b>Fiabilité limitée.</b></font> {t(mc["network_warning"])}'))

    if comparaison:
        flux.append(p(f"Évolution depuis {t(comparaison['fichier_avant'])}", "h2"))
        morceaux = []
        score = comparaison.get("score") or {}
        if score.get("delta") is not None:
            morceaux.append(f"score {score['avant']} -> {score['apres']} ({score['delta']:+d} pts)")
        bilan = comparaison.get("bilan") or {}
        if comparaison.get("controles"):
            morceaux.append(f"{bilan.get('Amélioré', 0)} contrôle(s) amélioré(s), "
                            f"{bilan.get('Dégradé', 0)} dégradé(s)")
        for m in comparaison.get("mc") or []:
            if m["cle"] == "p80":
                morceaux.append(f"P80 {fr(m['avant'])} -> {fr(m['apres'])} j ({fr(m['delta'])} j)")
        for r in comparaison.get("prevision") or []:
            if r["cle"] == "p80":
                morceaux.append(f"fin P80 {t(r['avant'])} -> {t(r['apres'])} ({r['ecart_jours']:+d} j)")
        flux.append(p(t(" · ".join(morceaux)) if morceaux else "Aucune évolution mesurable."))

    # ------------------------------------------------------------ annexes
    annexes = []
    if dcma and dcma.get("rows"):
        annexes.append(p("Annexe A — Détail des 14 contrôles DCMA-14", "h2"))
        lignes = [["N°", "Contrôle", "Résultat", "Cible", "Verdict", "Action recommandée"]]
        styles_verdict = []
        for i, row in enumerate(dcma["rows"], start=1):
            lignes.append([
                str(row.get("num") or ""),
                p(t(libelles.get(row.get("num"), row.get("name"))), "cellule_gras"),
                p(t(virgule(f"{row.get('value', '')} ({row.get('detail', '')})")), "cellule"),
                p(t(row.get("target", "")), "cellule"),
                p(t(verdicts.get(row.get("kind"), row.get("verdict"))), "cellule_gras"),
                p(t(row.get("comment", "")), "cellule"),
            ])
            styles_verdict.append(("TEXTCOLOR", (4, i), (4, i), COULEUR_VERDICT.get(row.get("kind"), TEXTE)))
        table = tableau(lignes, [8 * mm, 44 * mm, 24 * mm, 26 * mm, 18 * mm, largeur - 120 * mm])
        table.setStyle(TableStyle(styles_verdict))
        annexes.append(table)

        details_taches = dcma.get("task_details") or {}
        lignes = [["N°", "Contrôle", "Tâches concernées (N° MS Project)"]]
        for num in sorted(details_taches):
            ids = details_taches[num]
            if ids:
                lignes.append([str(num), p(t(libelles.get(num, "")), "cellule"),
                               p(t(", ".join(str(i) for i in ids)), "cellule")])
        if len(lignes) > 1:
            annexes.append(p("Annexe B — Tâches concernées", "h2"))
            annexes.append(tableau(lignes, [8 * mm, 60 * mm, largeur - 68 * mm]))

    if mc and not mc.get("stop_message"):
        if mc.get("criticality"):
            lignes = [["Tâche", "Criticité", "Lecture"]]
            lignes += [[p(t(c["name"]), "cellule"), f"{fr(c['pct_num'])} %", t(c["level"])]
                       for c in mc["criticality"]]
            annexes.append(KeepTogether([p("Annexe C — Criticité des tâches", "h2"),
                                         tableau(lignes, [largeur - 70 * mm, 25 * mm, 45 * mm])]))
        if mc.get("sensitivity"):
            lignes = [["Tâche", "Corrélation durée / fin"]]
            lignes += [[p(t(s["name"]), "cellule"), f"{s['value']:+.2f}".replace(".", ",")]
                       for s in mc["sensitivity"]]
            annexes.append(KeepTogether([
                p("Annexe D — Sensibilité (tornado)", "h2"),
                p("Les tâches en tête pèsent le plus sur la date de fin : une estimation affinée ou une "
                  "réduction de leur risque y rapporte le plus.", "petit"),
                Spacer(1, 3),
                tableau(lignes, [largeur - 45 * mm, 45 * mm])]))
        if mc.get("distribution") and len(mc["distribution"]) >= 2:
            metriques = {m["key"]: m.get("value_num") for m in mc.get("metrics") or []}
            annexes.append(KeepTogether([p("Annexe E — Distribution des durées simulées", "h2"),
                                         histogramme(mc["distribution"], metriques, largeur)]))

    if comparaison and comparaison.get("controles"):
        lignes = [["N°", "Contrôle", "Avant", "Actuel", "Évolution", "Tâches nouvelles / résolues"]]
        for c in comparaison["controles"]:
            taches = []
            if c["nouvelles"]:
                taches.append("nouvelles : " + ", ".join(map(str, c["nouvelles"][:20])))
            if c["resolues"]:
                taches.append("résolues : " + ", ".join(map(str, c["resolues"][:20])))
            lignes.append([str(c["num"]), p(t(c["nom"]), "cellule"), t(virgule(c["avant"])), t(virgule(c["apres"])),
                           t(c["evolution"]), p(t(" · ".join(taches) or "—"), "cellule")])
        annexes.append(p(f"Annexe F — Comparaison avec {t(comparaison['fichier_avant'])}", "h2"))
        annexes.append(tableau(lignes, [8 * mm, 50 * mm, 20 * mm, 20 * mm, 20 * mm, largeur - 118 * mm]))

    if annexes:
        flux.append(PageBreak())
        flux.extend(annexes)

    def pied(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(TEXTE_3)
        canvas.drawString(marge, 9 * mm, f"MPPCR {version} · {filename}".translate(TRANSCRIPTION))
        canvas.drawRightString(A4[0] - marge, 9 * mm, f"page {document.page}")
        canvas.restoreState()

    doc.build(flux, onFirstPage=pied, onLaterPages=pied)
    return tampon.getvalue()
