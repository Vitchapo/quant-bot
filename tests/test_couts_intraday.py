"""L'etape 1 du momentum intrajournalier, sur un terminal MT5 simule.

Aucune vraie donnee ici. On verifie que le releve lit les ticks des bonnes
minutes (heure du serveur, changements d'heure compris), seulement des seances
du calendrier, les plus recentes d'abord, et qu'il retient le plus eleve des
ticks et du tableau de tendance. On verifie aussi qu'il remplit le protocole a
la bonne place, une seule fois, sans toucher a rien d'autre.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import couts_intraday as S  # noqa: E402
import faux_mt5  # noqa: E402
import intraday_synthetique as SY  # noqa: E402
import tester_tendance as TT  # noqa: E402
from quantbot import intraday_donnees as I  # noqa: E402
from quantbot import tendance_donnees as TD  # noqa: E402

T = pd.Timestamp
VRAI_PROTOCOLE = Path(__file__).resolve().parent.parent / "docs" / "protocole_momentum_intrajournalier.md"
TYPE_TICK = [("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("volume", "<u8"),
             ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8")]
TRANSACTIONS = {"US500": 0.00008102, "GER40": 0.00004773, "UK100": 0.00007015, "JP225": 0.00018706,
                "AUS200": 0.00011326, "HK50": 0.00027344}


class FauxTicks(faux_mt5.FauxMT5):
    """Un terminal qui rend des ticks toutes les 2 secondes, du 1er aout au
    13 novembre 2026, avec un spread choisi par le test (sym, heure du serveur)
    -> spread relatif. Il deborde de 5 secondes de chaque cote de la demande,
    comme pour verifier que le script ne garde que sa minute."""
    COPY_TICKS_INFO = 1

    def __init__(self, spread, sans=(), refus_avant=0, depuis="2026-08-01", synchro=0):
        super().__init__()
        self.spread, self.sans = spread, set(T(j) for j in sans)
        self.refus_avant, self.depuis = refus_avant, T(depuis)
        self.synchro, self.par_mois = synchro, {}           # synchro : demandes vides par mois et symbole
        self.demandes = []

    def copy_ticks_range(self, sym, d1, d2, drapeau):
        a, b = T(d1).tz_localize(None), T(d2).tz_localize(None)
        self.demandes.append((sym, a, b, drapeau))
        if len(self.demandes) <= self.refus_avant:
            self._erreur = (-1, "Terminal: Call failed")
            return None
        cle = (sym, a.year, a.month)
        self.par_mois[cle] = self.par_mois.get(cle, 0) + 1
        if self.par_mois[cle] <= self.synchro:               # le mois se telecharge encore
            return np.zeros(0, dtype=TYPE_TICK)
        if a.normalize() in self.sans or a < self.depuis or a > T("2026-11-13"):
            return np.zeros(0, dtype=TYPE_TICK)
        t = pd.date_range(a - pd.Timedelta(seconds=5), b + pd.Timedelta(seconds=5), freq="2s")
        x = np.zeros(len(t), dtype=TYPE_TICK)
        ms = np.asarray((t - SY.EPOQUE) // pd.Timedelta(milliseconds=1), dtype="int64")
        x["time_msc"], x["time"] = ms, ms // 1000
        x["bid"] = 5000.0
        sp = np.array([self.spread(sym, u) for u in t])
        x["ask"] = 5000.0 * (1 + sp / 2) / (1 - sp / 2)            # (ask - bid) / milieu = sp
        return x


def _seances():
    return {n: SY.seances("2026-01-05", "2026-10-30") for n in I.INDICES}


# -- une minute ------------------------------------------------------------------------
def test_le_spread_d_une_minute_est_la_mediane_de_ses_ticks_et_rien_qu_elle():
    def spread(sym, t):
        if t < T("2026-10-15 22:30") or t >= T("2026-10-15 22:31"):
            return 0.01                                             # hors de la minute : enorme
        return 0.0001 if t.second < 20 else 0.0003                  # 10 ticks a 0,0001 %, 20 a 0,0003 %
    mt5 = FauxTicks(spread)
    x = S.spread_minute(mt5, "US500.cash", T("2026-10-15 22:30"), T("2026-10-15 22:31"))
    assert x == pytest.approx(0.0003)                               # la mediane, pas la moyenne
    sym, a, b, drapeau = mt5.demandes[0]
    assert (a, b, drapeau) == (T("2026-10-15 22:30"), T("2026-10-15 22:31"), 1)


def test_un_tick_sans_cours_valable_est_ecarte():
    mt5 = FauxTicks(lambda s, t: 0.0002)
    vrai = mt5.copy_ticks_range

    def abime(*a):
        x = vrai(*a)
        x["bid"][::3] = 0.0                                         # pas de cours acheteur
        x["ask"][1::3] = 4000.0                                     # vendeur sous l'acheteur
        return x
    mt5.copy_ticks_range = abime
    x = S.spread_minute(mt5, "GER40.cash", T("2026-10-15 18:00"), T("2026-10-15 18:01"))
    assert x == pytest.approx(0.0002)


def test_un_refus_du_terminal_est_redemande_puis_abandonne():
    mt5 = FauxTicks(lambda s, t: 0.0002, refus_avant=2)
    assert S.spread_minute(mt5, "UK100.cash", T("2026-10-15 18:00"), T("2026-10-15 18:01"),
                           attendre=lambda _: None) == pytest.approx(0.0002)
    mt5 = FauxTicks(lambda s, t: 0.0002, refus_avant=99)
    assert S.spread_minute(mt5, "UK100.cash", T("2026-10-15 18:00"), T("2026-10-15 18:01"),
                           attendre=lambda _: None) is None
    assert len(mt5.demandes) == S.ESSAIS


# -- les minutes et les seances ---------------------------------------------------------
def test_la_premiere_demande_d_un_mois_attend_que_le_terminal_ait_ses_ticks():
    """Le terminal ne rend rien tant qu'il telecharge le mois : aucune seance n'est perdue."""
    mt5 = FauxTicks(lambda s, t: 0.0002, synchro=4)
    pauses = []
    rel = S.relever_indice(mt5, "US500", "US500.cash", _seances()["US500"], attendre=pauses.append,
                           dire=lambda _: None)
    assert len(rel["entree"]) == len(rel["sortie"]) == 60 and rel["regardees"] == 60
    assert rel["entree"][0][0] == T("2026-10-30")
    assert sum(pauses) <= 4 * sum(S.PATIENCE_S)                # quatre mois, au plus 15,5 s chacun


def test_les_minutes_mesurees_sont_la_premiere_et_la_derniere_de_la_fenetre():
    assert S.minutes_mesurees("US500", "2026-10-15") == (
        (T("2026-10-15 22:30"), T("2026-10-15 22:31")), (T("2026-10-15 22:59"), T("2026-10-15 23:00")))
    # GER40 pendant le decalage d'automne : 17:00 a Francfort = 19:00 au serveur
    assert S.minutes_mesurees("GER40", "2026-10-28")[0] == (T("2026-10-28 19:00"), T("2026-10-28 19:01"))
    # JP225 : 15:30 a Tokyo = 2:30 a New York (heure d'ete) = 9:30 au serveur
    assert S.minutes_mesurees("JP225", "2026-10-15")[1] == (T("2026-10-15 09:29"), T("2026-10-15 09:30"))


def test_le_releve_prend_les_60_seances_les_plus_recentes_du_calendrier():
    mt5 = FauxTicks(lambda s, t: 0.0002)
    rel = S.relever_indice(mt5, "US500", "US500.cash", _seances()["US500"],
                          attendre=lambda _: None, dire=lambda _: None)
    jours = [j for j, _ in rel["entree"]]
    assert len(jours) == 60 and len(rel["sortie"]) == 60 and rel["regardees"] == 60
    assert max(jours) == T("2026-10-30") and jours == sorted(jours, reverse=True)
    assert all(a <= T("2026-10-30 23:00") for _, a, _, _ in mt5.demandes)      # rien apres le calendrier


def test_une_seance_sans_ticks_est_sautee_et_on_remonte_d_autant():
    sans = ["2026-10-30", "2026-10-29", "2026-10-15"]
    mt5 = FauxTicks(lambda s, t: 0.0002, sans=sans)
    rel = S.relever_indice(mt5, "HK50", "HK50.cash", _seances()["HK50"],
                          attendre=lambda _: None, dire=lambda _: None)
    jours = [j for j, _ in rel["entree"]]
    assert len(jours) == 60 and rel["regardees"] == 63 and not set(map(T, sans)) & set(jours)


def test_sans_historique_de_ticks_le_releve_s_arrete_vite_et_garde_le_tableau():
    mt5 = FauxTicks(lambda s, t: 0.0002, depuis="2027-01-01")
    vus = []
    rel = S.relever_indice(mt5, "JP225", "JP225.cash", _seances()["JP225"],
                          attendre=lambda _: None, dire=vus.append)
    assert rel["regardees"] == S.SANS_TICKS and rel["entree"] == [] and rel["sortie"] == []
    assert any("historique indisponible" in v for v in vus)
    r = S.resumer(rel, TRANSACTIONS["JP225"])
    assert r["entree"] == r["sortie"] == TRANSACTIONS["JP225"] and r["entree_ticks"] is None


def test_un_historique_court_donne_moins_de_60_seances_et_s_arrete_a_120():
    mt5 = FauxTicks(lambda s, t: 0.0002, depuis="2026-09-15")
    rel = S.relever_indice(mt5, "UK100", "UK100.cash", _seances()["UK100"],
                          attendre=lambda _: None, dire=lambda _: None)
    assert 0 < len(rel["entree"]) < 60 and rel["regardees"] == S.SEANCES_MAX


def test_le_spread_retenu_est_le_plus_eleve_des_ticks_et_du_tableau():
    def spread(sym, t):
        sortie = t.minute in (59, 29)                               # derniere minute de la fenetre
        return 0.00004 if sortie else 0.00030
    rel = S.relever_indice(FauxTicks(spread), "GER40", "GER40.cash", _seances()["GER40"],
                          attendre=lambda _: None, dire=lambda _: None)
    r = S.resumer(rel, TRANSACTIONS["GER40"])
    assert r["entree_ticks"] == pytest.approx(0.00030) and r["entree"] == pytest.approx(0.00030)
    assert r["sortie_ticks"] == pytest.approx(0.00004) and r["sortie"] == TRANSACTIONS["GER40"]
    assert (r["premiere"], r["derniere"]) == (min(j for j, _ in rel["entree"]), T("2026-10-30"))


def test_les_heures_suivent_les_changements_d_heure_du_serveur_et_de_la_bourse():
    """Le spread n'est petit qu'a 17:00-17:01 a Francfort, quelle que soit l'heure du serveur."""
    def spread(sym, t):
        u = (t - I.DECALAGE_SERVEUR).tz_localize(I.NEW_YORK).tz_convert("Europe/Berlin")
        return 0.0001 if (u.hour, u.minute) == (17, 0) else 0.01
    rel = S.relever_indice(FauxTicks(spread), "GER40", "GER40.cash", _seances()["GER40"],
                          attendre=lambda _: None, dire=lambda _: None)
    assert [x for _, x in rel["entree"]] == pytest.approx([0.0001] * 60)
    assert any(I.semaines_de_decalage([j])[0] for j, _ in rel["entree"])      # semaine du 26/10 comprise


# -- les sorties ------------------------------------------------------------------------
def _res():
    rel = {"entree": [(T("2026-10-30"), 0.0003), (T("2026-08-05"), 0.0001)],
           "sortie": [(T("2026-10-30"), 0.00002)], "regardees": 70}
    res = {n: S.resumer(rel, TRANSACTIONS[n]) for n in I.INDICES}
    res["HK50"] = S.resumer({"entree": [], "sortie": [], "regardees": 10}, TRANSACTIONS["HK50"])
    return res


def test_le_fichier_des_couts_donne_les_fractions_que_lira_le_test(tmp_path):
    f = tmp_path / "intraday_couts.csv"
    S.ecrire_csv(_res(), f)
    with f.open(encoding="utf-8", newline="") as fh:
        lignes = {l["instrument"]: l for l in csv.DictReader(fh)}
    assert list(lignes) == list(I.INDICES) and list(lignes["US500"]) == S.CHAMPS
    assert float(lignes["US500"]["entree"]) == pytest.approx(0.0002)        # mediane de 0,0003 et 0,0001
    assert float(lignes["US500"]["sortie"]) == pytest.approx(TRANSACTIONS["US500"])
    assert lignes["HK50"]["entree_ticks"] == "" and float(lignes["HK50"]["entree"]) == pytest.approx(0.00027344)
    assert b"\r" not in f.read_bytes()


def test_le_protocole_recoit_le_tableau_avant_la_periode_et_la_date_de_l_etape_1():
    texte = VRAI_PROTOCOLE.read_text(encoding="utf-8")
    bloc = S.bloc_protocole(_res(), "18 novembre 2026")
    neuf = S.consigner(texte, bloc, "18 novembre 2026")
    avant, apres = neuf.split(S.ENTETE)
    assert avant.rstrip().endswith("`docs/intraday_couts.csv`.") and "Aucune commission" in avant
    assert apres.index("### La période") < apres.index("## Les cinq critères")
    assert ("| US500 | 0,0200 % | 0,0020 % | 2 à l'entrée, 1 à la sortie, du 05/08/2026 au 30/10/2026 "
            "| 0,0081 % | 0,0200 % | 0,0081 % |") in apres
    assert "| HK50 | pas de ticks | pas de ticks | aucune : historique de ticks indisponible | 0,0273 % |" in apres
    assert "historique de ticks), consigner le tableau, committer | 18 novembre 2026 |" in neuf
    # rien d'autre ne bouge
    retire = neuf.replace(bloc + "\n\n", "").replace("committer | 18 novembre 2026 |", "committer | |", 1)
    assert retire == texte
    with pytest.raises(ValueError, match="deja dans le protocole"):
        S.consigner(neuf, bloc, "19 novembre 2026")


def test_le_protocole_garde_ses_fins_de_ligne_windows():
    texte = VRAI_PROTOCOLE.read_text(encoding="utf-8").replace("\n", "\r\n")
    neuf = S.consigner(texte, S.bloc_protocole(_res(), "18 novembre 2026"), "18 novembre 2026")
    assert neuf.count("\r\n") == neuf.count("\n")


# -- avant MT5 ------------------------------------------------------------------------
def _depot(tmp_path, abime=None):
    brut = tmp_path / "brut"
    brut.mkdir(parents=True)
    lignes = [TT.ENTETE_DONNEES, "|---|---|---|"]
    jours = SY.seances("2025-09-01", "2026-10-23")
    for n in I.INDICES:
        f = brut / I.fichier_calendrier(n)
        SY.ecrire_yahoo(f, pd.Series(np.linspace(100, 120, len(jours)), index=jours))
        sha = "0" * 64 if n == abime else TD.sha256(f)
        lignes.append("| `%s` | `%s` | %s, source principale : retenu, aucun défaut |" % (f.name, sha, n))
    (tmp_path / "tendance.md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    couts = tmp_path / "couts.csv"
    couts.write_text("instrument,transaction,marge_achat,marge_vente\n"
                     + "".join("%s,%.8f,0.05,0.04\n" % (n, v) for n, v in TRANSACTIONS.items()), encoding="utf-8")
    protocole = tmp_path / "protocole.md"
    protocole.write_bytes(VRAI_PROTOCOLE.read_bytes())
    return dict(protocole=protocole, tendance=tmp_path / "tendance.md", brut=brut, couts=couts)


def test_avant_mt5_le_calendrier_et_les_couts_sont_verifies(tmp_path):
    p = _depot(tmp_path)
    seances, trans, problemes = S.preparer(ecrire=True, **p)
    assert problemes == [] and trans == pytest.approx(TRANSACTIONS)
    assert seances["AUS200"].max() == T("2026-10-23")
    assert list(seances["AUS200"]) == list(SY.seances("2025-09-01", "2026-10-23"))


def test_un_calendrier_change_ou_une_etape_1_deja_faite_arretent_tout(tmp_path):
    p = _depot(tmp_path, abime="AUS200")
    _, _, problemes = S.preparer(**p)
    assert problemes == ["yahoo__AXJO.json : l'empreinte ne correspond plus a celle du protocole de tendance"]
    p = _depot(tmp_path / "b")
    texte = p["protocole"].read_text(encoding="utf-8")
    p["protocole"].write_text(S.consigner(texte, S.bloc_protocole(_res(), "1 janvier 2027"), "1 janvier 2027"),
                              encoding="utf-8")
    _, _, problemes = S.preparer(ecrire=True, **p)
    assert problemes == ["le tableau des couts est deja dans le protocole : l'etape 1 est faite"]


def test_le_releve_complet_ne_lit_que_des_ticks(tmp_path):
    """Aucun cours de barre ni aucune cloture : le releve n'appelle que copy_ticks_range."""
    mt5 = FauxTicks(lambda s, t: 0.0002)
    mt5.copy_rates_range = mt5.copy_rates_from_pos = None
    res = S.relever(mt5, dict(I.SYMBOLES), _seances(), TRANSACTIONS, attendre=lambda _: None, dire=lambda _: None)
    assert list(res) == list(I.INDICES)
    assert all(r["seances_entree"] == 60 and r["entree"] == pytest.approx(max(0.0002, TRANSACTIONS[n]))
               for n, r in res.items())
    assert {d[0] for d in mt5.demandes} == set(I.SYMBOLES.values())
