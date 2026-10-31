"""
Render voxplay-paper.md to PDF.

ReportLab rather than a Markdown-to-HTML-to-PDF chain, because every
HTML-based renderer available here either needs GTK system libraries that
are not present on Windows or supports too little CSS to lay out tables and
figures properly. Platypus handles both, at the cost of parsing the subset
of Markdown this document actually uses.

Run from the repository root:

    python docs/paper/build_pdf.py
"""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "voxplay-paper.md"
OUT = ROOT / "voxplay-paper.pdf"
FIGS = ROOT / "figures"

INK = colors.HexColor("#1a1a1a")
RULE = colors.HexColor("#999999")
FAINT = colors.HexColor("#dddddd")
MUTED = colors.HexColor("#555555")

#: Figures are anchored to the heading they follow. Width is a fraction of
#: the text column; the screenshots are tall, so they get less.
FIGURES = {
    "III. System Architecture": (
        "fig1_pipeline.png", 0.52,
        "Fig. 1. The six-stage pipeline. Stages 1–3 compute chess facts; "
        "stages 4–6 are domain-agnostic and shared unmodified with a "
        "second sport."),
    "B. Move classification": (
        "fig8_thresholds.png", 0.50,
        "Fig. 8. Classifier agreement with Lichess annotations under "
        "documented versus fitted thresholds, over 24,255 moves."),
    "E. Pacing as constrained selection": (
        "fig2_trace.png", 0.95,
        "Fig. 2. White win probability across the 1858 Opera Game, with "
        "selected beats marked. The turning point (half-move 18) coincides "
        "with the largest single swing."),
    "V. Implementation and Interface": (
        "fig6_studio_review.jpg", 0.78,
        "Fig. 6. The review screen: computed statistics, the verification "
        "banner, and per-beat boards. Speech is gated behind human review."),
    "A. Tactical motif recall": (
        "fig3_motif_recall.png", 0.92,
        "Fig. 3. Per-motif recall against 14,000 human-tagged Lichess "
        "puzzles. Six detectors reach 100%; the pin detector trades recall "
        "for precision (Section VIII-B)."),
    "C. Pacing against both baselines": (
        "fig4_pacing.png", 0.88,
        "Fig. 4. Spoken duration by selection strategy. The advantage over "
        "every-move narration widens from 2.1× to 5.1× as the game "
        "lengthens, because the paced budget is capped and the baseline's "
        "is not."),
    "D. Hallucination and the verification gate": (
        "fig5_hallucination.png", 0.48,
        "Fig. 5. Hallucinations per 100 beats on live model output, with "
        "the verification gate disabled and enabled."),
    "G. The verification gate": (
        "fig7_gate_flagged.jpg", 0.82,
        "Fig. 7. The gate rejecting text typed by a user. Three checks fire "
        "on one sentence: notation reaching the speech backend, a name "
        "absent from the game, and a tactic no detector found."),
}


def styles():
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontName="Times-Roman", fontSize=17,
        leading=21, alignment=TA_CENTER, spaceAfter=5 * mm, textColor=INK)
    s["authors"] = ParagraphStyle(
        "authors", fontName="Times-Roman", fontSize=10, leading=14,
        alignment=TA_CENTER, spaceAfter=2 * mm, textColor=INK)
    s["h1"] = ParagraphStyle(
        "h1", fontName="Times-Bold", fontSize=11.5, leading=14,
        spaceBefore=6 * mm, spaceAfter=1.8 * mm, textColor=INK)
    s["h2"] = ParagraphStyle(
        "h2", fontName="Times-Italic", fontSize=10, leading=13,
        spaceBefore=3.6 * mm, spaceAfter=1.2 * mm, textColor=INK)
    s["body"] = ParagraphStyle(
        "body", fontName="Times-Roman", fontSize=9.6, leading=13.4,
        alignment=TA_JUSTIFY, spaceAfter=2.1 * mm, textColor=INK)
    s["quote"] = ParagraphStyle(
        "quote", parent=s["body"], fontName="Times-Italic",
        leftIndent=6 * mm, rightIndent=4 * mm, textColor=MUTED,
        spaceBefore=1.4 * mm)
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], leftIndent=5 * mm, bulletIndent=1.5 * mm,
        spaceAfter=1.2 * mm)
    s["code"] = ParagraphStyle(
        "code", fontName="Courier", fontSize=7.8, leading=10.2,
        backColor=colors.HexColor("#f6f5f2"), borderPadding=3,
        leftIndent=2 * mm, rightIndent=2 * mm, spaceBefore=1.5 * mm,
        spaceAfter=2.5 * mm, textColor=INK)
    s["caption"] = ParagraphStyle(
        "caption", fontName="Helvetica", fontSize=8, leading=10.5,
        alignment=TA_LEFT, textColor=MUTED, spaceBefore=1.6 * mm,
        spaceAfter=4 * mm)
    s["cell"] = ParagraphStyle(
        "cell", fontName="Helvetica", fontSize=7.9, leading=10, textColor=INK)
    s["cellh"] = ParagraphStyle(
        "cellh", fontName="Helvetica-Bold", fontSize=7.9, leading=10,
        textColor=INK)
    return s


def inline(text: str) -> str:
    """The Markdown inline subset this paper uses, as ReportLab markup."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.4">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"\[(\d+)\]", r"[\1]", text)
    return text


def figure_for(heading: str, width: float, S):
    key = next((k for k in FIGURES if heading.strip().startswith(k)), None)
    if key is None:
        return []
    name, frac, caption = FIGURES[key]
    path = FIGS / name
    if not path.exists():
        return []
    iw, ih = ImageReader(str(path)).getSize()
    w = width * frac
    img = Image(str(path), width=w, height=w * ih / iw)
    img.hAlign = "CENTER"
    return [Spacer(1, 2 * mm), KeepTogether([img, Paragraph(caption, S["caption"])])]


def build_table(rows, width, S):
    head, body = rows[0], rows[1:]
    ncol = len(head)
    data = [[Paragraph(inline(c), S["cellh"]) for c in head]]
    data += [[Paragraph(inline(c), S["cell"]) for c in r] for r in body]
    first = max(0.26, 1.0 - 0.17 * (ncol - 1)) if ncol > 2 else 0.5
    rest = (1.0 - first) / (ncol - 1) if ncol > 1 else 1.0
    widths = [width * first] + [width * rest] * (ncol - 1)
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, FAINT),
        ("LINEBELOW", (0, -1), (-1, -1), 0.9, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.2),
    ]))
    return [Spacer(1, 1.6 * mm), t, Spacer(1, 3.4 * mm)]


def parse(md: str, width: float, S):
    # Author-facing notes are not part of the paper.
    md = re.sub(r"^> \*\*DRAFT\.\*\*.*?(?=\n\*\*Authors)", "", md, flags=re.S)
    md = md.split("## Figures to prepare")[0].rstrip()

    flow, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]

        if ln.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i].replace("&", "&amp;")
                           .replace("<", "&lt;").replace(">", "&gt;"))
                i += 1
            flow.append(Paragraph("<br/>".join(buf) or " ", S["code"]))
            i += 1
            continue

        if ln.startswith("|") and i + 1 < len(lines) and set(lines[i + 1]) <= set("|-: "):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if rows:
                flow += build_table(rows, width, S)
            continue

        if ln.startswith("# "):
            flow.append(Paragraph(inline(ln[2:]), S["title"]))
        elif ln.startswith("## "):
            h = ln[3:]
            flow.append(Paragraph(inline(h), S["h1"]))
            flow += figure_for(h, width, S)
        elif ln.startswith("### "):
            h = ln[4:]
            flow.append(Paragraph(inline(h), S["h2"]))
            flow += figure_for(h, width, S)
        elif ln.startswith("> "):
            flow.append(Paragraph(inline(ln[2:]), S["quote"]))
        elif re.match(r"^[-*] ", ln):
            flow.append(Paragraph(inline(ln[2:]), S["bullet"], bulletText="•"))
        elif re.match(r"^\d+\. ", ln):
            n, rest = ln.split(". ", 1)
            flow.append(Paragraph(inline(rest), S["bullet"], bulletText=n + "."))
        elif ln.strip() in ("---", "***"):
            flow.append(Spacer(1, 2.5 * mm))
        elif ln.startswith("**Authors:**") or ln.startswith("**Guide:**") \
                or ln.startswith("**Department:**"):
            flow.append(Paragraph(inline(ln), S["authors"]))
        elif ln.strip():
            buf = [ln]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
                    r"^(#|\||>|```|[-*] |\d+\. |---)", lines[i + 1]):
                i += 1
                buf.append(lines[i])
            flow.append(Paragraph(inline(" ".join(buf)), S["body"]))
        i += 1
    return flow


def main():
    S = styles()
    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=19 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=20 * mm,
        title="Voxplay: Pacing and Verifiable Grounding for Spoken Chess Commentary",
        author="Voxplay project")
    width = doc.width

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 8.5)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(A4[0] / 2, 12 * mm, str(canvas.getPageNumber()))
        canvas.setStrokeColor(FAINT)
        canvas.setLineWidth(0.3)
        canvas.line(19 * mm, 16 * mm, A4[0] - 18 * mm, 16 * mm)
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, width, doc.height, id="col")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=footer)])
    doc.build(parse(SRC.read_text(encoding="utf-8"), width, S))
    print(f"written {OUT}  ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
