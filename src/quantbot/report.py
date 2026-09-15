"""Rapport de backtest : graphiques et page HTML autonome.

Les graphiques sont rendus en PNG par matplotlib puis integres en base64
dans un fichier HTML unique, sans dependance externe : le rapport s'ouvre
hors ligne et se transmet tel quel.

Palette : bleu = strategie, orange = indice de reference, rouge = perte.
Les couleurs suivent une palette validee pour le daltonisme, et l'identite
d'une serie n'est jamais portee par la seule couleur (legende + libelles).
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from . import metrics

# --- palette --------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
GRID = "#e2e1dc"
STRATEGY = "#2a78d6"   # bleu, serie 1
BENCHMARK = "#eb6834"  # orange, serie 2
LOSS = "#d03b3b"       # rouge, pole negatif
GAIN = "#2a78d6"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "text.color": INK,
    "axes.labelcolor": INK_SOFT, "xtick.color": INK_SOFT, "ytick.color": INK_SOFT,
    "axes.edgecolor": GRID, "grid.color": GRID, "grid.linewidth": 0.8,
    "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "600",
    "figure.dpi": 130, "axes.spines.top": False, "axes.spines.right": False,
})


def _finish(ax, title: str, ylabel: str = "") -> None:
    ax.set_title(title, loc="left", pad=12, color=INK)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", alpha=0.7, zorder=0)
    ax.set_axisbelow(True)


def _to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
def fig_equity(result, log_scale: bool = True) -> str:
    """Courbe de valeur. Echelle logarithmique : sur 20 ans, une echelle
    lineaire ecrase visuellement les premieres annees et exagere les
    dernieres, ce qui fait paraitre toute strategie meilleure qu'elle n'est."""
    fig, ax = plt.subplots(figsize=(11, 4.6))
    eq = result.equity
    ax.plot(eq.index, eq.to_numpy(), color=STRATEGY, linewidth=2.0,
            label="Strategie", zorder=3)
    if result.benchmark is not None:
        b = result.benchmark.dropna()
        ax.plot(b.index, b.to_numpy(), color=BENCHMARK, linewidth=2.0,
                label="Achat-conservation de l'indice", zorder=2)
    if log_scale:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", " ")))
    ax.legend(frameon=False, loc="upper left", fontsize=10)
    _finish(ax, "Valeur du portefeuille" + (" (echelle logarithmique)" if log_scale else ""))
    return _to_b64(fig)


def fig_drawdown(result) -> str:
    fig, ax = plt.subplots(figsize=(11, 2.9))
    dd = metrics.drawdown_series(result.equity) * 100
    ax.fill_between(dd.index, dd.to_numpy(), 0, color=LOSS, alpha=0.18, zorder=2)
    ax.plot(dd.index, dd.to_numpy(), color=LOSS, linewidth=1.6, zorder=3)
    worst = dd.min()
    ax.annotate(f"pire : {worst:.1f}%", xy=(dd.idxmin(), worst),
                xytext=(6, 10), textcoords="offset points",
                fontsize=10, color=INK, fontweight="600")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    _finish(ax, "Ecart au plus haut historique")
    return _to_b64(fig)


def fig_annual_returns(result) -> str:
    """Rendements par annee civile. Barres groupees, jamais deux axes."""
    eq = result.equity
    yearly = eq.groupby(eq.index.year).last().pct_change().dropna() * 100
    first_year = eq.index[0].year
    partial = eq[eq.index.year == first_year]
    if len(partial) > 20:
        yearly.loc[first_year] = (partial.iloc[-1] / partial.iloc[0] - 1) * 100
        yearly = yearly.sort_index()

    fig, ax = plt.subplots(figsize=(11, 3.4))
    x = np.arange(len(yearly))
    has_bench = result.benchmark is not None
    width = 0.38 if has_bench else 0.62

    # Avec deux series, la couleur identifie la serie (sinon la legende ment).
    # Avec une seule, on peut coder le signe : bleu positif / rouge negatif.
    colors = STRATEGY if has_bench else [GAIN if v >= 0 else LOSS for v in yearly]
    ax.bar(x - (width / 2 if has_bench else 0), yearly.to_numpy(), width,
           color=colors, label="Strategie", zorder=3)
    if has_bench:
        b = result.benchmark.dropna()
        by = b.groupby(b.index.year).last().pct_change().dropna() * 100
        # Meme traitement que la strategie pour la premiere annee, partielle :
        # sans cela la barre de l'indice manque et la comparaison est faussee.
        b_partial = b[b.index.year == first_year]
        if len(b_partial) > 20:
            by.loc[first_year] = (b_partial.iloc[-1] / b_partial.iloc[0] - 1) * 100
        by = by.sort_index().reindex(yearly.index)
        ax.bar(x + width / 2, by.to_numpy(), width, color=BENCHMARK,
               alpha=0.85, label="Indice", zorder=3)
        ax.legend(frameon=False, fontsize=10)

    ax.axhline(0, color=INK_SOFT, linewidth=1.0, zorder=4)
    ax.set_xticks(x)
    ax.set_xticklabels([str(y) for y in yearly.index], rotation=45, ha="right", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    _finish(ax, "Performance par annee civile")
    return _to_b64(fig)


def fig_exposure(result) -> str:
    fig, ax = plt.subplots(figsize=(11, 2.6))
    exp = result.net_exposure * 100
    # L'exposition change par paliers : un trace en marches est plus honnete
    # qu'une interpolation lineaire entre deux rebalancements.
    ax.fill_between(exp.index, exp.to_numpy(), 0, color=STRATEGY, alpha=0.20,
                    step="post", linewidth=0, zorder=2)
    ax.plot(exp.index, exp.to_numpy(), color=STRATEGY, linewidth=0.9,
            drawstyle="steps-post", alpha=0.9, zorder=3)
    moy = float(exp.mean())
    ax.axhline(moy, color=INK_SOFT, linewidth=1.0, linestyle=(0, (4, 3)), zorder=4)
    ax.annotate(f"moyenne {moy:.0f}%", xy=(exp.index[int(len(exp) * 0.02)], moy),
                xytext=(0, 5), textcoords="offset points", fontsize=9, color=INK_SOFT)
    ax.set_ylim(0, max(105, float(exp.max()) * 1.05))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    _finish(ax, "Exposition au marche (le reste est en liquidites)")
    return _to_b64(fig)


# ---------------------------------------------------------------------------
_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; background:#f4f3ef; color:#0b0b0b;
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 40px 24px 72px; }
h1 { font-size: 27px; margin: 0 0 4px; letter-spacing: -0.02em; }
h2 { font-size: 17px; margin: 40px 0 14px; letter-spacing: -0.01em; }
.sub { color:#52514e; margin: 0 0 28px; font-size: 14px; }
.card { background:#fcfcfb; border:1px solid #e2e1dc; border-radius:10px; padding:18px; margin-bottom:18px; }
.card img { width:100%; height:auto; display:block; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:22px; }
.kpi { background:#fcfcfb; border:1px solid #e2e1dc; border-radius:10px; padding:14px 16px; }
.kpi .label { font-size:12px; color:#52514e; text-transform:uppercase; letter-spacing:.05em; }
.kpi .value { font-size:24px; font-weight:650; margin-top:5px; letter-spacing:-0.02em;
  font-variant-numeric: tabular-nums; }
.pos { color:#0ca30c; } .neg { color:#d03b3b; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th,td { text-align:right; padding:8px 12px; border-bottom:1px solid #e2e1dc; font-variant-numeric:tabular-nums; }
th:first-child, td:first-child { text-align:left; font-variant-numeric:normal; }
th { font-weight:600; color:#52514e; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
tbody tr:last-child td { border-bottom:none; }
.warn { background:#fff8e8; border:1px solid #f0d9a0; border-left:4px solid #fab219;
  border-radius:8px; padding:16px 18px; margin:28px 0; font-size:14px; }
.warn strong { display:block; margin-bottom:6px; }
.warn ul { margin:8px 0 0; padding-left:20px; } .warn li { margin:4px 0; }
footer { margin-top:44px; padding-top:18px; border-top:1px solid #e2e1dc; color:#52514e; font-size:13px; }
"""

_DISCLAIMER = """
<div class="warn">
<strong>A lire avant d'interpreter ces chiffres</strong>
<ul>
<li><b>Biais du survivant</b> : l'univers ne contient que des societes encore
cotees aujourd'hui. Les rendements affiches sont donc optimistes, de l'ordre
de 1 a 4 points par an selon les etudes.</li>
<li><b>Un backtest n'est pas un resultat</b> : c'est une simulation sur le
passe. Elle ne dit rien de ce que la strategie fera demain.</li>
<li><b>Surajustement</b> : plus on essaie de reglages, plus le meilleur d'entre
eux doit sa performance au hasard. Seule la validation walk-forward donne une
estimation a peu pres honnete.</li>
<li><b>Frais</b> : commissions et slippage sont modelises forfaitairement.
L'impact de marche reel, l'ecart achat-vente sur les titres peu liquides et la
fiscalite ne le sont pas.</li>
<li>Outil de recherche a but educatif. Ceci ne constitue pas un conseil en
investissement.</li>
</ul>
</div>
"""


def _kpi(label: str, value: str, tone: str = "") -> str:
    cls = f" {tone}" if tone else ""
    return f'<div class="kpi"><div class="label">{label}</div><div class="value{cls}">{value}</div></div>'


def _stats_table(stats: dict) -> str:
    rows = []
    for key, value in stats.items():
        label = metrics.LABELS.get(key, key)
        if isinstance(value, float) and not np.isfinite(value):
            shown = "n/a"
        elif key in metrics.PERCENT_KEYS and isinstance(value, (int, float)):
            shown = f"{value:.2%}"
        elif isinstance(value, float):
            shown = f"{value:.2f}"
        else:
            shown = str(value)
        rows.append(f"<tr><td>{label}</td><td>{shown}</td></tr>")
    return ('<div class="card scroll"><table><thead><tr><th>Mesure</th><th>Valeur</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def build_html(result, stats: dict, title: str = "Rapport de backtest",
               walk_forward=None) -> str:
    tone = "pos" if stats.get("cagr", 0) >= 0 else "neg"
    dd_tone = "neg" if stats.get("max_drawdown", 0) < -0.20 else ""
    kpis = "".join([
        _kpi("Perf. annualisee", f"{stats.get('cagr', 0):.1%}", tone),
        _kpi("Ratio de Sharpe", f"{stats.get('sharpe', 0):.2f}"),
        _kpi("Perte maximale", f"{stats.get('max_drawdown', 0):.1%}", dd_tone),
        _kpi("Volatilite", f"{stats.get('volatility', 0):.1%}"),
        _kpi("Rotation / an", f"{stats.get('annual_turnover', 0):.0%}"),
        _kpi("Exposition moy.", f"{stats.get('avg_exposure', 0):.0%}"),
    ])

    charts = "".join(
        f'<div class="card"><img alt="{alt}" src="data:image/png;base64,{png}"></div>'
        for alt, png in [
            ("Courbe de valeur du portefeuille", fig_equity(result)),
            ("Ecart au plus haut historique", fig_drawdown(result)),
            ("Performance par annee civile", fig_annual_returns(result)),
            ("Exposition au marche", fig_exposure(result)),
        ]
    )

    wf_html = ""
    if walk_forward is not None and len(walk_forward.windows):
        w = walk_forward.windows.copy()
        for col in ("test_cagr", "test_max_dd"):
            if col in w:
                w[col] = w[col].map(lambda v: f"{v:.1%}" if pd.notna(v) else "n/a")
        if "test_sharpe" in w:
            w["test_sharpe"] = w["test_sharpe"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "n/a")
        w = w.rename(columns={"train_start": "Debut apprentissage", "train_end": "Fin apprentissage",
                              "test_end": "Fin test", "params": "Parametres retenus",
                              "test_cagr": "CAGR test", "test_sharpe": "Sharpe test",
                              "test_max_dd": "Perte max test"})
        keep = [c for c in w.columns if not c.startswith(("train_sharpe", "train_calmar", "train_cagr"))]
        o = walk_forward.oos_stats
        wf_html = (
            "<h2>Validation hors echantillon (walk-forward)</h2>"
            "<p class=\"sub\">Parametres choisis sur la fenetre d'apprentissage, "
            "appliques a la fenetre suivante jamais vue. C'est le seul resultat "
            "a peu pres honnete de ce rapport.</p>"
            '<div class="kpis">'
            + _kpi("CAGR hors echantillon", f"{o.get('cagr', 0):.1%}",
                   "pos" if o.get("cagr", 0) >= 0 else "neg")
            + _kpi("Sharpe hors echantillon", f"{o.get('sharpe', 0):.2f}")
            + _kpi("Perte max hors ech.", f"{o.get('max_drawdown', 0):.1%}")
            + "</div>"
            + '<div class="card scroll">' + w[keep].to_html(index=False, border=0) + "</div>"
        )

    meta = result.meta
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{title}</h1>
<p class="sub">Periode {meta.get('start', '?')} au {meta.get('end', '?')} &middot;
{meta.get('n_assets', '?')} titres dans l'univers &middot;
{meta.get('n_signal_dates', '?')} dates de rebalancement &middot;
frais {meta.get('cost_rate', 0) * 10000:.0f} points de base par transaction</p>
<div class="kpis">{kpis}</div>
{charts}
<h2>Mesures detaillees</h2>
{_stats_table(stats)}
{wf_html}
{_DISCLAIMER}
<footer>Genere par quantbot v0.1 &middot; {pd.Timestamp.now():%d/%m/%Y %H:%M}</footer>
</div></body></html>"""


def save_report(result, stats: dict, path: str | Path,
                title: str = "Rapport de backtest", walk_forward=None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(result, stats, title, walk_forward), encoding="utf-8")
    return path
