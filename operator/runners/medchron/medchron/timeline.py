"""The Treatment Timeline visuals: a provider swimlane with the incident
marker, one calendar block per claim-period year with treatment days filled,
and the colour legend. Every marker is a dated entry from the assembled
chronology; every lane is a facility through the same canon the exhibits
use (a separate table here once drew one provider as three lanes); the
incident line is the envelope's date, never derived from gap arithmetic.
Treatment gaps are reported within the claim period only (pre-incident
intervals are gaps in ordinary care, not breaks in injury treatment), at the
firm's `chronology.treatment_gap_days`.
"""

from __future__ import annotations

import re
from calendar import Calendar, month_name
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

PALETTE = ["#e8447f", "#f5a623", "#7ac943", "#9b59d0", "#2ecc9b", "#2d9cdb", "#f2c94c", "#eb5757", "#56ccf2", "#27ae60"]
FONT = "DejaVu Sans"
ENTRY_SPLIT = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE_HEAD = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")


def _provider_of(lines: list[str], headings: set[str]) -> str:
    """The provider on an entry's header line: the first `|` line whose left
    side is a name rather than one of the section titles."""
    for ln in lines[1:5]:
        if "|" not in ln:
            continue
        left = ln.split("|")[0].strip()
        if left and left.lower() not in headings:
            return left
    return ""


def load(md_text: str, canon: Callable[[str], str], headings: set[str]) -> dict[str, list[date]]:
    body = md_text.split("## Medical Chronology\n", 1)[1].split("## Exhibit List", 1)[0]
    per: dict[str, set[date]] = defaultdict(set)
    for chunk in ENTRY_SPLIT.split(body):
        chunk = chunk.strip()
        m = DATE_HEAD.match(chunk)
        if not m:
            continue
        mm, dd, yy = m.groups()
        try:
            d = date(int(yy), int(mm), int(dd))
        except ValueError:
            continue
        prov = _provider_of(chunk.splitlines(), headings)
        if prov:
            per[canon(prov) or prov.split(",")[0].strip()].add(d)
    return {k: sorted(v) for k, v in per.items()}


def gaps(all_dates: list[date], incident: date, gap_days: int) -> list[tuple[date, date, int]]:
    return [(a, b, (b - a).days) for a, b in zip(all_dates, all_dates[1:]) if (b - a).days > gap_days and b > incident]


def _plt() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def draw_swimlane(per: dict[str, list[date]], colors: dict[str, str], incident: date, out: Path) -> list[str]:
    plt = _plt()
    lanes = sorted(per, key=lambda f: per[f][0])
    fig, ax = plt.subplots(figsize=(9.2, max(2.6, 0.42 * len(lanes) + 1.5)), dpi=200)
    lo = min(d for ds in per.values() for d in ds)
    hi = max(d for ds in per.values() for d in ds)
    pad = max(20, (hi - lo).days // 25)
    x0, x1 = lo.toordinal() - pad, hi.toordinal() + pad
    ax.axvspan(x0, incident.toordinal(), color="#000000", alpha=0.055, zorder=0)
    for i, fac in enumerate(lanes):
        y = len(lanes) - 1 - i
        ax.axhline(y, color="#e3e3e3", lw=0.8, zorder=1)
        ds, c = per[fac], colors[fac]
        runs, start, prev = [], ds[0], ds[0]
        for d in ds[1:]:
            if (d - prev).days <= 21:
                prev = d
                continue
            runs.append((start, prev))
            start = prev = d
        runs.append((start, prev))
        for a, b in runs:
            if a == b:
                ax.plot([a.toordinal()], [y], "o", ms=5.2, color=c, zorder=3, markeredgewidth=0)
            else:
                ax.plot([a.toordinal(), b.toordinal()], [y, y], lw=6.0, color=c, solid_capstyle="round", zorder=3)
    ax.axvline(incident.toordinal(), color="#1f3864", ls="--", lw=1.2, zorder=4)
    ax.annotate(
        f"Date of Incident\n{incident.strftime('%m/%d/%Y')}",
        xy=(incident.toordinal(), len(lanes) - 0.35),
        ha="center",
        va="bottom",
        fontsize=7.5,
        color="#1f3864",
        fontname=FONT,
        linespacing=1.3,
        zorder=6,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none", alpha=0.92),
    )
    ax.set_yticks(range(len(lanes)))
    ax.set_yticklabels([f if len(f) <= 30 else f[:29] + "…" for f in reversed(lanes)], fontsize=7.5, fontname=FONT)
    ticks, labels = [], []
    cur = date(lo.year, ((lo.month - 1) // 3) * 3 + 1, 1)
    while cur.toordinal() <= x1:
        ticks.append(cur.toordinal())
        labels.append(cur.strftime("%b %Y"))
        cur = date(cur.year + (cur.month + 2) // 12, (cur.month + 2) % 12 + 1, 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right", fontname=FONT)
    ax.set_xlim(x0, x1)
    ax.set_ylim(-0.8, len(lanes) - 0.05)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#bdbdbd")
    ax.tick_params(axis="both", length=0)
    fig.tight_layout()
    fig.savefig(str(out), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return lanes


def draw_year(year: int, months: list[int], marked: dict[date, str], out: Path) -> None:
    import matplotlib.patches as mpatches

    plt = _plt()
    ncol = 4
    nrow = (len(months) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(9.2, 1.62 * nrow + 0.45), dpi=200)
    axes = [axes] if nrow * ncol == 1 else list(axes.flat)
    cal = Calendar(firstweekday=6)
    for ax in axes:
        ax.axis("off")
    fw, fh = fig.get_size_inches()
    for idx, mo in enumerate(months):
        ax = axes[idx]
        bb = ax.get_position()
        aspect = (bb.width * fw) / (bb.height * fh)
        ax.add_patch(
            mpatches.FancyBboxPatch(
                (0.01, 0.01),
                0.98,
                0.98,
                boxstyle="round,pad=0.012,rounding_size=0.03",
                facecolor="#f4f4f6",
                edgecolor="none",
                transform=ax.transAxes,
                zorder=0,
            )
        )
        ax.text(
            0.5,
            0.93,
            month_name[mo],
            ha="center",
            va="center",
            fontsize=8.2,
            fontname=FONT,
            transform=ax.transAxes,
            color="#222222",
        )
        for c, dn in enumerate("SMTWTFS"):
            ax.text(
                0.09 + c * 0.137,
                0.80,
                dn,
                ha="center",
                va="center",
                fontsize=6.4,
                color="#8a8a8a",
                fontname=FONT,
                transform=ax.transAxes,
            )
        for r, week in enumerate(cal.monthdayscalendar(year, mo)):
            for c, day in enumerate(week):
                if not day:
                    continue
                x, y = 0.09 + c * 0.137, 0.68 - r * 0.115
                hit = date(year, mo, day) in marked
                if hit:
                    ax.add_patch(
                        mpatches.Ellipse(
                            (x, y),
                            0.086,
                            0.086 * aspect,
                            facecolor=marked[date(year, mo, day)],
                            edgecolor="none",
                            transform=ax.transAxes,
                            zorder=2,
                        )
                    )
                ax.text(
                    x,
                    y,
                    f"{day:02d}",
                    ha="center",
                    va="center",
                    fontsize=6.2,
                    fontname=FONT,
                    zorder=3,
                    color="white" if hit else "#4a4a4a",
                    transform=ax.transAxes,
                )
    fig.suptitle(str(year), x=0.055, y=0.99, ha="left", fontsize=13, fontname=FONT, color="#1a1a1a")
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(str(out), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def draw_legend(lanes: list[str], colors: dict[str, str], out: Path) -> None:
    import matplotlib.patches as mpatches

    plt = _plt()
    ncol = 4
    nrow = (len(lanes) + ncol - 1) // ncol
    fig, ax = plt.subplots(figsize=(9.2, 0.34 * nrow + 0.3), dpi=200)
    ax.axis("off")
    ax.text(0, 1.0, "Legend:", fontsize=8, fontweight="bold", fontname=FONT, va="top", transform=ax.transAxes)
    for i, fac in enumerate(lanes):
        r, c = divmod(i, ncol)
        x, y = c * 0.253, 0.74 - r * (0.9 / max(nrow, 1))
        ax.add_patch(
            mpatches.FancyBboxPatch(
                (x, y - 0.11),
                0.238,
                0.22,
                boxstyle="round,pad=0.004,rounding_size=0.09",
                facecolor=colors[fac],
                edgecolor="none",
                transform=ax.transAxes,
                zorder=1,
            )
        )
        ax.text(
            x + 0.012,
            y,
            fac if len(fac) <= 27 else fac[:26] + "…",
            fontsize=6.6,
            fontname=FONT,
            va="center",
            ha="left",
            color="#1a1a1a",
            zorder=2,
            transform=ax.transAxes,
        )
    fig.savefig(str(out), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render(
    md_text: str, outdir: Path, incident: date, gap_days: int, canon: Callable[[str], str], headings: set[str]
) -> dict[str, Any]:
    """Draw the three visuals into `outdir` (stale charts cleared first: the
    renderer globs cal-*.png, and one client's calendar once rendered inside
    another client's chronology). Returns the gaps and the lane order."""
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in outdir.iterdir():
        if stale.name.startswith(("cal-", "timeline", "legend")) and stale.suffix == ".png":
            stale.unlink()
    per = load(md_text, canon, headings)
    if not per:
        return {"facilities": 0, "dates": 0, "years": [], "gaps": [], "lane_order": []}
    colors = {f: PALETTE[i % len(PALETTE)] for i, f in enumerate(sorted(per, key=lambda f: per[f][0]))}
    lanes = draw_swimlane(per, colors, incident, outdir / "timeline.png")
    draw_legend(lanes, colors, outdir / "legend.png")
    marked: dict[date, str] = {}
    for fac in lanes:
        for d in per[fac]:
            marked.setdefault(d, colors[fac])
    all_dates = sorted(marked)
    years = sorted({d.year for d in all_dates if d.year >= incident.year})
    for y in years:
        yd = [d for d in all_dates if d.year == y]
        first, last = min(yd).month, max(yd).month
        months = list(range(first, last + 1))
        while len(months) % 4:
            months.insert(0, months[0] - 1) if months[0] > 1 else months.append(months[-1] + 1)
        months = [m for m in months if 1 <= m <= 12]
        draw_year(y, months, marked, outdir / f"cal-{y}.png")
    g = gaps(all_dates, incident, gap_days)
    return {"facilities": len(per), "dates": len(all_dates), "years": years, "gaps": g, "lane_order": lanes}
