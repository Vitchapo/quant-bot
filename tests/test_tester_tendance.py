"""L'etape 4 du protocole de tendance, sur un depot FABRIQUE.

Aucune vraie donnee ici. Les fichiers bruts sont ecrits a partir de
tendance.synthetique(), aux formats des vraies sources. On verifie que le
script refuse tout ecart avant de lancer et qu'il ecrit l'en-tete avant de
calculer. On verifie aussi qu'il ne tourne qu'une fois et qu'il remplit le
protocole sans toucher a rien d'autre.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tester_tendance as S  # noqa: E402
from quantbot import tendance as T  # noqa: E402
from quantbot import tendance_donnees as D  # noqa: E402

VRAI_TESTER = T.tester
LIGNE_COUTS_FTMO = "| US500 | US500.cash | 1 | 0,6 | 0 | −175,78 | −8,27 | 1:15 | non versés |"


def _ecrire_brut(dossier: Path, prix: dict, taux: dict) -> None:
    """Les series fabriquees, ecrites aux formats des vraies sources."""
    dossier.mkdir(parents=True, exist_ok=True)
    for nom, (princ, _) in D.SERIES.items():
        s = prix[nom]
        f = dossier / D.nom_fichier(princ)
        if princ[0] == "yahoo":
            f.write_text(json.dumps({"chart": {"result": [{
                "meta": {"exchangeTimezoneName": "UTC"},
                "timestamp": [int(t.timestamp()) for t in s.index.tz_localize("UTC")],
                "indicators": {"quote": [{"close": [float(v) for v in s]}]}}]}}))
        elif princ[0] == "fred":
            f.write_text("observation_date,%s\n" % princ[1]
                         + "".join("%s,%.6f\n" % (t.date(), v) for t, v in s.items()))
        else:
            f.write_text("Date,Open,High,Low,Close\n"
                         + "".join("%s,%.6f,%.6f,%.6f,%.6f\n" % (t.date(), v, v, v, v) for t, v in s.items()))
    taux = dict(taux, DE=taux["XM"][taux["XM"].index < "1999-01-01"])
    for code, src in D.TAUX.items():
        (dossier / D.nom_fichier(src)).write_text(
            "FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\n"
            + "".join("D,%s,%s,%.4f\n" % (code, j.date(), v) for j, v in taux[code].items()))


def _protocole(brut: Path, exclus=()) -> str:
    """Le squelette du protocole : couts, donnees, criteres, etapes."""
    L = ["# Protocole fabrique pour les tests", "",
         "| instrument | nom MT5 | contrat | spread | commission | swap achat | swap vente | levier | dividendes |",
         "|---|---|---|---|---|---|---|---|---|", LIGNE_COUTS_FTMO, "",
         S.ENTETE_DONNEES, "|---|---|---|"]
    for nom, (princ, repli) in D.SERIES.items():
        f = brut / D.nom_fichier(princ)
        sha = "`%s`" % D.sha256(f) if f.exists() else "—"
        verdict = "**exclu** : fichier absent" if nom in exclus else "retenu, aucun défaut"
        L.append("| `%s` | %s | %s, source principale : %s |" % (f.name, sha, nom, verdict))
        L.append("| `%s` | — | %s, repli : non téléchargé, aucun défaut à trancher |"
                 % (D.nom_fichier(repli), nom))
    for code, src in D.TAUX.items():
        f = brut / D.nom_fichier(src)
        L.append("| `%s` | `%s` | taux directeur %s |" % (f.name, D.sha256(f), code))
    L += ["", "Relevé fabriqué.", "",
          "| # | critère | mesure | seuil | résultat |", "|---|---|---|---|---|",
          "| 1 | pouvoir prédictif | régression | pente > 0, t > 2 | |",
          "| 2 | profil par groupes | quintiles | t > 2 | |",
          "| 3 | mieux que l'exposition passive | alpha | t > 2 | |",
          "| 4 | test du hasard | 1 000 tirages | p < 0,05 | |",
          "| 5 | rendement net des coûts FTMO | moyenne | t > 2 | |", "",
          "| # | étape | fait le |", "|---|---|---|",
          "| 3 | écrire le code | 30 octobre 2026 |",
          "| 4 | lancer le test **une seule fois**, remplir la colonne « résultat », committer la sortie brute | |",
          "| 5 | appliquer la décision | |", ""]
    return "\n".join(L)


def _couts(chemin: Path, noms, **lignes) -> None:
    chemin.write_text("instrument,transaction,marge_achat,marge_vente\n"
                      + "".join("%s,%s\n" % (n, lignes.get(n, "0.0002,0.03,0.02")) for n in noms))


@pytest.fixture
def depot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # main() change de dossier : on le rendra
    prix, taux = T.synthetique(graine=11, tendance=1.0)
    brut = tmp_path / "data" / "tendance" / "brut"
    _ecrire_brut(brut, prix, taux)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "protocole_tendance_multi_actifs.md").write_text(_protocole(brut), encoding="utf-8")
    _couts(docs / "tendance_couts.csv", T.INSTRUMENTS)
    monkeypatch.setattr(S, "RACINE", tmp_path)
    monkeypatch.setattr(S, "PROTOCOLE", docs / "protocole_tendance_multi_actifs.md")
    monkeypatch.setattr(S, "COUTS", docs / "tendance_couts.csv")
    monkeypatch.setattr(S, "SORTIE", docs / "tendance_sortie_brute.txt")
    monkeypatch.setattr(S, "BRUT", brut)
    monkeypatch.setattr(S, "verifier_git", lambda: ("c0ffee1234567", []))
    monkeypatch.setattr(T, "tester", lambda p, t, c: VRAI_TESTER(p, t, c, tirages=20))
    return tmp_path


# -- avant le test ----------------------------------------------------------------------------
def test_sans_lancer_il_verifie_et_ne_lance_rien(depot, capsys):
    assert S.main([]) == 0
    assert "PRET : 15 instruments retenus" in capsys.readouterr().out
    assert not S.SORTIE.exists()


def test_refuse_tant_que_les_couts_ne_sont_pas_consignes(depot):
    S.COUTS.unlink()
    texte = S.PROTOCOLE.read_text(encoding="utf-8")
    S.PROTOCOLE.write_text(texte.replace(LIGNE_COUTS_FTMO, S.VIDE_ETAPE_1 + " | | | | | | | |"),
                           encoding="utf-8")
    p = S.verifier()["problemes"]
    assert any("(etape 1) n'est pas rempli" in x for x in p)
    assert any("tendance_couts.csv n'existe pas" in x for x in p)
    assert S.main(["--lancer"]) == 2 and not S.SORTIE.exists()


def test_refuse_des_couts_manquants_ou_dans_la_mauvaise_unite(depot):
    """3 au lieu de 0,03 : des pourcentages pris pour des fractions."""
    _couts(S.COUTS, [n for n in T.INSTRUMENTS if n != "HK50"], GER40="0.0002,3.0,2.0")
    p = S.verifier()["problemes"]
    assert "couts : HK50 manque" in p
    assert any(x.startswith("couts : GER40 hors des bornes") for x in p)


def test_refuse_si_une_empreinte_a_change(depot):
    f = S.BRUT / D.nom_fichier(D.SERIES["NZDUSD"][0])
    f.write_bytes(f.read_bytes() + b"\n")
    assert S.verifier()["problemes"] == [
        "%s : l'empreinte ne correspond plus a celle du protocole" % f.name]


def test_un_instrument_exclu_a_l_etape_2_n_est_ni_verifie_ni_charge(depot):
    (S.BRUT / D.nom_fichier(D.SERIES["XAGUSD"][0])).unlink()
    S.PROTOCOLE.write_text(_protocole(S.BRUT, exclus=("XAGUSD",)), encoding="utf-8")
    _couts(S.COUTS, [n for n in T.INSTRUMENTS if n != "XAGUSD"])
    e = S.verifier()
    assert e["problemes"] == [] and e["exclus"] == ["XAGUSD"] and "XAGUSD" not in e["retenus"]


def test_refuse_si_une_case_du_protocole_est_introuvable(depot):
    ligne = "| 4 | test du hasard | 1 000 tirages | p < 0,05 | |"
    texte = S.PROTOCOLE.read_text(encoding="utf-8").replace(ligne, ligne[:-1] + "déjà rempli |")
    S.PROTOCOLE.write_text(texte, encoding="utf-8")
    assert any("cases introuvables" in x and "c4" in x for x in S.verifier()["problemes"])


# -- le test, une seule fois -----------------------------------------------------------------
def test_le_test_ne_tourne_qu_une_fois(depot, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "LANCER")
    avant = S.PROTOCOLE.read_text(encoding="utf-8")
    assert S.main(["--lancer"]) == 0
    sortie = S.SORTIE.read_text(encoding="utf-8")
    assert sortie.index("le test compte comme lance") < sortie.index("--- resultats ---")
    assert "c0ffee1234567" in sortie and "SCORE :" in sortie and "Decision prevue d'avance" in sortie
    mois = [l for l in sortie.splitlines() if re.match(r"^\d{4}-\d{2},", l)]
    assert mois[0].startswith("2000-01,") and mois[-1].startswith("2026-09,") and len(mois) == 321
    # seules les cinq cases "resultat" et la date de l'etape 4 ont change
    apres = S.PROTOCOLE.read_text(encoding="utf-8")
    assert len(avant.splitlines()) == len(apres.splitlines())
    changees = [(a, b) for a, b in zip(avant.splitlines(), apres.splitlines()) if a != b]
    assert len(changees) == 6
    assert all(a.endswith("| |") and b.startswith(a[:-1]) and not b.endswith("| |") for a, b in changees)
    # une seconde fois : refus, sans rien toucher
    octets = S.SORTIE.read_bytes()
    assert S.main(["--lancer"]) == 1
    assert S.SORTIE.read_bytes() == octets
    assert "deja tourne" in capsys.readouterr().out


def test_sans_confirmation_rien_n_est_lance(depot, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "oui")
    assert S.main(["--lancer"]) == 3 and not S.SORTIE.exists()


def test_un_plantage_laisse_sa_trace_et_bloque_la_relance(depot, monkeypatch):
    def plante(p, t, c):
        raise RuntimeError("panne simulee")
    monkeypatch.setattr(T, "tester", plante)
    monkeypatch.setattr("builtins.input", lambda _: "LANCER")
    with pytest.raises(RuntimeError):
        S.main(["--lancer"])
    sortie = S.SORTIE.read_text(encoding="utf-8")
    assert "ECHEC DU CALCUL" in sortie and "panne simulee" in sortie
    assert S.verifier()["deja_lance"]


def test_la_colonne_resultat_en_clair():
    res = {"score": 3,
           "c1": {"pente": 0.0312, "t": 2.414, "ok": True},
           "c2": {"moyennes": [-0.05, 0.01, 0.02, 0.04, 0.09], "inversions": 0, "ecart": 0.14,
                  "t": 1.87, "ok": False},
           "c3": {"alpha_mensuel": 0.0021, "t": 2.31, "beta": 0.12, "ok": True},
           "c4": {"sharpe": 0.45, "au_moins_aussi_bons": 12, "tirages": 1000, "p": 0.013, "ok": True},
           "c5": {"moyenne_mensuelle": 0.0018, "t": 1.62, "mois": 321, "moyenne_2013": -0.0004, "ok": False}}
    c = S.cellules(res)
    assert c["c1"] == "pente 0,0312, t = 2,41 : **oui**"
    assert c["c2"] == ("groupes −0,050 / 0,010 / 0,020 / 0,040 / 0,090 ; 0 inversion(s) ; "
                       "5 − 1 = 0,140, t = 1,87 : non")
    assert c["c4"] == "Sharpe net 0,45 ; 12 tirages sur 1000 au moins aussi bons ; p = 0,0130 : **oui**"
    assert c["c5"] == "0,180 % par mois, t = 1,62 sur 321 mois ; 2013-2026 : −0,040 % par mois : non"
    assert S.decision(3).startswith("3 sur 5 : aucun paiement")
    assert S.decision(5).startswith("5 sur 5 : l'avantage est demontre")
    assert S.decision(0) == "0 sur 5 : aucun paiement."


# -- git : commite et pousse -------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_les_entrees_doivent_etre_commitees_et_poussees(tmp_path):
    depot, distant = tmp_path / "depot", tmp_path / "distant.git"
    depot.mkdir()

    def git(*args, cwd=depot):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git("init", "--bare", str(distant), cwd=tmp_path)
    git("init", "-b", "main")
    git("config", "user.email", "test@exemple.invalid")
    git("config", "user.name", "test")
    git("config", "commit.gpgsign", "false")
    f = depot / "protocole.md"
    f.write_text("v1\n")
    assert any("pas de commit" in x for x in S.verifier_git(depot, [f])[1])
    git("add", "protocole.md")
    git("commit", "-m", "v1")
    commit, p = S.verifier_git(depot, [f])
    assert len(commit) == 40
    assert p == ["le dernier commit n'est pas pousse : faire git push (sa date sur GitHub fait foi)"]
    git("remote", "add", "origin", str(distant))
    git("push", "-u", "origin", "main")
    assert S.verifier_git(depot, [f]) == (commit, [])
    f.write_text("v2\n")
    assert any(x.startswith("modifications non commitees") for x in S.verifier_git(depot, [f])[1])
