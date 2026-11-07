"""L'etape 4 du protocole du tournant du mois, sur un depot FABRIQUE.

Aucune vraie donnee ici. Les fichiers bruts sont ecrits a partir de
tournant.synthetique(), au format des vraies sources. On verifie que le
script refuse tout ecart avant de lancer et qu'il ecrit l'en-tete avant de
calculer. On verifie aussi qu'il ne tourne qu'une fois et qu'il remplit le
protocole sans toucher a rien d'autre.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tester_tendance as TT  # noqa: E402
import tester_tournant as S  # noqa: E402
from quantbot import tendance_donnees as D  # noqa: E402
from quantbot import tournant as TM  # noqa: E402

VRAI_TESTER = TM.tester
VRAIS_DOCS = Path(__file__).resolve().parent.parent / "docs"
LIGNE_COUTS_FTMO = "| US500 | US500.cash | 1 | 0,6 | 0 | −175,78 | −8,27 | 1:15 | non versés |"


def _ecrire(f: Path, texte: str) -> None:
    """Fins de ligne \\n partout, Windows compris : les empreintes ne dependent pas du systeme."""
    with open(str(f), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(texte)


def _ecrire_brut(dossier: Path, prix: dict, taux: dict) -> None:
    """Les series fabriquees, ecrites aux formats des vraies sources."""
    dossier.mkdir(parents=True, exist_ok=True)
    for nom in TM.INDICES:
        princ = D.SERIES[nom][0]
        assert princ[0] == "yahoo"
        s = prix[nom]
        _ecrire(dossier / D.nom_fichier(princ), json.dumps({"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "UTC"},
            "timestamp": [int(t.timestamp()) for t in s.index.tz_localize("UTC")],
            "indicators": {"quote": [{"close": [float(v) for v in s]}]}}]}}))
    taux = dict(taux, DE=taux["XM"][taux["XM"].index < "1999-01-01"])
    for code, src in D.TAUX.items():
        _ecrire(dossier / D.nom_fichier(src), "FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\n"
                + "".join("D,%s,%s,%.4f\n" % (code, j.date(), v) for j, v in taux[code].items()))


def _protocole_tendance(brut: Path, exclus=()) -> str:
    """Les tableaux des etapes 1 et 2 du protocole de tendance ; seuls les
    fichiers des 6 indices et des taux existent."""
    L = ["# Protocole de tendance fabrique pour les tests", "",
         "| instrument | nom MT5 | contrat | spread | commission | swap achat | swap vente | levier | dividendes |",
         "|---|---|---|---|---|---|---|---|---|", LIGNE_COUTS_FTMO, "",
         TT.ENTETE_DONNEES, "|---|---|---|"]
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
    L += ["", "Relevé fabriqué.", ""]
    return "\n".join(L)


def _protocole_tournant() -> str:
    return "\n".join([
        "# Protocole du tournant fabrique pour les tests", "",
        "| # | critère | mesure | seuil | résultat |", "|---|---|---|---|---|",
        "| 1 | l'effet existe | régression | coefficient > 0, t > 2,5 | |",
        "| 2 | partout, pas sur un seul marché | indice par indice | au moins 5 des 6 | |",
        "| 3 | mieux que l'exposition passive | alpha | t > 2,5 | |",
        "| 4 | test du hasard | 1 000 tirages | p < 0,01 | |",
        "| 5 | rendement net des coûts FTMO | moyenne | t > 2,5 | |", "",
        "| # | étape | fait le |", "|---|---|---|",
        "| 3 | écrire le code | 7 novembre 2026 |",
        "| 4 | lancer le test **une seule fois**, remplir la colonne « résultat », committer la sortie brute | |",
        "| 5 | appliquer la décision | |", ""])


def _couts(chemin: Path, noms, **lignes) -> None:
    _ecrire(chemin, "instrument,transaction,marge_achat,marge_vente\n"
            + "".join("%s,%s\n" % (n, lignes.get(n, "0.0002,0.05,0.04")) for n in noms))


@pytest.fixture
def depot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # main() change de dossier : on le rendra
    prix, taux = TM.synthetique(graine=11, effet=0.1)
    brut = tmp_path / "data" / "tendance" / "brut"
    _ecrire_brut(brut, prix, taux)
    docs = tmp_path / "docs"
    docs.mkdir()
    _ecrire(docs / "protocole_tendance_multi_actifs.md", _protocole_tendance(brut))
    _ecrire(docs / "protocole_tournant_du_mois.md", _protocole_tournant())
    _couts(docs / "tendance_couts.csv", list(D.SERIES))
    monkeypatch.setattr(S, "RACINE", tmp_path)
    monkeypatch.setattr(S, "PROTOCOLE", docs / "protocole_tournant_du_mois.md")
    monkeypatch.setattr(S, "DONNEES", docs / "protocole_tendance_multi_actifs.md")
    monkeypatch.setattr(S, "COUTS", docs / "tendance_couts.csv")
    monkeypatch.setattr(S, "SORTIE", docs / "tournant_sortie_brute.txt")
    monkeypatch.setattr(S, "BRUT", brut)
    monkeypatch.setattr(S, "verifier_git", lambda: ("c0ffee1234567", []))
    monkeypatch.setattr(TM, "tester", lambda p, t, c: VRAI_TESTER(p, t, c, tirages=20))
    return tmp_path


# -- avant le test ----------------------------------------------------------------------------
def test_sans_lancer_il_verifie_et_ne_lance_rien(depot, capsys):
    assert S.main([]) == 0
    assert "PRET : les 6 indices" in capsys.readouterr().out
    assert not S.SORTIE.exists()


def test_refuse_si_une_empreinte_a_change(depot):
    for f in (S.BRUT / D.nom_fichier(D.SERIES["HK50"][0]), S.BRUT / D.nom_fichier(D.TAUX["JP"])):
        octets = f.read_bytes()
        f.write_bytes(octets + b"\n")
        assert S.verifier()["problemes"] == [
            "%s : l'empreinte ne correspond plus a celle du protocole de tendance" % f.name]
        f.write_bytes(octets)
    assert S.verifier()["problemes"] == []


def test_refuse_si_un_fichier_manque(depot):
    f = S.BRUT / D.nom_fichier(D.SERIES["AUS200"][0])
    f.unlink()
    assert S.verifier()["problemes"] == ["%s : fichier absent de %s" % (f.name, S.BRUT)]


def test_refuse_si_un_indice_a_ete_exclu_a_l_etape_2(depot):
    _ecrire(S.DONNEES, _protocole_tendance(S.BRUT, exclus=("JP225",)))
    assert any(x.startswith("JP225 a ete exclu") for x in S.verifier()["problemes"])


def test_l_exclusion_d_un_autre_instrument_ne_compte_pas(depot):
    _ecrire(S.DONNEES, _protocole_tendance(S.BRUT, exclus=("XAGUSD",)))
    assert S.verifier()["problemes"] == []


def test_refuse_des_couts_manquants_ou_dans_la_mauvaise_unite(depot):
    """4,7 au lieu de 0,047 : des pourcentages pris pour des fractions."""
    _couts(S.COUTS, [n for n in TM.INDICES if n != "HK50"], GER40="0.0002,4.7,3.2")
    p = S.verifier()["problemes"]
    assert "couts : HK50 manque" in p
    assert any(x.startswith("couts : GER40 hors des bornes") for x in p)
    S.COUTS.unlink()
    assert any("tendance_couts.csv n'existe pas" in x for x in S.verifier()["problemes"])


def test_refuse_si_les_tableaux_du_protocole_de_tendance_manquent(depot):
    texte = S.DONNEES.read_text(encoding="utf-8")
    _ecrire(S.DONNEES, texte.replace(LIGNE_COUTS_FTMO, TT.VIDE_ETAPE_1 + " | | | | | | | |"))
    assert any("(etape 1) n'est pas rempli" in x for x in S.verifier()["problemes"])
    _ecrire(S.DONNEES, "# rien\n")
    assert any("(etape 2) est absent" in x for x in S.verifier()["problemes"])


def test_refuse_si_une_case_du_protocole_est_introuvable(depot):
    ligne = "| 2 | partout, pas sur un seul marché | indice par indice | au moins 5 des 6 | |"
    texte = S.PROTOCOLE.read_text(encoding="utf-8").replace(ligne, ligne[:-1] + "déjà rempli |")
    _ecrire(S.PROTOCOLE, texte)
    assert any("cases introuvables" in x and "c2" in x for x in S.verifier()["problemes"])


# -- le test, une seule fois -----------------------------------------------------------------
def test_le_test_ne_tourne_qu_une_fois(depot, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "LANCER")
    avant = S.PROTOCOLE.read_text(encoding="utf-8")
    assert S.main(["--lancer"]) == 0
    sortie = S.SORTIE.read_text(encoding="utf-8")
    assert sortie.index("le test compte comme lance") < sortie.index("--- resultats ---")
    assert "c0ffee1234567" in sortie and "SCORE :" in sortie and "Decision prevue d'avance" in sortie
    assert "graine 20261106" in sortie and "decalages de 4 a 14 seances" in sortie
    mois = [l for l in sortie.splitlines() if re.match(r"^\d{4}-\d{2},", l)]
    assert mois[0].startswith("2000-01,") and mois[-1].startswith("2026-09,") and len(mois) == 321
    assert all(len(l.split(",")) == 4 for l in mois)
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


def test_seuls_les_6_indices_et_les_taux_sont_charges(depot, monkeypatch):
    vus = {}

    def espion(p, t, c):
        vus.update(prix=sorted(p), taux=sorted(t), couts=sorted(c))
        return VRAI_TESTER(p, t, c, tirages=5)
    monkeypatch.setattr(TM, "tester", espion)
    monkeypatch.setattr("builtins.input", lambda _: "LANCER")
    assert S.main(["--lancer"]) == 0
    assert vus["prix"] == sorted(TM.INDICES) and vus["couts"] == sorted(TM.INDICES)
    assert vus["taux"] == sorted(set(D.TAUX) - {"DE"})          # DE prolonge XM avant 1999


def test_sans_confirmation_rien_n_est_lance(depot, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "oui")
    assert S.main(["--lancer"]) == 3 and not S.SORTIE.exists()


def test_un_plantage_laisse_sa_trace_et_bloque_la_relance(depot, monkeypatch):
    def plante(p, t, c):
        raise RuntimeError("panne simulee")
    monkeypatch.setattr(TM, "tester", plante)
    monkeypatch.setattr("builtins.input", lambda _: "LANCER")
    avant = S.PROTOCOLE.read_bytes()
    with pytest.raises(RuntimeError):
        S.main(["--lancer"])
    sortie = S.SORTIE.read_text(encoding="utf-8")
    assert "ECHEC DU CALCUL" in sortie and "panne simulee" in sortie
    assert S.verifier()["deja_lance"] and S.PROTOCOLE.read_bytes() == avant


def test_la_colonne_resultat_en_clair():
    res = {"score": 2,
           "c1": {"coef": 0.0123, "t": 2.614, "ok": True},
           "c2": {"par_indice": {"US500": {"coef": 0.004, "t": 0.5}, "GER40": {"coef": -0.0021, "t": -0.3}},
                  "positifs": 1, "indices": 2, "ok": False},
           "c3": {"alpha_mensuel": 0.0021, "t": 2.31, "beta": 0.18, "ok": False},
           "c4": {"sharpe": 0.45, "au_moins_aussi_bons": 7, "tirages": 1000, "p": 0.008, "ok": True},
           "c5": {"moyenne_mensuelle": 0.0018, "t": 1.62, "mois": 321, "moyenne_2009": -0.0004, "ok": False}}
    c = S.cellules(res)
    assert c["c1"] == "coefficient 0,0123, t = 2,61 : **oui**"
    assert c["c2"] == "1 sur 2 (US500 0,0040 ; GER40 −0,0021) : non"
    assert c["c3"] == "alpha 0,210 % par mois, t = 2,31 (bêta 0,18) : non"
    assert c["c4"] == "Sharpe net 0,45 ; 7 tirages sur 1000 au moins aussi bons ; p = 0,0080 : **oui**"
    assert c["c5"] == "0,180 % par mois, t = 1,62 sur 321 mois ; 2009-2026 : −0,040 % par mois : non"
    assert S.decision(2).startswith("2 sur 5 : aucun paiement")
    assert S.decision(5).startswith("5 sur 5 : l'effet est demontre")
    assert S.decision(0) == "0 sur 5 : aucun paiement."


# -- les vrais documents (lus, jamais calcules) ---------------------------------------------------
def test_les_entrees_du_test_doivent_etre_commitees_et_poussees(monkeypatch):
    vu = {}
    monkeypatch.setattr(TT, "verifier_git", lambda racine, suivis: (vu.update(suivis=suivis), ("abc", []))[1])
    assert S.verifier_git() == ("abc", [])
    assert {p.name for p in vu["suivis"]} == {
        "protocole_tournant_du_mois.md", "protocole_tendance_multi_actifs.md", "tendance_couts.csv",
        "tournant.py", "tendance.py", "tendance_donnees.py", "tester_tendance.py", "tester_tournant.py"}
    assert all(Path(p).exists() for p in vu["suivis"])


def test_le_vrai_protocole_a_ses_cases():
    if (VRAIS_DOCS / "tournant_sortie_brute.txt").exists():
        pytest.skip("le test a tourne : les cases sont remplies")
    texte = (VRAIS_DOCS / "protocole_tournant_du_mois.md").read_text(encoding="utf-8")
    rempli = S.remplir_protocole(texte, S._res_factice(), "01/01/2000")
    assert sum(a != b for a, b in zip(texte.splitlines(), rempli.splitlines())) == 6


def test_le_vrai_tableau_de_l_etape_2_couvre_les_6_indices_et_les_taux():
    tableau = TT.tableau_donnees((VRAIS_DOCS / "protocole_tendance_multi_actifs.md").read_text(encoding="utf-8"))
    fichiers = ([D.nom_fichier(D.SERIES[n][0]) for n in TM.INDICES]
                + [D.nom_fichier(s) for s in D.TAUX.values()])
    assert all(tableau[f][0] and len(tableau[f][0]) == 64 for f in fichiers)
    assert not any(TT.EXCLU.match(tableau[D.nom_fichier(D.SERIES[n][0])][1]) for n in TM.INDICES)
