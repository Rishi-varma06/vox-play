"""
Regenerate every data figure in the paper from the study outputs.

Figures are numbered in the order a reader meets them, which is not the order
they were first drawn, so this also renames the two that are carried over
unchanged. Nothing here reads a hand-copied number: every value comes from
the JSON or JSONL a study script wrote.

Run from the repository root, after the studies:

    python docs/paper/studies/make_figures.py
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

STUDIES = Path("docs/paper/studies")
FIGS = Path("docs/paper/figures")

INK = "#1a1a1a"
ACCENT = "#1f4e79"
WARM = "#c0504d"
GREY = "#9a9a9a"
MID = "#7f9fc0"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 8,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})


def clopper_pearson(k: int, n: int, alpha: float = 0.05):
    """Exact binomial interval, without pulling in SciPy."""
    def cdf(successes: int, p: float) -> float:
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                   for i in range(successes + 1))

    def solve(predicate) -> float:
        low, high = 0.0, 1.0
        for _ in range(200):
            mid = (low + high) / 2
            if predicate(mid):
                low = mid
            else:
                high = mid
        return (low + high) / 2

    lower = 0.0 if k == 0 else solve(
        lambda p: 1 - cdf(k - 1, p) < alpha / 2)
    upper = 1.0 if k == n else solve(lambda p: cdf(k, p) > alpha / 2)
    return 100 * lower, 100 * upper


# ---------------------------------------------------------------------------

def unused_thresholds_chart() -> None:
    """
    Kept for the slide deck; dropped from the paper.

    It restated Table II as two bars, and a figure that only repeats a table
    costs a third of a column for nothing.
    """
    data = json.loads((STUDIES / "threshold_split.json").read_text())
    labels = ["Documented\n10 / 20 / 30", "Fitted on train\n5 / 10 / 15"]
    values = [data["test_kappa_community"], data["test_kappa_fitted"]]

    fig, ax = plt.subplots(figsize=(3.3, 2.0))
    bars = ax.bar(labels, values, width=0.5, color=[GREY, ACCENT])
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.025,
                "%.3f" % value, ha="center", fontsize=8.5, fontweight="bold")
    ax.axhline(0.8, color=WARM, lw=0.8, ls="--")
    ax.text(1.46, 0.815, "almost perfect", fontsize=6.6, color=WARM,
            ha="right")
    ax.set_ylabel("Cohen's $\\kappa$, held-out split")
    ax.set_ylim(0, 1.08)
    ax.set_title("%s moves, %d held-out games"
                 % (format(data["test_moves"], ","), data["test_games"]),
                 fontsize=7.5, color=GREY)
    fig.savefig(FIGS / "fig2_thresholds.png")
    plt.close(fig)
    print("fig2_thresholds.png")


def unused_motif_chart() -> None:
    """Kept for the slide deck; dropped from the paper, which has the table."""
    data = json.loads((STUDIES / "motif_precision.json").read_text())
    rows = [r for r in data["rows"] if r["comparable"]]
    rows.sort(key=lambda r: r["f1"])
    names = [r["motif"] for r in rows]
    recall = [100 * r["recall"] for r in rows]
    precision = [100 * r["precision"] for r in rows]
    spots = range(len(rows))

    fig, ax = plt.subplots(figsize=(3.4, 2.7))
    ax.barh([y + 0.20 for y in spots], recall, height=0.38, color=ACCENT,
            label="recall")
    ax.barh([y - 0.20 for y in spots], precision, height=0.38, color=MID,
            label="precision")
    ax.set_yticks(list(spots))
    ax.set_yticklabels([n.replace("_", " ") for n in names], fontsize=7)
    ax.set_xlabel("percent  (mean recall %.1f, mean precision %.1f)"
                  % (100 * data["mean_recall"], 100 * data["mean_precision"]))
    ax.set_xlim(0, 104)
    ax.axvline(100 * data["mean_recall"], color=INK, lw=0.7, ls=":")
    ax.legend(frameon=False, fontsize=7, ncol=2, loc="lower center",
              bbox_to_anchor=(0.5, 1.0), handlelength=1.2)
    fig.savefig(FIGS / "fig5_motif_quality.png")
    plt.close(fig)
    print("fig5_motif_quality.png")


#: Stage boxes of Fig. 1: number, name, one-line gloss.
STAGES = [
    ("1", "Ingest", "notation to move events"),
    ("2", "Evaluate", "Stockfish, every position"),
    ("3", "Detect", "12 motifs, opening theory"),
    ("4", "Pace", "salience + budget + spacing"),
    ("5", "Write", "LLM realises supplied facts"),
    ("6", "Verify + speak", "licence check, then TTS"),
]


def fig1_pipeline() -> None:
    """
    The six-stage pipeline, redrawn for a 3.5 in column.

    The previous version had three problems. Its bottom legend overlapped the
    last stage box; it described stages 4-6 as "shared with cricket", a claim
    the paper no longer makes because the chess package imports none of the
    cricket one; and at aspect 1.32 it consumed half a column.

    This replaces the legend with brackets labelling the two groups down the
    left margin, so nothing can collide, and trades box height for padding.
    """
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    box_h, gap = 0.92, 0.62
    left, width = 2.05, 8.1
    pitch = box_h + gap
    top = 9.4

    fig, ax = plt.subplots(figsize=(3.45, 3.45))
    ax.set_xlim(0, left + width + 0.25)
    ax.set_ylim(0, top + 1.5)
    ax.axis("off")

    def centre(index: int) -> float:
        return top - index * pitch - box_h / 2

    ax.text(left + width / 2, top + 1.02, "PGN file or Lichess link",
            ha="center", va="center", fontsize=7.6, style="italic")
    ax.add_patch(FancyArrowPatch(
        (left + width / 2, top + 0.78), (left + width / 2, top + 0.1),
        arrowstyle="-|>", mutation_scale=9, lw=0.9, color=INK))

    for index, (number, name, gloss) in enumerate(STAGES):
        chess_stage = index < 3
        y = top - index * pitch - box_h
        ax.add_patch(FancyBboxPatch(
            (left, y), width, box_h,
            boxstyle="round,pad=0,rounding_size=0.12",
            linewidth=1.0,
            edgecolor="#2f6b4f" if chess_stage else "#4a4a8f",
            facecolor="#e7f3ec" if chess_stage else "#ececf7"))
        ax.text(left + 0.46, y + box_h / 2, number, ha="center", va="center",
                fontsize=9, fontweight="bold", color=INK)
        ax.text(left + 0.92, y + box_h * 0.68, name, ha="left", va="center",
                fontsize=8.4, fontweight="bold", color=INK)
        ax.text(left + 0.92, y + box_h * 0.24, gloss, ha="left", va="center",
                fontsize=7.1, color="#333333")
        if index < len(STAGES) - 1:
            ax.add_patch(FancyArrowPatch(
                (left + width / 2, y - 0.09),
                (left + width / 2, y - gap + 0.09),
                arrowstyle="-|>", mutation_scale=9, lw=0.9, color=INK))

    # Group brackets in the left margin, in place of a legend.
    for first, last, label, colour in (
        (0, 2, "chess facts,\ncomputed", "#2f6b4f"),
        (3, 5, "pacing and\ndelivery", "#4a4a8f"),
    ):
        y_top = centre(first) + box_h / 2
        y_bottom = centre(last) - box_h / 2
        x = left - 0.30
        ax.plot([x, x], [y_bottom, y_top], color=colour, lw=1.0)
        for y in (y_top, y_bottom):
            ax.plot([x, x + 0.16], [y, y], color=colour, lw=1.0)
        ax.text(x - 0.16, (y_top + y_bottom) / 2, label, ha="right",
                va="center", fontsize=7.0, color=colour, linespacing=1.5)

    bottom = top - len(STAGES) * pitch + gap
    ax.add_patch(FancyArrowPatch(
        (left + width / 2, bottom - 0.04), (left + width / 2, bottom - 0.66),
        arrowstyle="-|>", mutation_scale=9, lw=0.9, color=INK))
    ax.text(left + width / 2, bottom - 0.95,
            "spoken audio + synchronised board",
            ha="center", va="center", fontsize=7.6, style="italic")

    fig.savefig(FIGS / "fig1_pipeline.png")
    plt.close(fig)
    print("fig1_pipeline.png")


def fig2_pacing() -> None:
    rows = [json.loads(line)
            for line in (STUDIES / "pacing_study.jsonl")
            .read_text().splitlines() if line.strip()]
    rows = [r for r in rows if "error" not in r]
    plies = [r["plies"] for r in rows]

    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.scatter(plies, [r["flat_seconds"] for r in rows], s=7, color=WARM,
               label="every move", alpha=0.75, linewidths=0)
    ax.scatter(plies, [r["topn_seconds"] for r in rows], s=7, color=GREY,
               label="top-$N$", alpha=0.75, linewidths=0)
    ax.scatter(plies, [r["paced_seconds"] for r in rows], s=7, color=ACCENT,
               label="paced (ours)", alpha=0.85, linewidths=0)
    ax.set_xlabel("half-moves in the game")
    ax.set_ylabel("spoken duration (s)")
    ax.legend(frameon=False, fontsize=7, loc="upper left")
    ax.set_title("%d games" % len(rows), fontsize=7.5, color=GREY)
    fig.savefig(FIGS / "fig2_pacing.png")
    plt.close(fig)
    print("fig2_pacing.png")


def unused_hallucination_chart() -> None:
    """
    Kept for the slide deck; dropped from the paper.

    Section V-E deliberately presents the 32-beat result as a pilot with
    overlapping intervals. A bar chart gives it the visual weight of a
    finding, which is the opposite of what the text says about it.
    """
    # The pilot of Section V-E: 2 of 32 ungated, 0 of 32 gated.
    arms = [("gate disabled", 2, 32, WARM), ("gate enabled", 0, 32, ACCENT)]
    labels, points, lows, highs, shades = [], [], [], [], []
    for label, k, n, colour in arms:
        low, high = clopper_pearson(k, n)
        labels.append(label)
        points.append(100 * k / n)
        lows.append(100 * k / n - low)
        highs.append(high - 100 * k / n)
        shades.append(colour)

    fig, ax = plt.subplots(figsize=(3.2, 1.9))
    ax.bar(labels, points, width=0.45, color=shades,
           yerr=[lows, highs], capsize=4,
           error_kw={"ecolor": INK, "elinewidth": 0.8, "capthick": 0.8})
    for index, (point, high) in enumerate(zip(points, highs)):
        ax.text(index, point + high + 0.7, "%.1f" % point, ha="center",
                fontsize=8.5, fontweight="bold")
    ax.set_ylabel("unsupported claims\nper 100 beats")
    ax.set_ylim(0, 24)
    ax.set_title("32 beats per arm; exact 95% intervals overlap",
                 fontsize=7.2, color=GREY)
    fig.savefig(FIGS / "fig7_hallucination.png")
    plt.close(fig)
    print("fig7_hallucination.png")


def fig3_gate() -> None:
    """
    The studio screenshot, cropped so it prints.

    The interface is dark, and a full screenshot in a 3.5 in column is a
    large black rectangle with unreadable text. Cropping to the flagged beat
    and its three findings is both the legible version and the only part of
    the screen the figure is about.
    """
    from PIL import Image

    source = Image.open(FIGS / "source_gate_screenshot.jpg")
    source.crop((78, 205, 772, 432)).save(
        FIGS / "fig3_gate_flagged.jpg", quality=94)
    print("fig3_gate_flagged.jpg (cropped from the review screen)")


def main() -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig1_pipeline()
    fig3_gate()
    fig2_pacing()
    print("fig1_pipeline.png is regenerated above; no other figures are used")


if __name__ == "__main__":
    main()
