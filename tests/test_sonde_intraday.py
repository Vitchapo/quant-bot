"""La sonde de l'historique intrajournalier, sur un terminal MT5 simule.

Elle ne doit rendre que des dates et des nombres de barres, jamais un cours,
et dire quand c'est la limite du terminal qui tronque l'historique.
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


class FauxHistorique(faux_mt5.FauxMT5):
    """Un terminal dont chaque symbole a un historique commencant a une date donnee."""
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M30 = 1, 5, 30

    def __init__(self, debuts, maxbars=100_000, fins_m1=None, prets_apres=0):
        super().__init__()
        self.debuts = debuts                    # symbole -> premiere barre M30
        self.m1_depuis = fins_m1 or {}          # symbole -> premiere barre M5/M1, si plus tard
        self.maxbars = maxbars
        self.fin = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
        self.prets_apres = prets_apres          # appels avant que le telechargement soit fini
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

    def copy_rates_from_pos(self, sym, tf, pos, count):
        self.appels += 1
        b = self._barres(sym, tf, datetime(1990, 1, 1, tzinfo=UTC), self.fin)
        if b is None:
            return None
        if self.appels <= self.prets_apres:     # telechargement en cours : un bout seulement
            b = b[-1000:]
        limite = min(count, self.maxbars) if self.maxbars else count    # 0 : limite inconnue
        return b[max(0, len(b) - limite):]

    def copy_rates_range(self, sym, tf, d1, d2):
        return self._barres(sym, tf, d1, d2)


def _sans_pause(_):
    return None


def test_un_historique_profond_tronque_par_la_limite_du_terminal_est_signale():
    mt5 = FauxHistorique({s: datetime(2012, 1, 2, tzinfo=UTC) for s in CARTE.values()}, maxbars=100_000)
    lignes = S.sonder(mt5, CARTE, attendre=_sans_pause)
    assert [l["instrument"] for l in lignes] == S.INDICES
    for l in lignes:
        assert l["barres_m30"] == 100_000 and l["tronque"] == "oui"
        assert l["premiere_m30"] > "2021-01-01" and l["derniere_m30"] == "2026-11-06"


def test_sans_limite_la_sonde_trouve_le_debut_reel():
    debuts = {s: datetime(2012, 1, 2, tzinfo=UTC) for s in CARTE.values()}
    mt5 = FauxHistorique(debuts, maxbars=2_147_483_647,
                         fins_m1={"HK50.cash": datetime(2019, 6, 3, tzinfo=UTC)})
    lignes = {l["instrument"]: l for l in S.sonder(mt5, CARTE, attendre=_sans_pause)}
    us = lignes["US500"]
    assert us["premiere_m30"] == "2012-01-02" and us["tronque"] == "non"
    assert us["m5_au_debut"] == "oui" and us["m1_au_debut"] == "oui"
    assert lignes["HK50"]["m5_au_debut"] == "non" and lignes["HK50"]["m1_au_debut"] == "non"


def test_la_sonde_attend_la_fin_du_telechargement():
    mt5 = FauxHistorique({"US500.cash": datetime(2016, 3, 1, tzinfo=UTC)}, maxbars=0, prets_apres=2)
    l = S.sonder(mt5, {"US500": "US500.cash"}, attendre=_sans_pause)[0]
    assert l["premiere_m30"] == "2016-03-01" and l["barres_m30"] > 1000


def test_un_indice_absent_est_note_sans_planter():
    mt5 = FauxHistorique({"US500.cash": datetime(2015, 1, 5, tzinfo=UTC)})
    lignes = {l["instrument"]: l for l in S.sonder(mt5, {"US500": "US500.cash", "JP225": "JP225.cash"},
                                                   attendre=_sans_pause)}
    assert lignes["GER40"]["note"] == "absent du catalogue"
    assert lignes["JP225"]["note"] == "aucune barre rendue"
    assert lignes["US500"]["note"] == ""


def test_le_fichier_ne_contient_que_des_dates_et_des_comptes(tmp_path):
    mt5 = FauxHistorique({s: datetime(2014, 1, 6, tzinfo=UTC) for s in CARTE.values()})
    chemin = tmp_path / "sonde.csv"
    S.ecrire(S.sonder(mt5, CARTE, attendre=_sans_pause), chemin)
    with chemin.open(encoding="utf-8", newline="") as fh:
        lignes = list(csv.DictReader(fh))
    assert len(lignes) == 6 and list(lignes[0]) == S.COLONNES
    assert not any(c in S.COLONNES for c in ("open", "high", "low", "close", "prix"))
    assert all("5000" not in v for l in lignes for v in l.values())     # aucun cours ne fuit
