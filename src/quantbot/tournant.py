"""Le tournant du mois sur les indices FTMO : le test du protocole.

    docs/protocole_tournant_du_mois.md, commite le 7 novembre 2026 (58def69)

Ce module ne sert qu'a ce test. Aucun code du robot ne l'importe. Il reprend
du module de tendance ce que le protocole dit identique, avec le meme code,
deja teste : rendements en exces, volatilite estimee, statistiques.

Tout ce que le protocole fige est une CONSTANTE, ici ou dans le module de
tendance, et aucune fonction ne permet d'en changer une. Seuls `tirages` et
`graine` sont des arguments : les tests sur donnees synthetiques en tirent
moins pour tourner vite ; le script du test (`scripts/tester_tournant.py`)
passe toujours les valeurs figees.

La fenetre
----------
Au calendrier PROPRE de chaque indice (ses jours de cotation) :

    -2           avant-dernier jour de cotation du mois : achat a la cloture
    -1           dernier jour du mois             \\  les quatre jours de
    +1, +2, +3   trois premiers du mois suivant   /  rendement de la fenetre
    +3           vente a la cloture

Nominal = 40 % / volatilite estimee a la cloture d'achat, fixe jusqu'a la
vente. Hors fenetre : aucune position. La position ne lit donc que des dates
et des rendements deja connus a l'achat.

Le calendrier est celui des donnees. Une fermeture imprevue (11 septembre
2001, ouragan Sandy) y est traitee comme connue d'avance, ce qui ne fait lire
a aucune decision un cours qu'elle n'aurait pas eu.

Les donnees s'arretent le 30 septembre 2026, un jour de fin de mois. La
fenetre achetee fin septembre 2026 n'a donc dans la periode jugee que son
jour -1, sans sa vente ; celle de decembre 1999 n'y a que ses jours +1 a +3,
avec sa vente. Les strategies tirees au hasard (critere 4) sont traitees de
la meme facon.

Le portefeuille
---------------
Moyenne des positions des indices disponibles. Un indice compte des qu'il a
60 jours de rendements (sa volatilite existe), qu'il soit en fenetre ou non.
Chaque jour du calendrier commun (union des cotations) :
rendement = somme des resultats des indices / nombre d'indices disponibles.

Les couts FTMO (criteres 4 et 5)
--------------------------------
Pour chaque indice, ceux de docs/tendance_couts.csv :
  * transaction (spread entier + commission) x nominal, a l'achat et a la
    vente, le jour ou elles ont lieu ;
  * marge_achat (par an) x nominal x jours calendaires / 365, pour chaque
    jour de rendement de la fenetre : autant de jours que de nuits entre
    l'achat et la vente.

Les cinq criteres, au seuil t > 2,5
-----------------------------------
1. l'effet existe : regression empilee, sur les couples indice-jour, du
   rendement en exces du jour divise par la volatilite estimee la veille,
   sur une variable valant 1 les jours de la fenetre ; constante commune ;
   ecarts-types regroupes par mois ; coefficient > 0 et t > 2,5.
2. partout : le meme coefficient, indice par indice ; positif pour au moins
   5 des 6 indices.
3. mieux que l'exposition passive : rendements mensuels bruts regresses sur
   ceux du portefeuille toujours acheteur des 6 indices, meme dosage (la
   taille fixee a chaque achat vaut jusqu'a l'achat suivant) ; Newey-West
   3 retards ; alpha > 0 et t > 2,5.
4. hasard : 1 000 strategies ou, chaque mois, la fenetre est decalee d'un
   meme nombre k de seances pour tous les indices (vers le futur, au
   calendrier de chacun), k tire au hasard entre 4 et 14 ; tout le reste est
   identique (dosage, couts, nombre de jours) ; Sharpe net des couts ;
   p = (1 + tirages au moins aussi bons) / 1 001 < 0,01.
   Une fenetre decalee ne touche jamais la vraie fenetre dont elle vient. Elle
   ne touche la suivante que si le mois suivant compte 17 seances ou moins :
   le hasard recupere alors un peu de l'effet.
   Limite connue, mesuree avant le test sur 300 jeux synthetiques sans
   effet : ce critere conclut a tort dans 4 a 5 % des cas, et non 1 %. Les
   strategies tirees partagent leurs jours (deux decalages voisins se
   recouvrent), donc leur Sharpe varie moins d'un tirage a l'autre que celui
   d'une fenetre qui porte sur d'autres jours, comme la vraie. Le protocole
   est fige : le critere reste tel quel. La decision exige 5 sur 5, et sur
   les memes jeux les criteres 1 et 3 ne concluent a tort que dans 1 a 2 %
   des cas.
5. rendement net : moyenne des rendements mensuels nets, t > 2,5
   (Newey-West, 3 retards) sur 2000-2026, et moyenne > 0 sur 2009-2026.

Criteres 1 a 3 avant les couts FTMO, 4 et 5 apres.

Ce que le test peut voir
------------------------
Mesure avant le test, sur donnees synthetiques : effet identique sur les 6
indices, correlation 0,5 entre eux, marches qui montent un peu (Sharpe 0,3),
couts de l'ordre de ceux de FTMO, 200 jeux par ligne (300 sans effet).

    rendement en plus par jour de fenetre     part des jeux a 5 sur 5
    0,10 % (l'ordre de grandeur des etudes)           87 %
    0,07 %                                            42 %
    0,05 % (la moitie)                                22 %
    0,03 %                                             6 %
    aucun                                     2 sur 300 (moins de 1 %)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantbot import tendance as T

# -- fige par le protocole -----------------------------------------------------
#: Les 6 indices, avec les fichiers bruts et les couts du protocole de tendance.
INDICES = ("US500", "GER40", "UK100", "JP225", "AUS200", "HK50")
JOURS_FENETRE = 4           # -1, +1, +2, +3 : les rendements des 4 seances apres l'achat
VOL_POSITION = 0.40         # nominal = 40 % / volatilite estimee
DEBUT_JUGE = pd.Period("2000-01", "M")
FIN_JUGE = pd.Period("2026-09", "M")
DEBUT_APRES = pd.Period("2009-01", "M")     # apres McConnell et Xu (2008)
RETARDS_NW = 3
TIRAGES = 1000
GRAINE = 20261106
DECALAGE_MIN = 4            # seances
DECALAGE_MAX = 14
SEUIL_T = 2.5
SEUIL_P = 0.01
MIN_POSITIFS = 5            # indices au coefficient positif, sur 6
# Repris tels quels du module de tendance (le protocole les dit identiques) :
# T.COM_VOL = 60, T.JOURS_AN = 261, T.MIN_JOURS_VOL = 60, T.BASE_TAUX = 365,
# T.DEBUT_DONNEES = 1998-01-01, T.DEBUT_MAX_SERIE = 2004-01-31, T.INSTRUMENTS.


# =============================================================================
# 1. Les donnees
# =============================================================================
def preparer(prix: dict, taux: dict) -> dict:
    """Rendements en exces des 6 indices, chacun a son propre calendrier.

    Les memes regles que pour la tendance : donnees des janvier 1998 ; une
    serie qui commence apres janvier 2004 aurait du etre exclue a l'etape 2,
    et un indice absent rendrait le test incomplet : on refuse dans les deux
    cas plutot que de tester autre chose en silence.
    """
    manque = [n for n in INDICES if n not in prix]
    if manque:
        raise ValueError("indices absents : %s" % ", ".join(manque))
    rend = {}
    for nom in INDICES:
        p = prix[nom].dropna()
        p = p[p.index >= T.DEBUT_DONNEES]
        if len(p) == 0 or p.index.min() > T.DEBUT_MAX_SERIE:
            raise ValueError("%s commence apres janvier 2004 : a exclure a l'etape 2" % nom)
        rend[nom] = T.rendements_exces(p, T.INSTRUMENTS[nom], taux)
    return rend


class Indice:
    """Un indice a son calendrier propre.

      dates     jours de rendement (cotations, sans la premiere)
      r         rendement en exces du jour
      j         jours calendaires couverts par ce rendement / 365
      vol       volatilite estimee a la cloture (NaN avant 60 jours)
      dispo     la volatilite existe
      entree    position de chaque jour -2 (achat), un par mois d'au moins
                deux seances ; la fenetre = les positions entree+1 a entree+4
      mois      le mois de chaque achat
    """

    def __init__(self, nom: str, rend: pd.DataFrame):
        self.nom = nom
        self.dates = pd.DatetimeIndex(rend.index)
        self.n = len(self.dates)
        self.r = rend["r"].to_numpy(dtype=float)
        self.j = rend["jours"].to_numpy(dtype=float) / T.BASE_TAUX
        self.vol = T.volatilite(rend["r"]).to_numpy(dtype=float)
        self.dispo = np.isfinite(self.vol) & (self.vol > 0)
        mois = self.dates.to_period("M")
        pos = pd.Series(np.arange(self.n), index=self.dates).groupby(mois)
        dernier, premier = pos.max(), pos.min()
        deux = (dernier - premier).to_numpy() >= 1          # au moins deux seances
        self.entree = dernier.to_numpy()[deux].astype(np.int64) - 1
        self.mois = dernier.index[deux]
        self.u = None       # positions au calendrier commun (posees par la Grille)
        self.m = None       # positions de self.mois dans la liste commune des mois

    def fenetre(self) -> np.ndarray:
        """Vrai les jours de la fenetre (-1, +1, +2, +3), qu'on y achete ou non."""
        f = np.zeros(self.n, dtype=bool)
        for d in range(1, JOURS_FENETRE + 1):
            q = self.entree + d
            f[q[q < self.n]] = True
        return f


# =============================================================================
# 2. Le portefeuille, sur le calendrier commun
# =============================================================================
class Grille:
    """Tout ce qu'il faut pour rejouer la vraie fenetre ou une fenetre decalee.

    Un decalage est un tableau d'entiers aligne sur `mois_fen` (les mois
    d'achat, communs a tous les indices) : la fenetre achetee le mois m l'est
    k[m] seances plus tard, au calendrier de chaque indice. k = 0 partout :
    la vraie strategie.
    """

    def __init__(self, rend: dict):
        self.indices = [Indice(n, rend[n]) for n in rend]
        self.noms = [ix.nom for ix in self.indices]
        cal = self.indices[0].dates
        for ix in self.indices[1:]:
            cal = cal.union(ix.dates)
        self.cal = pd.DatetimeIndex(cal)
        nb = len(self.cal)
        mois_fen = self.indices[0].mois
        for ix in self.indices[1:]:
            mois_fen = mois_fen.union(ix.mois)
        self.mois_fen = mois_fen
        self.n_dispo = np.zeros(nb)
        for ix in self.indices:
            ix.u = self.cal.get_indexer(ix.dates).astype(np.int64)
            ix.m = self.mois_fen.get_indexer(ix.mois).astype(np.int64)
            if ix.dispo.any():
                self.n_dispo[ix.u[int(np.argmax(ix.dispo))]:] += 1.0
        mc = self.cal.to_period("M")
        code = self.cal.year.to_numpy() * 12 + self.cal.month.to_numpy()
        self.debuts = np.r_[0, np.flatnonzero(np.diff(code) != 0) + 1].astype(np.int64)
        self.periodes = mc[self.debuts]

    # -- positions --------------------------------------------------------------
    def zero(self) -> np.ndarray:
        return np.zeros(len(self.mois_fen), dtype=np.int64)

    def tirage(self, rng) -> np.ndarray:
        """Un decalage du critere 4 : k uniforme entre 4 et 14, un par mois."""
        return rng.integers(DECALAGE_MIN, DECALAGE_MAX + 1, size=len(self.mois_fen))

    def achats(self, ix: Indice, k: np.ndarray = None) -> tuple:
        """(positions d'achat, nominaux) des fenetres de l'indice, decalees de k.

        Une fenetre n'est achetee que si l'achat tombe dans les donnees et que
        la volatilite y existe.
        """
        k = self.zero() if k is None else np.asarray(k, dtype=np.int64)
        a = ix.entree + k[ix.m]
        a = a[a < ix.n]
        a = a[ix.dispo[a]]
        return a, VOL_POSITION / ix.vol[a]

    def positions(self, k: np.ndarray = None) -> dict:
        """Nominal de chaque indice, chaque jour de son calendrier (0 hors fenetre)."""
        out = {}
        for ix in self.indices:
            a, w = self.achats(ix, k)
            p = np.zeros(ix.n)
            for d in range(1, JOURS_FENETRE + 1):
                q = a + d
                v = q < ix.n
                p[q[v]] = w[v]
            out[ix.nom] = pd.Series(p, index=ix.dates)
        return out

    # -- rendements -------------------------------------------------------------
    def _moyenne(self, idx: list, val: list) -> np.ndarray:
        tot = np.bincount(np.concatenate(idx), weights=np.concatenate(val), minlength=len(self.cal))
        return np.where(self.n_dispo > 0, tot / np.maximum(self.n_dispo, 1.0), 0.0)

    def jour(self, k: np.ndarray = None, couts: dict = None) -> np.ndarray:
        """Rendement quotidien du portefeuille, au calendrier commun.

        `couts` : {indice: {"transaction": fraction du nominal echange,
        "marge_achat": fraction par an, ...}}. None = avant les couts FTMO.
        """
        idx, val = [np.zeros(0, dtype=np.int64)], [np.zeros(0)]
        for ix in self.indices:
            a, w = self.achats(ix, k)
            c = couts[ix.nom] if couts is not None else None
            for d in range(1, JOURS_FENETRE + 1):
                q = a + d
                v = q < ix.n
                qq, ww = q[v], w[v]
                res = ww * ix.r[qq]
                if c is not None:
                    res = res - ww * c["marge_achat"] * ix.j[qq]
                idx.append(ix.u[qq])
                val.append(res)
            if c is not None:
                idx.append(ix.u[a])                                 # achat
                val.append(-c["transaction"] * w)
                s = a + JOURS_FENETRE                                 # vente
                v = s < ix.n
                idx.append(ix.u[s[v]])
                val.append(-c["transaction"] * w[v])
        return self._moyenne(idx, val)

    def passif(self) -> np.ndarray:
        """Le portefeuille toujours acheteur du critere 3, meme dosage.

        Chaque indice garde en permanence le nominal fixe a son dernier achat
        (-2) : 40 % / la volatilite estimee ce jour-la, jusqu'a l'achat suivant.
        Sur les jours de fenetre, sa position est donc celle de la strategie.
        """
        idx, val = [np.zeros(0, dtype=np.int64)], [np.zeros(0)]
        for ix in self.indices:
            a, w = self.achats(ix)
            t = np.arange(ix.n)
            dern = np.searchsorted(a, t, side="left") - 1       # dernier achat AVANT t
            ok = dern >= 0
            idx.append(ix.u[t[ok]])
            val.append(w[dern[ok]] * ix.r[t[ok]])
        return self._moyenne(idx, val)

    def mensuels(self, jour: np.ndarray) -> pd.Series:
        """Les rendements quotidiens composes dans chaque mois."""
        return pd.Series(np.multiply.reduceat(1.0 + np.asarray(jour, dtype=float), self.debuts) - 1.0,
                         index=self.periodes)

    # -- critere 1 et controle du calendrier ---------------------------------
    def couples(self) -> pd.DataFrame:
        """Les couples indice-jour de la periode jugee dont la volatilite de la veille existe."""
        lignes = []
        for ix in self.indices:
            veille = np.r_[np.nan, ix.vol[:-1]]
            ok = np.isfinite(veille) & (veille > 0)
            lignes.append(pd.DataFrame({
                "indice": ix.nom, "mois": ix.dates.to_period("M")[ok],
                "y": ix.r[ok] / veille[ok], "fenetre": ix.fenetre()[ok].astype(float)}))
        c = pd.concat(lignes, ignore_index=True)
        return c[(c["mois"] >= DEBUT_JUGE) & (c["mois"] <= FIN_JUGE)].reset_index(drop=True)

    def irregulieres(self) -> int:
        """Fenetres touchant la periode jugee dont les jours +1 a +3 ne sont pas
        les trois premieres seances du mois qui suit l'achat (attendu : 0)."""
        nb = 0
        for ix in self.indices:
            mois = ix.dates.to_period("M")
            for e, m in zip(ix.entree, ix.mois):
                jours = [e + d for d in range(1, JOURS_FENETRE + 1) if e + d < ix.n]
                if not any(DEBUT_JUGE <= mois[q] <= FIN_JUGE for q in jours):
                    continue
                if any(mois[q] != m + 1 for q in jours[1:]):
                    nb += 1
        return nb


# =============================================================================
# 3. Le test
# =============================================================================
def tester(prix: dict, taux: dict, couts: dict, tirages: int = TIRAGES,
           graine: int = GRAINE) -> dict:
    """Le test complet. Rend les cinq criteres et tout ce qui les justifie."""
    g = Grille(preparer(prix, taux))
    out = {"indices": g.noms}
    zero = g.zero()

    # -- critere 1 : l'effet existe ------------------------------------------
    c = g.couples()
    grp = c["mois"].astype(str).to_numpy()
    X = np.column_stack([np.ones(len(c)), c["fenetre"].to_numpy()])
    b, t = T.ols_cluster(c["y"].to_numpy(), X, grp)
    out["c1"] = {"coef": float(b[1]), "t": float(t[1]), "constante": float(b[0]), "n": len(c),
                 "n_fenetre": int(c["fenetre"].sum()),
                 "ok": bool(b[1] > 0 and t[1] > SEUIL_T)}

    # -- critere 2 : partout, pas sur un seul marche -------------------------
    par = {}
    for nom in g.noms:
        sel = (c["indice"] == nom).to_numpy()
        bi, ti = T.ols_cluster(c["y"].to_numpy()[sel], X[sel], grp[sel])
        par[nom] = {"coef": float(bi[1]), "t": float(ti[1]), "n": int(sel.sum())}
    positifs = sum(1 for v in par.values() if v["coef"] > 0)
    out["c2"] = {"par_indice": par, "positifs": positifs, "indices": len(par),
                 "ok": bool(positifs >= MIN_POSITIFS)}

    # -- critere 3 : mieux que l'exposition passive -----------------------------
    brut = T.periode(g.mensuels(g.jour(zero)), DEBUT_JUGE, FIN_JUGE)
    pas = T.periode(g.mensuels(g.passif()), DEBUT_JUGE, FIN_JUGE)
    a = pd.concat([brut, pas], axis=1, keys=["s", "p"]).dropna()
    b3, t3 = T.ols_nw(a["s"].to_numpy(), np.column_stack([np.ones(len(a)), a["p"].to_numpy()]), RETARDS_NW)
    out["c3"] = {"alpha_mensuel": float(b3[0]), "t": float(t3[0]), "beta": float(b3[1]),
                 "ok": bool(b3[0] > 0 and t3[0] > SEUIL_T)}

    # -- criteres 4 et 5 : apres les couts FTMO --------------------------------
    net = T.periode(g.mensuels(g.jour(zero, couts)), DEBUT_JUGE, FIN_JUGE)
    sh = T.sharpe(net)
    rng = np.random.default_rng(graine)
    hasard = np.empty(tirages)
    for i in range(tirages):
        hasard[i] = T.sharpe(T.periode(g.mensuels(g.jour(g.tirage(rng), couts)), DEBUT_JUGE, FIN_JUGE))
    meilleurs = int((hasard >= sh).sum())
    p = (1.0 + meilleurs) / (tirages + 1.0)
    out["c4"] = {"sharpe": sh, "tirages": tirages, "au_moins_aussi_bons": meilleurs, "p": p,
                 "hasard_mediane": float(np.median(hasard)) if tirages else float("nan"),
                 "hasard_99": float(np.percentile(hasard, 99)) if tirages else float("nan"),
                 "hasard_max": float(hasard.max()) if tirages else float("nan"),
                 "ok": bool(p < SEUIL_P)}

    b5, t5 = T.ols_nw(net.to_numpy(), np.ones((len(net), 1)), RETARDS_NW)
    apres = net[net.index >= DEBUT_APRES]
    out["c5"] = {"moyenne_mensuelle": float(b5[0]), "t": float(t5[0]),
                 "moyenne_2009": float(apres.mean()), "mois": len(net), "mois_2009": len(apres),
                 "ok": bool(b5[0] > 0 and t5[0] > SEUIL_T and apres.mean() > 0)}

    out["score"] = sum(out[k]["ok"] for k in ("c1", "c2", "c3", "c4", "c5"))
    out["infos"] = {"brut_moyen": float(brut.mean()), "net_moyen": float(net.mean()),
                    "couts_moyens": float((brut - net).mean()), "passif_moyen": float(pas.mean()),
                    "sharpe_brut": T.sharpe(brut), "fenetres_irregulieres": g.irregulieres()}
    out["brut"], out["net"], out["passif"] = brut, net, pas
    return out


def rapport(res: dict) -> str:
    """La sortie brute du test, telle qu'on la committe."""
    def v(ok):
        return "OUI" if ok else "non"
    L = ["Indices (%d) : %s" % (len(res["indices"]), ", ".join(res["indices"]))]
    c = res["c1"]
    L.append("1. l'effet existe : coefficient %.4f, t = %.2f (%d couples indice-jour, dont %d en fenetre)  -> %s"
             % (c["coef"], c["t"], c["n"], c["n_fenetre"], v(c["ok"])))
    c = res["c2"]
    L.append("2. partout : %s ; %d positif(s) sur %d  -> %s"
             % (", ".join("%s %.4f (t %.2f)" % (n, x["coef"], x["t"]) for n, x in c["par_indice"].items()),
                c["positifs"], c["indices"], v(c["ok"])))
    c = res["c3"]
    L.append("3. alpha contre l'acheteur permanent : %.3f %% par mois, t = %.2f (beta %.2f)  -> %s"
             % (100 * c["alpha_mensuel"], c["t"], c["beta"], v(c["ok"])))
    c = res["c4"]
    L.append("4. hasard : Sharpe net %.2f ; %d tirage(s) sur %d au moins aussi bons ; p = %.4f"
             " (Sharpe des tirages : mediane %.2f, 99e centile %.2f, max %.2f)  -> %s"
             % (c["sharpe"], c["au_moins_aussi_bons"], c["tirages"], c["p"], c["hasard_mediane"],
                c["hasard_99"], c["hasard_max"], v(c["ok"])))
    c = res["c5"]
    L.append("5. rendement net : %.3f %% par mois, t = %.2f sur %d mois ; 2009-2026 : %.3f %% par mois  -> %s"
             % (100 * c["moyenne_mensuelle"], c["t"], c["mois"], 100 * c["moyenne_2009"], v(c["ok"])))
    L.append("SCORE : %d sur 5" % res["score"])
    i = res["infos"]
    L.append("Pour information, hors criteres : brut %.3f %% par mois (Sharpe %.2f) ; couts %.3f %% par mois ;"
             " acheteur permanent %.3f %% par mois ; fenetres irregulieres : %d"
             % (100 * i["brut_moyen"], i["sharpe_brut"], 100 * i["couts_moyens"], 100 * i["passif_moyen"],
                i["fenetres_irregulieres"]))
    return "\n".join(L)


# =============================================================================
# 4. Donnees synthetiques (mise au point et tests, jamais pour le test reel)
# =============================================================================
def jours_de_fenetre(cal: pd.DatetimeIndex) -> np.ndarray:
    """1 les jours -1, +1, +2, +3 d'apres leur RANG dans le mois, 0 sinon.

    La definition du protocole, ecrite independamment de `Indice` (qui passe
    par les positions des achats) : les donnees synthetiques implantent
    l'effet avec elle, et les tests verifient que la strategie le trouve.
    """
    mois = pd.Series(pd.DatetimeIndex(cal).to_period("M"))
    rang = mois.groupby(mois).cumcount().to_numpy() + 1                   # 1 = premier du mois
    depuis_fin = mois.groupby(mois).cumcount(ascending=False).to_numpy() + 1   # 1 = dernier
    return ((depuis_fin == 1) | (rang <= 3)).astype(float)


def synthetique(graine: int = 0, effet=0.0, derive: float = 0.0, correlation: float = 0.5,
                feries: float = 0.03, debut: str = "1998-01-01", fin: str = "2026-09-30") -> tuple:
    """Les 6 indices a calendriers propres, et leurs taux.

    Comme pour la tendance, on fabrique d'abord le rendement EN EXCES voulu,
    puis le cours qui le donne une fois le taux retire.

    effet       rendement en exces ajoute a chaque jour de fenetre, en
                ecarts-types quotidiens : un nombre (tous les indices) ou
                {indice: effet}. 0 = aucun effet de calendrier.
    derive      rendement en exces annuel moyen de chaque indice, en
                volatilites annuelles : un marche qui monte, sans effet de
                calendrier.
    correlation entre les indices (un facteur commun aux jours ouvres).
    feries      part des jours ouvres sans cotation, tiree pour chaque indice ;
                le rendement d'une cotation couvre tous les jours ouvres
                depuis la precedente.
    """
    rng = np.random.default_rng(graine)
    jours = pd.bdate_range(debut, fin)
    taux = {}
    for code, niveau in T.TAUX_SYNTHETIQUES.items():
        marche = np.cumsum(rng.normal(0.0, 0.01, len(jours)))
        taux[code] = pd.Series(np.maximum(niveau + marche - marche.mean(), 0.0), index=jours)
    vol = T.VOL_SYNTHETIQUE["indice"]
    ect = vol / np.sqrt(T.JOURS_AN)
    commun = rng.standard_normal(len(jours))
    effets = effet if isinstance(effet, dict) else {n: effet for n in INDICES}
    prix = {}
    for nom in INDICES:
        garde = rng.random(len(jours)) >= feries
        cal = jours[garde]
        z = np.sqrt(correlation) * commun + np.sqrt(1.0 - correlation) * rng.standard_normal(len(jours))
        chocs = np.diff(np.r_[0.0, np.cumsum(z)[garde]])                 # depuis la cotation precedente
        ouvres = np.diff(np.r_[-1, np.flatnonzero(garde)])
        exces = (ect * chocs + derive * vol / T.JOURS_AN * ouvres
                 + effets.get(nom, 0.0) * ect * jours_de_fenetre(cal))
        jours_cal = pd.Series(cal, index=cal).diff().dt.days.fillna(1).to_numpy()
        porte = T.portage(T.INSTRUMENTS[nom], cal, taux).shift(1).fillna(0.0).to_numpy()
        variation = exces - porte * jours_cal / T.BASE_TAUX
        variation[0] = 0.0
        prix[nom] = pd.Series(100.0 * np.cumprod(1.0 + variation), index=cal)
    return prix, taux


def couts_nuls() -> dict:
    return {n: {"transaction": 0.0, "marge_achat": 0.0, "marge_vente": 0.0} for n in INDICES}
