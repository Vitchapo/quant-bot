"""Qualite d'execution : ce qui a ete prevu contre ce qui a ete obtenu.

Le module renvoie des DONNEES, jamais du texte : le script de terminal et le
tableau de bord affichent tous deux le meme calcul, ce qui evite qu'ils
finissent par raconter deux histoires differentes.

Trois references possibles, par ordre de validite decroissante. La hierarchie
est le coeur du module, parce que se tromper de reference conduit a des
conclusions inversees - c'est arrive deux fois avant que ce module existe :

A. la FOURCHETTE relevee au moment de l'envoi. Seule reference contemporaine
   de l'execution, donc seule a mesurer un COUT.
B. la CLOTURE du jour d'execution, et seulement si la seance est terminee.
   Compare encore un matin a un soir : indicatif.
C. le COURS DU PLAN. Separe de l'execution par une ou plusieurs seances : ne
   mesure que la derive du marche. Jamais un cout.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

ACHAT = "buy"


def barre_etablie(date_iso: str, horloge_marche: str = None) -> bool:
    """Une barre n'est exploitable qu'une fois la seance terminee.

    Avant la cloture, un fournisseur de donnees renvoie une barre EN COURS
    dont la "cloture" n'est que le dernier echange connu. La comparer a un
    remplissage du matin mesure le chemin parcouru depuis, pas un cout.

    `horloge_marche` est l'horodatage renvoye par le courtier : il porte deja
    le decalage horaire du marche. L'utiliser evite d'avoir a manipuler des
    fuseaux - `zoneinfo` n'existe pas avant Python 3.9, et surtout le courtier
    est la source qui fait autorite sur ses propres horaires. Sans lui, on
    tranche dans le sens prudent : une seance n'est reputee terminee que le
    lendemain.
    """
    try:
        jour = datetime.fromisoformat(date_iso).date()
    except Exception:
        return False
    if horloge_marche:
        try:
            maintenant = datetime.fromisoformat(horloge_marche)
            if jour < maintenant.date():
                return True
            # marge de 20 minutes : les donnees de cloture ne sont pas immediates
            return jour == maintenant.date() and (
                maintenant.hour > 16 or (maintenant.hour == 16 and maintenant.minute >= 20))
        except Exception:
            pass
    return jour < datetime.now(timezone.utc).date()


def _clotures(cfg, besoins):
    """{(ticker, date): cloture} depuis le cache, pour les couples demandes."""
    if not besoins:
        return {}
    try:
        import pandas as pd
        from . import datafeed
        panel = datafeed.load_panel(cfg)
    except Exception:
        return {}
    out = {}
    for ticker, date in besoins:
        df = panel.get(ticker)
        if df is None or not date:
            continue
        try:
            jour = pd.Timestamp(date).normalize()
            if jour in df.index:
                out[(ticker, date)] = float(df.loc[jour, "close"])
        except Exception:
            continue
    return out


def _agrege(releves, cle):
    """Moyenne ponderee par les montants, mediane, extremes."""
    retenus = [r for r in releves if r.get(cle) is not None]
    if not retenus:
        return None
    montants = sum(r["montant"] for r in retenus)
    valeurs = [r[cle] for r in retenus]
    return {
        "n": len(retenus),
        "pondere": sum(r[cle] * r["montant"] for r in retenus) / montants if montants else 0.0,
        "simple": statistics.fmean(valeurs),
        "median": statistics.median(valeurs),
        "pire": max(valeurs),
        "meilleur": min(valeurs),
        "montant": montants,
    }


def analyser(cfg, api, lignes) -> dict:
    """Rapproche le journal des ordres des executions reelles du courtier."""
    releves, sans_suite = [], []
    for ligne in lignes:
        identifiant = (ligne.get("id_courtier") or "").strip()
        if not identifiant or ligne.get("statut") == "ECHEC":
            sans_suite.append({"ticker": ligne.get("ticker"),
                               "raison": ligne.get("statut") or "sans identifiant"})
            continue
        try:
            ordre = api.ordre(identifiant)
        except Exception as exc:
            sans_suite.append({"ticker": ligne.get("ticker"), "raison": str(exc)[:80]})
            continue
        prix = ordre.get("filled_avg_price")
        if not prix or ordre.get("status") != "filled":
            sans_suite.append({"ticker": ligne.get("ticker"),
                               "raison": ordre.get("status", "inconnu")})
            continue

        prevu, obtenu = float(ligne["cours"]), float(prix)
        sens = ligne["sens"]
        signe = 1.0 if sens == ACHAT else -1.0
        quantite = float(ordre.get("filled_qty") or 0.0)
        marche = (ligne.get("cours_marche") or "").strip()
        r = {
            "ticker": ligne["ticker"], "sens": sens, "prevu": prevu, "obtenu": obtenu,
            "montant": quantite * obtenu, "signe": signe,
            "date_cours": (ligne.get("date_cours") or "").strip(),
            "rempli_le": (ordre.get("filled_at") or "")[:10],
            "fourchette_bps": float(ligne["fourchette_bps"])
            if (ligne.get("fourchette_bps") or "").strip() else None,
            "ecart_plan": signe * (obtenu - prevu) / prevu,
            "ecart_marche": (signe * (obtenu - float(marche)) / float(marche)) if marche else None,
        }
        releves.append(r)

    if not releves:
        return {"releves": [], "sans_suite": sans_suite, "n": 0}

    try:
        horloge = (api.horloge() or {}).get("timestamp")
    except Exception:
        horloge = None
    clotures = _clotures(cfg, {(r["ticker"], r["rempli_le"]) for r in releves})
    for r in releves:
        c = clotures.get((r["ticker"], r["rempli_le"]))
        r["cloture"] = c
        r["ecart_cloture"] = r["signe"] * (r["obtenu"] - c) / c if c else None

    seances_non_finies = sorted({r["rempli_le"] for r in releves
                                 if r.get("ecart_cloture") is not None
                                 and not barre_etablie(r["rempli_le"], horloge)})
    decalages = []
    for r in releves:
        if r["date_cours"] and r["rempli_le"]:
            try:
                import numpy as np
                decalages.append(int(np.busday_count(r["date_cours"], r["rempli_le"])))
            except Exception:
                pass

    hypothese = (float(cfg.get("execution.commission_bps", 0.0))
                 + float(cfg.get("execution.slippage_bps", 0.0)))
    marche = _agrege(releves, "ecart_marche")
    cloture = _agrege(releves, "ecart_cloture") if not seances_non_finies else None

    # -- verdict : une seule reference a le droit de conclure ---------------
    if marche:
        reel = 10_000 * marche["pondere"]
        if reel < -1.0:
            verdict, niveau = (
                "Cout d'execution NEGATIF (%.1f bps) : obtenir systematiquement mieux "
                "que la fourchette serait de l'argent gratuit. La mesure est faussee, "
                "pas l'execution excellente. Ne t'en sers pas." % reel, "bad")
        elif reel > hypothese:
            verdict, niveau = (
                "Cout reel %.1f bps contre %.1f bps suppose par le backtest : remonte "
                "execution.slippage_bps a environ %.0f bps." % (reel, hypothese, reel), "bad")
        else:
            verdict, niveau = (
                "Cout reel %.1f bps, sous les %.1f bps supposes : l'hypothese du "
                "backtest tient." % (reel, hypothese), "good")
    elif seances_non_finies:
        verdict, niveau = (
            "Mesure impossible : la seance du %s n'est pas terminee. La barre du jour "
            "est une barre EN COURS, sa \"cloture\" n'est que le dernier echange connu. "
            "Relance la recuperation des donnees apres 22h00 (heure de Paris)."
            % ", ".join(seances_non_finies), "flat")
    else:
        verdict, niveau = (
            "Aucun cout mesurable : la fourchette n'a pas ete relevee au moment de "
            "l'envoi. C'est la seule reference contemporaine d'une execution. Les "
            "prochains ordres l'enregistreront automatiquement.", "flat")

    return {
        "n": len(releves),
        "releves": releves,
        "sans_suite": sans_suite,
        "hypothese_bps": hypothese,
        "marche": marche,
        "cloture": cloture,
        "plan": _agrege(releves, "ecart_plan"),
        "seances_non_finies": seances_non_finies,
        "decalage_seances": statistics.median(decalages) if decalages else None,
        "verdict": verdict,
        "niveau": niveau,
    }
