#!/usr/bin/env python3
"""Etape 4 du protocole de tendance : LE test, une seule fois.

    python scripts/tester_tendance.py            # verifie que tout est pret ; ne lance rien
    python scripts/tester_tendance.py --lancer   # verifie, demande confirmation, lance le test

Avant de lancer quoi que ce soit, le script verifie les points suivants. Au
moindre ecart, il refuse :
  1. le test n'a jamais tourne : pas de sortie brute ;
  2. le protocole a ses tableaux des couts (etape 1) et des donnees (etape 2) ;
  3. chaque fichier brut utilise a l'empreinte SHA-256 consignee dans le protocole ;
  4. les couts couvrent chaque instrument retenu, en fractions plausibles ;
  5. le protocole, les couts et le code du test sont commites, sans
     modification en cours, et le commit est pousse : sa date sur GitHub fait foi.
Aucune de ces verifications ne calcule un resultat de la strategie.

Ensuite, le script ecrit l'en-tete de la sortie brute AVANT de lancer le
calcul. A partir de la, le test compte comme lance. S'il plante, la trace de
l'erreur reste dans la sortie. Le relancer demande d'effacer ce fichier a la
main, ce que le protocole oblige a consigner.

A la fin, il ecrit les resultats dans la sortie brute. Il remplit aussi la
colonne "resultat" du protocole et la date de l'etape 4. Il reste a committer
ces deux fichiers.

Les couts viennent de docs/tendance_couts.csv, ecrit a l'etape 1 : une ligne
par instrument, colonnes instrument, transaction (fraction du nominal
echange : spread entier + commission), marge_achat et marge_vente (fractions
par an, jamais negatives).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

import pandas as pd

from quantbot import tendance as T
from quantbot import tendance_donnees as D

RACINE = Path(__file__).resolve().parent.parent
PROTOCOLE = RACINE / "docs" / "protocole_tendance_multi_actifs.md"
COUTS = RACINE / "docs" / "tendance_couts.csv"
SORTIE = RACINE / "docs" / "tendance_sortie_brute.txt"
BRUT = RACINE / D.BRUT
#: Ce qui doit etre commite tel quel avant le test.
SUIVIS = [PROTOCOLE, COUTS, RACINE / "src" / "quantbot" / "tendance.py",
          RACINE / "src" / "quantbot" / "tendance_donnees.py", Path(__file__).resolve()]
TRANSACTION_MAX = 0.01        # 1 % du nominal par transaction : au-dela, une erreur d'unite
MARGE_MAX = 0.50              # 50 % par an : idem
CONFIRMATION = "LANCER"

ENTETE_DONNEES = "| fichier brut | empreinte SHA-256 | exclusion éventuelle et raison |"
LIGNE_DONNEES = re.compile(r"^\| `([^`]+)` \| (?:`([0-9a-f]{64})`|—) \| (.*) \|$")
EXCLU = re.compile(r"^(\w+), source principale : \*\*exclu\*\*")
VIDE_ETAPE_1 = "| *(étape 1)* |"
VIDE_ETAPE_2 = "| *(étape 2)* |"
CRITERES = {"c1": "| 1 | pouvoir prédictif |", "c2": "| 2 | profil par groupes |",
            "c3": "| 3 | mieux que l'exposition passive |", "c4": "| 4 | test du hasard |",
            "c5": "| 5 | rendement net des coûts FTMO |"}
ETAPE_4 = "| 4 | lancer le test **une seule fois**"


# -- verifications : rien n'est calcule ici -------------------------------------------
def tableau_donnees(texte: str) -> dict:
    """Le tableau de l'etape 2 : fichier -> (empreinte ou None, verdict)."""
    lignes = texte.splitlines()
    try:
        k = next(i for i, l in enumerate(lignes) if l.strip() == ENTETE_DONNEES)
    except StopIteration:
        return {}
    out = {}
    for l in lignes[k + 2:]:
        if not l.startswith("|"):
            break
        m = LIGNE_DONNEES.match(l.strip())
        if m:
            out[m.group(1)] = (m.group(2), m.group(3))
    return out


def verifier_donnees(texte: str, brut: Path) -> tuple:
    """(retenus, exclus, problemes) d'apres le tableau de l'etape 2."""
    tableau = tableau_donnees(texte)
    if not tableau:
        return [], [], ["le tableau des donnees (etape 2) est absent du protocole"]
    exclus = [m.group(1) for _, v in tableau.items() for m in [EXCLU.match(v[1])] if m]
    problemes = ["instrument exclu inconnu : %s" % n for n in exclus if n not in T.INSTRUMENTS]
    retenus = [n for n in T.INSTRUMENTS if n not in exclus]
    attendus = ([D.nom_fichier(D.SERIES[n][0]) for n in retenus]
                + [D.nom_fichier(s) for s in D.TAUX.values()])
    for f in attendus:
        sha = tableau.get(f, (None, ""))[0]
        if not sha:
            problemes.append("%s : pas d'empreinte dans le protocole" % f)
        elif not (brut / f).exists():
            problemes.append("%s : fichier absent de %s" % (f, brut))
        elif D.sha256(brut / f) != sha:
            problemes.append("%s : l'empreinte ne correspond plus a celle du protocole" % f)
    return retenus, exclus, problemes


def lire_couts(chemin: Path, retenus: list) -> tuple:
    """(couts, problemes). Fractions finies, positives et plausibles."""
    if not chemin.exists():
        return {}, ["%s n'existe pas : les couts de l'etape 1 ne sont pas consignes" % chemin.name]
    with chemin.open(encoding="utf-8", newline="") as fh:
        lignes = {(l.get("instrument") or "").strip(): l for l in csv.DictReader(fh)}
    couts, problemes = {}, []
    for n in retenus:
        if n not in lignes:
            problemes.append("couts : %s manque" % n)
            continue
        try:
            c = {k: float(lignes[n][k]) for k in ("transaction", "marge_achat", "marge_vente")}
        except (KeyError, TypeError, ValueError):
            problemes.append("couts : la ligne de %s est illisible" % n)
            continue
        if (not all(math.isfinite(v) and v >= 0.0 for v in c.values())
                or c["transaction"] >= TRANSACTION_MAX
                or max(c["marge_achat"], c["marge_vente"]) >= MARGE_MAX):
            problemes.append("couts : %s hors des bornes plausibles %s" % (n, c))
            continue
        couts[n] = c
    return couts, problemes


def _git(racine: Path, *args) -> subprocess.CompletedProcess:
    # --no-optional-locks : une simple lecture ne doit laisser aucun verrou.
    return subprocess.run(["git", "--no-optional-locks", *args], cwd=racine,
                          capture_output=True, text=True)


def verifier_git(racine: Path = None, suivis: list = None) -> tuple:
    """(commit, problemes) : les entrees du test sont-elles commitees et poussees ?"""
    racine = racine or RACINE
    suivis = [str(Path(p).resolve().relative_to(Path(racine).resolve())).replace("\\", "/")
              for p in (suivis or SUIVIS)]
    try:
        tete = _git(racine, "rev-parse", "HEAD")
    except FileNotFoundError:
        return "", ["git introuvable : rien ne prouve que les entrees du test sont commitees"]
    if tete.returncode != 0:
        return "", ["pas de commit lisible dans %s" % racine]
    problemes = ["%s n'est pas commite" % f for f in suivis
                 if _git(racine, "ls-files", "--error-unmatch", f).returncode != 0]
    sale = _git(racine, "status", "--porcelain", "--", *suivis).stdout.strip()
    if sale:
        problemes.append("modifications non commitees : %s" % " ; ".join(sale.splitlines()))
    if not _git(racine, "branch", "-r", "--contains", "HEAD").stdout.strip():
        problemes.append("le dernier commit n'est pas pousse : faire git push (sa date sur GitHub fait foi)")
    return tete.stdout.strip(), problemes


def verifier() -> dict:
    """Tout ce qui doit etre vrai avant le test. Ne calcule aucun resultat."""
    if SORTIE.exists():
        return {"problemes": ["le test a deja tourne : %s existe. Le protocole interdit de le relancer."
                              % SORTIE.name], "deja_lance": True}
    texte = PROTOCOLE.read_text(encoding="utf-8")
    problemes = []
    if VIDE_ETAPE_1 in texte:
        problemes.append("le tableau des couts du protocole (etape 1) n'est pas rempli")
    if VIDE_ETAPE_2 in texte:
        problemes.append("le tableau des donnees du protocole (etape 2) n'est pas rempli")
    retenus, exclus, p = verifier_donnees(texte, BRUT)
    problemes += p
    couts, p = lire_couts(COUTS, retenus)
    problemes += p
    try:
        remplir_protocole(texte, _res_factice(), "01/01/2000")      # a vide : les cases existent ?
    except ValueError as exc:
        problemes.append(str(exc))
    commit, p = verifier_git()
    problemes += p
    return {"problemes": problemes, "deja_lance": False, "retenus": retenus, "exclus": exclus,
            "couts": couts, "commit": commit}


# -- le test ------------------------------------------------------------------------------
def _res_factice() -> dict:
    """Un resultat vide, pour verifier a l'avance que le protocole a ses cases."""
    return {"score": 0,
            "c1": {"pente": 0.0, "t": 0.0, "ok": False},
            "c2": {"moyennes": [0.0] * T.QUINTILES, "inversions": 0, "ecart": 0.0, "t": 0.0, "ok": False},
            "c3": {"alpha_mensuel": 0.0, "t": 0.0, "beta": 0.0, "ok": False},
            "c4": {"sharpe": 0.0, "au_moins_aussi_bons": 0, "tirages": T.TIRAGES, "p": 1.0, "ok": False},
            "c5": {"moyenne_mensuelle": 0.0, "t": 0.0, "mois": 0, "moyenne_2013": 0.0, "ok": False}}


def _json(o):
    return o.item() if hasattr(o, "item") else str(o)


def _n(x: float, d: int = 2) -> str:
    return ("%.*f" % (d, x)).replace(".", ",").replace("-", "−")


def cellules(res: dict) -> dict:
    """La colonne "resultat" du protocole, critere par critere."""
    def v(ok):
        return "**oui**" if ok else "non"
    c1, c2, c3, c4, c5 = (res[k] for k in ("c1", "c2", "c3", "c4", "c5"))
    return {
        "c1": "pente %s, t = %s : %s" % (_n(c1["pente"], 4), _n(c1["t"]), v(c1["ok"])),
        "c2": "groupes %s ; %d inversion(s) ; 5 − 1 = %s, t = %s : %s"
              % (" / ".join(_n(m, 3) for m in c2["moyennes"]), c2["inversions"],
                 _n(c2["ecart"], 3), _n(c2["t"]), v(c2["ok"])),
        "c3": "alpha %s %% par mois, t = %s (bêta %s) : %s"
              % (_n(100 * c3["alpha_mensuel"], 3), _n(c3["t"]), _n(c3["beta"]), v(c3["ok"])),
        "c4": "Sharpe net %s ; %d tirages sur %d au moins aussi bons ; p = %s : %s"
              % (_n(c4["sharpe"]), c4["au_moins_aussi_bons"], c4["tirages"], _n(c4["p"], 4), v(c4["ok"])),
        "c5": "%s %% par mois, t = %s sur %d mois ; 2013-2026 : %s %% par mois : %s"
              % (_n(100 * c5["moyenne_mensuelle"], 3), _n(c5["t"]), c5["mois"],
                 _n(100 * c5["moyenne_2013"], 3), v(c5["ok"])),
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
        return ("5 sur 5 : l'avantage est demontre au standard du projet. On passe a "
                "\"Apres le test\", toujours sans payer.")
    if score == 0:
        return "0 sur 5 : aucun paiement."
    return ("%d sur 5 : aucun paiement. On consigne lequel a echoue et pourquoi. "
            "Cette idee n'est pas retouchee." % score)


def lancer(etat: dict, maintenant: datetime = None) -> dict:
    """Le test, une seule fois. L'en-tete est ecrit AVANT le calcul."""
    maintenant = maintenant or datetime.now(timezone.utc)
    entete = [
        "Test de la tendance multi-actifs - sortie brute",
        "Etape 4 de docs/protocole_tendance_multi_actifs.md, lancee une seule fois.",
        "Lance le %s, sur le commit %s." % (maintenant.strftime("%Y-%m-%d %H:%M:%S UTC"), etat["commit"]),
        "Fichiers bruts : empreintes conformes au protocole.",
        "Instruments retenus (%d) : %s" % (len(etat["retenus"]), ", ".join(etat["retenus"])),
        "Exclus a l'etape 2 : %s" % (", ".join(etat["exclus"]) or "aucun"),
        "Tirages du critere 4 : %d, graine %d." % (T.TIRAGES, T.GRAINE),
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
        prix, taux = D.charger(etat["exclus"], dossier=BRUT)
        res = T.tester(prix, taux, etat["couts"])
    except Exception:
        with SORTIE.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("ECHEC DU CALCUL :\n" + traceback.format_exc())
        raise
    complet = {k: v for k, v in res.items() if k not in ("net", "brut")}
    mens = pd.concat([res["brut"], res["net"]], axis=1, keys=["brut", "net"])
    with SORTIE.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("--- resultats ---\n" + T.rapport(res) + "\n")
        fh.write("Decision prevue d'avance : %s\n\n" % decision(res["score"]))
        fh.write("--- resultats complets ---\n" + json.dumps(complet, indent=1, ensure_ascii=False, default=_json) + "\n\n")
        fh.write("--- rendements mensuels de la strategie (brut : avant couts FTMO ; net : apres) ---\n")
        fh.write("mois,brut,net\n")
        for mois, l in mens.iterrows():
            fh.write("%s,%.8f,%.8f\n" % (mois, l["brut"], l["net"]))
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
    print("PRET : %d instruments retenus (%s), exclus : %s ; commit %s."
          % (len(etat["retenus"]), " ".join(etat["retenus"]), ", ".join(etat["exclus"]) or "aucun",
             etat["commit"][:7]))
    if not args.lancer:
        print("Rien n'a ete lance. Pour lancer LE test : python scripts/tester_tendance.py --lancer")
        return 0
    print()
    print("Le test ne tourne qu'une fois : il ne pourra pas etre relance avec d'autres reglages.")
    if input("Tape %s pour le lancer : " % CONFIRMATION).strip() != CONFIRMATION:
        print("Abandon : rien n'a ete lance.")
        return 3
    print("Calcul en cours (une minute environ)...")
    res = lancer(etat)
    print()
    print(T.rapport(res))
    print()
    print("Decision prevue d'avance : " + decision(res["score"]))
    print()
    print("Sortie brute : %s ; colonne \"resultat\" du protocole remplie." % SORTIE.relative_to(RACINE))
    print("A committer et pousser tels quels :")
    print("  git add %s %s" % (SORTIE.relative_to(RACINE).as_posix(), PROTOCOLE.relative_to(RACINE).as_posix()))
    print("  git commit -m \"tendance multi-actifs, etape 4 : le test, %d sur 5\"" % res["score"])
    print("  git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
