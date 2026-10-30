"""Le releve des conditions FTMO (etape 1 du protocole de tendance), sans MT5.

Le double de `tests/faux_mt5.py` est complete des champs que lit le releve :
swaps, unite, jour triple, valeur du tick. Ce qui compte ici : chaque
instrument est retrouve sous son nom courtier, un marche ferme n'est jamais
compte, et le spread retenu suit la regle du protocole.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import releve_ftmo as R  # noqa: E402
from faux_mt5 import FauxMT5, _Obj  # noqa: E402
from quantbot import mt5broker  # noqa: E402

PRIX = {"US500.cash": 6800.0, "GER40.cash": 24000.0, "UK100.cash": 9700.0,
        "JP225.cash": 49000.0, "AUS200.cash": 8900.0, "HK50.cash": 26000.0,
        "EURUSD": 1.16, "GBPUSD": 1.33, "USDJPY": 152.0, "USDCHF": 0.80, "USDCAD": 1.40,
        "AUDUSD": 0.66, "NZDUSD": 0.58, "XAUUSD": 4000.0, "XAGUSD": 48.0}
DECALAGE_H = 2                  # serveur FTMO en GMT+2 l'hiver


class FauxTerminal(FauxMT5):
    """Un terminal dont on regle l'age des cotations, instrument par instrument."""

    def __init__(self, maintenant, ages=None, **kw):
        super().__init__(symboles=PRIX, **kw)
        self.maintenant = maintenant
        self.ages = ages or {}

    def symbol_info(self, nom):
        i = super().symbol_info(nom)
        if i is None:
            return None
        fx = len(nom) == 6
        i.__dict__.update(swap_mode=1 if fx else 5, swap_long=-7.5 if fx else -5.2,
                          swap_short=2.1 if fx else 1.1, swap_rollover3days=3 if fx else 5,
                          currency_base=nom[:3], currency_profit=nom[3:6] if fx else "USD",
                          currency_margin=nom[:3], trade_tick_value=1.0, trade_tick_size=0.01,
                          spread=12, trade_contract_size=100000.0 if fx else 1.0)
        return i

    def symbol_info_tick(self, nom):
        if nom not in self.prix:
            return None
        p = self.prix[nom]
        heure = self.maintenant.timestamp() + 3600 * DECALAGE_H - self.ages.get(nom, 5)
        return _Obj(bid=p * 0.9999, ask=p * 1.0001, last=p, time=heure)


def _lire(maintenant, ages=None):
    faux = FauxTerminal(maintenant, ages)
    api = mt5broker.MT5(mt5=faux)
    api.initialiser()
    carte = api.construire_carte(R.INSTRUMENTS)
    return R.releve(faux, carte, maintenant), carte


def test_les_15_instruments_sous_leur_nom_courtier():
    lignes, carte = _lire(datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc))
    assert len(lignes) == 15
    assert carte["US500"] == "US500.cash" and carte["EURUSD"] == "EURUSD"
    l = {x["instrument"]: x for x in lignes}
    assert l["EURUSD"]["swap_unite"] == "points" and l["EURUSD"]["swap_triple"] == "mercredi"
    assert l["US500"]["swap_unite"].startswith("% par an") and l["US500"]["swap_triple"] == "vendredi"
    assert l["US500"]["spread_relatif"] == pytest.approx(0.0002, rel=1e-6)
    assert l["US500"]["levier"] > 0
    assert all(x["decalage_serveur_h"] == DECALAGE_H for x in lignes)


def test_un_marche_sans_cotation_recente_est_ferme():
    ages = {"JP225.cash": 3 * 3600, "HK50.cash": 600, "AUS200.cash": 3 * 3600}
    lignes, _ = _lire(datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc), ages)
    ouverts = {x["instrument"] for x in lignes if x["ouvert"]}
    assert "JP225" not in ouverts and "HK50" not in ouverts and "AUS200" not in ouverts
    assert {"US500", "GER40", "EURUSD", "XAUUSD"} <= ouverts


def test_le_week_end_tout_est_ferme():
    """Aucune cotation fraiche, pas meme le change : rien n'est ouvert."""
    tous_vieux = {s: 40 * 3600 + 1234 for s in PRIX}
    lignes, _ = _lire(datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc), tous_vieux)
    assert not any(x["ouvert"] for x in lignes)


def test_spread_retenu_le_plus_eleve_de_trois_jours(tmp_path):
    journal = tmp_path / "releves.csv"
    debut = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    for jour in range(4):
        for heure in range(3):
            t = debut + timedelta(days=jour, hours=heure)
            faux = FauxTerminal(t)
            # le spread de l'or s'elargit le 2e jour, et une pointe isolee le 4e
            ecart = {1: 0.0008}.get(jour, 0.0001)
            if jour == 3 and heure == 0:
                ecart = 0.01
            faux.prix = dict(PRIX)
            api = mt5broker.MT5(mt5=faux)
            api.initialiser()
            carte = api.construire_carte(R.INSTRUMENTS)
            lignes = R.releve(faux, carte, t)
            for l in lignes:
                if l["instrument"] == "XAUUSD":
                    l["bid"], l["ask"] = 4000.0 * (1 - ecart), 4000.0 * (1 + ecart)
                    l["spread_prix"] = l["ask"] - l["bid"]
                    l["spread_relatif"] = l["spread_prix"] / 4000.0
            R.ecrire(lignes, journal)
    s = R.synthese(journal)
    assert s["XAUUSD"]["complet"] and s["XAUUSD"]["jours"] == ["2026-11-02", "2026-11-03", "2026-11-04"]
    # le 4e jour (et sa pointe) ne compte pas ; le 2e jour fixe le spread retenu
    assert s["XAUUSD"]["spread_retenu"] == pytest.approx(4000.0 * 2 * 0.0008)
    assert all(v["complet"] for v in s.values())


def test_la_tache_lance_ce_script_toutes_les_heures():
    cmd = R.commande_tache(python="C:/Python310/python.exe")
    assert cmd[:2] == ["schtasks", "/Create"] and "HOURLY" in cmd and R.NOM_TACHE in cmd
    assert cmd[-1].startswith('"C:/Python310/python.exe"') and cmd[-1].endswith('releve_ftmo.py"')
