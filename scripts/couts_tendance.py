#!/usr/bin/env python3
"""Etape 1 du protocole de tendance : le tableau des couts FTMO, tire des releves.

    python scripts/couts_tendance.py            # calcule et affiche, n'ecrit rien
    python scripts/couts_tendance.py --ecrire   # ecrit docs/tendance_couts.csv
                                                # et data/tendance/tableau_couts.md

Ce que demande docs/protocole_tendance_multi_actifs.md ("Les couts FTMO") :
  - spread retenu : le plus eleve de trois releves faits trois jours
    differents, marche sous-jacent ouvert ;
  - spread entier et commission a chaque transaction ;
  - marge sur les swaps = portage du modele le jour du releve - swap FTMO
    annualise, a l'achat et a la vente, jamais negative.

Comment c'est mesure :
  * Marche sous-jacent ouvert. Indice : les heures de seance de SA bourse, a
    l'heure locale, hors jours feries ; le jour est celui de la bourse (Sydney
    ouvre la veille au soir en UTC). Devises et metaux : tout releve frais
    (cotation de moins de 2 minutes) d'un jour de semaine, date UTC.
  * Releve d'un jour : le spread median des releves de ce jour, la regle
    ecrite dans scripts/releve_ftmo.py avant les releves. Spread retenu : le
    plus eleve des trois premiers jours couverts. Un spread nul est un vrai cours.
  * Swaps : en points par lot et par nuit chez FTMO, changes chaque jour.
    Annualises sur 365 nuits (le swap triple couvre le week-end) et rapportes
    au cours du meme releve. Marge : la mediane, sur les releves des jours de
    semaine, de (portage du modele ce jour-la - swap annualise), plancher 0.
  * Les jours ex-dividende, FTMO credite le dividende aux acheteurs et le
    preleve aux vendeurs, par le swap. Ce n'est pas un cout de financement :
    ces releves sont ecartes (le swap achat monte et le swap vente baisse
    d'autant ; leur somme, elle, ne bouge pas).
  * Taux du modele : ceux de la BRI au jour du releve, puis les decisions
    annoncees apres la derniere date publiee par la BRI (DECISIONS).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

import numpy as np
import pandas as pd

from quantbot import tendance as T
from quantbot import tendance_donnees as D

RACINE = Path(__file__).resolve().parent.parent
RELEVES = RACINE / "data" / "tendance" / "releves_ftmo.csv"
MISES_A_JOUR = RACINE / "data" / "tendance" / "couts"
SORTIE = RACINE / "docs" / "tendance_couts.csv"
TABLEAU = RACINE / "data" / "tendance" / "tableau_couts.md"
JOURS_VOULUS = 3
FRAICHEUR_S = 120
SEUIL_DIVIDENDE = 0.10      # ecart (achat - vente) > 10 % de (|achat| + |vente|) usuels
#: Heures de seance des bourses sous-jacentes, a l'heure locale.
SEANCES = {
    "US500": ("America/New_York", [("09:30", "16:00")]),
    "GER40": ("Europe/Berlin", [("09:00", "17:30")]),
    "UK100": ("Europe/London", [("08:00", "16:30")]),
    "JP225": ("Asia/Tokyo", [("09:00", "15:30")]),
    "AUS200": ("Australia/Sydney", [("10:00", "16:00")]),
    "HK50": ("Asia/Hong_Kong", [("09:30", "12:00"), ("13:00", "16:00")]),
}
#: Jours feries tombes pendant les releves (30/10 - 06/11/2026).
FERIES = {"JP225": {"2026-11-03"}}          # Japon : fete de la culture
#: Commission par lot ET par transaction, en dollars. Devises : 3 $ par lot,
#: lu comme 3 $ par cote (lecture prudente des deux sources : Propvator,
#: "Ftmo trading conditions", 24/08/2026 ; forum BabyPips, "FTMO overall
#: costs"). Indices et metaux : aucune commission, selon les memes sources.
COMMISSION_LOT = {n: (3.0 if T.INSTRUMENTS[n]["famille"] == "devise" else 0.0) for n in T.INSTRUMENTS}
#: Decisions annoncees apres la derniere date publiee par la BRI au 06/11/2026.
DECISIONS = {
    "AU": [("2026-11-04", 4.85)],   # RBA, 03/11/2026 : +0,25 point (savings.com.au)
    "GB": [("2026-11-05", 4.00)],   # BoE, 05/11/2026 : +0,25 point (forexfundamentals.com)
}


# -- les releves -------------------------------------------------------------------
def lire_releves(chemin: Path = RELEVES) -> pd.DataFrame:
    d = pd.read_csv(chemin)
    d["t"] = pd.to_datetime(d["releve_utc"], utc=True)
    d = d[(d["bid"] > 0) & (d["ask"] >= d["bid"]) & (d["t"].dt.dayofweek < 5)].copy()
    d["milieu"] = (d["bid"] + d["ask"]) / 2.0
    d["spread_rel"] = (d["ask"] - d["bid"]) / d["milieu"]
    return d


def ouverts(d: pd.DataFrame, nom: str) -> pd.DataFrame:
    """Les releves frais pris marche sous-jacent ouvert, avec le jour qui compte."""
    x = d[(d["instrument"] == nom) & (d["age_s"] <= FRAICHEUR_S)].copy()
    if nom not in SEANCES:
        x["jour"] = x["t"].dt.strftime("%Y-%m-%d")
        return x
    tz, plages = SEANCES[nom]
    local = x["t"].dt.tz_convert(tz)
    x["jour"] = local.dt.strftime("%Y-%m-%d")
    heure = local.dt.strftime("%H:%M")
    dedans = np.zeros(len(x), dtype=bool)
    for a, b in plages:
        dedans |= ((heure >= a) & (heure < b)).to_numpy()
    garde = dedans & (local.dt.dayofweek < 5).to_numpy() & ~x["jour"].isin(FERIES.get(nom, set())).to_numpy()
    return x[garde]


def spread_retenu(d: pd.DataFrame, nom: str) -> tuple:
    """(spread relatif retenu, jours utilises) : le plus eleve des medianes des
    trois premiers jours couverts. None si moins de trois jours."""
    par_jour = ouverts(d, nom).groupby("jour")["spread_rel"].median()
    if len(par_jour) < JOURS_VOULUS:
        return None, list(par_jour.index)
    trois = par_jour.iloc[:JOURS_VOULUS]
    return float(trois.max()), list(trois.index)


# -- les taux et les swaps ---------------------------------------------------------
def taux_journaliers(brut: Path = None, mises_a_jour: Path = None) -> dict:
    """Code -> serie quotidienne (en %) : BRI, puis decisions annoncees."""
    brut = Path(brut) if brut else RACINE / D.BRUT
    mises_a_jour = Path(mises_a_jour) if mises_a_jour else MISES_A_JOUR
    out = {}
    for code in ("US", "XM", "GB", "JP", "CH", "CA", "AU", "NZ"):
        s = D.lire(brut / ("bri_%s.csv" % code), "bri")
        maj = mises_a_jour / ("bri_%s_2026-11.csv" % code)
        if maj.exists():
            s = pd.concat([s, D.lire(maj, "bri")])
            s = s[~s.index.duplicated(keep="last")].sort_index()
        for jour, niveau in DECISIONS.get(code, []):
            j = pd.Timestamp(jour)
            if j > s.index.max():
                s.loc[j] = niveau
        out[code] = s.sort_index()
    return out


def niveau(serie: pd.Series, jour) -> float:
    """Le taux en vigueur ce jour-la, en fraction."""
    return float(serie[serie.index <= pd.Timestamp(jour)].iloc[-1]) / 100.0


def portage_achat(nom: str, jour, taux: dict) -> float:
    """Ce que le modele du test fait rapporter, par an, a une position acheteuse
    hors variation de cours (voir tendance.portage)."""
    spec = T.INSTRUMENTS[nom]
    if spec["famille"] == "devise":
        return niveau(taux[spec["achetee"]], jour) - niveau(taux[spec["vendue"]], jour)
    return -niveau(taux[spec["taux"]], jour)


def swaps_annualises(x: pd.DataFrame) -> pd.DataFrame:
    """Swaps en fraction du nominal par an : points x point x 365 / cours."""
    x = x.copy()
    x["achat_an"] = x["swap_achat"] * x["point"] * 365.0 / x["milieu"]
    x["vente_an"] = x["swap_vente"] * x["point"] * 365.0 / x["milieu"]
    return x


def jours_de_dividende(x: pd.DataFrame, nom: str) -> pd.Series:
    """Vrai pour les releves dont le swap porte un ajustement de dividende.

    Seuls les indices versent des dividendes. Les swaps des devises et des
    metaux bougent d'un jour a l'autre avec les taux : ce n'est pas un
    dividende, et rien n'y est ecarte.
    """
    if T.INSTRUMENTS[nom]["famille"] != "indice":
        return pd.Series(False, index=x.index)
    ecart = (x["swap_achat"] - x["swap_vente"]) - (x["swap_achat"] - x["swap_vente"]).median()
    usuel = (x["swap_achat"].abs() + x["swap_vente"].abs()).median()
    return ecart.abs() > SEUIL_DIVIDENDE * usuel


def marges(d: pd.DataFrame, nom: str, taux: dict) -> dict:
    x = swaps_annualises(d[d["instrument"] == nom])
    div = jours_de_dividende(x, nom)
    y = x[~div].copy()
    y["portage"] = [portage_achat(nom, t.strftime("%Y-%m-%d"), taux) for t in y["t"]]
    m_a = (y["portage"] - y["achat_an"]).median()
    m_v = (-y["portage"] - y["vente_an"]).median()
    return {"marge_achat": max(0.0, float(m_a)), "marge_vente": max(0.0, float(m_v)),
            "swap_achat_an": float(y["achat_an"].median()), "swap_vente_an": float(y["vente_an"].median()),
            "swap_achat_pts": float(y["swap_achat"].median()), "swap_vente_pts": float(y["swap_vente"].median()),
            "portage_achat": float(y["portage"].median()), "releves_swap": int(len(y)),
            "releves_dividende": int(div.sum()),
            "jours_dividende": [j.strftime("%d/%m") for j in sorted(set(x.loc[div, "t"].dt.normalize()))]}


# -- le tableau -------------------------------------------------------------------
def calculer(d: pd.DataFrame, taux: dict) -> dict:
    out = {}
    for nom in T.INSTRUMENTS:
        x = d[d["instrument"] == nom]
        spread, jours = spread_retenu(d, nom)
        m = marges(d, nom, taux)
        notionnel = float(x["notionnel_compte"].median())
        commission = COMMISSION_LOT[nom] / notionnel if notionnel > 0 else None
        out[nom] = dict(m, spread=spread, jours=jours, commission=commission, notionnel=notionnel,
                        symbole=x["symbole"].iloc[-1], contrat=float(x["contrat"].median()),
                        devise=x["devise_profit"].iloc[-1], base=x["devise_base"].iloc[-1],
                        levier=float(x["levier"].median()),
                        transaction=None if spread is None else spread + (commission or 0.0))
    return out


CHAMPS = ["instrument", "transaction", "marge_achat", "marge_vente", "spread_relatif", "commission_relative",
          "swap_achat_an", "swap_vente_an", "portage_achat", "jours_spread", "releves_swap", "releves_dividende"]


def ecrire_csv(res: dict, chemin: Path = SORTIE) -> None:
    with chemin.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(CHAMPS)
        for nom, r in res.items():
            w.writerow([nom, "%.8f" % r["transaction"], "%.6f" % r["marge_achat"], "%.6f" % r["marge_vente"],
                        "%.8f" % r["spread"], "%.8f" % r["commission"], "%.6f" % r["swap_achat_an"],
                        "%.6f" % r["swap_vente_an"], "%.6f" % r["portage_achat"], " ".join(r["jours"]),
                        r["releves_swap"], r["releves_dividende"]])


def _pc(x: float, d: int = 2) -> str:
    return ("%.*f %%" % (d, 100 * x)).replace(".", ",").replace("-", "−")


def _nb(x: float, d: int = 2) -> str:
    return ("%.*f" % (d, x)).replace(".", ",").replace("-", "−")


def _milliers(x: float) -> str:
    return "{:,.0f}".format(x).replace(",", " ")


def tableau(res: dict) -> str:
    L = ["| instrument | nom MT5 | contrat | spread | commission | swap achat | swap vente | levier | dividendes |",
         "|---|---|---|---|---|---|---|---|---|"]
    for nom, r in res.items():
        famille = T.INSTRUMENTS[nom]["famille"]
        if famille == "devise":
            contrat = "%s %s" % (_milliers(r["contrat"]), r["base"])
        elif famille == "metal":
            contrat = "%s oz" % _milliers(r["contrat"])
        else:
            contrat = "%s × indice (%s)" % ("%d" % r["contrat"], r["devise"])
        com = "%s $ par lot (%s)" % (_nb(COMMISSION_LOT[nom], 0), _pc(r["commission"], 4)) if COMMISSION_LOT[nom] else "aucune"
        if famille != "indice":
            div = "—"
        elif r["releves_dividende"]:
            div = "versés aux acheteurs, prélevés aux vendeurs, par le swap (vu le %s)" % ", ".join(r["jours_dividende"])
        elif nom == "GER40":
            div = "aucun ajustement vu (le DAX réinvestit ses dividendes)"
        else:
            div = "aucun ajustement pendant les relevés"
        L.append("| %s | %s | %s | %s du cours | %s | %s pts/nuit = %s/an ; marge %s | %s pts/nuit = %s/an ; marge %s | 1:%.0f | %s |"
                 % (nom, r["symbole"], contrat, _pc(r["spread"], 4), com,
                    _nb(r["swap_achat_pts"]), _pc(r["swap_achat_an"]), _pc(r["marge_achat"]),
                    _nb(r["swap_vente_pts"]), _pc(r["swap_vente_an"]), _pc(r["marge_vente"]),
                    r["levier"], div))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ecrire", action="store_true")
    args = ap.parse_args(argv)
    d = lire_releves()
    res = calculer(d, taux_journaliers())
    manque = [n for n, r in res.items() if r["spread"] is None]
    print("%-7s %-10s %10s %10s %9s %9s  %s" % ("", "jours", "spread", "transac.", "marge A", "marge V", "dividendes"))
    for n, r in res.items():
        print("%-7s %-10s %9s %10s %8.2f%% %8.2f%%  %s"
              % (n, len(r["jours"]), "-" if r["spread"] is None else "%.4f%%" % (100 * r["spread"]),
                 "-" if r["transaction"] is None else "%.4f%%" % (100 * r["transaction"]),
                 100 * r["marge_achat"], 100 * r["marge_vente"],
                 "%d releve(s) ecarte(s)" % r["releves_dividende"] if r["releves_dividende"] else ""))
    if manque:
        print("\nPas encore %d jours de marche ouvert pour : %s" % (JOURS_VOULUS, ", ".join(manque)))
        return 1
    if args.ecrire:
        ecrire_csv(res)
        TABLEAU.write_text(tableau(res) + "\n", encoding="utf-8")
        print("\nEcrit : %s et %s" % (SORTIE.relative_to(RACINE), TABLEAU.relative_to(RACINE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
