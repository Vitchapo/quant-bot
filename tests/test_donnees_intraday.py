"""L'etape 2 du momentum intrajournalier, sur un terminal MT5 simule et des barres FABRIQUEES.

Aucune vraie donnee ici. On verifie que le telechargement ne se fait qu'une
fois, n'ecrit rien tant que l'historique n'est pas complet, et scelle des
fichiers relus a l'identique. On verifie aussi que les controles arretent
l'etape 2 si l'heure du serveur n'est pas celle annoncee, et que le tableau
entre dans le protocole a la bonne place, une seule fois.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import donnees_intraday as S  # noqa: E402
import faux_mt5  # noqa: E402
import intraday_synthetique as SY  # noqa: E402
import tester_tendance as TT  # noqa: E402
from quantbot import intraday_donnees as I  # noqa: E402
from quantbot import tendance_donnees as TD  # noqa: E402

T = pd.Timestamp
VRAI_PROTOCOLE = Path(__file__).resolve().parent.parent / "docs" / "protocole_momentum_intrajournalier.md"
JOURS = SY.seances("2017-12-28", "2019-12-31")
DEBUTS = {"AUS200": "2019-02-08", "HK50": "2018-12-17"}         # comme chez FTMO


def _fabriquer(serveur_us500="new_york"):
    barres, yahoo = {}, {}
    for i, n in enumerate(I.INDICES):
        b = SY.barres(n, JOURS, graine=10 + i, serveur=serveur_us500 if n == "US500" else "new_york")
        yahoo[n] = SY.clotures_yahoo(n, JOURS, b, graine=20 + i)
        if n in DEBUTS:
            b = b[b["time"] >= int((T(DEBUTS[n]) - SY.EPOQUE).total_seconds())]
        barres[I.SYMBOLES[n]] = b
    return barres, yahoo


@pytest.fixture(scope="module")
def sain():
    return _fabriquer()


class FauxBarres(faux_mt5.FauxMT5):
    """Un terminal qui rend les barres M30 fabriquees.

    prets_apres  lectures pendant lesquelles le telechargement n'est pas fini
    sans_fin     chaque lecture rend 100 barres de plus : jamais stable
    sans_borne   ignore la fin demandee (pour verifier que le script coupe lui-meme)
    """
    TIMEFRAME_M30 = 30

    def __init__(self, barres, prets_apres=0, sans_fin=False, sans_borne=False):
        super().__init__()
        self.barres, self.prets_apres, self.sans_fin, self.sans_borne = barres, prets_apres, sans_fin, sans_borne
        self.demandes = []

    def copy_rates_range(self, sym, tf, d1, d2):
        self.demandes.append((sym, tf, d1, d2))
        if sym not in self.barres:
            self._erreur = (-1, "Terminal: Call failed")
            return None
        b = self.barres[sym]
        b = b[b["time"] >= int(d1.timestamp())]
        if not self.sans_borne:
            b = b[b["time"] <= int(d2.timestamp())]
        k = sum(1 for d in self.demandes if d[0] == sym)
        if k <= self.prets_apres:
            return b[-500:]
        if self.sans_fin:
            return b[:len(b) // 2 + 100 * k]
        return b


def _depot(tmp_path, barres, yahoo, mt5=None):
    """Un depot fabrique : calendrier et protocole de tendance, protocole intraday, barres."""
    brut_t = tmp_path / "tendance_brut"
    brut_t.mkdir()
    lignes = [TT.ENTETE_DONNEES, "|---|---|---|"]
    for n in I.INDICES:
        f = brut_t / I.fichier_calendrier(n)
        SY.ecrire_yahoo(f, yahoo[n])
        lignes.append("| `%s` | `%s` | %s, source principale : retenu, aucun défaut |"
                      % (f.name, TD.sha256(f), n))
    (tmp_path / "tendance.md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    (tmp_path / "protocole.md").write_bytes(VRAI_PROTOCOLE.read_bytes())
    p = dict(brut=tmp_path / "intraday" / "brut", manifeste=tmp_path / "intraday" / "manifeste.csv")
    S.telecharger(mt5 or FauxBarres(barres), dict(I.SYMBOLES), maintenant=T("2026-11-18 20:00", tz="UTC"),
                  attendre=lambda _: None, dire=lambda _: None, **p)
    return dict(p, tendance=tmp_path / "tendance.md", brut_tendance=brut_t)


@pytest.fixture
def chemins(monkeypatch, tmp_path):
    """Les chemins du script, detournes vers un depot fabrique."""
    def poser(p):
        monkeypatch.setattr(S, "PROTOCOLE", tmp_path / "protocole.md")
        monkeypatch.setattr(S, "TENDANCE", p["tendance"])
        monkeypatch.setattr(S, "BRUT", p["brut"])
        monkeypatch.setattr(S, "MANIFESTE", p["manifeste"])
        monkeypatch.setattr(S, "CONTROLE", tmp_path / "intraday" / "controle.txt")
        monkeypatch.setattr(S, "BRUT_TENDANCE", p["brut_tendance"])
    return poser


# -- le telechargement ---------------------------------------------------------------
def test_le_telechargement_scelle_six_fichiers_relus_a_l_identique(tmp_path, sain):
    barres, yahoo = sain
    mt5 = FauxBarres(barres)
    p = _depot(tmp_path, barres, yahoo, mt5)
    m = I.lire_manifeste(p["manifeste"])
    assert sorted(m) == sorted(I.nom_fichier(n) for n in I.INDICES)
    for n in I.INDICES:
        l, b = m[I.nom_fichier(n)], barres[I.SYMBOLES[n]]
        d = I.lire_barres(p["brut"] / l["fichier"])
        assert int(l["barres"]) == len(b) == len(d) and np.array_equal(d["close"].to_numpy(), b["close"])
        assert l["sha256"] == TD.sha256(p["brut"] / l["fichier"]) and l["telecharge_utc"] == "2026-11-18T20:00:00Z"
        assert l["premiere_barre"] == I.depuis_mt5([b["time"][0]])[0].strftime("%Y-%m-%d %H:%M")
    assert m["ftmo_AUS200_cash_M30.csv"]["premiere_barre"].startswith("2019-02-08")
    sym, tf, d1, d2 = mt5.demandes[0]
    assert (sym, tf) == ("US500.cash", 30)
    assert (d1.year, d1.utcoffset().total_seconds()) == (2000, 0)
    assert (d2.year, d2.month, d2.day, d2.hour, d2.minute) == (2026, 10, 31, 23, 59)
    assert I.verifier_empreintes(p["brut"], p["manifeste"]) == []


def test_les_barres_apres_le_31_octobre_2026_ne_sont_pas_gardees(tmp_path, sain):
    barres, yahoo = sain
    plus = dict(barres)
    b = barres["US500.cash"]
    tard = b[-40:].copy()
    tard["time"] = int((T("2026-11-02 09:00") - SY.EPOQUE).total_seconds()) + 1800 * np.arange(40)
    plus["US500.cash"] = np.concatenate([b, tard])
    p = _depot(tmp_path, plus, yahoo, FauxBarres(plus, sans_borne=True))
    d = I.lire_barres(p["brut"] / I.nom_fichier("US500"))
    assert len(d) == len(b) and d.index.max() < I.FIN_BARRES


def test_un_second_telechargement_est_refuse(tmp_path, sain):
    barres, yahoo = sain
    p = _depot(tmp_path, barres, yahoo)
    avant = {f.name: f.read_bytes() for f in p["brut"].iterdir()}
    with pytest.raises(RuntimeError, match="une seule fois"):
        S.telecharger(FauxBarres(barres), dict(I.SYMBOLES), p["brut"], p["manifeste"], dire=lambda _: None)
    p["manifeste"].unlink()
    with pytest.raises(RuntimeError, match="n'est pas vide"):
        S.telecharger(FauxBarres(barres), dict(I.SYMBOLES), p["brut"], p["manifeste"], dire=lambda _: None)
    assert {f.name: f.read_bytes() for f in p["brut"].iterdir()} == avant


def test_un_historique_qui_ne_se_stabilise_pas_n_ecrit_rien(tmp_path, sain):
    barres, _ = sain
    brut, manifeste = tmp_path / "brut", tmp_path / "manifeste.csv"
    with pytest.raises(RuntimeError, match="pas stable"):
        S.telecharger(FauxBarres(barres, sans_fin=True), dict(I.SYMBOLES), brut, manifeste,
                      attendre=lambda _: None, dire=lambda _: None)
    assert not manifeste.exists() and not brut.exists()


def test_un_telechargement_trop_long_s_arrete_au_bout_du_budget(tmp_path, sain):
    barres, _ = sain
    temps, vus = [0.0], []

    def horloge():
        temps[0] += 10.0
        return temps[0]
    with pytest.raises(RuntimeError, match="pas stable"):
        S.telecharger(FauxBarres(barres, sans_fin=True), dict(I.SYMBOLES), tmp_path / "b", tmp_path / "m.csv",
                      attendre=lambda _: None, horloge=horloge, dire=vus.append)
    assert len([v for v in vus if "lecture" in v]) < S.ESSAIS


def test_le_telechargement_attend_que_l_historique_soit_complet(tmp_path, sain):
    barres, _ = sain
    vus = []
    S.telecharger(FauxBarres(barres, prets_apres=2), dict(I.SYMBOLES), tmp_path / "brut", tmp_path / "m.csv",
                  attendre=lambda _: None, dire=vus.append)
    for n in I.INDICES:
        assert len(I.lire_barres(tmp_path / "brut" / I.nom_fichier(n))) == len(barres[I.SYMBOLES[n]])
    assert "US500 (US500.cash) :" in vus and any("lecture 5 :" in v for v in vus)


# -- les controles -------------------------------------------------------------------
def test_un_depot_sain_passe_tous_les_controles(tmp_path, sain):
    barres, yahoo = sain
    p = _depot(tmp_path, barres, yahoo)
    res = S.controler(p["brut"], p["manifeste"], p["tendance"], p["brut_tendance"])
    assert res["problemes"] == [] and res["heure"]["ok"]
    assert all(c["retenu"] for c in res["indices"].values())
    assert res["indices"]["AUS200"]["debut_juge"] == T("2019-02-08")
    texte = S.rapport(res)
    assert "-> VERIFIEE" in texte and texte.count("-> retenu") == 6
    texte.encode("ascii")                                       # la console Windows l'affiche tel quel


def test_un_fichier_modifie_bloque_les_controles(tmp_path, sain):
    barres, yahoo = sain
    p = _depot(tmp_path, barres, yahoo)
    f = p["brut"] / I.nom_fichier("UK100")
    f.write_bytes(f.read_bytes() + b"\n")
    res = S.controler(p["brut"], p["manifeste"], p["tendance"], p["brut_tendance"])
    assert res["problemes"] == ["%s : l'empreinte ne correspond plus au manifeste" % f.name]
    assert S.rapport(res).startswith("PAS DE CONTROLE")


def test_le_tableau_de_l_etape_2(tmp_path, sain):
    barres, yahoo = sain
    p = _depot(tmp_path, barres, yahoo)
    res = S.controler(p["brut"], p["manifeste"], p["tendance"], p["brut_tendance"])
    bloc = S.bloc_protocole(res, "18 novembre 2026")
    lignes = bloc.split("\n")
    assert lignes[0] == S.ENTETE and len([l for l in lignes if l.startswith("| `ftmo_")]) == 6
    m = res["manifeste"]["ftmo_HK50_cash_M30.csv"]
    assert ("| `ftmo_HK50_cash_M30.csv` | `%s` | %s | 17/12/2018 | 31/12/2019 | 0 sur "
            % (m["sha256"], "{:,}".format(int(m["barres"])).replace(",", " "))) in bloc
    assert "**Aucun indice n'est exclu.**" in bloc and "vérifiée" in bloc


def test_un_indice_aux_barres_trop_lacunaires_est_exclu_dans_le_tableau(tmp_path, sain):
    barres, yahoo = sain
    troue = dict(barres)
    b = barres["HK50.cash"]
    jours = JOURS[JOURS >= "2018-12-17"][::10]                     # 10 % des seances jugees
    otees = (I.debut_fenetre("HK50", jours) - SY.EPOQUE) // pd.Timedelta(seconds=1)
    troue["HK50.cash"] = b[~np.isin(b["time"], np.asarray(otees))]
    p = _depot(tmp_path, troue, yahoo)
    res = S.controler(p["brut"], p["manifeste"], p["tendance"], p["brut_tendance"])
    assert not res["indices"]["HK50"]["retenu"]
    bloc = S.bloc_protocole(res, "18 novembre 2026")
    assert "| **exclu** : barre de fenêtre manquante pour 10," in bloc and "**Exclu : HK50.**" in bloc
    assert "EXCLU : barre de fenetre manquante pour 10." in S.rapport(res)


# -- le programme ---------------------------------------------------------------------
def test_ecrire_consigne_le_tableau_avant_la_fenetre_et_date_l_etape_2(tmp_path, sain, chemins, capsys):
    barres, yahoo = sain
    chemins(_depot(tmp_path, barres, yahoo))
    texte = (tmp_path / "protocole.md").read_text(encoding="utf-8")
    assert S.main([]) == 0                                       # sans --ecrire : rien dans le protocole
    assert (tmp_path / "protocole.md").read_text(encoding="utf-8") == texte
    assert S.main(["--ecrire"]) == 0
    neuf = (tmp_path / "protocole.md").read_text(encoding="utf-8")
    avant, apres = neuf.split(S.ENTETE)
    assert avant.rstrip().endswith("même si le CFD cote.")
    assert apres.index(S.AVANT) < apres.index("### Les coûts FTMO")
    jour = [l for l in neuf.split("\n") if l.startswith(S.ETAPE_2)][0]
    assert re.search(r"committer \| \d{1,2} \w+ \d{4} \|$", jour)
    assert (tmp_path / "intraday" / "controle.txt").exists()
    assert S.main(["--ecrire"]) == 1                             # une seule fois
    assert (tmp_path / "protocole.md").read_text(encoding="utf-8") == neuf
    # rien d'autre n'a bouge
    bloc = apres.split("\n\n" + S.AVANT)[0]
    retire = neuf.replace(S.ENTETE + bloc + "\n\n", "")
    retire = retire.replace(jour, jour[:jour.rindex("|", 0, len(jour) - 1)] + "| |")
    assert retire == texte


def test_une_heure_du_serveur_au_rythme_europeen_arrete_l_etape_2(tmp_path, chemins, capsys):
    barres, yahoo = _fabriquer(serveur_us500="athenes")
    chemins(_depot(tmp_path, barres, yahoo))
    texte = (tmp_path / "protocole.md").read_bytes()
    assert S.main(["--ecrire"]) == 3
    assert (tmp_path / "protocole.md").read_bytes() == texte
    sortie = capsys.readouterr().out
    assert "ECHEC : l'etape 2 s'arrete et le test n'est pas lance" in sortie


def test_telecharger_avec_un_faux_terminal(tmp_path, sain, chemins, monkeypatch, capsys):
    barres, yahoo = sain
    p = _depot(tmp_path, barres, yahoo)
    for f in p["brut"].iterdir():
        f.unlink()
    p["manifeste"].unlink()
    chemins(p)

    class Api:
        _mt5 = FauxBarres(barres)
        ferme = False

        def construire_carte(self, indices):
            return {n: I.SYMBOLES[n] for n in indices}

        def fermer(self):
            Api.ferme = True
    monkeypatch.setattr(S, "PAUSE_S", 0.0)
    monkeypatch.setattr(S.Config, "load", staticmethod(lambda chemin: None))
    monkeypatch.setattr(S.mt5broker, "connecter", lambda cfg, exiger_trading=True: Api())
    assert S.main(["--telecharger"]) == 0 and Api.ferme
    assert I.verifier_empreintes(p["brut"], p["manifeste"]) == []
    assert "VERIFIEE" in capsys.readouterr().out
    assert S.main(["--telecharger"]) == 1                        # une seule fois
