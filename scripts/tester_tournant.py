#!/usr/bin/env python3
"""Etape 4 du protocole du tournant du mois : LE test, une seule fois.

    python scripts/tester_tournant.py            # verifie que tout est pret ; ne lance rien
    python scripts/tester_tournant.py --lancer   # verifie, demande confirmation, lance le test

Avant de lancer quoi que ce soit, le script verifie les points suivants. Au
moindre ecart, il refuse :
  1. le test n'a jamais tourne : pas de sortie brute ;
  2. les fichiers bruts des 6 indices et des 9 taux ont l'empreinte SHA-256
     consignee a l'etape 2 du protocole de TENDANCE, dont le protocole du
     tournant reprend les donnees telles quelles, et aucun des 6 indices n'y
     est exclu ;
  3. les couts FTMO de l'etape 1 du protocole de tendance
     (docs/tendance_couts.csv) couvrent les 6 indices, en fractions plausibles ;
  4. le protocole du tournant a ses cinq cases "resultat" et la case de
     l'etape 4, encore vides ;
  5. les deux protocoles, les couts et le code du test sont commites, sans
     modification en cours, et le commit est pousse : sa date sur GitHub fait foi.
Aucune de ces verifications ne calcule un resultat de la strategie.

Ensuite, le script ecrit l'en-tete de la sortie brute AVANT de lancer le
calcul. A partir de la, le test compte comme lance. S'il plante, la trace de
l'erreur reste dans la sortie. Le relancer demande d'effacer ce fichier a la
main, ce que le protocole oblige a consigner.

A la fin, il ecrit les resultats dans la sortie brute. Il remplit aussi la
colonne "resultat" du protocole et la date de l'etape 4. Il reste a committer
ces deux fichiers.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

import numpy as np
import pandas as pd

import tester_tendance as TT        # les verifications du test de tendance, deja commitees
from quantbot import tendance_donnees as D
from quantbot import tournant as TM

RACINE = Path(__file__).resolve().parent.parent
PROTOCOLE = RACINE / "docs" / "protocole_tournant_du_mois.md"
#: Le protocole de tendance : les empreintes des fichiers bruts (etape 2) et
#: le tableau des couts (etape 1), repris tels quels.
DONNEES = RACINE / "docs" / "protocole_tendance_multi_actifs.md"
COUTS = RACINE / "docs" / "tendance_couts.csv"
SORTIE = RACINE / "docs" / "tournant_sortie_brute.txt"
BRUT = RACINE / D.BRUT
#: Ce qui doit etre commite tel quel avant le test.
SUIVIS = [PROTOCOLE, DONNEES, COUTS,
          RACINE / "src" / "quantbot" / "tournant.py",
          RACINE / "src" / "quantbot" / "tendance.py",
          RACINE / "src" / "quantbot" / "tendance_donnees.py",
          RACINE / "scripts" / "tester_tendance.py",
          Path(__file__).resolve()]
CONFIRMATION = "LANCER"
#: Les autres instruments du protocole de tendance : ni verifies ni charges.
AUTRES = [n for n in D.SERIES if n not in TM.INDICES]
CRITERES = {"c1": "| 1 | l'effet existe |",
            "c2": "| 2 | partout, pas sur un seul marché |",
            "c3": "| 3 | mieux que l'exposition passive |",
            "c4": "| 4 | test du hasard |",
            "c5": "| 5 | rendement net des coûts FTMO |"}
ETAPE_4 = "| 4 | lancer le test **une seule fois**"


# -- verifications : rien n'est calcule ici -------------------------------------------
def verifier_donnees(texte: str, brut: Path) -> list:
    """Les ecarts entre les fichiers bruts et l'etape 2 du protocole de tendance."""
    problemes = []
    if TT.VIDE_ETAPE_1 in texte:
        problemes.append("le tableau des couts du protocole de tendance (etape 1) n'est pas rempli")
    tableau = TT.tableau_donnees(texte)
    if not tableau:
        return problemes + ["le tableau des donnees (etape 2) est absent du protocole de tendance"]
    exclus = [m.group(1) for _, v in tableau.items() for m in [TT.EXCLU.match(v[1])] if m]
    problemes += ["%s a ete exclu a l'etape 2 du protocole de tendance : le test porte sur les 6 indices"
                  % n for n in TM.INDICES if n in exclus]
    attendus = ([D.nom_fichier(D.SERIES[n][0]) for n in TM.INDICES]
                + [D.nom_fichier(s) for s in D.TAUX.values()])
    for f in attendus:
        sha = tableau.get(f, (None, ""))[0]
        if not sha:
            problemes.append("%s : pas d'empreinte dans le protocole de tendance" % f)
        elif not (brut / f).exists():
            problemes.append("%s : fichier absent de %s" % (f, brut))
        elif D.sha256(brut / f) != sha:
            problemes.append("%s : l'empreinte ne correspond plus a celle du protocole de tendance" % f)
    return problemes


def verifier_git() -> tuple:
    """(commit, problemes) : les entrees du test sont-elles commitees et poussees ?"""
    return TT.verifier_git(RACINE, SUIVIS)


def verifier() -> dict:
    """Tout ce qui doit etre vrai avant le test. Ne calcule aucun resultat."""
    if SORTIE.exists():
        return {"problemes": ["le test a deja tourne : %s existe. Le protocole interdit de le relancer."
                              % SORTIE.name], "deja_lance": True}
    problemes = verifier_donnees(DONNEES.read_text(encoding="utf-8"), BRUT)
    couts, p = TT.lire_couts(COUTS, list(TM.INDICES))
    problemes += p
    try:
        remplir_protocole(PROTOCOLE.read_text(encoding="utf-8"), _res_factice(), "01/01/2000")
    except ValueError as exc:                     # a vide : les cases existent-elles ?
        problemes.append(str(exc))
    commit, p = verifier_git()
    problemes += p
    return {"problemes": problemes, "deja_lance": False, "couts": couts, "commit": commit}


# -- le test ------------------------------------------------------------------------------
def _res_factice() -> dict:
    """Un resultat vide, pour verifier a l'avance que le protocole a ses cases."""
    return {"score": 0,
            "c1": {"coef": 0.0, "t": 0.0, "ok": False},
            "c2": {"par_indice": {n: {"coef": 0.0, "t": 0.0} for n in TM.INDICES}, "positifs": 0,
                   "indices": len(TM.INDICES), "ok": False},
            "c3": {"alpha_mensuel": 0.0, "t": 0.0, "beta": 0.0, "ok": False},
            "c4": {"sharpe": 0.0, "au_moins_aussi_bons": 0, "tirages": TM.TIRAGES, "p": 1.0, "ok": False},
            "c5": {"moyenne_mensuelle": 0.0, "t": 0.0, "mois": 0, "moyenne_2009": 0.0, "ok": False}}


def cellules(res: dict) -> dict:
    """La colonne "resultat" du protocole, critere par critere."""
    def v(ok):
        return "**oui**" if ok else "non"
    n = TT._n
    c1, c2, c3, c4, c5 = (res[k] for k in ("c1", "c2", "c3", "c4", "c5"))
    return {
        "c1": "coefficient %s, t = %s : %s" % (n(c1["coef"], 4), n(c1["t"]), v(c1["ok"])),
        "c2": "%d sur %d (%s) : %s"
              % (c2["positifs"], c2["indices"],
                 " ; ".join("%s %s" % (k, n(x["coef"], 4)) for k, x in c2["par_indice"].items()),
                 v(c2["ok"])),
        "c3": "alpha %s %% par mois, t = %s (bêta %s) : %s"
              % (n(100 * c3["alpha_mensuel"], 3), n(c3["t"]), n(c3["beta"]), v(c3["ok"])),
        "c4": "Sharpe net %s ; %d tirages sur %d au moins aussi bons ; p = %s : %s"
              % (n(c4["sharpe"]), c4["au_moins_aussi_bons"], c4["tirages"], n(c4["p"], 4), v(c4["ok"])),
        "c5": "%s %% par mois, t = %s sur %d mois ; 2009-2026 : %s %% par mois : %s"
              % (n(100 * c5["moyenne_mensuelle"], 3), n(c5["t"]), c5["mois"],
                 n(100 * c5["moyenne_2009"], 3), v(c5["ok"])),
    }


def remplir_protocole(texte: str, res: dict, jour: str) -> str:
    """Remplit les cinq cases "resultat" et la date de l'etape 4, rien d'autre."""
    crlf = "\r\n" in texte
    lignes = texte.replace("\r\n", "\n").split("\n")
    cel = cellules(res)
    faits = set()
    for i, l in enumerate(lignes):
        if not l.rstrip().endswith("| |"):
            continue
        for k, debut in CRITERES.items():
            if l.startswith(debut):
                lignes[i] = l.rstrip()[:-1] + cel[k] + " |"
                faits.add(k)
        if l.startswith(ETAPE_4):
            lignes[i] = l.rstrip()[:-1] + "%s, score %d sur 5 |" % (jour, res["score"])
            faits.add("etape 4")
    manque = (set(CRITERES) | {"etape 4"}) - faits
    if manque:
        raise ValueError("cases introuvables ou deja remplies dans le protocole : %s"
                         % ", ".join(sorted(manque)))
    texte = "\n".join(lignes)
    return texte.replace("\n", "\r\n") if crlf else texte


def decision(score: int) -> str:
    """La decision prevue d'avance par le protocole."""
    if score == 5:
        return ("5 sur 5 : l'effet est demontre au standard du projet. On passe a "
                "\"Apres le test\", toujours sans payer.")
    if score == 0:
        return "0 sur 5 : aucun paiement."
    return ("%d sur 5 : aucun paiement. On consigne lequel a echoue et pourquoi. "
            "Cette idee n'est pas retouchee." % score)


def lancer(etat: dict, maintenant: datetime = None) -> dict:
    """Le test, une seule fois. L'en-tete est ecrit AVANT le calcul."""
    maintenant = maintenant or datetime.now(timezone.utc)
    entete = [
        "Test du tournant du mois - sortie brute",
        "Etape 4 de docs/protocole_tournant_du_mois.md, lancee une seule fois.",
        "Lance le %s, sur le commit %s." % (maintenant.strftime("%Y-%m-%d %H:%M:%S UTC"), etat["commit"]),
        "Python %s, numpy %s, pandas %s." % (platform.python_version(), np.__version__, pd.__version__),
        "Fichiers bruts : ceux du protocole de tendance, empreintes conformes a son etape 2.",
        "Indices (%d) : %s" % (len(TM.INDICES), ", ".join(TM.INDICES)),
        "Fenetre : achat a la cloture du jour -2, vente a la cloture du jour +3, "
        "nominal 40 % / volatilite estimee a l'achat.",
        "Periode jugee : %s a %s ; apres publication : des %s."
        % (TM.DEBUT_JUGE, TM.FIN_JUGE, TM.DEBUT_APRES),
        "Tirages du critere 4 : %d, graine %d, decalages de %d a %d seances."
        % (TM.TIRAGES, TM.GRAINE, TM.DECALAGE_MIN, TM.DECALAGE_MAX),
        "Couts FTMO (fractions ; transaction par nominal echange, marges par an) :",
    ]
    entete += ["  %-7s transaction %.6f  marge achat %.6f  marge vente %.6f"
               % (n, c["transaction"], c["marge_achat"], c["marge_vente"])
               for n, c in etat["couts"].items()]
    entete += ["", "A partir d'ici, le test compte comme lance.", ""]
    with SORTIE.open("x", encoding="utf-8", newline="\n") as fh:     # "x" : echoue s'il existe
        fh.write("\n".join(entete))
        fh.flush()
        os.fsync(fh.fileno())
    try:
        prix, taux = D.charger(AUTRES, dossier=BRUT)
        res = TM.tester(prix, taux, etat["couts"])
    except Exception:
        with SORTIE.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("ECHEC DU CALCUL :\n" + traceback.format_exc())
        raise
    complet = {k: v for k, v in res.items() if k not in ("net", "brut", "passif")}
    mens = pd.concat([res["brut"], res["net"], res["passif"]], axis=1, keys=["brut", "net", "passif"])
    with SORTIE.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("--- resultats ---\n" + TM.rapport(res) + "\n")
        fh.write("Decision prevue d'avance : %s\n\n" % decision(res["score"]))
        fh.write("--- resultats complets ---\n"
                 + json.dumps(complet, indent=1, ensure_ascii=False, default=TT._json) + "\n\n")
        fh.write("--- rendements mensuels (brut : strategie avant couts FTMO ; net : apres ;"
                 " passif : toujours acheteur, meme dosage) ---\n")
        fh.write("mois,brut,net,passif\n")
        for mois, l in mens.iterrows():
            fh.write("%s,%.8f,%.8f,%.8f\n" % (mois, l["brut"], l["net"], l["passif"]))
    texte = PROTOCOLE.read_bytes().decode("utf-8")
    jour = maintenant.strftime("%d/%m/%Y")
    PROTOCOLE.write_bytes(remplir_protocole(texte, res, jour).encode("utf-8"))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lancer", action="store_true", help="lancer le test (une seule fois)")
    args = ap.parse_args(argv)
    os.chdir(RACINE)
    etat = verifier()
    if etat["problemes"]:
        print("PAS PRET :")
        for p in etat["problemes"]:
            print("  - " + p)
        return 1 if etat["deja_lance"] else 2
    print("PRET : les %d indices (%s), empreintes et couts conformes ; commit %s."
          % (len(TM.INDICES), " ".join(TM.INDICES), etat["commit"][:7]))
    if not args.lancer:
        print("Rien n'a ete lance. Pour lancer LE test : python scripts/tester_tournant.py --lancer")
        return 0
    print()
    print("Le test ne tourne qu'une fois : il ne pourra pas etre relance avec d'autres reglages.")
    if input("Tape %s pour le lancer : " % CONFIRMATION).strip() != CONFIRMATION:
        print("Abandon : rien n'a ete lance.")
        return 3
    print("Calcul en cours (quelques secondes)...")
    res = lancer(etat)
    print()
    print(TM.rapport(res))
    print()
    print("Decision prevue d'avance : " + decision(res["score"]))
    print()
    print("Sortie brute : %s ; colonne \"resultat\" du protocole remplie." % SORTIE.relative_to(RACINE))
    print("A committer et pousser tels quels :")
    print("  git add %s %s" % (SORTIE.relative_to(RACINE).as_posix(), PROTOCOLE.relative_to(RACINE).as_posix()))
    print("  git commit -m \"tournant du mois, etape 4 : le test, %d sur 5\"" % res["score"])
    print("  git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
