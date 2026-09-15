"""Definition de l'univers investissable.

BIAIS DU SURVIVANT — a lire avant d'interpreter le moindre backtest
-------------------------------------------------------------------
Les listes ci-dessous sont les constituants ACTUELS des indices. Les societes
sorties de la cote (faillites, retraits, fusions, sorties d'indice) en sont
absentes. Un backtest lance sur cet univers ne voit donc que des entreprises
qui ont survecu jusqu'a aujourd'hui, ce qui gonfle mecaniquement la
performance : selon les etudes academiques, l'effet est de l'ordre de 1 a 4
points de rendement annuel selon la periode et la taille des societes.

Consequence pratique : les rendements absolus produits ici sont OPTIMISTES.
Ce qui reste exploitable, c'est la comparaison relative entre la strategie et
un buy-and-hold du meme univers, puisque les deux subissent le meme biais.

Pour supprimer ce biais il faut des donnees point-in-time (CRSP, Sharadar,
Norgate Data) qui sont payantes. C'est le premier achat a envisager si le
prototype se revele concluant.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# France : constituants CAC 40 / SBF 120 (a verifier et mettre a jour).
# Le module d'ingestion signale et retire automatiquement les tickers qui ne
# renvoient aucune donnee, donc une erreur ici est visible, pas silencieuse.
# --------------------------------------------------------------------------
FR_TICKERS: list[str] = [
    # --- CAC 40 ---
    "AC.PA", "ACA.PA", "AI.PA", "AIR.PA", "ALO.PA", "BN.PA", "BNP.PA",
    "BVI.PA", "CA.PA", "CAP.PA", "CS.PA", "DG.PA", "DSY.PA", "EDEN.PA",
    "EL.PA", "EN.PA", "ENGI.PA", "ERF.PA", "GLE.PA", "HO.PA", "KER.PA",
    "LR.PA", "MC.PA", "ML.PA", "OR.PA", "ORA.PA", "PUB.PA", "RI.PA",
    "RMS.PA", "RNO.PA", "SAF.PA", "SAN.PA", "SGO.PA", "STLAP.PA",
    "STMPA.PA", "SU.PA", "TEP.PA", "TTE.PA", "VIE.PA", "VIV.PA",
    # --- complements SBF 120 ---
    "AKE.PA", "ALD.PA", "AMUN.PA", "ATO.PA", "BB.PA", "BEN.PA", "BOL.PA",
    "COFA.PA", "COV.PA", "DEC.PA", "ELIS.PA", "EO.PA", "ERA.PA", "ETL.PA",
    "FDJ.PA", "FGR.PA", "FR.PA", "GET.PA", "GFC.PA", "GTT.PA", "ICAD.PA",
    "IPN.PA", "LI.PA", "MMT.PA", "NEX.PA", "NXI.PA", "OVH.PA", "POM.PA",
    "RCO.PA", "RUI.PA", "RXL.PA", "SCR.PA", "SESG.PA", "SK.PA", "SOI.PA",
    "SOP.PA", "SPIE.PA", "SW.PA", "TE.PA", "TFI.PA", "TKO.PA", "TRI.PA",
    "UBI.PA", "VCT.PA", "VK.PA", "VRLA.PA", "WLN.PA",
]

# Repli si Wikipedia est injoignable : quelques grandes capitalisations US.
US_FALLBACK_TICKERS: list[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "BRK-B",
    "JPM", "JNJ", "V", "PG", "UNH", "HD", "MA", "XOM", "CVX", "ABBV",
    "PFE", "KO", "PEP", "MRK", "COST", "WMT", "BAC", "TMO", "CSCO",
    "MCD", "ACN", "ADBE", "ABT", "CRM", "LIN", "DHR", "NKE", "TXN",
    "NEE", "VZ", "PM", "ORCL", "INTC", "AMD", "QCOM", "HON", "UPS",
    "IBM", "GE", "CAT", "BA", "GS", "MS", "AXP", "BLK", "SPGI", "NOW",
    "INTU", "ISRG", "AMGN", "GILD", "LMT", "RTX", "DE", "MMM", "T",
]

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Wikipedia renvoie 403 au User-Agent par defaut de Python. `pd.read_html(url)`
# echouait donc SYSTEMATIQUEMENT, et non par intermittence : le repli sur la
# liste statique n'etait pas un incident rare, c'etait le comportement normal.
# Un backtest annonce "S&P 500" tournait en realite sur 64 grandes
# capitalisations. On passe par requests avec un en-tete explicite.
HTTP_HEADERS = {
    "User-Agent": "quantbot/0.2 (projet personnel de backtest ; python-requests)",
    "Accept-Language": "en-US,en;q=0.9",
}

#: En dessous de ce nombre, la table recuperee n'est pas le S&P 500.
SP500_MIN_ROWS = 400


class UniverseError(RuntimeError):
    """L'univers n'a pas pu etre constitue tel que la configuration l'exige.

    Cette exception existe pour une raison precise : un univers incomplet ne
    produit pas une erreur visible plus tard, il produit des resultats
    plausibles sur un autre univers que celui annonce. Mieux vaut ne pas
    demarrer que produire un backtest dont le titre ment.
    """


def _sp500_from_wikipedia(timeout: float = 20.0) -> list[str]:
    """Constituants actuels du S&P 500, depuis Wikipedia.

    Leve une exception a la moindre anomalie (reseau, table absente, table
    trop courte) : c'est l'appelant qui decide quoi faire, pas cette fonction.
    """
    import io

    import pandas as pd
    import requests

    resp = requests.get(WIKI_SP500_URL, timeout=timeout, headers=HTTP_HEADERS)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    for table in tables:
        if "Symbol" not in table.columns:
            continue
        symbols = table["Symbol"].astype(str).str.strip().tolist()
        # Yahoo utilise "-" la ou l'indice utilise "." (BRK.B -> BRK-B)
        tickers = [s.replace(".", "-") for s in symbols
                   if s and s.lower() not in ("nan", "")]
        if len(tickers) < SP500_MIN_ROWS:
            raise ValueError(
                "table 'Symbol' trouvee mais elle ne contient que %d lignes "
                "(minimum attendu : %d). La page a probablement change de "
                "structure." % (len(tickers), SP500_MIN_ROWS))
        return tickers
    raise ValueError("aucune colonne 'Symbol' trouvee dans les tables Wikipedia")


def get_universe(cfg) -> list[str]:
    """Renvoie la liste des tickers de l'univers defini par la configuration.

    Deux garde-fous, tous deux destines a rendre BRUYANT ce qui etait
    silencieux :

    * `universe.allow_fallback` (defaut : false) - si la source distante est
      injoignable, on leve `UniverseError` au lieu de retomber discretement
      sur la liste statique.
    * `universe.min_tickers` (defaut : 0) - nombre de tickers en dessous
      duquel on refuse de demarrer. Verifie AVANT `max_tickers`, qui est un
      bouton de test volontaire et non un accident.
    """
    source = cfg.get("universe.source", "static")
    name = cfg.get("name", "us")
    min_tickers = int(cfg.get("universe.min_tickers", 0) or 0)
    allow_fallback = bool(cfg.get("universe.allow_fallback", False))

    if source == "file":
        path = Path(cfg.get("universe.file"))
        tickers = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    elif source == "sp500":
        try:
            tickers = _sp500_from_wikipedia()
            logger.info("S&P 500 : %d tickers recuperes depuis Wikipedia", len(tickers))
        except Exception as exc:  # pragma: no cover - depend du reseau
            if not allow_fallback:
                raise UniverseError(
                    "Impossible de recuperer les constituants du S&P 500 : %s\n"
                    "\n"
                    "Le code precedent se repliait ici sur une liste statique de "
                    "%d grandes capitalisations. Ce repli est refuse par defaut : "
                    "un backtest lance sur 64 megacapitalisations et presente "
                    "comme un backtest S&P 500 est un resultat faux, pas un "
                    "resultat degrade.\n"
                    "\n"
                    "  - verifie ta connexion, puis relance ;\n"
                    "  - ou mets 'universe.allow_fallback: true' dans la config "
                    "si tu acceptes explicitement de travailler sur la liste de "
                    "repli, en sachant que ce n'est pas le S&P 500."
                    % (exc, len(US_FALLBACK_TICKERS)))
            logger.warning(
                "Wikipedia injoignable (%s) - repli EXPLICITEMENT autorise sur "
                "la liste statique de %d tickers. Ce n'est PAS le S&P 500.",
                exc, len(US_FALLBACK_TICKERS))
            tickers = list(US_FALLBACK_TICKERS)
    elif source == "static":
        tickers = list(FR_TICKERS) if name == "fr" else list(US_FALLBACK_TICKERS)
    else:
        raise ValueError(f"universe.source inconnu : {source!r}")

    # Deduplication en preservant l'ordre
    seen: set = set()
    tickers = [t for t in tickers if not (t in seen or seen.add(t))]

    # Verifie AVANT max_tickers : on controle ce que la source a fourni, pas
    # ce qu'on a volontairement tronque pour un essai rapide.
    if min_tickers and len(tickers) < min_tickers:
        raise UniverseError(
            "Univers '%s' : %d tickers obtenus, %d attendus au minimum "
            "(universe.min_tickers).\n"
            "Soit la source a change, soit elle a echoue en silence. Corrige "
            "la source ou baisse min_tickers en connaissance de cause."
            % (name, len(tickers), min_tickers))

    limit = cfg.get("universe.max_tickers")
    if limit:
        tickers = tickers[: int(limit)]
        logger.info("universe.max_tickers=%s : univers tronque a %d tickers "
                    "(essai rapide, resultats non representatifs)", limit, len(tickers))
    return tickers
