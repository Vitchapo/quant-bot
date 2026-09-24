"""L'historique du compte, tel que le tableau de bord le dessine.

Ordre d'importance :
1. la perte du jour dessinee est EXACTEMENT celle que mesure le garde-fou ;
2. l'indice n'est jamais invente aux dates ou il n'a pas de cloture ;
3. un compte neuf ou un courtier muet donnent une phrase, pas une exception.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from quantbot import defi
from quantbot.broker import Alpaca
from quantbot.operations import Operations, serie_historique


def _ts(jour, heure=4):
    """Horodatage Unix d'une seance, pose comme le fait le courtier."""
    return int(datetime(*map(int, jour.split("-")), heure, tzinfo=timezone.utc).timestamp())


def _ohlcv(close, index):
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=index)


class FauxCourtier:
    """Juste ce que `historique()` touche."""

    def __init__(self, jours, valeurs):
        self._brut = {"timestamp": [_ts(j) for j in jours], "equity": list(valeurs)}
        self.periodes = []

    def historique(self, periode="3M"):
        self.periodes.append(periode)
        return self._brut


class CourtierSansHistorique:
    def compte(self):
        return {}


@pytest.fixture
def jours():
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-09-14", periods=6)]


@pytest.fixture
def panneau(jours):
    idx = pd.to_datetime(jours[:-1])            # la derniere seance n'est pas encore en cache
    return {"^BENCH": _ohlcv(np.array([500.0, 505.0, 502.0, 510.0, 508.0]), idx)}


@pytest.fixture
def cfg(base_config):
    return base_config.with_overrides({"universe.benchmark": "^BENCH",
                                       "defi.active": True})


@pytest.fixture(autouse=True)
def etat_isole(tmp_path, monkeypatch):
    """Jamais le vrai `data/defi_etat.json` du depot."""
    chemin = tmp_path / "defi.json"
    monkeypatch.setattr(defi, "ETAT_DEFI", chemin)
    return chemin


def _ops(cfg, panneau, api):
    o = Operations(cfg, lambda: panneau)
    o._api = api
    return o


# ---------------------------------------------------------------------------
class TestLectureBrute:
    def test_les_zeros_d_avant_le_premier_versement_disparaissent(self):
        brut = {"timestamp": [_ts("2026-09-10"), _ts("2026-09-11"), _ts("2026-09-14")],
                "equity": [0, None, 100000.0]}
        dates, equity = serie_historique(brut)
        assert dates == ["2026-09-14"] and equity == [100000.0]

    def test_la_date_est_celle_de_la_seance_quelle_que_soit_l_heure(self):
        """04:00 UTC (minuit a New York) ou 13:30 UTC (ouverture) : meme seance."""
        for heure in (4, 5, 13, 14):
            dates, _ = serie_historique({"timestamp": [_ts("2026-09-15", heure)],
                                         "equity": [1.0]})
            assert dates == ["2026-09-15"], heure

    def test_deux_points_le_meme_jour_on_garde_le_dernier(self):
        brut = {"timestamp": [_ts("2026-09-15", 4), _ts("2026-09-15", 14)],
                "equity": [100.0, 101.0]}
        assert serie_historique(brut) == (["2026-09-15"], [101.0])

    def test_une_reponse_vide_ou_absurde_ne_leve_pas(self):
        for brut in (None, {}, {"timestamp": ["x"], "equity": ["y"]},
                     {"timestamp": [1], "equity": [float("nan")]}):
            assert serie_historique(brut) == ([], [])


# ---------------------------------------------------------------------------
class TestPerteDuJour:
    def test_rapportee_au_capital_de_depart_et_non_a_la_veille(
            self, cfg, panneau, jours, etat_isole):
        """Le coeur : monte a 200 000 $ puis -4 % en une seance. Le contrat
        compte 8 000 $ sur une base de 100 000 $, soit 8 % de son plafond
        journalier - pas 4 %."""
        defi.ecrire_etat({"capital_depart": 100000.0, "plus_haut": 200000.0})
        api = FauxCourtier(jours[:2], [200000.0, 192000.0])
        h = _ops(cfg, panneau, api).historique()
        assert h["ok"]
        assert h["perte_jour"][0] is None
        assert h["perte_jour"][1] == pytest.approx(-0.08)

    def test_identique_a_ce_que_mesure_le_garde_fou(self, cfg, panneau, jours, etat_isole):
        """La barre dessinee et la limite qui declenche doivent mesurer la
        meme chose. Ce test lie les deux par construction : si quelqu'un
        change la definition dans `defi.evaluer`, il casse ici."""
        etat = {"capital_depart": 100000.0, "plus_haut": 104000.0}
        defi.ecrire_etat(etat)
        valeurs = [100000.0, 103000.0, 104000.0, 99500.0, 101200.0, 97800.0]
        h = _ops(cfg, panneau, FauxCourtier(jours, valeurs)).historique()
        params = defi.parametres(cfg)
        for i in range(1, len(valeurs)):
            attendu = defi.evaluer(valeurs[i], valeurs[i - 1], params, dict(etat))["perte_jour"]
            assert h["perte_jour"][i] == pytest.approx(attendu), jours[i]

    def test_sans_etat_du_defi_on_part_du_premier_point(self, cfg, panneau, jours):
        h = _ops(cfg, panneau, FauxCourtier(jours[:2], [50000.0, 49000.0])).historique()
        assert h["perte_jour"][1] == pytest.approx(-0.02)


# ---------------------------------------------------------------------------
class TestIndice:
    def test_aligne_aux_dates_du_compte(self, cfg, panneau, jours):
        h = _ops(cfg, panneau, FauxCourtier(jours, [1e5] * 6)).historique()
        assert h["indice"][:5] == [500.0, 505.0, 502.0, 510.0, 508.0]
        assert h["indice_nom"] == "^BENCH"

    def test_jamais_invente_sans_cloture(self, cfg, panneau, jours):
        """La derniere seance n'est pas encore en cache : reporter le cours de
        la veille montrerait l'indice immobile le jour ou le compte bouge."""
        h = _ops(cfg, panneau, FauxCourtier(jours, [1e5] * 6)).historique()
        assert h["indice"][5] is None

    def test_indice_absent_du_panel(self, cfg, jours):
        h = _ops(cfg, {}, FauxCourtier(jours, [1e5] * 6)).historique()
        assert h["ok"] and h["indice"] == [None] * 6 and h["indice_nom"] is None


# ---------------------------------------------------------------------------
class TestQuandCaManque:
    def test_courtier_sans_historique(self, cfg, panneau):
        """MT5 n'a pas d'historique d'equity : une phrase, pas une exception."""
        h = _ops(cfg, panneau, CourtierSansHistorique()).historique()
        assert h["ok"] is False and "courtier" in h["raison"]
        assert h["defi"]["actif"] is True, "les barrieres restent affichables"

    def test_courtier_qui_echoue(self, cfg, panneau):
        class Casse:
            def historique(self, periode="3M"):
                raise RuntimeError("HTTP 503")
        h = _ops(cfg, panneau, Casse()).historique()
        assert h["ok"] is False and "503" in h["raison"]

    def test_compte_trop_neuf(self, cfg, panneau):
        h = _ops(cfg, panneau, FauxCourtier([], [])).historique()
        assert h["ok"] is False and "aucune seance" in h["raison"]


# ---------------------------------------------------------------------------
class TestBarrieres:
    def test_suivent_la_config_et_le_contrat(self, cfg, panneau, jours):
        d = _ops(cfg, panneau, FauxCourtier(jours, [1e5] * 6)).historique()["defi"]
        assert d["perte_jour_max"] == pytest.approx(cfg.get("defi.perte_jour_max"))
        assert d["perte_totale_max"] == pytest.approx(cfg.get("defi.perte_totale_max"))
        assert d["contrat_jour"] == pytest.approx(0.05)
        assert d["contrat_total"] == pytest.approx(0.10)
        # La marge n'a de sens que si le bot s'arrete AVANT le contrat.
        assert d["perte_jour_max"] < d["contrat_jour"]
        assert d["perte_totale_max"] < d["contrat_total"]

    def test_le_verrou_pose_est_transmis(self, cfg, panneau, jours, etat_isole):
        verrou = {"raison": "perte", "detail": "x", "depuis": "2026-09-16", "equity": 91000.0}
        defi.ecrire_etat({"capital_depart": 1e5, "plus_haut": 1e5, "verrou": verrou})
        d = _ops(cfg, panneau, FauxCourtier(jours, [1e5] * 6)).historique()["defi"]
        assert d["verrou"] == verrou

    def test_defi_inactif(self, base_config, panneau, jours):
        cfg = base_config.with_overrides({"defi.active": False})
        d = _ops(cfg, panneau, FauxCourtier(jours, [1e5] * 6)).historique()["defi"]
        assert d["actif"] is False


# ---------------------------------------------------------------------------
class TestAppelCourtier:
    def test_le_bon_point_d_acces(self, monkeypatch):
        vu = {}
        a = Alpaca("CLE", "SECRET")

        def faux_appel(methode, chemin, base=None, **kw):
            vu.update(methode=methode, chemin=chemin, **kw)
            return {"timestamp": [], "equity": []}

        monkeypatch.setattr(a, "_appel", faux_appel)
        a.historique("1M")
        assert vu["methode"] == "GET"
        assert vu["chemin"] == "/v2/account/portfolio/history"
        assert vu["params"] == {"period": "1M", "timeframe": "1D"}
