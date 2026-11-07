"""La sonde de l'historique intrajournalier, sur un terminal MT5 simule.

Elle ne doit rendre que des dates et des nombres de barres, jamais un cours,
dire quand c'est la limite du terminal qui tronque l'historique, et ne pas
rester muette ni bloquee quand le terminal refuse ou tarde.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import faux_mt5  # noqa: E402
import sonde_intraday as S  # noqa: E402

UTC = timezone.utc
CARTE = {"US500": "US500.cash", "GER40": "GER40.cash", "UK100": "UK100.cash",
         "JP225": "JP225.cash", "AUS200": "AUS200.cash", "HK50": "HK50.cash"}
TYPE_BARRE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
              ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]
REFUS = (-2, "Terminal: Invalid params")


class FauxHistorique(faux_mt5.FauxMT5):
    """Un terminal dont chaque symbole a un historique commencant a une date donnee.

    maxbars        limite "Max bars in chart" : jamais plus de barres rendues
    prets_apres    appels pendant lesquels le telechargement n'est pas fini
    sans_fin       chaque appel rend un peu plus : le telechargement ne finit jamais
    refuse_au_dela une demande de plus de barres que cela : "Invalid params"
    plage_refusee  copy_rates_range ne rend rien en M30
    """
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M30 = 1, 5, 30

    def __init__(self, debuts, maxbars=100_000, m1_depuis=None, prets_apres=0, sans_fin=False,
                 refuse_au_dela=None, plage_refusee=False):
        super().__init__()
        self.debuts = debuts
        self.m1_depuis = m1_depuis or {}
        self.maxbars = maxbars
        self.fin = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
        self.prets_apres, self.sans_fin = prets_apres, sans_fin
        self.refuse_au_dela, self.plage_refusee = refuse_au_dela, plage_refusee
        self.appels = 0

    def terminal_info(self):
        return faux_mt5._Obj(trade_allowed=True, name="FauxTerminal", build=4000, maxbars=self.maxbars)

    def _barres(self, sym, tf, d1, d2):
        if sym not in self.debuts:
            return None
        debut = self.debuts[sym] if tf == 30 else self.m1_depuis.get(sym, self.debuts[sym])
        debut = max(debut, d1)
        n = max(0, int((min(d2, self.fin) - debut) / timedelta(minutes=tf)))
        b = np.zeros(n, dtype=TYPE_BARRE)
        b["time"] = int(debut.timestamp()) + np.arange(n, dtype=np.int64) * tf * 60
        b["close"] = 5000.0
        return b

    def _servir(self, sym, tf, d1, d2, compte=None):
        self.appels += 1
        b = self._barres(sym, tf, d1, d2)
        if b is None:
            self._erreur = (-1, "Terminal: Call failed")
            return None
        if self.appels <= self.prets_apres:
            b = b[-1000:]
        if self.sans_fin:
            b = b[-1000 * self.appels:]
        limite = self.maxbars or len(b)
        if compte is not None:
            limite = min(limite, compte)
        return b[max(0, len(b) - limite):]

    def copy_rates_from_pos(self, sym, tf, pos, count):
        if self.refuse_au_dela is not None and count > self.refuse_au_dela:
            self.appels += 1
            self._erreur = REFUS
            return None
        return self._servir(sym, tf, datetime(1990, 1, 1, tzinfo=UTC), self.fin, count)

    def copy_rates_range(self, sym, tf, d1, d2):
        if self.plage_refusee and tf == 30:
            self.appels += 1
            self._erreur = REFUS
            return None
        return self._servir(sym, tf, d1, d2)


def _sans_pause(_):
    return None


def _sonder(mt5, carte=CARTE, **kw):
    kw.setdefault("attendre", _sans_pause)
    kw.setdefault("dire", lambda _: None)
    return S.sonder(mt5, carte, maintenant=datetime(2026, 11, 7, 10, 0, tzinfo=UTC), **kw)


def test_un_historique_profond_tronque_par_la_limite_du_terminal_est_signale():
    mt5 = FauxHistorique({s: datetime(2012, 1, 2, tzinfo=UTC) for s in CARTE.values()}, maxbars=100_000)
    lignes = _sonder(mt5)
    assert [l["instrument"] for l in lignes] == S.INDICES
    for l in lignes:
        assert l["barres_m30"] == 100_000 and l["tronque"] == "oui"
        assert l["premiere_m30"] > "2021-01-01" and l["derniere_m30"] == "2026-11-06"


def test_sans_limite_la_sonde_trouve_le_debut_reel():
    mt5 = FauxHistorique({s: datetime(2012, 1, 2, tzinfo=UTC) for s in CARTE.values()},
                         maxbars=2_147_483_647, m1_depuis={"HK50.cash": datetime(2019, 6, 3, tzinfo=UTC)})
    lignes = {l["instrument"]: l for l in _sonder(mt5)}
    us = lignes["US500"]
    assert us["premiere_m30"] == "2012-01-02" and us["tronque"] == "non" and us["note"] == ""
    assert us["m5_au_debut"] == "oui" and us["m1_au_debut"] == "oui"
    assert lignes["HK50"]["m5_au_debut"] == "non" and lignes["HK50"]["m1_au_debut"] == "non"


def test_la_plage_de_dates_marche_meme_quand_un_nombre_de_barres_est_refuse():
    """Ce qu'a fait le terminal le 07/11 : 100 000 barres demandees, "Invalid params"."""
    mt5 = FauxHistorique({"US500.cash": datetime(2014, 2, 3, tzinfo=UTC)}, maxbars=0, refuse_au_dela=0)
    l = _sonder(mt5, {"US500": "US500.cash"})[0]
    assert l["premiere_m30"] == "2014-02-03" and l["tronque"] == "non" and l["note"] == ""


def test_si_tout_est_refuse_la_sonde_demande_moins_de_barres():
    mt5 = FauxHistorique({"US500.cash": datetime(2012, 1, 2, tzinfo=UTC)}, maxbars=100_000,
                         refuse_au_dela=60_000, plage_refusee=True)
    vus = []
    l = _sonder(mt5, {"US500": "US500.cash"}, dire=vus.append)[0]
    assert l["barres_m30"] == 50_000 and l["tronque"] == "oui"
    assert "n'a accepte que 50000 barres" in l["note"]
    assert any("Invalid params" in v for v in vus)


def test_la_sonde_attend_la_fin_du_telechargement():
    mt5 = FauxHistorique({"US500.cash": datetime(2016, 3, 1, tzinfo=UTC)}, maxbars=0, prets_apres=2)
    l = _sonder(mt5, {"US500": "US500.cash"})[0]
    assert l["premiere_m30"] == "2016-03-01" and l["barres_m30"] > 1000


def test_un_telechargement_trop_long_est_note_et_la_sonde_passe_au_suivant():
    """Une horloge simulee : chaque lecture prend 100 s et rend un peu plus de barres."""
    temps = [0.0]

    def horloge():
        temps[0] += 100.0
        return temps[0]
    mt5 = FauxHistorique({"US500.cash": datetime(2012, 1, 2, tzinfo=UTC),
                          "GER40.cash": datetime(2012, 1, 2, tzinfo=UTC)}, maxbars=0, sans_fin=True)
    vus = []
    lignes = _sonder(mt5, {"US500": "US500.cash", "GER40": "GER40.cash"}, horloge=horloge, dire=vus.append)
    us = lignes[0]
    assert us["note"].startswith("telechargement pas fini") and us["premiere_m30"] != ""
    assert lignes[1]["instrument"] == "GER40" and lignes[1]["barres_m30"] > 0     # le suivant est lu
    assert sum(1 for v in vus if v.strip().startswith("lecture")) < 2 * S.ESSAIS


def test_la_sonde_dit_ou_elle_en_est():
    mt5 = FauxHistorique({"US500.cash": datetime(2016, 3, 1, tzinfo=UTC)}, prets_apres=1)
    vus = []
    _sonder(mt5, {"US500": "US500.cash"}, dire=vus.append)
    assert vus[0].startswith("Limite du terminal")
    assert "US500 (US500.cash) :" in vus
    lectures = [v for v in vus if v.strip().startswith("lecture")]
    assert len(lectures) >= S.STABLE and "barres M30 depuis 2000" in lectures[0]


def test_un_indice_absent_ou_sans_historique_est_note_sans_planter():
    mt5 = FauxHistorique({"US500.cash": datetime(2015, 1, 5, tzinfo=UTC)})
    lignes = {l["instrument"]: l for l in _sonder(mt5, {"US500": "US500.cash", "JP225": "JP225.cash"})}
    assert lignes["GER40"]["note"] == "absent du catalogue"
    assert lignes["JP225"]["note"].startswith("aucune barre rendue, meme pour 1000 demandees")
    assert lignes["US500"]["note"] == ""


def test_le_fichier_ne_contient_que_des_dates_et_des_comptes(tmp_path):
    mt5 = FauxHistorique({s: datetime(2014, 1, 6, tzinfo=UTC) for s in CARTE.values()})
    chemin = tmp_path / "sonde.csv"
    S.ecrire(_sonder(mt5), chemin)
    with chemin.open(encoding="utf-8", newline="") as fh:
        lignes = list(csv.DictReader(fh))
    assert len(lignes) == 6 and list(lignes[0]) == S.COLONNES
    assert not any(c in S.COLONNES for c in ("open", "high", "low", "close", "prix"))
    assert all("5000" not in v for l in lignes for v in l.values())     # aucun cours ne fuit
