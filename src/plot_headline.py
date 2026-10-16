"""Headline chart: quality and cost per question against store size, one panel each.

    python -m src.plot_headline            # reads results/summary.csv and results/crossover.json, writes docs/

Two panels rather than one dual-axis chart. Colours are the first three slots of the reference
categorical palette (validated all-pairs for colour-vision deficiency); every line is also direct-labelled
and dash-coded, so identity never rests on colour alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = ROOT / "docs"

SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
ARMS = {  # arm: (label, colour, dash)
    "CTX": ("Full prompt", "#2a78d6", "solid"),
    "EMB": ("Embeddings", "#eb6834", (0, (6, 3))),
    "MD": ("Markdown index", "#1baf7a", (0, (1.5, 2.5))),
}
X_TICKS = [40_000, 100_000, 200_000, 400_000, 800_000]


def _k(v, _pos=None) -> str:
    return f"{v / 1000:.0f}k"


def _style(ax, title: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(FixedLocator(X_TICKS))
    ax.xaxis.set_major_formatter(FuncFormatter(_k))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlim(34_000, 1_150_000)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.grid(axis="x", visible=False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=11, length=0, pad=6)
    ax.set_xlabel("Size of the clinic's memory store (tokens, log scale)", color=MUTED, fontsize=11, labelpad=8)
    ax.set_title(title, loc="left", color=INK, fontsize=13.5, pad=12)


def _end_label(ax, x, y, text, colour, dy=0.0) -> None:
    ax.annotate(text, (x, y), xytext=(8, dy), textcoords="offset points", va="center", ha="left",
                fontsize=11, color=INK_2)
    ax.plot([x], [y], "o", ms=7, color=colour, mec=SURFACE, mew=2, zorder=5)


def main(argv: list[str] | None = None) -> int:
    s = pd.read_csv(RESULTS / "summary.csv")
    cross = json.loads((RESULTS / "crossover.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)

    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "svg.fonttype": "none"})
    fig, (ax_q, ax_c) = plt.subplots(1, 2, figsize=(14, 6.4), dpi=150, facecolor=SURFACE)
    fig.subplots_adjust(left=0.05, right=0.865, top=0.8, bottom=0.2, wspace=0.45)

    # --- quality --------------------------------------------------------------------------------
    _style(ax_q, "Answer quality (0–3, mean of 48 questions, 95% CI)")
    q_offsets = {"MD": 9, "EMB": 0, "CTX": -9}
    for arm, (label, colour, dash) in ARMS.items():
        g = s[s.arm == arm].sort_values("store_tokens")
        x, y = g.store_tokens.to_numpy(), g.quality_mean.to_numpy()
        ax_q.fill_between(x, g.quality_ci_low, g.quality_ci_high, color=colour, alpha=0.08, linewidth=0)
        ax_q.plot(x, y, color=colour, linestyle=dash, linewidth=2, solid_capstyle="round", dash_capstyle="round")
        _end_label(ax_q, x[-1], y[-1], f"{label} {y[-1]:.2f}", colour, q_offsets[arm])
    ax_q.set_ylim(2.3, 3.02)
    ax_q.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}"))
    qx = cross["quality"]["EMB"].get("crossover_tokens")
    if qx:
        ax_q.axvline(qx, color=AXIS, linewidth=1, linestyle=(0, (2, 2)), zorder=0)
        ax_q.text(qx * 1.05, 2.33, f"embeddings ≥ full prompt\nfrom ~{qx / 1000:.0f}k (not significant)",
                  color=MUTED, fontsize=10, va="bottom")

    # --- cost -----------------------------------------------------------------------------------
    _style(ax_c, "Cost per 1,000 questions (US$, log scale)")
    for arm, (label, colour, dash) in ARMS.items():
        g = s[s.arm == arm].sort_values("store_tokens")
        x, y = g.store_tokens.to_numpy(), g.cost_actual_mean.to_numpy() * 1000
        ax_c.plot(x, y, color=colour, linestyle=dash, linewidth=2, solid_capstyle="round", dash_capstyle="round")
        _end_label(ax_c, x[-1], y[-1], f"{label} ${y[-1]:.2f}", colour, {"CTX": 0, "EMB": 7, "MD": -7}[arm])
    g = s[s.arm == "CTX"].sort_values("store_tokens")
    x, y = g.store_tokens.to_numpy(), g.cost_uncached_mean.to_numpy() * 1000
    ax_c.plot(x, y, color=ARMS["CTX"][1], linestyle=(0, (6, 3)), linewidth=1.5, alpha=0.55)
    _end_label(ax_c, x[-1], y[-1], f"Full prompt,\nno caching ${y[-1]:.0f}", ARMS["CTX"][1])
    ax_c.set_yscale("log")
    ax_c.yaxis.set_major_locator(FixedLocator([0.5, 1, 2, 5, 10, 20, 50, 100]))
    ax_c.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:g}"))
    ax_c.yaxis.set_minor_locator(NullLocator())
    ax_c.set_ylim(0.4, 200)
    cx = cross["cost"]["actual"]["EMB"].get("crossover_tokens")
    if cx:
        ax_c.axvline(cx, color=AXIS, linewidth=1, linestyle=(0, (2, 2)), zorder=0)
        ax_c.text(cx * 1.05, 150, f"embeddings cheaper\nfrom ~{cx / 1000:.0f}k", color=MUTED, fontsize=10, va="top")

    fig.text(0.05, 0.94, "When does retrieval beat putting the whole record store in the prompt?",
             color=INK, fontsize=17)
    fig.text(0.05, 0.895, "DeepSeek V4.1 Flash answering 48 doctor and patient questions over synthetic clinic "
                          "records, as the store grows from 40k to 800k tokens.", color=INK_2, fontsize=12)
    fig.text(0.05, 0.05, "Full prompt = whole store in the prompt, with prompt caching. Embeddings = semantic search "
                         "tool (bge-base, top 8). Markdown index = file index in the prompt, files read on demand.\n"
                         "Quality graded fact by fact by a blinded LLM grader (NVIDIA Nemotron 3 Super); no quality "
                         "difference is statistically significant (Wilcoxon, Holm). Cost = actual OpenRouter charges "
                         "(DeepInfra). All patients are synthetic.",
             color=MUTED, fontsize=9.5, va="bottom")

    for ext in ("png", "svg"):
        fig.savefig(OUT / f"headline.{ext}", facecolor=SURFACE)
    print(f"wrote {OUT / 'headline.png'} and .svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
