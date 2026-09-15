#!/usr/bin/env python3
"""Compare ce que le plan avait prevu a ce que le courtier a reellement execute.

    python scripts/verifier_executions.py --config config/us.yaml
    python scripts/verifier_executions.py --config config/us.yaml --depuis 2026-09-08

C'est la SEULE mesure qu'une courte periode de simulation produise vraiment.
Le rendement du portefeuille, sur quelques jours, n'est que du bruit : avec 18 %
de volatilite annuelle, une seance a +1,1 % est un ecart-type, soit une seance
sur six. Il faudrait des annees pour en tirer quoi que ce soit.

L'execution, elle, se mesure PAR ORDRE. Vingt ordres passes en un mois font
vingt observations, et vingt observations suffisent a savoir si l'hypothese de
frais du backtest est vaguement juste ou completement fantaisiste.

Ce que ce script mesure exactement
----------------------------------
L'ecart entre le cours qui a servi a DIMENSIONNER l'ordre (la derniere cloture
en cache) et le cours reellement OBTENU. Cet ecart contient deux choses qu'on
ne peut pas separer sans enregistrer la fourchette au moment de l'envoi :

  * le glissement d'execution proprement dit (fourchette, impact) ;
  * la derive du marche entre la cloture utilisee et le moment de l'execution.

Les deux comptent, et c'est bien cette somme que le backtest doit modeliser :
lui aussi suppose une execution a un cours posterieur au signal. Le script
affiche le decalage en jours pour que tu saches quelle part vient de quoi.

Enfin : en SIMULATION, Alpaca remplit au cours affiche, sans impact de marche.
Le chiffre obtenu est donc une BORNE BASSE. Le reel sera pire.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import warnings
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import broker
from quantbot.config import Config
from quantbot.universe import UniverseError

warnings.filterwarnings("ignore", category=FutureWarning)

JOURNAL = Path("data/journal_ordres.csv")


def _bps(x):
    return "%+7.1f" % (10_000 * x)


from quantbot.executions import barre_etablie as _barre_etablie


def _cloture_du_jour(cfg, tickers, dates):
    """Cloture de chaque titre a la date de son execution, depuis le cache.

    C'est le comparateur que le backtest utilise reellement : il suppose une
    execution AU COURS DE CLOTURE de la seance d'execution. L'ecart entre le
    prix obtenu et cette cloture est donc exactement l'erreur de modelisation,
    debarrassee de toute derive multi-journees.
    """
    try:
        from quantbot import datafeed
        panel = datafeed.load_panel(cfg)
    except Exception:
        return {}
    out = {}
    for ticker, date in zip(tickers, dates):
        df = panel.get(ticker)
        if df is None or not date:
            continue
        try:
            import pandas as pd
            jour = pd.Timestamp(date).normalize()
            if jour in df.index:
                out[(ticker, date)] = float(df.loc[jour, "close"])
        except Exception:
            continue
    return out


def analyser(cfg, api, lignes, verbeux=False) -> int:
    releves, sans_suite = [], []
    for ligne in lignes:
        identifiant = (ligne.get("id_courtier") or "").strip()
        if not identifiant or ligne.get("statut") == "ECHEC":
            sans_suite.append((ligne.get("ticker"), ligne.get("statut") or "sans identifiant"))
            continue
        try:
            ordre = api.ordre(identifiant)
        except broker.BrokerError as exc:
            sans_suite.append((ligne.get("ticker"), str(exc)[:60]))
            continue

        prix_obtenu = ordre.get("filled_avg_price")
        if not prix_obtenu or ordre.get("status") != "filled":
            sans_suite.append((ligne.get("ticker"), ordre.get("status", "?")))
            continue

        prevu = float(ligne["cours"])
        obtenu = float(prix_obtenu)
        sens = ligne["sens"]
        signe = 1.0 if sens == "buy" else -1.0
        quantite = float(ordre.get("filled_qty") or 0.0)
        rempli_a = (ordre.get("filled_at") or "")[:10]
        releves.append({
            "ticker": ligne["ticker"], "sens": sens, "prevu": prevu, "obtenu": obtenu,
            "signe": signe,
            "ecart": signe * (obtenu - prevu) / prevu,
            "montant": quantite * obtenu,
            "date_cours": (ligne.get("date_cours") or "").strip(),
            "rempli_le": rempli_a,
            "fourchette_bps": float(ligne["fourchette_bps"])
            if (ligne.get("fourchette_bps") or "").strip() else None,
        })
        marche = (ligne.get("cours_marche") or "").strip()
        releves[-1]["ecart_marche"] = (
            signe * (obtenu - float(marche)) / float(marche)) if marche else None
        releves[-1]["cout"] = releves[-1]["ecart"] * releves[-1]["montant"]

    if not releves:
        print("Aucun ordre execute a analyser.")
        for t, raison in sans_suite:
            print("  %-8s %s" % (t, raison))
        return 1

    # -- comparateur propre : la cloture de la seance d'execution -----------
    clotures = _cloture_du_jour(cfg, [r["ticker"] for r in releves],
                                [r["rempli_le"] for r in releves])
    for r in releves:
        c = clotures.get((r["ticker"], r["rempli_le"]))
        r["cloture"] = c
        r["ecart_cloture"] = r["signe"] * (r["obtenu"] - c) / c if c else None

    print("\n%s\n  EXECUTIONS  (%d ordres remplis)\n%s" % ("=" * 84, len(releves), "=" * 84))
    print("  %-8s %-6s %10s %10s %11s %11s" %
          ("ticker", "sens", "prevu", "obtenu", "/plan bps", "/cloture"))
    for r in sorted(releves, key=lambda x: -abs(x["ecart"])):
        vs_cloture = _bps(r["ecart_cloture"]) if r["ecart_cloture"] is not None else "      n/a"
        print("  %-8s %-6s %10.2f %10.2f %11s %11s" %
              (r["ticker"], "VENTE" if r["sens"] == "sell" else "ACHAT",
               r["prevu"], r["obtenu"], _bps(r["ecart"]), vs_cloture))

    echange = sum(r["montant"] for r in releves)
    ecarts = [r["ecart"] for r in releves]

    # ---- A. la seule mesure honnete : la fourchette au moment de l'envoi ---
    avec_fourchette = [r for r in releves if r.get("ecart_marche") is not None]
    print("\n%s\n  A. ECART A LA FOURCHETTE AU MOMENT DE L'ENVOI  (le cout reel)\n%s"
          % ("=" * 84, "=" * 84))
    if not avec_fourchette:
        print("""  Indisponible pour ces ordres : la fourchette n'a pas ete relevee au moment
  de l'envoi. C'est la seule reference contemporaine d'une execution, et donc
  la seule qui mesure un COUT plutot qu'un mouvement de marche.

  Les prochains ordres passes par trade.py l'enregistreront automatiquement.
  Les blocs B et C ci-dessous sont donnes pour information, mais aucun des
  deux ne peut servir a regler execution.slippage_bps.""")
    else:
        e2 = sum(r["montant"] for r in avec_fourchette)
        pond = sum(r["ecart_marche"] * r["montant"] for r in avec_fourchette) / e2
        vals = [r["ecart_marche"] for r in avec_fourchette]
        fourchettes = [r["fourchette_bps"] for r in avec_fourchette if r.get("fourchette_bps")]
        print("  sur %d ordre(s)" % len(avec_fourchette))
        print("  cout moyen pondere             %s bps" % _bps(pond))
        print("  mediane                        %s bps" % _bps(statistics.median(vals)))
        if fourchettes:
            print("  demi-fourchette moyenne        %7.1f bps  (plancher theorique)"
                  % (statistics.fmean(fourchettes) / 2))
        hypothese = (float(cfg.get("execution.commission_bps", 0.0))
                     + float(cfg.get("execution.slippage_bps", 0.0)))
        reel = 10_000 * pond
        print("  hypothese du backtest          %7.1f bps" % hypothese)
        if reel < -1.0:
            print("""
  ATTENTION : un cout d'execution NEGATIF n'existe pas durablement. Obtenir
  systematiquement mieux que la fourchette voudrait dire gagner de l'argent
  en passant des ordres. C'est le signe que la mesure est faussee, pas d'une
  bonne execution. Ne t'en sers pas.""")
        elif reel > hypothese:
            print("  -> remonte execution.slippage_bps a environ %.0f bps." % reel)
        else:
            print("  -> l'hypothese du backtest tient.")

    # ---- B. la cloture du jour, si tant est qu'elle soit etablie ----------
    propres = [r for r in releves if r.get("ecart_cloture") is not None]
    print("\n%s\n  B. ECART A LA CLOTURE DU JOUR D'EXECUTION\n%s" % ("=" * 84, "=" * 84))
    non_etablies = sorted({r["rempli_le"] for r in propres if not _barre_etablie(r["rempli_le"])})
    if non_etablies:
        print("""  MESURE REFUSEE : la seance %s n'est pas terminee.

  La barre du jour presente dans le cache est une barre EN COURS - sa
  "cloture" n'est que le dernier echange au moment du telechargement. La
  comparer a un remplissage du matin revient a mesurer le chemin parcouru par
  le titre depuis, pas le cout de l'ordre.

  Relance fetch_data.py APRES la cloture (22h00 heure de Paris), puis ce
  script.""" % ", ".join(non_etablies))
    elif not propres:
        print("  Indisponible : le cache ne contient pas la seance d'execution.")
        print("  Lance  python scripts/fetch_data.py --config <ta config>  puis relance.")
    else:
        e3 = sum(r["montant"] for r in propres)
        pond3 = sum(r["ecart_cloture"] * r["montant"] for r in propres) / e3
        print("  sur %d ordre(s), moyenne ponderee %s bps" % (len(propres), _bps(pond3)))
        print("""
  A lire avec prudence : meme sur une seance terminee, ce chiffre compare un
  remplissage du matin a un cours du soir. Il melange encore la derive de la
  journee au cout de l'ordre. Le bloc A reste la mesure de reference.""")

    # ---- C. le cours du plan : contexte, jamais une estimation de frais ----
    pondere = sum(r["cout"] for r in releves) / echange if echange else 0.0
    print("\n%s\n  C. ECART AU COURS DU PLAN  (derive du marche, pour information)\n%s"
          % ("=" * 84, "=" * 84))
    print("  moyenne ponderee %s bps   |   simple %s bps   |   pire %s bps"
          % (_bps(pondere), _bps(statistics.fmean(ecarts)), _bps(max(ecarts))))
    ecarts_jours = []
    for r in releves:
        if r["date_cours"] and r["rempli_le"]:
            try:
                import numpy as np
                ecarts_jours.append(int(np.busday_count(r["date_cours"], r["rempli_le"])))
            except Exception:
                pass
    if ecarts_jours:
        print("  seances entre la reference et l'execution : %d" % statistics.median(ecarts_jours))
    print("""
  Ce bloc n'est PAS une estimation de frais et ne doit jamais servir a regler
  execution.slippage_bps. Une derive de marche est du bruit qui s'annule en
  moyenne sur beaucoup de rebalancements ; un cout de frais se paie a chaque
  fois. Les confondre rendrait le backtest absurdement pessimiste.""")

    print("\n  montant echange                %12.2f" % echange)
    print("  Rappel : en simulation, le courtier remplit au cours affiche, sans impact")
    print("  de marche. Tout chiffre obtenu ici est une BORNE BASSE.")

    if sans_suite:
        print("\n  %d ordre(s) sans execution exploitable :" % len(sans_suite))
        for t, raison in sans_suite[:10]:
            print("    %-8s %s" % (t, raison))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--reel", action="store_true")
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--depuis", help="ne prendre que les ordres a partir de cette date (AAAA-MM-JJ)")
    args = ap.parse_args()

    chemin = Path(args.journal)
    if not chemin.exists():
        print("Journal introuvable : %s" % chemin)
        print("Il est ecrit par scripts/trade.py --envoyer.")
        return 2
    with chemin.open(encoding="utf-8") as fh:
        lignes = list(csv.DictReader(fh))
    if args.depuis:
        lignes = [l for l in lignes if (l.get("horodatage") or "") >= args.depuis]
    if not lignes:
        print("Aucun ordre dans le journal pour cette periode.")
        return 1

    cfg = Config.load(args.config)
    api = broker.connecter(cfg, reel=args.reel)
    numero = str(api.compte().get("account_number", ""))

    # On n'analyse que les ordres du compte auquel on est connecte. Sans ce
    # filtre, des lignes venues d'un autre compte - un essai, une autre paire
    # de cles - se melangent en silence et faussent toute la mesure.
    connus = [l for l in lignes if l.get("compte")]
    autres = [l for l in connus if l["compte"] != numero]
    if autres:
        print("  %d ligne(s) d'un autre compte, ecartee(s)" % len(autres))
    anciennes = len(lignes) - len(connus)
    if anciennes:
        print("  %d ligne(s) anterieure(s) a la colonne 'compte' : conservees, "
              "mais leur origine n'est pas verifiable" % anciennes)
    lignes = [l for l in lignes if not l.get("compte") or l["compte"] == numero]

    print("%d ordre(s) a analyser, compte %s %s"
          % (len(lignes), "REEL" if args.reel else "de simulation", numero))
    return analyser(cfg, api, lignes)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (broker.BrokerError, UniverseError, FileNotFoundError) as exc:
        print("\n" + "-" * 72); print(exc); print("-" * 72)
        sys.exit(2)
