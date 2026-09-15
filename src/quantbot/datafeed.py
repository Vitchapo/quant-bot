"""Ingestion et stockage des cours.

Principes
---------
1. On ne telecharge qu'une fois. Les cours sont stockes en Parquet, un fichier
   par ticker, et les executions suivantes ne recuperent que les jours
   manquants. Les API gratuites limitent le debit : le cache est ce qui rend
   le projet utilisable au quotidien.
2. Les cours sont AJUSTES des dividendes et des divisions (auto_adjust). Sans
   cet ajustement, chaque detachement de dividende apparait comme une baisse
   et fausse tous les calculs de momentum.
3. Un ticker qui ne renvoie rien est signale explicitement, jamais ignore en
   silence : c'est ainsi qu'on repere une liste de tickers obsolete.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Normalisation de ce que renvoie yfinance
# ---------------------------------------------------------------------------
def _normalise(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Ramene la sortie de yfinance a un DataFrame propre en minuscules.

    yfinance renvoie selon les versions des colonnes plates ou un MultiIndex
    (Champ, Ticker) ou (Ticker, Champ). On gere les trois cas.
    """
    if raw is None or len(raw) == 0:
        return None

    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        levels = [set(map(str, df.columns.get_level_values(i))) for i in range(2)]
        fields = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        if levels[0] & fields:          # (Champ, Ticker)
            df = df.xs(df.columns.get_level_values(1)[0], axis=1, level=1)
        elif levels[1] & fields:        # (Ticker, Champ)
            df = df.xs(df.columns.get_level_values(0)[0], axis=1, level=0)
        else:
            df.columns = df.columns.get_level_values(-1)

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "close" not in df.columns and "adj_close" in df.columns:
        df["close"] = df["adj_close"]

    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        logger.warning("%s : colonnes manquantes %s, ticker ignore", ticker, missing)
        return None

    df = df[OHLCV].astype("float64")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # Un cours nul ou negatif est une donnee corrompue, pas une information.
    df = df[df["close"] > 0]
    return df if len(df) else None


# ---------------------------------------------------------------------------
# Fournisseurs
# ---------------------------------------------------------------------------
def _download_yfinance(tickers: list[str], start: str, end: str | None) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    batch_size = 40
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        logger.info("Telechargement %d-%d / %d", i + 1, i + len(batch), len(tickers))
        try:
            raw = yf.download(
                batch, start=start, end=end, auto_adjust=True,
                progress=False, group_by="ticker", threads=True, timeout=30,
            )
        except Exception as exc:
            logger.error("Echec du lot %s : %s", batch[:3], exc)
            continue

        for ticker in batch:
            try:
                sub = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = _normalise(sub, ticker)
            if df is not None:
                out[ticker] = df
        time.sleep(1.0)  # on reste poli avec une API gratuite et non officielle
    return out


def _download_stooq(tickers: list[str], start: str, end: str | None) -> dict[str, pd.DataFrame]:
    """Source de secours. Couverture US solide, europeenne partielle."""
    import io

    import requests

    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        if ticker.endswith(".PA"):
            symbol = ticker[:-3].lower() + ".fr"
        elif ticker.startswith("^"):
            symbol = "^" + ticker[1:].lower()
        else:
            symbol = ticker.lower() + ".us"
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200 or "Date" not in resp.text[:200]:
                continue
            df = pd.read_csv(io.StringIO(resp.text), parse_dates=["Date"]).set_index("Date")
            df = _normalise(df, ticker)
            if df is not None:
                out[ticker] = df.loc[start:end] if end else df.loc[start:]
        except Exception as exc:
            logger.debug("Stooq %s : %s", ticker, exc)
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------------------
# Cache Parquet
# ---------------------------------------------------------------------------
def _cache_path(cache_dir: Path, ticker: str) -> Path:
    safe = ticker.replace("^", "_IDX_").replace("/", "_")
    return cache_dir / f"{safe}.parquet"


def seances_ecoulees(derniere, aujourdhui=None) -> int:
    """Nombre de seances ENTIEREMENT ecoulees depuis `derniere`.

    On compte les jours ouvres strictement compris entre la derniere seance en
    cache et aujourd'hui. La seance du jour meme n'est jamais comptee : tant
    qu'elle n'est pas close, sa barre est partielle, et le projet a deja paye
    le prix d'avoir decide sur une barre en cours.

        cache mardi, on est mercredi -> 0 (mardi reste la derniere cloture)
        cache mardi, on est jeudi    -> 1 (mercredi manque)
        cache vendredi, on est lundi -> 0 (le week-end ne cote pas)
        cache mardi 8, on est ven 11 -> 2 (mercredi et jeudi manquent)

    Remplace la regle "plus de 3 jours calendaires", qui etait fausse d'une
    facon particulierement vicieuse : elle tolerait en permanence jusqu'a
    trois jours de retard, si bien que le cache n'etait jamais complete tant
    qu'il n'avait pas quatre jours. Le 11 septembre 2026, un cache arrete au
    8 affichait exactement 3 jours d'ecart - aucun telechargement declenche,
    et un message de succes malgre tout.
    """
    derniere = pd.Timestamp(derniere).normalize()
    aujourdhui = pd.Timestamp(aujourdhui or pd.Timestamp.today()).normalize()
    if aujourdhui <= derniere:
        return 0
    ouvres = pd.bdate_range(derniere + pd.Timedelta(days=1),
                            aujourdhui - pd.Timedelta(days=1))
    return int(len(ouvres))


def fetch(cfg, tickers: list[str], force: bool = False,
          rapport: dict | None = None, aujourdhui=None) -> dict[str, pd.DataFrame]:
    """Telecharge (ou complete) les cours et les stocke en Parquet.

    Renvoie le dictionnaire {ticker: DataFrame OHLCV} de tout ce qui est
    disponible en cache apres l'operation.

    `rapport`, s'il est fourni, est REMPLI avec ce qui s'est reellement passe :
    combien de series attendaient des seances, combien ont ete completees,
    combien de lignes ont ete ajoutees. Sans cela, l'appelant ne peut annoncer
    que "504 series disponibles" - vrai, rassurant, et parfaitement muet sur
    le fait que rien n'a ete telecharge.
    """
    cache_dir = Path(cfg.get("data.cache_dir", "data/prices"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    start = str(cfg.get("data.start", "2005-01-01"))
    end = cfg.get("data.end")
    provider = cfg.get("data.provider", "yfinance")

    cached: dict[str, pd.DataFrame] = {}
    to_download: list[str] = []
    # `aujourdhui` n'existe que pour les tests : sans lui, impossible de
    # verifier le comportement a une date PRECISE, et c'est exactement a une
    # date precise que l'ancienne regle echouait.
    today = pd.Timestamp(aujourdhui or pd.Timestamp.today()).normalize()

    for ticker in tickers:
        path = _cache_path(cache_dir, ticker)
        if path.exists() and not force:
            df = pd.read_parquet(path)
            cached[ticker] = df
            last = df.index.max()
            if seances_ecoulees(last, today) > 0:
                to_download.append(ticker)
        else:
            to_download.append(ticker)

    avant = {t: (df.index.max() if len(df) else None) for t, df in cached.items()}
    if rapport is not None:
        rapport.update({
            "demandes": len(tickers), "en_cache": len(cached),
            "a_completer": len(to_download),
            "derniere_seance_avant": (max(d for d in avant.values() if d is not None)
                                      if any(v is not None for v in avant.values()) else None),
            "telecharges": 0, "seances_ajoutees": 0, "echecs": [],
        })

    if to_download:
        logger.info("%d tickers a telecharger (%d deja en cache)",
                    len(to_download), len(cached))
        # Telechargement incremental : on repart du dernier jour connu
        oldest_needed = start
        if all(t in cached for t in to_download) and cached:
            last_dates = [cached[t].index.max() for t in to_download if t in cached]
            if last_dates:
                oldest_needed = (min(last_dates) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")

        downloader = _download_yfinance if provider == "yfinance" else _download_stooq
        fresh = downloader(to_download, oldest_needed, end)

        failed = sorted(set(to_download) - set(fresh))
        if failed:
            logger.warning("Aucune donnee pour %d tickers : %s",
                           len(failed), ", ".join(failed[:15]))

        ajoutees = 0
        for ticker, df in fresh.items():
            if ticker in cached:
                merged = pd.concat([cached[ticker], df])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            else:
                merged = df
            precedent = avant.get(ticker)
            if precedent is not None:
                ajoutees += int((merged.index > precedent).sum())
            cached[ticker] = merged
            merged.to_parquet(_cache_path(cache_dir, ticker), compression="snappy")

        if rapport is not None:
            rapport.update({"telecharges": len(fresh), "seances_ajoutees": ajoutees,
                            "echecs": failed[:20]})

    if rapport is not None:
        dates = [df.index.max() for df in cached.values() if len(df)]
        rapport["derniere_seance_apres"] = max(dates) if dates else None
        rapport["retard_seances"] = (seances_ecoulees(rapport["derniere_seance_apres"], today)
                                     if rapport["derniere_seance_apres"] is not None else None)
        # Le diagnostic qui manquait : des seances etaient attendues et rien
        # n'est arrive. Ce n'est pas un succes, c'est une panne silencieuse.
        rapport["ok"] = not (rapport["a_completer"] > 0
                             and rapport["seances_ajoutees"] == 0)

    return cached


def save_panel(prices: dict[str, pd.DataFrame], cache_dir: str | Path) -> None:
    """Ecrit chaque serie dans le cache (utilise par le mode synthetique)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for ticker, df in prices.items():
        df.to_parquet(_cache_path(cache_dir, ticker), compression="snappy")


def load_panel(cfg, tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Charge depuis le cache uniquement, sans acces reseau."""
    cache_dir = Path(cfg.get("data.cache_dir", "data/prices"))
    if not cache_dir.exists():
        raise FileNotFoundError(
            f"Cache introuvable : {cache_dir}. Lance d'abord scripts/fetch_data.py"
        )
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(cache_dir.glob("*.parquet")):
        ticker = path.stem.replace("_IDX_", "^")
        if tickers is not None and ticker not in tickers:
            continue
        out[ticker] = pd.read_parquet(path)
    return out


def to_matrix(prices: dict[str, pd.DataFrame], field: str = "close",
              min_history: int = 0) -> pd.DataFrame:
    """Convertit {ticker: OHLCV} en matrice large (dates x tickers).

    C'est le format de travail : il permet de comparer tous les titres entre
    eux a une date donnee en une seule operation vectorisee, et il occupe
    environ 8 octets par cellule au lieu d'une ligne par couple date/ticker.
    """
    series = {
        t: df[field] for t, df in prices.items()
        if field in df.columns and len(df) >= min_history
    }
    if not series:
        raise ValueError(f"Aucune serie exploitable pour le champ {field!r}")
    mat = pd.DataFrame(series).sort_index()
    mat.index.name = "date"
    return mat.astype("float64")
