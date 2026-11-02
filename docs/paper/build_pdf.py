"""
Render voxplay-paper.md to a two-column IEEE-style PDF.

ReportLab rather than a Markdown-to-HTML-to-PDF chain, because every
HTML-based renderer available here either needs GTK system libraries absent
on Windows or supports too little CSS to lay out tables and figures properly.
Platypus handles both, at the cost of parsing the subset of Markdown this
document actually uses.

Geometry follows the IEEE conference template on US Letter: 0.625 in side
margins, two 3.5 in columns with a 0.25 in gutter, Times 10 pt. The title
block spans both columns on page one; everything after it flows in columns.

Run from the repository root:

    python docs/paper/build_pdf.py            # normal build
    python docs/paper/build_pdf.py --blind    # strip identifying details
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, FrameBreak, Image, KeepTogether, NextPageTemplate,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "voxplay-paper.md"
OUT = ROOT / "voxplay-paper.pdf"
FIGS = ROOT / "figures"

INK = colors.HexColor("#1a1a1a")
FAINT = colors.HexColor("#cccccc")
MUTED = colors.HexColor("#444444")

# --- IEEE page geometry ----------------------------------------------------
#: Measured from the reference layout: US Letter, 49 pt side margins, two
#: 246 pt columns with a 22 pt gutter, 10 pt Times. These are the numbers a
#: LaTeX IEEEtran letterpaper build produces, so matching them here keeps the
#: output visually interchangeable with one.
PAGE = letter
SIDE = 49.0
TOP = 56.0
BOTTOM = 56.0
GUTTER = 22.0
COL = (PAGE[0] - 2 * SIDE - GUTTER) / 2.0      # 246 pt, about 3.42 in
TITLE_BLOCK = 1.58 * inch                       # full-width band on page 1

#: Figures anchored to the heading they follow, in order of appearance, so
#: the numbering in the captions matches the order a reader meets them.
#: Width is a fraction of the column.
#: Four figures, each showing something no table in the paper shows. Three
#: earlier ones were dropped because they restated a table as a bar chart.
FIGURES = {
    "III. System Architecture": (
        "fig1_pipeline.png", 0.58,
        "Fig. 1. The six-stage pipeline. Stages 1-3 compute chess facts "
        "before the model is invoked; stages 4-6 pace, realise and speak "
        "them."),
    "D. Pacing as budgeted selection": (
        "fig2_trace.png", 0.98,
        "Fig. 2. White win probability across the 1858 Opera Game with the "
        "selected beats marked. The turning point, half-move 18, coincides "
        "with the largest single swing."),
    "C. Pacing over 173 games": (
        "fig2_pacing.png", 0.70,
        "Fig. 3. Spoken duration against game length over 173 standard "
        "games. The "
        "paced arm is clamped by (6); the every-move baseline grows without "
        "bound; top-N is constant by construction."),
}

#: Replaced when --blind is passed, for double-blind submission. The author
#: block collapses to a single anonymous line and every pointer that would
#: identify the group — the code host, the phrase "the repository" — becomes
#: a pointer to the anonymous supplementary material instead.
BLIND_PATTERNS = [
    (r"\*\*Authors:\*\*.*", "Anonymous submission"),
    (r"\*\*Guide:\*\*.*\n", ""),
    (r"\*\*Department:\*\*.*\n", ""),
    (r"https://github\.com/\S+", "[URL withheld for review]"),
    (r"\bin the repository\b", "in the supplementary material"),
    (r"\bthe repository\b", "the supplementary material"),
    (r"`docs/paper/studies/`", "the supplementary material"),
]


def styles():
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontName="Times-Bold", fontSize=18,
        leading=21, alignment=TA_CENTER, spaceAfter=4 * mm, textColor=INK)
    s["authors"] = ParagraphStyle(
        "authors", fontName="Times-Roman", fontSize=10.5, leading=13.5,
        alignment=TA_CENTER, spaceAfter=1 * mm, textColor=INK)
    s["h1"] = ParagraphStyle(
        "h1", fontName="Times-Bold", fontSize=10, leading=12.5,
        alignment=TA_CENTER, spaceBefore=4 * mm, spaceAfter=1.6 * mm,
        textColor=INK)
    s["h2"] = ParagraphStyle(
        "h2", fontName="Times-Italic", fontSize=10, leading=12,
        spaceBefore=2.6 * mm, spaceAfter=1.1 * mm, textColor=INK)
    # IEEE body text separates paragraphs by first-line indent alone, with
    # no vertical space between them. The previous 1.5 mm gap was both
    # non-standard and, over ninety paragraphs, more than half a page.
    s["body"] = ParagraphStyle(
        "body", fontName="Times-Roman", fontSize=10, leading=11.4,
        alignment=TA_JUSTIFY, spaceAfter=0, firstLineIndent=4 * mm,
        textColor=INK)
    s["abstract"] = ParagraphStyle(
        "abstract", parent=s["body"], fontName="Times-Bold", fontSize=9,
        leading=10.9, firstLineIndent=4 * mm, spaceAfter=1.5 * mm)
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], leftIndent=4.5 * mm,
        bulletIndent=1 * mm, firstLineIndent=0, spaceAfter=1 * mm,
        spaceBefore=0.8 * mm)
    s["eq"] = ParagraphStyle(
        "eq", fontName="Times-Italic", fontSize=10, leading=12.5,
        alignment=TA_CENTER, textColor=INK)
    s["eqnum"] = ParagraphStyle(
        "eqnum", fontName="Times-Roman", fontSize=10, leading=12.5,
        alignment=TA_LEFT, textColor=INK)
    s["caption"] = ParagraphStyle(
        "caption", fontName="Times-Roman", fontSize=7.8, leading=9.2,
        alignment=TA_JUSTIFY, textColor=INK, spaceBefore=1.2 * mm,
        spaceAfter=2.4 * mm)
    s["tcaption"] = ParagraphStyle(
        "tcaption", fontName="Times-Roman", fontSize=7.8, leading=9.0,
        alignment=TA_CENTER, textColor=INK, spaceAfter=0.9 * mm)
    s["cell"] = ParagraphStyle(
        "cell", fontName="Times-Roman", fontSize=8, leading=9.4,
        textColor=INK)
    s["cellh"] = ParagraphStyle(
        "cellh", fontName="Times-Bold", fontSize=8, leading=9.4,
        alignment=TA_CENTER, textColor=INK)
    # IEEE sets the reference list two points below body size, with a
    # hanging indent under the bracketed number.
    s["ref"] = ParagraphStyle(
        "ref", fontName="Times-Roman", fontSize=8, leading=9.0,
        alignment=TA_JUSTIFY, textColor=INK, leftIndent=4.2 * mm,
        firstLineIndent=-4.2 * mm, spaceAfter=0.45 * mm)
    s["code"] = ParagraphStyle(
        "code", fontName="Courier", fontSize=7, leading=9,
        leftIndent=1.5 * mm, spaceBefore=1 * mm, spaceAfter=1.6 * mm,
        textColor=INK)
    return s


def inline(text: str) -> str:
    """The Markdown inline subset this paper uses, as ReportLab markup."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8">\1</font>',
                  text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def mathify(text: str) -> str:
    """Subscripts, superscripts and spacing for an equation line."""
    text = inline(text)
    text = re.sub(r"\^\(([^)]+)\)", r"<super>\1</super>", text)
    text = re.sub(r"_\{([^}]+)\}", r"<sub>\1</sub>", text)
    text = re.sub(r"_([A-Za-z0-9]+)", r"<sub>\1</sub>", text)
    text = re.sub(r"\^([A-Za-z0-9]+)", r"<super>\1</super>", text)
    return text


def equation(number: str, expr: str, S):
    """A centred equation with its number flush right, IEEE style."""
    table = Table(
        [[Paragraph(mathify(expr), S["eq"]),
          Paragraph("(%s)" % number, S["eqnum"])]],
        colWidths=[COL - 9 * mm, 9 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return [Spacer(1, 1.2 * mm), table, Spacer(1, 1.8 * mm)]


def figure_for(heading: str, S):
    key = next((k for k in FIGURES if heading.strip().startswith(k)), None)
    if key is None:
        return []
    name, frac, caption = FIGURES[key]
    path = FIGS / name
    if not path.exists():
        return []
    iw, ih = ImageReader(str(path)).getSize()
    width = COL * frac
    height = width * ih / iw
    # A figure taller than a column cannot be placed; scale it to fit.
    limit = PAGE[1] - TOP - BOTTOM - 22 * mm
    if height > limit:
        width *= limit / height
        height = limit
    img = Image(str(path), width=width, height=height)
    img.hAlign = "CENTER"
    return [Spacer(1, 2 * mm),
            KeepTogether([img, Paragraph(caption, S["caption"])])]


def column_widths(rows, total):
    """
    Proportional to the longest cell in each column, clamped.

    The previous fixed heuristic gave a wide first column regardless of
    content, which is what made several tables look misaligned.
    """
    ncol = len(rows[0])
    longest = [
        max(len(row[i]) for row in rows if i < len(row))
        for i in range(ncol)
    ]
    # A long cell wraps, so weight by a dampened length rather than the raw
    # one: otherwise one prose column swallows the whole table.
    weights = [max(4.0, min(42.0, value)) ** 0.75 for value in longest]
    scale = total / sum(weights)
    return [weight * scale for weight in weights]


def build_table(rows, S, caption=None):
    head, body = rows[0], rows[1:]
    data = [[Paragraph(inline(c), S["cellh"]) for c in head]]
    data += [[Paragraph(inline(c), S["cell"]) for c in r] for r in body]
    table = Table(data, colWidths=column_widths(rows, COL), repeatRows=1,
                  hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.8),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
    ]))
    out = [Spacer(1, 1.3 * mm)]
    if caption:
        out.append(Paragraph(caption, S["tcaption"]))
    out += [table, Spacer(1, 1.6 * mm)]
    return out


def strip_draft_note(md: str) -> str:
    """
    Remove the author-facing banner, which is not part of the paper.

    Done before any anonymisation, because the banner is delimited by the
    author block and blinding rewrites that block away.
    """
    return re.sub(r"> \*\*DRAFT\.\*\*.*?(?=\n\*\*Authors)", "", md, flags=re.S)


def parse(md: str, S):
    flow = []
    lines = md.split("\n")
    i = 0
    table_number = 0
    pending_caption = None
    in_front_matter = True

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i].replace("&", "&amp;")
                           .replace("<", "&lt;").replace(">", "&gt;"))
                i += 1
            flow.append(Paragraph("<br/>".join(buf) or " ", S["code"]))
            i += 1
            continue

        # A "Table: ..." line titles the table that follows it.
        if line.startswith("Table: "):
            pending_caption = line[len("Table: "):].strip()
            i += 1
            continue

        # Table, with a numbered caption above it in IEEE style.
        if line.startswith("|") and i + 1 < len(lines) \
                and set(lines[i + 1]) <= set("|-: "):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip()
                         for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if rows:
                table_number += 1
                caption = "TABLE %s" % _roman(table_number)
                if pending_caption:
                    caption += "<br/>%s" % inline(pending_caption.upper())
                    pending_caption = None
                flow += build_table(rows, S, caption)
            continue

        if line.startswith("# "):
            flow.append(Paragraph(inline(line[2:]), S["title"]))
        elif line.startswith("## "):
            heading = line[3:]
            if in_front_matter and heading.startswith("Abstract"):
                flow += [NextPageTemplate("rest"), FrameBreak()]
                in_front_matter = False
            # Every level-2 heading in this paper is an IEEE section head,
            # which is set in capitals. The previous rule capitalised only
            # headings beginning with a roman numeral or "A", which left
            # "References" in mixed case beside "V. EXPERIMENTAL VALIDATION".
            flow.append(Paragraph(inline(heading).upper(), S["h1"]))
            flow += figure_for(heading, S)
        elif line.startswith("### "):
            heading = line[4:]
            flow.append(Paragraph(inline(heading), S["h2"]))
            flow += figure_for(heading, S)
        elif re.match(r"^> \((\w+)\)\s", line):
            match = re.match(r"^> \((\w+)\)\s+(.*)$", line)
            flow += equation(match.group(1), match.group(2), S)
        elif line.startswith("> "):
            flow.append(Paragraph(inline(line[2:]), S["body"]))
        elif re.match(r"^[-*] ", line):
            flow.append(Paragraph(inline(line[2:]), S["bullet"],
                                  bulletText="•"))
        elif re.match(r"^\d+\. ", line):
            number, rest = line.split(". ", 1)
            buf = [rest]
            while i + 1 < len(lines) and lines[i + 1].startswith("   "):
                i += 1
                buf.append(lines[i].strip())
            flow.append(Paragraph(inline(" ".join(buf)), S["bullet"],
                                  bulletText=number + "."))
        elif line.strip() in ("---", "***"):
            pass
        elif line.startswith(("**Authors:**", "**Guide:**", "**Department:**")):
            flow.append(Paragraph(inline(line), S["authors"]))
        elif line.strip():
            buf = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
                    r"^(#|\||>|```|[-*] |\d+\. |---|\*\*Authors)",
                    lines[i + 1]):
                i += 1
                buf.append(lines[i])
            text = " ".join(buf)
            if re.match(r"^\[\d+\] ", text):
                style = S["ref"]
            elif text.startswith("Millions of chess"):
                style = S["abstract"]
            else:
                style = S["body"]
            flow.append(Paragraph(inline(text), style))
        i += 1
    return flow


def _roman(n: int) -> str:
    numerals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for value, symbol in numerals:
        while n >= value:
            out += symbol
            n -= value
    return out


def main():
    blind = "--blind" in sys.argv
    S = styles()
    doc = BaseDocTemplate(
        str(OUT), pagesize=PAGE,
        leftMargin=SIDE, rightMargin=SIDE, topMargin=TOP, bottomMargin=BOTTOM,
        title="Voxplay: Pacing and Verifiable Grounding for Spoken Chess "
              "Commentary Generated from Move Notation",
        author="Anonymous" if blind else "Voxplay project")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 9)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PAGE[0] / 2, 0.5 * inch,
                                 str(canvas.getPageNumber()))
        canvas.restoreState()

    body_top = PAGE[1] - TOP
    body_height = PAGE[1] - TOP - BOTTOM

    banner = Frame(SIDE, body_top - TITLE_BLOCK, PAGE[0] - 2 * SIDE,
                   TITLE_BLOCK, id="banner", leftPadding=0, rightPadding=0,
                   topPadding=0, bottomPadding=0)
    first_cols = [
        Frame(SIDE, BOTTOM, COL, body_height - TITLE_BLOCK, id="f1",
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(SIDE + COL + GUTTER, BOTTOM, COL, body_height - TITLE_BLOCK,
              id="f2", leftPadding=0, rightPadding=0, topPadding=0,
              bottomPadding=0),
    ]
    rest_cols = [
        Frame(SIDE, BOTTOM, COL, body_height, id="r1", leftPadding=0,
              rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(SIDE + COL + GUTTER, BOTTOM, COL, body_height, id="r2",
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
    ]
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[banner] + first_cols, onPage=footer),
        PageTemplate(id="rest", frames=rest_cols, onPage=footer),
    ])

    md = strip_draft_note(SRC.read_text(encoding="utf-8"))
    if blind:
        for pattern, replacement in BLIND_PATTERNS:
            md = re.sub(pattern, replacement, md)

    doc.build(parse(md, S))
    print("written %s  (%s bytes)%s"
          % (OUT, format(OUT.stat().st_size, ","),
             "  [anonymised]" if blind else ""))


if __name__ == "__main__":
    main()
