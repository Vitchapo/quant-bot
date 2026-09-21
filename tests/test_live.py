"""Ce que le bot decide aujourd'hui doit etre ce que le backtest a mesure.

C'est la condition pour qu'un chiffre de backtest veuille dire quelque chose
sur l'argent reel. Un ecart ici - un jour de decalage, une barre partielle
prise pour une cloture - et la strategie vecue n'est plus celle qui a ete
validee, sans que rien ne le signale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import live
from quantbot.backtest import run_backtest
from quantbot.portfolio import rebalance_dates


def _ohlcv(close, index):
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=index)


@pytest.fixture(scope="module")
def univers():
    """Deux ans et demi de seances, dix titres aux trajectoires distinctes."""
    idx = pd.bdate_range("2023-01-02", "2026-10-02")
    rng = np.random.default_rng(11)
    prix = {}
    for i in range(10):
        derive = 0.0001 + i * 0.00006
        prix["T%02d" % i] = _ohlcv(100 * np.exp(rng.normal(derive, 0.012, len(idx)).cumsum()), idx)
    prix["^B"] = _ohlcv(100 * np.exp(np.linspace(0, 0.6, len(idx))), idx)
    return prix


@pytest.fixture(params=["monthly", "weekly"])
def cfg_live(base_config, request):
    """La CADENCE est pinnee, et parametree sur les deux valeurs utilisees.

    Elle etait auparavant heritee de `config/us.yaml` pendant que les tests
    calculaient les dates de signal avec "monthly" ecrit en dur. Le jour ou la
    config est passee en `weekly` - pour satisfaire le minimum de 5 jours de
    negociation du defi - les six tests de ce fichier se sont mis a echouer
    sans qu'aucun code metier n'ait bouge. Un test dont le SENS depend d'un
    fichier de configuration ne teste pas ce qu'il annonce.

    Parametrer sur les deux cadences repare davantage que le couplage : avant,
    l'equivalence live/backtest n'etait verifiee qu'en mensuel, alors que le
    compte reel tourne en hebdomadaire.
    """
    return base_config.with_overrides({
        "universe.benchmark": "^B", "data.min_history_days": 260,
        "portfolio.top_n": 4, "portfolio.weighting": "inv_vol",
        "regime.enabled": True, "execution.execution_lag": 1,
        "execution.rebalance": request.param})


def _dates_signal(idx, cfg):
    """Les dates de signal SELON LA CONFIG UTILISEE, jamais une cadence ecrite
    en dur : c'est ce qui rend impossible le desaccord entre le test et le
    portefeuille qu'il examine."""
    return set(rebalance_dates(idx, cfg.get("execution.rebalance")))


def _tronquer(prix, fin):
    return {t: df.loc[:fin] for t, df in prix.items()}


class TestJourDExecution:
    def test_le_bot_ne_s_active_que_le_lendemain_du_signal(self, univers, cfg_live):
        idx = univers["T00"].index
        fins_de_periode = _dates_signal(idx, cfg_live)
        signal = sorted(d for d in fins_de_periode if d < idx[-2])[-1]
        suivante = idx[idx.get_indexer([signal])[0] + 1]

        veille = live.portefeuille_cible(_tronquer(univers, signal), cfg_live,
                                         aujourdhui=signal)
        jour = live.portefeuille_cible(_tronquer(univers, suivante), cfg_live,
                                       aujourdhui=suivante)
        assert veille.est_jour_execution is False, "le jour du signal, on ne fait rien"
        assert jour.est_jour_execution is True, "le lendemain, on execute"
        assert jour.date_signal == signal

    def test_les_autres_jours_ne_declenchent_rien(self, univers, cfg_live):
        idx = univers["T00"].index
        fins_de_periode = _dates_signal(idx, cfg_live)
        declenchements = ordinaires = 0
        for fin in idx[-25:]:
            c = live.portefeuille_cible(_tronquer(univers, fin), cfg_live, aujourdhui=fin)
            position = idx.get_indexer([fin])[0]
            if c.est_jour_execution:
                declenchements += 1
                assert idx[position - 1] in fins_de_periode
            else:
                ordinaires += 1
        assert declenchements >= 1, "au moins un rebalancement sur les 25 dernieres seances"
        # Sans cette seconde borne, une cadence qui declencherait TOUS les
        # jours passerait le test : la boucle ci-dessus ne verifie que les
        # jours ou le bot s'active, jamais qu'il existe des jours ou il dort.
        assert ordinaires >= 1, "le bot s'active tous les jours : la cadence ne filtre rien"


class TestEquivalenceAvecLeBacktest:
    # Les cibles de volatilite sont choisies pour MORDRE sur cet univers : son
    # panier affiche 10,1 % de volatilite, donc une cible a 12 % plafonnerait
    # l'exposition a 1,000 et le test ne testerait rien. Une premiere version
    # faisait exactement cela - elle passait meme en debranchant le pilotage
    # de `live`, ce qui est precisement le bug qu'elle devait attraper.
    @pytest.mark.parametrize("options", [
        {},
        {"portfolio.volatilite.active": True, "portfolio.volatilite.cible": 0.05},
        {"portfolio.diversification.active": True,
         "portfolio.diversification.correlation_max": 0.30},
        {"portfolio.volatilite.active": True, "portfolio.volatilite.cible": 0.06,
         "portfolio.diversification.active": True,
         "portfolio.diversification.correlation_max": 0.30},
    ], ids=["defaut", "volatilite", "diversification", "les-deux"])
    def test_les_poids_vises_sont_ceux_du_backtest(self, univers, cfg_live, options):
        """Le coeur du sujet : meme date, memes poids, a la sixieme decimale.

        Parametre sur les OPTIONS, et pas seulement sur le defaut : le
        pilotage de volatilite avait ete cable dans le backtest mais pas dans
        `live`, si bien que le compte aurait trade sans pilotage pendant que
        la mesure, elle, en tenait compte. Le test au defaut seul ne voyait
        rien. Toute option ajoutee au backtest doit apparaitre ici.
        """
        idx = univers["T00"].index
        cfg_live = cfg_live.with_overrides(options) if options else cfg_live
        resultat = run_backtest(univers, cfg_live)
        fins_de_periode = _dates_signal(idx, cfg_live)

        verifies = 0
        for position in range(len(idx) - 40, len(idx)):
            jour = idx[position]
            if idx[position - 1] not in fins_de_periode:
                continue
            cible = live.portefeuille_cible(_tronquer(univers, jour), cfg_live, aujourdhui=jour)
            assert cible.est_jour_execution, "ce jour-la, le bot doit s'activer"
            attendus = resultat.weights.loc[jour]
            attendus = attendus[attendus > 1e-9]
            obtenus = cible.poids
            assert set(obtenus.index) == set(attendus.index), (
                "titres differents le %s : bot %s / backtest %s"
                % (jour.date(), sorted(obtenus.index), sorted(attendus.index)))
            for ticker in attendus.index:
                assert obtenus[ticker] == pytest.approx(attendus[ticker], abs=1e-9)
            if options.get("portfolio.volatilite.active"):
                # Sans cela, une cible trop haute plafonnerait l'exposition a
                # 1,0 et le test comparerait deux portefeuilles non pilotes.
                assert float(obtenus.sum()) < 0.98, (
                    "le pilotage ne mord pas (exposition %.3f) : ce test ne "
                    "prouverait rien" % obtenus.sum())
            verifies += 1
        assert verifies >= 1, "aucun jour d'execution trouve dans la fenetre"

    def test_une_barre_partielle_ne_change_pas_la_decision(self, univers, cfg_live):
        """Le jour de l'execution, la seance en cours n'a pas encore de cloture.
        Sa barre partielle ne doit pas influencer les poids."""
        idx = univers["T00"].index
        fins_de_periode = _dates_signal(idx, cfg_live)
        signal = sorted(d for d in fins_de_periode if d < idx[-2])[-1]
        jour = idx[idx.get_indexer([signal])[0] + 1]

        normal = live.portefeuille_cible(_tronquer(univers, jour), cfg_live, aujourdhui=jour)

        # on deforme violemment la seance en cours : la decision doit tenir
        abime = {t: df.copy() for t, df in _tronquer(univers, jour).items()}
        for t, df in abime.items():
            if not t.startswith("^"):
                df.loc[jour, "close"] = df.loc[jour, "close"] * 1.5
        deforme = live.portefeuille_cible(abime, cfg_live, aujourdhui=jour)

        assert list(deforme.poids.index) == list(normal.poids.index)
        for ticker in normal.poids.index:
            assert deforme.poids[ticker] == pytest.approx(normal.poids[ticker], abs=1e-9)
