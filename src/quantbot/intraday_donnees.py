"""Les donnees du momentum intrajournalier : heure du serveur FTMO, fenetres,
barres de 30 minutes, calendrier des seances, controles de l'etape 2.

Etapes 1 et 2 de docs/protocole_momentum_intrajournalier.md. Ce module ne
calcule AUCUN signal ni aucun rendement de strategie. Il convertit des heures,
ecrit et relit des fichiers de barres, et controle leur qualite. Les controles
ne regardent que les cours : jamais le sens d'une position, jamais le lien
entre r_RJ et r_DD. Le premier calcul de la strategie sur donnees reelles est
le test.

L'heure du serveur FTMO
-----------------------
Celle de New York plus 7 heures : GMT+3 pendant l'heure d'ete americaine,
GMT+2 sinon (bulletin FTMO du 22 octobre 2026). L'etape 2 le verifie sur tout
l'historique (`controle_heure`).

MetaTrader 5 donne les heures du serveur comme si c'etait de l'UTC : une barre
qui commence a 16:30 au serveur a pour `time` le nombre de secondes ecoulees
de 1970-01-01 00:00 a 16:30 "UTC". Il les prend de la meme facon. Ici, une
heure du serveur est un Timestamp naif ; `pour_mt5` en fait le datetime UTC
qu'attend MT5, `depuis_mt5` fait l'inverse.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

from quantbot import tendance_donnees as TD

INDICES = ("US500", "GER40", "UK100", "JP225", "AUS200", "HK50")
SYMBOLES = {n: n + ".cash" for n in INDICES}
FUSEAUX = {"US500": "America/New_York", "GER40": "Europe/Berlin", "UK100": "Europe/London",
           "JP225": "Asia/Tokyo", "AUS200": "Australia/Sydney", "HK50": "Asia/Hong_Kong"}
#: Debut de la fenetre, a l'heure locale de la bourse : la cloture du marche au
#: comptant moins 30 minutes. (premier jour de validite, heure locale)
DEBUTS = {
    "US500": (("1900-01-01", "15:30"),),
    "GER40": (("1900-01-01", "17:00"),),
    "UK100": (("1900-01-01", "16:00"),),
    # Tokyo : cloture a 15:00 jusqu'au 4 novembre 2024, a 15:30 depuis le 5.
    "JP225": (("1900-01-01", "14:30"), ("2024-11-05", "15:00")),
    "AUS200": (("1900-01-01", "15:30"),),
    "HK50": (("1900-01-01", "15:30"),),
}
DUREE = pd.Timedelta(minutes=30)
NEW_YORK = "America/New_York"
EUROPE = "Europe/Berlin"
DECALAGE_SERVEUR = pd.Timedelta(hours=7)        # heure du serveur = heure de New York + 7 h

DOSSIER = Path("data/intraday")
BRUT = DOSSIER / "brut"
MANIFESTE = DOSSIER / "manifeste.csv"
DEBUT_DEMANDE = pd.Timestamp("2000-01-01")      # "de la premiere barre disponible"
FIN_BARRES = pd.Timestamp("2026-11-01")         # exclue : barres jusqu'au 31 octobre 2026 inclus
FIN_CALENDRIER = pd.Timestamp("2026-10-30")
DEBUT_JUGE = pd.Timestamp("2018-04-01")
FIN_JUGE = pd.Timestamp("2026-10-31")
COLONNES = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")
CHAMPS_MANIFESTE = ("fichier", "instrument", "symbole", "telecharge_utc", "barres",
                    "premiere_barre", "derniere_barre", "octets", "sha256")

# -- controles de l'etape 2 (fixes avant de regarder les barres) -------------------
PART_HEURE = 0.90       # heure du serveur : part des seances ou la barre de 9:30 l'emporte
PART_MAX = 0.05         # barres de fenetre manquantes, ou figees : au-dela, exclusion
SAUT_MAX = 0.25         # saut d'une seance a l'autre : 25 % ou 10 ecarts-types,
SIGMAS_MAX = 10.0       # comme au protocole de tendance
ECART_MAX = 0.01        # corroboration d'un saut dans la fenetre : 1 %, ou la moitie du saut


# -- les heures ------------------------------------------------------------------------
def _jours(jours) -> pd.DatetimeIndex:
    j = pd.DatetimeIndex(pd.to_datetime(jours))
    if j.tz is not None:
        j = j.tz_localize(None)
    return j.normalize()


def heure_locale_debut(indice: str, jours) -> pd.TimedeltaIndex:
    """L'heure locale du debut de la fenetre, seance par seance."""
    j = _jours(jours)
    h = np.zeros(len(j), dtype="timedelta64[ns]")
    for debut, hhmm in DEBUTS[indice]:
        h[np.asarray(j >= pd.Timestamp(debut))] = pd.Timedelta(hhmm + ":00").to_timedelta64()
    return pd.TimedeltaIndex(h)


def vers_serveur(instants) -> pd.DatetimeIndex:
    """Instants avec fuseau -> heures du serveur FTMO (naives)."""
    return pd.DatetimeIndex(instants).tz_convert(NEW_YORK).tz_localize(None) + DECALAGE_SERVEUR


def debut_fenetre(indice: str, jours) -> pd.DatetimeIndex:
    """L'heure du serveur du debut de la fenetre, pour chaque seance (date locale
    de la bourse). Les changements d'heure des deux cotes sont compris."""
    j = _jours(jours)
    return vers_serveur((j + heure_locale_debut(indice, j)).tz_localize(FUSEAUX[indice]))


def pour_mt5(t):
    """Heure du serveur (naive) -> le datetime UTC qu'attend MT5."""
    return pd.Timestamp(t).tz_localize("UTC").to_pydatetime()


def depuis_mt5(temps, unite: str = "s") -> pd.DatetimeIndex:
    """Les `time` (secondes) ou `time_msc` (unite="ms") de MT5 -> heures du serveur."""
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(temps, dtype="int64"), unit=unite))


def _avance_sur_utc(jours, fuseau) -> np.ndarray:
    midi = _jours(jours) + pd.Timedelta(hours=12)
    return np.asarray(midi - midi.tz_localize(fuseau).tz_convert("UTC").tz_localize(None))


def semaines_de_decalage(jours) -> np.ndarray:
    """Vrai les jours ou l'Europe et les Etats-Unis n'ont pas encore tous les deux
    change d'heure. Berlin a 6 heures d'avance sur New York, sauf ces jours-la."""
    ecart = _avance_sur_utc(jours, EUROPE) - _avance_sur_utc(jours, NEW_YORK)
    return ecart != np.timedelta64(6, "h")


# -- les fichiers de barres --------------------------------------------------------------
def nom_fichier(indice: str) -> str:
    return "ftmo_%s_M30.csv" % SYMBOLES[indice].replace(".", "_")


def ecrire_barres(chemin, barres) -> None:
    """Les barres telles que MT5 les rend, en CSV. Les cours sont ecrits a leur
    precision exacte, avec des fins de ligne \\n : memes octets sur tout systeme."""
    cols = [barres[c].tolist() for c in COLONNES]
    with open(str(chemin), "w", encoding="ascii", newline="\n") as fh:
        fh.write(",".join(COLONNES) + "\n")
        for t, o, h, b, c, v, s, r in zip(*cols):
            fh.write("%d,%r,%r,%r,%r,%d,%d,%d\n" % (t, float(o), float(h), float(b), float(c), v, s, r))


def lire_barres(chemin) -> pd.DataFrame:
    """Un fichier de barres -> index : heure du serveur du debut de la barre."""
    d = pd.read_csv(str(chemin), float_precision="round_trip")
    if tuple(d.columns) != COLONNES:
        raise ValueError("%s : colonnes inattendues %s" % (chemin, list(d.columns)))
    idx = depuis_mt5(d["time"].to_numpy())
    d = d.drop(columns="time")
    d.index = idx
    d.index.name = "debut"
    if d.index.has_duplicates or not d.index.is_monotonic_increasing:
        raise ValueError("%s : heures de barres en double ou dans le desordre" % chemin)
    return d


def ecrire_manifeste(lignes, chemin=MANIFESTE) -> None:
    with open(str(chemin), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CHAMPS_MANIFESTE, lineterminator="\n")
        w.writeheader()
        w.writerows(lignes)


def lire_manifeste(chemin=MANIFESTE) -> dict:
    with open(str(chemin), encoding="utf-8", newline="") as fh:
        return {l["fichier"]: l for l in csv.DictReader(fh)}


def verifier_empreintes(dossier=BRUT, manifeste=MANIFESTE) -> list:
    """Les ecarts entre les fichiers de barres et le manifeste ecrit au telechargement."""
    if not Path(manifeste).exists():
        return ["%s n'existe pas : les barres n'ont pas ete telechargees" % manifeste]
    m = lire_manifeste(manifeste)
    problemes = []
    for n in INDICES:
        f = nom_fichier(n)
        if f not in m:
            problemes.append("%s : absent du manifeste" % f)
        elif not (Path(dossier) / f).exists():
            problemes.append("%s : fichier absent de %s" % (f, dossier))
        elif TD.sha256(Path(dossier) / f) != m[f]["sha256"]:
            problemes.append("%s : l'empreinte ne correspond plus au manifeste" % f)
    return problemes


# -- le calendrier des seances --------------------------------------------------------
def fichier_calendrier(indice: str) -> str:
    """Le fichier de cours quotidiens du protocole de tendance (source principale)."""
    return TD.nom_fichier(TD.SERIES[indice][0])


def verifier_calendrier(tableau: dict, dossier=TD.BRUT) -> list:
    """Les ecarts entre les fichiers du calendrier et l'etape 2 du protocole de tendance.

    `tableau` : le tableau de cette etape, fichier -> (empreinte, verdict).
    """
    problemes = []
    for n in INDICES:
        f = fichier_calendrier(n)
        sha = tableau.get(f, (None, ""))[0]
        if not sha:
            problemes.append("%s : pas d'empreinte dans le protocole de tendance" % f)
        elif not (Path(dossier) / f).exists():
            problemes.append("%s : fichier absent de %s" % (f, dossier))
        elif TD.sha256(Path(dossier) / f) != sha:
            problemes.append("%s : l'empreinte ne correspond plus a celle du protocole de tendance" % f)
    return problemes


def calendrier(dossier=TD.BRUT, fin=FIN_CALENDRIER) -> dict:
    """Indice -> clotures quotidiennes Yahoo (date locale -> cours), jusqu'a `fin`.

    Leurs dates sont les seances du protocole : un jour ou le marche au comptant
    est ferme n'en est jamais une, meme si le CFD cote. Les cours ne servent
    qu'aux controles de l'etape 2.
    """
    out = {}
    for n in INDICES:
        s = TD.lire(Path(dossier) / fichier_calendrier(n), "yahoo")
        out[n] = s[s.index <= pd.Timestamp(fin)]
    return out


# -- les barres de fenetre ------------------------------------------------------------
def barres_de_fenetre(barres: pd.DataFrame, indice: str, seances) -> pd.DataFrame:
    """La barre qui commence au debut de la fenetre, seance par seance.

    Index : les seances (dates locales). Colonnes : debut (heure du serveur),
    open, high, low, close et tick_volume de la barre (NaN si elle manque) ;
    pour les controles, la cloture de la barre d'avant et l'ouverture de celle
    d'apres (NaN si elles manquent).
    """
    j = _jours(seances)
    debut = debut_fenetre(indice, j)
    f = barres.reindex(debut)[["open", "high", "low", "close", "tick_volume"]]
    f.index = j
    f.index.name = "seance"
    f.insert(0, "debut", debut)
    f["cloture_avant"] = barres["close"].reindex(debut - DUREE).to_numpy()
    f["ouverture_apres"] = barres["open"].reindex(debut + DUREE).to_numpy()
    return f


# -- les controles de l'etape 2 ----------------------------------------------------------
def controle_heure(barres: pd.DataFrame, seances) -> dict:
    """Le controle de l'heure du serveur, sur les barres US500 (protocole, "Les donnees").

    Si l'heure du serveur est celle de New York plus 7 heures, alors parmi les
    barres de 8:00 a 12:00, heure de New York, celle au plus gros volume est
    celle de 9:30 : l'ouverture du marche au comptant. Le protocole l'exige pour
    au moins 90 % des seances, et aussi pour au moins 90 % des seances des
    semaines ou l'Europe et les Etats-Unis n'ont pas encore tous les deux change
    d'heure : si l'heure du serveur suivait l'Europe, c'est la que cela se verrait.
    Une seance sans barre de 9:30 compte comme un echec.
    """
    ny = barres.index - DECALAGE_SERVEUR              # l'hypothese a verifier
    jour = ny.normalize()
    minute = ny - jour
    j = _jours(seances)
    j = j[(j >= barres.index.min().normalize()) & (j <= barres.index.max().normalize())]
    dedans = (np.asarray(minute >= pd.Timedelta(hours=8)) & np.asarray(minute < pd.Timedelta(hours=12))
              & np.asarray(jour.isin(j)))
    x = pd.DataFrame({"jour": jour[dedans], "minute": minute[dedans],
                      "volume": barres["tick_volume"].to_numpy()[dedans]})
    maxi = x.groupby("jour")["volume"].max()
    v930 = (x[x["minute"] == pd.Timedelta(hours=9, minutes=30)].set_index("jour")["volume"]
            .reindex(maxi.index))
    ok = ((v930 == maxi) & (v930 > 0)).to_numpy()
    dec = semaines_de_decalage(maxi.index)
    annees = np.asarray(maxi.index.year)
    par_annee = {int(a): (int((annees == a).sum()), int(ok[annees == a].sum())) for a in sorted(set(annees))}
    n, k = int(len(ok)), int(ok.sum())
    nd, kd = int(dec.sum()), int(ok[dec].sum())
    part, part_d = (k / n if n else 0.0), (kd / nd if nd else 0.0)
    return {"seances": n, "reussies": k, "part": part, "sans_barres": int(len(j) - n),
            "decalage": {"seances": nd, "reussies": kd, "part": part_d}, "par_annee": par_annee,
            "ok": bool(n > 0 and nd > 0 and part >= PART_HEURE and part_d >= PART_HEURE)}


def controler_indice(indice: str, barres: pd.DataFrame, yahoo: pd.Series,
                     debut_juge=DEBUT_JUGE, fin_juge=FIN_JUGE) -> dict:
    """Les controles de l'etape 2 pour un indice. Ne regarde que les cours.

    `yahoo` : ses clotures quotidiennes ; leurs dates sont ses seances.

      1. Barres de fenetre manquantes, sur les seances jugees, a partir de sa
         premiere barre : au-dela de 5 %, l'indice est exclu (protocole).
      2. Barres de fenetre figees (plus haut = plus bas, ou volume nul) :
         au-dela de 5 %, un defaut que rien ne permet de trancher.
      3. Sauts d'une seance a l'autre : f(t) = C(t) / C(t-1) - 1 au-dela de
         25 % ou de 10 ecarts-types. Confirme si Yahoo bouge dans le meme sens
         d'au moins la moitie, entre les memes seances ou a une ou deux pres
         (meme regle qu'au protocole de tendance).
      4. Sauts dans la fenetre : C(t) / E(t) - 1 au-dela de 10 ecarts-types.
         Corrobore si les deux cours le sont, chacun par une observation
         independante : C(t) par Yahoo (f(t) et le mouvement de Yahoo different
         de moins de 1 %, ou de la moitie du saut), E(t) par la barre d'avant
         (sa cloture differe de E(t) de moins de 1 %, ou de la moitie du saut).
         Un mauvais cours a l'entree ou a la sortie se trahit dans l'un ou l'autre.
      5. Pour information, sans verdict : la correlation entre f et le
         mouvement quotidien de Yahoo, et leurs ecarts.
    Un saut ni confirme ni corrobore est un defaut non resolu : l'indice est exclu.
    """
    seances = yahoo.index
    premier = barres.index.min().normalize()
    fen = barres_de_fenetre(barres, indice, seances[(seances >= premier) & (seances <= pd.Timestamp(fin_juge))])
    debut = max(pd.Timestamp(debut_juge), premier)
    juge = np.asarray((fen.index >= debut) & (fen.index <= pd.Timestamp(fin_juge)))
    present = fen["open"].notna().to_numpy()
    figee = present & np.asarray((fen["high"] == fen["low"]) | (fen["tick_volume"] <= 0))
    n_juge = int(juge.sum())
    manquantes = list(fen.index[juge & ~present])
    figees = list(fen.index[juge & figee])

    f = fen["close"] / fen["close"].shift(1) - 1.0                 # seances consecutives du calendrier
    y = (yahoo / yahoo.shift(1) - 1.0).reindex(fen.index)
    rdd = fen["close"] / fen["open"] - 1.0
    sig_f, sig_d = float(f.std()), float(rdd.std())

    sauts = []
    for date, x in f.items():
        if not np.isfinite(x) or (abs(x) <= SAUT_MAX and abs(x) <= SIGMAS_MAX * sig_f):
            continue
        veille = fen.index[fen.index.get_loc(date) - 1]
        meilleurs = TD.mouvements_repli(yahoo, veille, date)
        conf = any(np.sign(m) == np.sign(x) and abs(m) >= 0.5 * abs(x) for m in meilleurs)
        sauts.append({"type": "seance", "date": date, "rendement": float(x), "sigmas": abs(x) / sig_f,
                      "yahoo": meilleurs, "resolu": bool(conf)})
    for date, x in rdd.items():
        if not np.isfinite(x) or abs(x) <= SIGMAS_MAX * sig_d:
            continue
        tol = max(ECART_MAX, 0.5 * abs(x))
        ecart_yahoo = abs(f[date] - y[date])
        continuite = abs(fen.at[date, "open"] / fen.at[date, "cloture_avant"] - 1.0)
        ok = bool(np.isfinite(ecart_yahoo) and ecart_yahoo <= tol
                  and np.isfinite(continuite) and continuite <= tol)
        sauts.append({"type": "fenetre", "date": date, "rendement": float(x), "sigmas": abs(x) / sig_d,
                      "ecart_yahoo": float(ecart_yahoo), "continuite": float(continuite), "resolu": ok})
    sauts.sort(key=lambda s: (s["date"], s["type"]))

    deux = (f.notna() & y.notna()).to_numpy()
    ecarts = (f - y).abs()[deux]
    part_m = len(manquantes) / n_juge if n_juge else 1.0
    part_f = len(figees) / n_juge if n_juge else 0.0
    raisons = []
    if n_juge == 0:
        raisons.append(("aucune", 0.0))
    if part_m > PART_MAX:
        raisons.append(("manquantes", part_m))
    if part_f > PART_MAX:
        raisons.append(("figees", part_f))
    raisons += [("saut", s) for s in sauts if not s["resolu"]]
    jugees = fen.index[juge]
    return {"indice": indice, "debut_juge": debut, "seances_jugees": n_juge,
            "premiere_jugee": jugees.min() if n_juge else None, "derniere_jugee": jugees.max() if n_juge else None,
            "manquantes": manquantes, "part_manquantes": part_m,
            "figees": figees, "part_figees": part_f,
            "sauts": sauts, "sigma_seance": sig_f, "sigma_fenetre": sig_d,
            "yahoo": {"comparees": int(deux.sum()),
                      "correlation": float(f[deux].corr(y[deux])) if deux.sum() > 2 else float("nan"),
                      "ecart_median": float(ecarts.median()) if len(ecarts) else float("nan"),
                      "ecarts_1pc": int((ecarts > ECART_MAX).sum())},
            "raisons": raisons, "retenu": not raisons}
