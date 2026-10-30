"""Tendance multi-actifs sur les instruments FTMO : le test du protocole.

    docs/protocole_tendance_multi_actifs.md, commite le 27/10/2026

Ce module ne sert qu'a ce test. Aucun code du robot ne l'importe, et il
n'importe rien du robot : le gel de la strategie actions n'est pas concerne.

Tout ce que le protocole fige est une CONSTANTE ci-dessous, et aucune
fonction ne permet d'en changer une. Seuls `tirages` et `graine` sont des
arguments : les tests sur donnees synthetiques en tirent moins pour tourner
vite ; le script du test (`scripts/tester_tendance.py`) passe toujours les
valeurs figees.

Ce qu'on mesure
---------------
Le rendement EN EXCES d'une position financee, en monnaie locale :

    devise : variation du cours + taux de la devise achetee - taux de la vendue
    indice : variation de l'indice - taux court local
    metal  : variation du cours - taux court americain

Les taux (en % par an) s'appliquent au prorata des jours CALENDAIRES ecoules
depuis la cotation precedente, au niveau connu a cette cotation. Une position
vendeuse rapporte l'oppose.

Le signal et les positions
--------------------------
A la cloture du dernier jour de cotation de chaque mois : signe du rendement
en exces des 12 derniers mois. Execution a la cloture de la cotation
suivante. Nominal = 40 % / volatilite estimee (moyenne exponentielle des
carres des rendements quotidiens en exces, centre de gravite 60 jours,
annualisee sur 261 jours). Portefeuille : moyenne des positions de tous les
instruments disponibles. Un instrument entre des qu'il a 12 mois de cours et
60 jours de volatilite.

Les cinq criteres
-----------------
1. pouvoir predictif : regression empilee (instrument x mois) du rendement du
   mois suivant sur celui des 12 derniers mois, divises par la volatilite ;
   ecarts-types regroupes par mois ; pente > 0 et t > 2.
2. profil par quintiles : croissant avec au plus une inversion entre voisins,
   et quintile 5 - quintile 1 > 0 avec t > 2 (regroupe par mois).
3. alpha contre le portefeuille toujours acheteur (indices et metaux, meme
   dosage) ; Newey-West 3 retards ; alpha > 0 et t > 2.
4. 1 000 strategies au hasard : memes periodes acheteuses et vendeuses, memes
   durees, dans un ordre tire au sort ; Sharpe net des couts ; p < 0,05.
5. rendement mensuel net des couts : t > 2 (Newey-West 3) sur 2000-2026, et
   moyenne > 0 sur 2013-2026.

Criteres 1 a 3 avant les couts FTMO, 4 et 5 apres.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# -- fige par le protocole -----------------------------------------------------
MOIS_SIGNAL = 12            # signe du rendement en exces des 12 derniers mois
COM_VOL = 60                # centre de gravite de la moyenne exponentielle
JOURS_AN = 261              # annualisation de la volatilite
VOL_POSITION = 0.40         # chaque position vise 40 % de volatilite par an
MIN_JOURS_VOL = 60          # jours de rendements avant d'estimer la volatilite
BASE_TAUX = 365.0           # les taux courent au prorata des jours calendaires
DEBUT_DONNEES = pd.Timestamp("1998-01-01")
DEBUT_JUGE = pd.Period("2000-01", "M")
FIN_JUGE = pd.Period("2026-09", "M")
DEBUT_APRES = pd.Period("2013-01", "M")     # apres la publication
DEBUT_MAX_SERIE = pd.Timestamp("2004-01-31")
RETARDS_NW = 3
TIRAGES = 1000
GRAINE = 20261010
SEUIL_T = 2.0
QUINTILES = 5

#: Les 15 instruments. Codes de taux : ceux de la BRI ; "XM" est la zone euro,
#: prolongee par l'Allemagne avant 1999 au chargement des donnees ; Hong Kong
#: prend le taux americain (monnaie arrimee au dollar).
INSTRUMENTS = {
    "US500": {"famille": "indice", "taux": "US"},
    "GER40": {"famille": "indice", "taux": "XM"},
    "UK100": {"famille": "indice", "taux": "GB"},
    "JP225": {"famille": "indice", "taux": "JP"},
    "AUS200": {"famille": "indice", "taux": "AU"},
    "HK50": {"famille": "indice", "taux": "US"},
    "EURUSD": {"famille": "devise", "achetee": "XM", "vendue": "US"},
    "GBPUSD": {"famille": "devise", "achetee": "GB", "vendue": "US"},
    "USDJPY": {"famille": "devise", "achetee": "US", "vendue": "JP"},
    "USDCHF": {"famille": "devise", "achetee": "US", "vendue": "CH"},
    "USDCAD": {"famille": "devise", "achetee": "US", "vendue": "CA"},
    "AUDUSD": {"famille": "devise", "achetee": "AU", "vendue": "US"},
    "NZDUSD": {"famille": "devise", "achetee": "NZ", "vendue": "US"},
    "XAUUSD": {"famille": "metal", "taux": "US"},
    "XAGUSD": {"famille": "metal", "taux": "US"},
}
#: Le portefeuille toujours acheteur du critere 3 : les devises n'ont pas de
#: sens "acheteur" naturel.
FAMILLES_PASSIVES = ("indice", "metal")


# =============================================================================
# 1. Rendements en exces, volatilite, decisions
# =============================================================================
def _niveau(taux: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Le taux (fraction par an) connu a chaque date de `index`."""
    t = taux.dropna().sort_index()
    return (t.reindex(t.index.union(index)).ffill().reindex(index) / 100.0)


def portage(spec: dict, index: pd.DatetimeIndex, taux: dict) -> pd.Series:
    """Portage annuel d'une position ACHETEUSE a chaque date (fraction par an)."""
    if spec["famille"] == "devise":
        return _niveau(taux[spec["achetee"]], index) - _niveau(taux[spec["vendue"]], index)
    return -_niveau(taux[spec["taux"]], index)


def rendements_exces(prix: pd.Series, spec: dict, taux: dict) -> pd.DataFrame:
    """Rendement quotidien en exces d'une position acheteuse.

    Sur le calendrier PROPRE de l'instrument : un jour sans cotation n'a pas de
    rendement ; ses interets courent jusqu'a la cotation suivante, au taux
    connu a la cotation precedente. Rend un tableau (date -> r, jours), sans
    la premiere cotation, ni les cotations dont un taux manque encore.
    """
    p = prix.dropna()
    p = p[p > 0].sort_index()
    p = p[~p.index.duplicated(keep="last")]
    idx = p.index
    jours = pd.Series(idx, index=idx).diff().dt.days
    porte = portage(spec, idx, taux).shift(1)            # connu a la veille
    r = p / p.shift(1) - 1.0 + porte * jours / BASE_TAUX
    out = pd.DataFrame({"r": r, "jours": jours}).iloc[1:]
    return out.dropna()


def volatilite(r: pd.Series) -> pd.Series:
    """Volatilite annualisee estimee a chaque cloture (NaN avant 60 jours)."""
    return np.sqrt(JOURS_AN * (r ** 2).ewm(com=COM_VOL, min_periods=MIN_JOURS_VOL).mean())


def decisions(r: pd.Series) -> pd.DataFrame:
    """Une ligne par mois calendaire : la decision prise a sa derniere cloture.

    Colonnes :
      date       derniere cloture du mois (la decision)
      execution  cloture suivante (NaT pour le dernier mois)
      r12        rendement en exces des 12 derniers mois (de la decision
                 d'il y a 12 mois a celle-ci)
      suivant    rendement en exces du mois suivant (decision -> decision)
      vol        volatilite estimee a la decision
      signal     signe de r12 (+1, -1, 0)
      taille     40 % / vol
      dispo      12 mois de cours ET 60 jours de volatilite
    """
    r = r.dropna().sort_index()
    idx = r.index
    # Indice de richesse en exces, releve a chaque fin de mois. Le premier
    # r12 attend douze fins de mois apres la premiere : au moins 12 mois de
    # cours, jamais moins.
    richesse = (1.0 + r).cumprod()
    mois = idx.to_period("M")
    s = pd.Series(np.arange(len(idx)), index=idx)
    dernier = s.groupby(mois).max()                     # position du dernier jour
    dates = idx[dernier.values]
    periodes = dernier.index
    w = pd.Series(richesse.iloc[dernier.values].to_numpy(), index=periodes)
    w12 = w.reindex(periodes - MOIS_SIGNAL).to_numpy()
    r12 = w.to_numpy() / w12 - 1.0
    suiv = w.reindex(periodes + 1).to_numpy() / w.to_numpy() - 1.0
    vol = volatilite(r).iloc[dernier.values].to_numpy()
    pos_exec = dernier.values + 1
    execution = pd.DatetimeIndex([idx[k] if k < len(idx) else pd.NaT for k in pos_exec])
    signal = np.sign(r12)
    dispo = np.isfinite(r12) & np.isfinite(vol) & (vol > 0)
    taille = np.where(dispo, VOL_POSITION / np.where(vol > 0, vol, np.nan), np.nan)
    return pd.DataFrame({"date": dates, "execution": execution, "r12": r12,
                         "suivant": suiv, "vol": vol, "signal": signal,
                         "taille": taille, "dispo": dispo}, index=periodes)


# =============================================================================
# 2. Le portefeuille, sur un calendrier commun
# =============================================================================
class Grille:
    """Tout ce qu'il faut pour rejouer n'importe quels signaux, en numpy.

    Calendrier commun = union des cotations. Pour chaque instrument :
      r[i, t]       rendement en exces du jour (0 si pas de cotation)
      j[i, t]       jours calendaires / 365 couverts par ce rendement
      per[i, t]     indice de la decision dont la position s'applique ce jour
                    (-1 si aucune) : la position prise a la cloture d'une
                    execution s'applique aux rendements des jours SUIVANTS
      exe[i][k]     indice du jour d'execution de la decision k (-1 si aucune)
    """

    def __init__(self, rend: dict, dec: dict):
        self.noms = list(rend)
        jours = sorted(set().union(*[set(rend[n].index) for n in self.noms]))
        self.cal = pd.DatetimeIndex(jours)
        T, N = len(self.cal), len(self.noms)
        self.r = np.zeros((N, T))
        self.j = np.zeros((N, T))
        self.per = np.full((N, T), -1, dtype=np.int64)
        self.dec = dec
        self.exe = []
        pos_cal = pd.Series(np.arange(T), index=self.cal)
        for i, n in enumerate(self.noms):
            df = rend[n]
            k = pos_cal.reindex(df.index).to_numpy()
            self.r[i, k] = df["r"].to_numpy()
            self.j[i, k] = df["jours"].to_numpy() / BASE_TAUX
            d = dec[n]
            e = d["execution"]
            ke = np.where(e.notna(), pos_cal.reindex(e).fillna(-1).to_numpy(), -1).astype(np.int64)
            self.exe.append(ke)
            # La decision k vaut pour les jours t > exe[k], jusqu'a l'execution
            # suivante incluse.
            marque = np.full(T, -1, dtype=np.int64)
            for kk, t in enumerate(ke):
                if t >= 0 and t + 1 < T:
                    marque[t + 1] = kk
            cour = -1
            for t in range(T):
                if marque[t] >= 0:
                    cour = marque[t]
                self.per[i, t] = cour
        self.mois = self.cal.to_period("M")

    def positions(self, signaux: dict) -> tuple:
        """Positions journalieres (N x T, NaN = indisponible) et par decision."""
        N, T = self.r.shape
        pos = np.full((N, T), np.nan)
        par_dec = []
        for i, n in enumerate(self.noms):
            d = self.dec[n]
            v = np.where(d["dispo"].to_numpy(), signaux[n] * d["taille"].to_numpy(), np.nan)
            par_dec.append(v)
            ok = self.per[i] >= 0
            pos[i, ok] = v[self.per[i, ok]]
        return pos, par_dec

    def rendements(self, signaux: dict, couts: dict = None) -> pd.Series:
        """Rendement quotidien du portefeuille (moyenne des positions).

        `couts` : {nom: {"transaction": fraction du nominal echange,
        "marge_achat": fraction par an, "marge_vente": fraction par an}}.
        None = avant les couts FTMO.
        """
        pos, par_dec = self.positions(signaux)
        dispo = np.isfinite(pos)
        n_dispo = dispo.sum(axis=0).astype(float)
        p0 = np.where(dispo, pos, 0.0)
        pnl = p0 * self.r
        if couts:
            N, T = self.r.shape
            frais = np.zeros(T)
            for i, n in enumerate(self.noms):
                c = couts[n]
                marge = np.where(p0[i] > 0, c["marge_achat"],
                                 np.where(p0[i] < 0, c["marge_vente"], 0.0))
                pnl[i] -= np.abs(p0[i]) * marge * self.j[i]
                # Transactions : a chaque execution, l'ecart entre la nouvelle
                # position et l'ancienne (0 si l'instrument n'etait pas la).
                v = np.nan_to_num(par_dec[i], nan=0.0)
                avant = np.concatenate([[0.0], v[:-1]])
                for k, t in enumerate(self.exe[i]):
                    if t >= 0 and v[k] != avant[k]:
                        frais[t] += c["transaction"] * abs(v[k] - avant[k]) * self._poids(n_dispo, t)
            jour = np.where(n_dispo > 0, pnl.sum(axis=0) / np.where(n_dispo > 0, n_dispo, 1.0), 0.0)
            jour = jour - frais
        else:
            jour = np.where(n_dispo > 0, pnl.sum(axis=0) / np.where(n_dispo > 0, n_dispo, 1.0), 0.0)
        return pd.Series(jour, index=self.cal)

    @staticmethod
    def _poids(n_dispo, t):
        """1 / nombre d'instruments en portefeuille juste apres l'execution."""
        n = n_dispo[t + 1] if t + 1 < len(n_dispo) else n_dispo[t]
        n = n if n > 0 else max(n_dispo[t], 1.0)
        return 1.0 / n


def mensuels(jour: pd.Series) -> pd.Series:
    """Rendements mensuels : les rendements quotidiens composes dans le mois."""
    m = (1.0 + jour).groupby(jour.index.to_period("M")).prod() - 1.0
    return m


def periode(serie: pd.Series, debut=DEBUT_JUGE, fin=FIN_JUGE) -> pd.Series:
    return serie[(serie.index >= debut) & (serie.index <= fin)]


# =============================================================================
# 3. Statistiques (numpy seul : pas de statsmodels dans le projet)
# =============================================================================
def ols_cluster(y, X, groupes):
    """MCO, ecarts-types regroupes (CR1). Rend (coefficients, t)."""
    y = np.asarray(y, float); X = np.asarray(X, float)
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    g = pd.Series(np.arange(n)).groupby(np.asarray(groupes)).indices
    meat = np.zeros((k, k))
    for idx in g.values():
        s = X[idx].T @ e[idx]
        meat += np.outer(s, s)
    G = len(g)
    c = G / (G - 1.0) * (n - 1.0) / (n - k)
    V = c * XtX_inv @ meat @ XtX_inv
    return b, b / np.sqrt(np.diag(V))


def ols_nw(y, X, retards=RETARDS_NW):
    """MCO, ecarts-types de Newey-West (noyau de Bartlett)."""
    y = np.asarray(y, float); X = np.asarray(X, float)
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    u = X * e[:, None]
    S = u.T @ u
    for l in range(1, retards + 1):
        w = 1.0 - l / (retards + 1.0)
        G = u[l:].T @ u[:-l]
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return b, b / np.sqrt(np.diag(V))


def sharpe(m: pd.Series) -> float:
    m = m.dropna()
    return float(m.mean() / m.std(ddof=1) * np.sqrt(12)) if m.std(ddof=1) > 0 else 0.0


# =============================================================================
# 4. Le hasard du critere 4 : memes periodes, autre ordre
# =============================================================================
def melanger(signaux: np.ndarray, dispo: np.ndarray, rng) -> np.ndarray:
    """Replace au hasard les periodes acheteuses et vendeuses d'un instrument.

    Garde le nombre de periodes de chaque sens, leurs durees, donc le temps
    passe acheteur et le nombre de retournements : les periodes acheteuses
    sont melangees entre elles, les vendeuses aussi, puis reposees en
    alternance. Seul le MOMENT change. Les mois indisponibles restent tels
    quels (et un signal nul, s'il y en a, reste une periode a part).
    """
    out = np.array(signaux, dtype=float)
    ok = np.where(dispo)[0]
    if len(ok) == 0:
        return out
    s = out[ok]
    debuts = np.r_[0, np.where(np.diff(s) != 0)[0] + 1]
    fins = np.r_[debuts[1:], len(s)]
    runs = [(s[a], b - a) for a, b in zip(debuts, fins)]
    par_signe = {}
    for v, l in runs:
        par_signe.setdefault(v, []).append(l)
    for v in par_signe:
        rng.shuffle(par_signe[v])
    # alternance : on repart du meme ordre de signes que la vraie sequence,
    # sauf entre +1 et -1 quand ils sont aussi nombreux (sens du depart tire
    # au sort, comme le reste).
    ordre = [v for v, _ in runs]
    n_pos, n_neg = ordre.count(1.0), ordre.count(-1.0)
    if 0.0 not in par_signe and n_pos == n_neg and n_pos > 0 and rng.random() < 0.5:
        ordre = [-v for v in ordre]
    curseur = {v: 0 for v in par_signe}
    nouv = []
    for v in ordre:
        l = par_signe[v][curseur[v]]
        curseur[v] += 1
        nouv.extend([v] * l)
    out[ok] = nouv
    return out


# =============================================================================
# 5. Le test
# =============================================================================
def preparer(prix: dict, taux: dict) -> tuple:
    """Rendements en exces et decisions des instruments presents.

    Une serie qui commence apres janvier 2004 aurait du etre exclue a
    l'etape 2 : on refuse de la tester plutot que de l'ecarter en silence.
    """
    rend, dec = {}, {}
    for nom, spec in INSTRUMENTS.items():
        if nom not in prix:
            continue
        p = prix[nom].dropna()
        p = p[p.index >= DEBUT_DONNEES]
        if len(p) == 0 or p.index.min() > DEBUT_MAX_SERIE:
            raise ValueError("%s commence apres janvier 2004 : a exclure a l'etape 2" % nom)
        df = rendements_exces(p, spec, taux)
        if len(df) < MIN_JOURS_VOL + 2:
            continue
        rend[nom] = df
        dec[nom] = decisions(df["r"])
    return rend, dec


def couples(dec: dict) -> pd.DataFrame:
    """Les couples instrument-mois des criteres 1 et 2, mois juge = mois suivant."""
    lignes = []
    for nom, d in dec.items():
        ok = d["dispo"] & np.isfinite(d["suivant"])
        dd = d[ok]
        lignes.append(pd.DataFrame({
            "instrument": nom, "mois": dd.index + 1,
            "x": dd["r12"] / dd["vol"], "y": dd["suivant"] / dd["vol"]}))
    c = pd.concat(lignes, ignore_index=True)
    return c[(c["mois"] >= DEBUT_JUGE) & (c["mois"] <= FIN_JUGE)].reset_index(drop=True)


def tester(prix: dict, taux: dict, couts: dict, tirages: int = TIRAGES,
           graine: int = GRAINE) -> dict:
    """Le test complet. Rend les cinq criteres et tout ce qui les justifie."""
    rend, dec = preparer(prix, taux)
    grille = Grille(rend, dec)
    vrais = {n: dec[n]["signal"].to_numpy() for n in grille.noms}
    out = {"instruments": grille.noms}

    # -- critere 1 : pouvoir predictif --------------------------------------
    c = couples(dec)
    X = np.column_stack([np.ones(len(c)), c["x"].to_numpy()])
    b, t = ols_cluster(c["y"].to_numpy(), X, c["mois"].astype(str).to_numpy())
    out["c1"] = {"pente": float(b[1]), "t": float(t[1]), "n": len(c),
                 "ok": bool(b[1] > 0 and t[1] > SEUIL_T)}

    # -- critere 2 : profil par quintiles -----------------------------------
    q = pd.qcut(c["x"], QUINTILES, labels=False) + 1
    moy = c["y"].groupby(q).mean()
    inversions = int((np.diff(moy.to_numpy()) < 0).sum())
    sel = q.isin([1, QUINTILES]).to_numpy()
    X2 = np.column_stack([np.ones(sel.sum()), (q[sel] == QUINTILES).to_numpy().astype(float)])
    b2, t2 = ols_cluster(c["y"].to_numpy()[sel], X2, c["mois"].astype(str).to_numpy()[sel])
    out["c2"] = {"moyennes": [float(v) for v in moy.to_numpy()], "inversions": inversions,
                 "ecart": float(b2[1]), "t": float(t2[1]),
                 "ok": bool(inversions <= 1 and b2[1] > 0 and t2[1] > SEUIL_T)}

    # -- critere 3 : mieux que l'exposition passive ---------------------------
    brut = periode(mensuels(grille.rendements(vrais)))
    passifs = [n for n in grille.noms if INSTRUMENTS[n]["famille"] in FAMILLES_PASSIVES]
    gp = Grille({n: rend[n] for n in passifs}, {n: dec[n] for n in passifs})
    pas = periode(mensuels(gp.rendements({n: np.ones(len(dec[n])) for n in passifs})))
    a = pd.concat([brut, pas], axis=1, keys=["s", "p"]).dropna()
    b3, t3 = ols_nw(a["s"].to_numpy(), np.column_stack([np.ones(len(a)), a["p"].to_numpy()]))
    out["c3"] = {"alpha_mensuel": float(b3[0]), "t": float(t3[0]), "beta": float(b3[1]),
                 "ok": bool(b3[0] > 0 and t3[0] > SEUIL_T)}

    # -- criteres 4 et 5 : apres les couts FTMO --------------------------------
    net = periode(mensuels(grille.rendements(vrais, couts)))
    sh = sharpe(net)
    rng = np.random.default_rng(graine)
    meilleurs = 0
    for _ in range(tirages):
        sig = {n: melanger(vrais[n], dec[n]["dispo"].to_numpy(), rng) for n in grille.noms}
        if sharpe(periode(mensuels(grille.rendements(sig, couts)))) >= sh:
            meilleurs += 1
    p = (1.0 + meilleurs) / (tirages + 1.0)
    out["c4"] = {"sharpe": sh, "tirages": tirages, "au_moins_aussi_bons": meilleurs,
                 "p": p, "ok": bool(p < 0.05)}

    b5, t5 = ols_nw(net.to_numpy(), np.ones((len(net), 1)))
    apres = net[net.index >= DEBUT_APRES]
    out["c5"] = {"moyenne_mensuelle": float(b5[0]), "t": float(t5[0]),
                 "moyenne_2013": float(apres.mean()), "mois": len(net),
                 "ok": bool(b5[0] > 0 and t5[0] > SEUIL_T and apres.mean() > 0)}
    out["score"] = sum(out[k]["ok"] for k in ("c1", "c2", "c3", "c4", "c5"))
    out["net"] = net
    out["brut"] = brut
    return out


def rapport(res: dict) -> str:
    """La sortie brute du test, telle qu'on la committe."""
    L = []
    L.append("Instruments retenus (%d) : %s" % (len(res["instruments"]), ", ".join(res["instruments"])))
    c = res["c1"]
    L.append("1. pouvoir predictif : pente %.4f, t = %.2f (%d couples)  -> %s"
             % (c["pente"], c["t"], c["n"], "OUI" if c["ok"] else "non"))
    c = res["c2"]
    L.append("2. quintiles : %s ; %d inversion(s) ; Q5 - Q1 = %.4f, t = %.2f  -> %s"
             % (", ".join("%.4f" % v for v in c["moyennes"]), c["inversions"], c["ecart"], c["t"],
                "OUI" if c["ok"] else "non"))
    c = res["c3"]
    L.append("3. alpha contre l'acheteur permanent : %.3f %% par mois, t = %.2f (beta %.2f)  -> %s"
             % (100 * c["alpha_mensuel"], c["t"], c["beta"], "OUI" if c["ok"] else "non"))
    c = res["c4"]
    L.append("4. hasard : Sharpe net %.2f ; %d tirage(s) sur %d au moins aussi bons ; p = %.4f  -> %s"
             % (c["sharpe"], c["au_moins_aussi_bons"], c["tirages"], c["p"], "OUI" if c["ok"] else "non"))
    c = res["c5"]
    L.append("5. rendement net : %.3f %% par mois, t = %.2f sur %d mois ; 2013-2026 : %.3f %% par mois  -> %s"
             % (100 * c["moyenne_mensuelle"], c["t"], c["mois"], 100 * c["moyenne_2013"],
                "OUI" if c["ok"] else "non"))
    L.append("SCORE : %d sur 5" % res["score"])
    return "\n".join(L)


# =============================================================================
# 6. Donnees synthetiques (mise au point et tests, jamais pour le test reel)
# =============================================================================
TAUX_SYNTHETIQUES = {"US": 3.0, "XM": 2.0, "GB": 4.0, "JP": 0.3, "CH": 1.0,
                     "CA": 3.0, "AU": 4.5, "NZ": 5.0}
VOL_SYNTHETIQUE = {"indice": 0.18, "devise": 0.09, "metal": 0.20}


def synthetique(graine: int = 0, tendance: float = 0.0, debut: str = "1998-01-01",
                fin: str = "2026-09-30") -> tuple:
    """15 instruments aux calendriers propres, et leurs taux.

    On fabrique d'abord le rendement EN EXCES voulu, puis le cours qui le
    donne une fois le portage retire : le portage synthetique ne cree donc
    aucune tendance par lui-meme.

    tendance = 0 : rendements en exces independants, sans aucune information.
    tendance > 0 : une derive persistante (regimes de signe constant, de
    duree moyenne 18 mois) d'intensite `tendance` x volatilite par an : le
    rendement des 12 derniers mois annonce alors celui du mois suivant.
    """
    rng = np.random.default_rng(graine)
    jours = pd.bdate_range(debut, fin)
    taux = {}
    for code, niveau in TAUX_SYNTHETIQUES.items():
        marche = np.cumsum(rng.normal(0.0, 0.01, len(jours)))
        taux[code] = pd.Series(np.maximum(niveau + marche - marche.mean(), 0.0), index=jours)
    prix = {}
    for nom, spec in INSTRUMENTS.items():
        cal = jours[rng.random(len(jours)) > 0.03]          # ~3 % de jours feries
        vol = VOL_SYNTHETIQUE[spec["famille"]]
        mois = cal.to_period("M")
        uniques = mois.unique()
        regime = np.empty(len(uniques))
        v = 1.0 if rng.random() < 0.5 else -1.0
        for k in range(len(uniques)):
            if rng.random() < 1.0 / 18.0:
                v = -v
            regime[k] = v
        derive = pd.Series(regime, index=uniques).reindex(mois).to_numpy() * tendance * vol / JOURS_AN
        exces = derive + rng.normal(0.0, vol / np.sqrt(JOURS_AN), len(cal))
        jours_cal = pd.Series(cal, index=cal).diff().dt.days.fillna(1).to_numpy()
        porte = portage(spec, cal, taux).shift(1).fillna(0.0).to_numpy()
        variation = exces - porte * jours_cal / BASE_TAUX
        variation[0] = 0.0
        prix[nom] = pd.Series(100.0 * np.cumprod(1.0 + variation), index=cal)
    return prix, taux


def couts_nuls() -> dict:
    return {n: {"transaction": 0.0, "marge_achat": 0.0, "marge_vente": 0.0} for n in INSTRUMENTS}
