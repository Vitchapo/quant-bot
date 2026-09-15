"""Meme mesure, sans le defaut de la premiere version.

Version 1 tronquait les cours a 2020-09, ce qui privait le momentum 12-1 de
son historique : les signaux de la premiere annee etaient degenerés. Ici on
garde TOUT l'historique et on n'applique le filtre d'appartenance qu'a partir
de la date ou il est connu (avant, le masque est entierement vrai). Les deux
backtests ont donc exactement la meme chauffe, et tout ecart apres cette date
vient du seul filtre d'appartenance.

    python scripts/mesurer_biais.py

Attention : le chiffre corrige reste OPTIMISTE. Les societes sorties de la
cote n'ont aucune serie de cours dans le cache, donc leurs rendements - le
plus souvent tres negatifs - manquent toujours au calcul.
"""
import argparse
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))
import numpy as np, pandas as pd
from quantbot.config import Config
from quantbot.datafeed import load_panel
from quantbot.backtest import run_backtest, _prepare_prices
from quantbot.pointintime import reconstruire
from quantbot import metrics

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--config", default="config/us.yaml")
ap.add_argument("--changements", default="data/sp500_changements.csv")
ap.add_argument("--debut", default="2020-09-21",
                help="premiere date ou l'appartenance est reellement connue")
args = ap.parse_args()

cfg = Config.load(args.config)
prices = load_panel(cfg)
close, raw, _ = _prepare_prices(prices, cfg)
tickers = list(close.columns)
changements = pd.read_csv(args.changements).fillna("")
app = reconstruire(tickers, changements)

DEBUT = pd.Timestamp(args.debut)
masque = app.masque(close.index, tickers)
masque.loc[masque.index < DEBUT] = True     # chauffe identique pour les deux

sans = run_backtest(prices, cfg)
avec = run_backtest(prices, cfg, appartenance=masque)

def sous_periode(res):
    eq = res.equity.loc[DEBUT:]
    r = res.returns.loc[DEBUT:]
    annees = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1/annees) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    sharpe = r.mean() / r.std(ddof=1) * np.sqrt(252)
    dd = float((eq / eq.cummax() - 1).min())
    return cagr, vol, sharpe, dd, annees

a = sous_periode(sans); b = sous_periode(avec)
bench = sans.benchmark.loc[DEBUT:].dropna()
bench_cagr = (bench.iloc[-1]/bench.iloc[0]) ** (1/a[4]) - 1

print("=== %s -> %s (%.1f ans), chauffe complete depuis 2005 ==="
      % (DEBUT.date(), sans.equity.index[-1].date(), a[4]))
print("  %-24s %14s %14s %10s" % ("", "liste 2026", "liste d'epoque", "ecart"))
for i, (lib, pct) in enumerate([("rendement annualise", True), ("volatilite", True),
                                ("Sharpe", False), ("perte maximale", True)]):
    if pct:
        print("  %-24s %13.2f%% %13.2f%% %8.2f pt" % (lib, a[i]*100, b[i]*100, (b[i]-a[i])*100))
    else:
        print("  %-24s %14.2f %14.2f %10.2f" % (lib, a[i], b[i], b[i]-a[i]))
print("  %-24s %13.2f%%" % ("SPY sur la periode", bench_cagr*100))

# --- le mecanisme : la strategie achete-t-elle des NON-MEMBRES ? ----------
print()
print("=== d'ou vient l'ecart ===")
w = sans.weights.loc[DEBUT:]
m = masque.loc[w.index, w.columns]
detenu = w > 1e-9
hors = detenu & ~m
print("  part du portefeuille investie dans des titres PAS ENCORE dans l'indice :")
for an, grp in (hors.sum(axis=1) / detenu.sum(axis=1).clip(lower=1)).groupby(w.index.year):
    print("    %d : %.0f%% des lignes" % (an, grp.mean() * 100))
poids_hors = (w.where(hors).sum(axis=1))
print("  soit %.0f%% du capital en moyenne sur la periode" % (poids_hors.mean()*100))

top = (hors.sum(axis=0).sort_values(ascending=False).head(12))
print("  titres les plus detenus AVANT leur entree dans l'indice :")
print("   ", ", ".join("%s (%d seances)" % (t, n) for t, n in top.items() if n > 0))
