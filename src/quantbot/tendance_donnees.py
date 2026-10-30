"""Les donnees du test de tendance : sources, lecture des fichiers bruts, controles.

Etape 2 de docs/protocole_tendance_multi_actifs.md. Ce module ne calcule
AUCUN signal ni aucun rendement de strategie : il lit des fichiers bruts et
verifie leur qualite. Le premier calcul sur donnees reelles est le test.

Les fichiers bruts sont gardes tels que telecharges, octet pour octet : leur
empreinte SHA-256 est consignee dans le protocole avant le test, et le test
refuse de tourner si un fichier a change.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

DOSSIER = Path("data/tendance")
BRUT = DOSSIER / "brut"
MANIFESTE = DOSSIER / "manifeste.csv"
DEBUT = pd.Timestamp("1998-01-01")
FIN = pd.Timestamp("2026-09-30")          # dernier mois juge
DEBUT_MAX = pd.Timestamp("2004-01-31")
TROU_MAX = 5                              # jours ouvres sans cotation
FIGE_MAX = 5                              # jours de cours identique
SAUT_MAX = 0.25                           # rendement quotidien
SIGMAS_MAX = 10.0

#: Yahoo refuse les clients sans en-tete de navigateur ; FRED refuse ceux qui
#: s'en donnent un sans en etre un. Chacun le sien.
UA_NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_SCRIPT = "quantbot/1.0 (protocole de tendance)"


def entete(source: str) -> str:
    return UA_NAVIGATEUR if source in ("yahoo", "stooq") else UA_SCRIPT


_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=883612800&period2=%d&interval=1d"


def _yahoo(sym):
    return ("yahoo", sym, _YAHOO % (sym.replace("^", "%5E").replace("=", "%3D"), 1893456000), "json")


def _fred(sid):
    return ("fred", sid, "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s" % sid, "csv")


def _stooq(sym):
    return ("stooq", sym, "https://stooq.com/q/d/l/?s=%s&i=d" % sym.lower(), "csv")


def page_stooq(src) -> str:
    """La page de Stooq a ouvrir dans un navigateur pour telecharger ce fichier.

    Depuis mars 2026, Stooq demande une cle et verifie le navigateur : ses
    fichiers se telechargent a la main (voir scripts/donnees_tendance.py).
    """
    return "https://stooq.com/q/d/?s=%s&i=d" % src[1].lower()


def _bri(pays):
    # Sans date de debut, la BRI renvoie parfois un fichier vide (le Japon).
    return ("bri", pays, "https://stats.bis.org/api/v1/data/WS_CBPOL/D.%s?format=csv"
            "&startPeriod=1997-01-01" % pays, "csv")


#: Instrument -> (source principale, source de repli). Fige par le protocole.
SERIES = {
    "US500": (_yahoo("^GSPC"), _stooq("^SPX")),
    "GER40": (_yahoo("^GDAXI"), _stooq("^DAX")),
    "UK100": (_yahoo("^FTSE"), _stooq("^UKX")),
    "JP225": (_yahoo("^N225"), _stooq("^NKX")),
    "AUS200": (_yahoo("^AXJO"), _stooq("^AXJO")),
    "HK50": (_yahoo("^HSI"), _stooq("^HSI")),
    "EURUSD": (_fred("DEXUSEU"), _yahoo("EURUSD=X")),
    "GBPUSD": (_fred("DEXUSUK"), _yahoo("GBPUSD=X")),
    "USDJPY": (_fred("DEXJPUS"), _yahoo("JPY=X")),
    "USDCHF": (_fred("DEXSZUS"), _yahoo("CHF=X")),
    "USDCAD": (_fred("DEXCAUS"), _yahoo("CAD=X")),
    "AUDUSD": (_fred("DEXUSAL"), _yahoo("AUDUSD=X")),
    "NZDUSD": (_fred("DEXUSNZ"), _yahoo("NZDUSD=X")),
    "XAUUSD": (_stooq("XAUUSD"), _yahoo("GC=F")),
    "XAGUSD": (_stooq("XAGUSD"), _yahoo("SI=F")),
}
#: Taux directeurs de la BRI. La zone euro est prolongee par l'Allemagne
#: avant 1999 ; Hong Kong prend le taux americain (voir tendance.INSTRUMENTS).
TAUX = {code: _bri(code) for code in ("US", "XM", "DE", "GB", "JP", "CH", "CA", "AU", "NZ")}


def nom_fichier(src) -> str:
    source, code, _, ext = src
    propre = "".join(c if c.isalnum() else "_" for c in code)
    return "%s_%s.%s" % (source, propre, ext)


def sha256(chemin) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as fh:
        for bloc in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


# -- lecture des fichiers bruts ------------------------------------------------
def lire(chemin, source: str) -> pd.Series:
    """Le fichier brut d'une source -> serie (date -> valeur), triee, sans trou."""
    brut = Path(chemin).read_bytes()
    if source == "yahoo":
        return _lire_yahoo(brut)
    texte = brut.decode("utf-8-sig")
    if source == "fred":
        lignes = list(csv.reader(io.StringIO(texte)))
        dates, vals = [], []
        for l in lignes[1:]:
            if len(l) >= 2 and l[1] not in ("", "."):
                dates.append(l[0]); vals.append(float(l[1]))
        return _serie(dates, vals)
    if source == "bri":
        df = pd.read_csv(io.StringIO(texte))
        df = df[df["OBS_VALUE"].notna()]
        # Un taux peut etre nul ou negatif (Suisse, Japon, zone euro) : pas
        # de filtre de positivite ici.
        return _serie(df["TIME_PERIOD"].astype(str), df["OBS_VALUE"].astype(float), positif=False)
    if source == "stooq":
        if texte.lstrip().startswith("<"):
            raise ValueError("une page web au lieu d'un fichier CSV : Stooq demande une cle "
                             "ou une verification de navigateur")
        df = pd.read_csv(io.StringIO(texte))
        cols = {str(c).strip().lower(): c for c in df.columns}
        date = cols.get("date", cols.get("data"))                 # stooq.com ou stooq.pl
        close = cols.get("close", cols.get("zamkniecie", cols.get("zamkni\u0119cie")))
        if date is None or close is None:
            raise ValueError("pas de colonnes Date et Close (%s)" % texte[:80].replace("\n", " "))
        return _serie(df[date].astype(str), df[close].astype(float))
    raise ValueError("source inconnue : %s" % source)


def _lire_yahoo(brut: bytes) -> pd.Series:
    d = json.loads(brut.decode("utf-8"))
    res = d["chart"]["result"][0]
    tz = res["meta"].get("exchangeTimezoneName") or "UTC"
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(tz)
    close = res["indicators"]["quote"][0]["close"]
    dates = ts.tz_localize(None).normalize()
    s = pd.Series(close, index=dates, dtype=float)
    return _nettoyer(s)


def _serie(dates, vals, positif: bool = True) -> pd.Series:
    return _nettoyer(pd.Series(list(vals), index=pd.to_datetime(list(dates)), dtype=float), positif)


def _nettoyer(s: pd.Series, positif: bool = True) -> pd.Series:
    """Valeurs finies, dates uniques et triees ; cours strictement positifs."""
    s = s[np.isfinite(s.to_numpy())]
    if positif and len(s):
        s = s[s > 0]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


# -- controles de l'etape 2 ------------------------------------------------------
def controler(principale: pd.Series, repli: pd.Series = None) -> dict:
    """Les controles du protocole, et ce qu'en dit la source de repli.

    Rend {debut, fin, n, defauts: [...]}, chaque defaut avec son verdict :
    "confirme par le repli" (un vrai mouvement, une vraie fermeture) ou
    "NON resolu" (exclusion de l'instrument avant le test).
    """
    p = principale[(principale.index >= DEBUT) & (principale.index <= FIN)]
    out = {"debut": p.index.min(), "fin": p.index.max(), "n": len(p), "defauts": []}
    if len(p) == 0:
        out["defauts"].append({"type": "vide", "detail": "aucune cotation", "resolu": False})
        return out
    if p.index.min() > DEBUT_MAX:
        out["defauts"].append({"type": "debut tardif", "resolu": False, "date": p.index.min(),
                               "detail": "premiere cotation le %s, apres janvier 2004" % p.index.min().date()})
    r = repli[(repli.index >= DEBUT - pd.Timedelta(days=10))] if repli is not None and len(repli) else None

    # trous de plus de 5 jours ouvres
    d = p.index.values.astype("datetime64[D]")
    entre = np.busday_count(d[:-1] + np.timedelta64(1, "D"), d[1:])
    for k in np.where(entre > TROU_MAX)[0]:
        a, b = p.index[k], p.index[k + 1]
        if r is None:
            resolu, avis = False, "pas de source de repli"
        else:
            dedans = r[(r.index > a) & (r.index < b)]
            resolu = len(dedans) == 0
            avis = ("le repli ne cote pas non plus : marche ferme" if resolu
                    else "le repli cote %d jour(s) dans le trou" % len(dedans))
        out["defauts"].append({"type": "trou", "resolu": resolu, "a": a, "b": b,
                               "jours": int(entre[k]), "repli": r is not None,
                               "detail": "%s -> %s : %d jours ouvres sans cotation ; %s"
                                         % (a.date(), b.date(), int(entre[k]), avis)})

    # cours figes plus de 5 jours
    v = p.to_numpy()
    debut_run = 0
    for k in range(1, len(v) + 1):
        if k == len(v) or v[k] != v[debut_run]:
            longueur = k - debut_run
            if longueur > FIGE_MAX:
                a, b = p.index[debut_run], p.index[k - 1]
                if r is None:
                    resolu, avis = False, "pas de source de repli"
                else:
                    rr = r[(r.index >= a) & (r.index <= b)]
                    bouge = len(rr) > 1 and (rr.max() / rr.min() - 1.0) > 1e-3
                    resolu = not bouge
                    avis = "le repli bouge sur la periode" if bouge else "le repli ne bouge pas non plus"
                out["defauts"].append({"type": "fige", "resolu": resolu, "a": a, "b": b,
                                       "n": longueur, "repli": r is not None,
                                       "detail": "%s -> %s : %d cotations identiques ; %s"
                                                 % (a.date(), b.date(), longueur, avis)})
            debut_run = k

    # sauts de plus de 25 % ou de 10 ecarts-types
    ret = p / p.shift(1) - 1.0
    sig = ret.std()
    for date, x in ret.items():
        if not np.isfinite(x) or (abs(x) <= SAUT_MAX and abs(x) <= SIGMAS_MAX * sig):
            continue
        veille = p.index[p.index.get_loc(date) - 1]
        conf, avis, meilleurs = False, "pas de source de repli", []
        if r is not None:
            meilleurs = mouvements_repli(r, veille, date)
            conf = any(np.sign(m) == np.sign(x) and abs(m) >= 0.5 * abs(x) for m in meilleurs)
            avis = ("le repli fait %s" % " / ".join("%+.1f %%" % (100 * m) for m in meilleurs)
                    if meilleurs else "le repli ne couvre pas cette date")
        out["defauts"].append({"type": "saut", "resolu": conf, "date": date, "rendement": float(x),
                               "sigmas": float(abs(x) / sig), "repli": r is not None,
                               "mouvements_repli": meilleurs,
                               "detail": "%s : %+.1f %% (%.1f ecarts-types) ; %s"
                                         % (date.date(), 100 * x, abs(x) / sig, avis)})
    return out


FENETRE_REPLI = 2       # seances de part et d'autre, pour les decalages d'horaire


def mouvements_repli(r: pd.Series, veille, date) -> list:
    """Le mouvement du repli entre `veille` et `date`, puis sur des fenetres
    elargies de 1 et 2 seances de chaque cote.

    Pourquoi elargir : les sources ne relevent pas leurs cours a la meme
    heure. FRED prend le cours de midi a New York, Yahoo la cloture de
    Londres ; et Yahoo date parfois ses barres de change du jour suivant. Un
    mouvement reel se retrouve dans le repli a une ou deux seances pres ; un
    cours aberrant de la source principale, lui, ne se retrouve nulle part.
    """
    out = []
    ia = r.index.searchsorted(veille, side="right") - 1      # dernier cours <= veille
    ib = r.index.searchsorted(date, side="right") - 1        # dernier cours <= date
    for k in range(FENETRE_REPLI + 1):
        a, b = ia - k, ib + k
        if 0 <= a < len(r) and 0 <= b < len(r) and a < b:
            out.append(float(r.iloc[b] / r.iloc[a] - 1.0))
    return out


# -- chargement pour le test -------------------------------------------------------
def lire_manifeste(chemin=MANIFESTE) -> dict:
    with open(chemin, encoding="utf-8") as fh:
        return {l["fichier"]: l for l in csv.DictReader(fh)}


def verifier_empreintes(dossier=BRUT, manifeste=MANIFESTE) -> list:
    """Les fichiers dont l'empreinte ne correspond plus au manifeste."""
    m = lire_manifeste(manifeste)
    return [f for f, l in m.items() if l.get("sha256")
            and (not (Path(dossier) / f).exists() or sha256(Path(dossier) / f) != l["sha256"])]


def charger(exclus=(), dossier=BRUT) -> tuple:
    """Cours des instruments retenus (sources principales) et taux, pour le test.

    `exclus` : les instruments exclus a l'etape 2, consignes dans le protocole.
    """
    prix = {}
    for nom, (princ, _) in SERIES.items():
        if nom in exclus:
            continue
        s = lire(Path(dossier) / nom_fichier(princ), princ[0])
        prix[nom] = s[s.index <= FIN]
    taux = {}
    for code, src in TAUX.items():
        taux[code] = lire(Path(dossier) / nom_fichier(src), "bri")
    # Zone euro : l'Allemagne avant 1999 (protocole, "Univers").
    xm, de = taux["XM"], taux.pop("DE")
    taux["XM"] = pd.concat([de[de.index < pd.Timestamp("1999-01-01")],
                            xm[xm.index >= pd.Timestamp("1999-01-01")]]).sort_index()
    return prix, taux
