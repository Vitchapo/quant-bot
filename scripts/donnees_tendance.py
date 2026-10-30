#!/usr/bin/env python3
"""Etape 2 du protocole de tendance : recupere et controle les donnees.

    python scripts/donnees_tendance.py              # recupere ce qui manque, puis controle
    python scripts/donnees_tendance.py --controle   # controle seulement

Ecrit dans data/tendance/ :
  brut/                 les fichiers tels que telecharges, jamais modifies
  manifeste.csv         source, adresse, date, taille et empreinte SHA-256 de chacun
  controle.txt          le compte rendu des controles du protocole
  tableau_protocole.md  le tableau de l'etape 2, pret a recopier dans le protocole

Stooq se telecharge A LA MAIN. Depuis mars 2026, il demande une cle et
verifie le navigateur ; un script n'a pas a forcer ces barrages. Le script
indique la page a ouvrir. On y telecharge le CSV (lien en bas de page) et on
le depose dans data/tendance/a_importer/. Au passage suivant, le script le
recopie dans brut/ octet pour octet et l'inscrit au manifeste.

Un fichier deja present n'est JAMAIS retelecharge : son empreinte va dans le
protocole avant le test, et le test refuse de tourner si elle change.

Ce script ne calcule aucun signal ni aucun rendement de strategie. Le premier
calcul sur donnees reelles est le test lui-meme (etape 4).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import tendance_donnees as D

RACINE = Path(__file__).resolve().parent.parent
A_IMPORTER = D.DOSSIER / "a_importer"
CHAMPS = ["fichier", "role", "instrument", "source", "code", "adresse", "telecharge_utc",
          "octets", "sha256", "statut"]


def telecharger(adresse: str, source: str, essais: int = 4) -> bytes:
    requete = urllib.request.Request(adresse, headers={"User-Agent": D.entete(source)})
    attente = 5.0
    for k in range(essais):
        try:
            with urllib.request.urlopen(requete, timeout=60) as rep:
                return rep.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and k + 1 < essais:
                time.sleep(attente)
                attente *= 2
                continue
            raise
    raise RuntimeError("echec apres %d essais" % essais)


def taches():
    for nom, (princ, repli) in D.SERIES.items():
        yield ("principale", nom, princ)
        yield ("repli", nom, repli)
    for code, src in D.TAUX.items():
        yield ("taux", code, src)


# -- Stooq, a la main ------------------------------------------------------------
def candidats(src, depot=None) -> list:
    """Les fichiers deposes a la main qui correspondent a cette serie Stooq.

    Stooq nomme ses fichiers d'apres la serie ("xauusd_d.csv", "^nkx_d.csv") ;
    un navigateur ajoute " (1)" a un second telechargement du meme fichier.
    """
    depot = Path(depot) if depot is not None else A_IMPORTER
    if not depot.is_dir():
        return []
    code = src[1].lower().lstrip("^")
    motif = re.compile(r"\^?%s(?:[_ .(-]|$)" % re.escape(code))
    return sorted(f for f in depot.iterdir()
                  if f.is_file() and f.suffix.lower() == ".csv" and motif.match(f.name.lower()))


def importer(src, chemin: Path, depot=None) -> dict:
    """Recopie dans brut/, octet pour octet, le fichier Stooq depose a la main.

    Leve FileNotFoundError si rien n'est depose, ValueError si le depot est
    ambigu ou si le fichier n'est pas un CSV de cours (la page de
    verification de Stooq, par exemple). Le fichier depose n'est pas efface.
    """
    trouves = candidats(src, depot)
    if not trouves:
        raise FileNotFoundError(D.page_stooq(src))
    if len(trouves) > 1:
        raise ValueError("plusieurs fichiers deposes pour %s (%s) : n'en garder qu'un"
                         % (src[1], ", ".join(f.name for f in trouves)))
    f = trouves[0]
    s = D.lire(f, "stooq")
    if not len(s):
        raise ValueError("%s ne contient aucune cotation" % f.name)
    if chemin.exists():
        raise FileExistsError("%s existe deja : un fichier brut ne se remplace pas" % chemin)
    brut = f.read_bytes()
    chemin.write_bytes(brut)
    depose = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
    return {"telecharge_utc": depose.strftime("%Y-%m-%dT%H:%M:%SZ"), "octets": len(brut),
            "sha256": D.sha256(chemin), "statut": "ok, telecharge a la main (%s)" % f.name}


def recuperer(manifeste: dict) -> dict:
    D.BRUT.mkdir(parents=True, exist_ok=True)
    for role, nom, src in taches():
        fichier = D.nom_fichier(src)
        chemin = D.BRUT / fichier
        ligne = {"fichier": fichier, "role": role, "instrument": nom, "source": src[0],
                 "code": src[1], "adresse": src[2]}
        if chemin.exists():
            if not manifeste.get(fichier, {}).get("sha256"):
                ligne.update(telecharge_utc="(present)", octets=chemin.stat().st_size,
                             sha256=D.sha256(chemin), statut="ok")
                manifeste[fichier] = ligne
            continue
        try:
            if src[0] == "stooq":
                ligne.update(importer(src, chemin))
                print("  ok     %-26s %8d octets, depose a la main" % (fichier, ligne["octets"]))
            else:
                brut = telecharger(src[2], src[0])
                chemin.write_bytes(brut)
                try:
                    D.lire(chemin, src[0])              # lisible ? sinon on ne garde pas
                except Exception as exc:
                    chemin.unlink()
                    raise ValueError("illisible : %s" % str(exc)[:120])
                ligne.update(telecharge_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             octets=len(brut), sha256=D.sha256(chemin), statut="ok")
                print("  ok     %-26s %8d octets" % (fichier, len(brut)))
                if src[0] == "yahoo":
                    time.sleep(2.0)                     # Yahoo limite le rythme
        except FileNotFoundError:
            ligne.update(telecharge_utc="", octets="", sha256="", statut="A TELECHARGER A LA MAIN")
            print("  ...    %-26s a telecharger a la main" % fichier)
        except Exception as exc:
            ligne.update(telecharge_utc="", octets="", sha256="",
                         statut="ECHEC %s" % str(exc)[:100].replace("\n", " "))
            print("  ECHEC  %-26s %s" % (fichier, str(exc)[:100]))
        manifeste[fichier] = ligne
    return manifeste


def ecrire_manifeste(manifeste: dict) -> None:
    with D.MANIFESTE.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CHAMPS)
        w.writeheader()
        for f in sorted(manifeste):
            w.writerow({k: manifeste[f].get(k, "") for k in CHAMPS})


# -- controles ---------------------------------------------------------------------
def controler() -> tuple:
    """Le compte rendu des controles, et le verdict de chaque instrument."""
    L = ["Controles de l'etape 2 - %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
         "Periode controlee : %s -> %s. Defaut non resolu par le repli = exclusion."
         % (D.DEBUT.date(), D.FIN.date()), ""]
    verdicts = {}
    for nom, (princ, repli) in D.SERIES.items():
        cp, cr = D.BRUT / D.nom_fichier(princ), D.BRUT / D.nom_fichier(repli)
        if not cp.exists():
            L.append("%-7s source principale ABSENTE (%s) : rien a controler"
                     % (nom, D.nom_fichier(princ)))
            verdicts[nom] = {"absent": True, "exclu": True, "defauts": []}
            continue
        p = D.lire(cp, princ[0])
        r = D.lire(cr, repli[0]) if cr.exists() else None
        c = D.controler(p, r)
        non = [x for x in c["defauts"] if not x["resolu"]]
        L.append("%-7s %s -> %s, %5d cotations ; repli %s ; %d defaut(s), %d non resolu(s)"
                 % (nom, c["debut"].date(), c["fin"].date(), c["n"],
                    "%s -> %s" % (r.index.min().date(), r.index.max().date()) if r is not None and len(r) else "ABSENT",
                    len(c["defauts"]), len(non)))
        for x in c["defauts"]:
            L.append("          [%s] %-6s %s" % ("ok " if x["resolu"] else "NON", x["type"], x["detail"]))
        verdicts[nom] = {"absent": False, "exclu": bool(non), "defauts": c["defauts"]}
    L.append("")
    for code, src in D.TAUX.items():
        c = D.BRUT / D.nom_fichier(src)
        if not c.exists():
            L.append("taux %-3s ABSENT" % code)
            continue
        t = D.lire(c, "bri")
        avant = t[t.index < D.DEBUT]
        t = t[(t.index >= D.DEBUT) & (t.index <= D.FIN)]
        tardif = "" if len(avant) else (
            "  (zone euro : l'Allemagne la precede jusqu'au 31/12/1998)" if code == "XM"
            else "  (COMMENCE APRES 1998)")
        L.append("taux %-3s %s -> %s, %d observations, de %.2f %% a %.2f %%%s"
                 % (code, t.index.min().date(), t.index.max().date(), len(t), t.min(), t.max(), tardif))
    if D.MANIFESTE.exists():
        changes = D.verifier_empreintes()
        L.append("")
        L.append("EMPREINTES DIFFERENTES DU MANIFESTE : %s" % ", ".join(changes) if changes
                 else "Empreintes : tous les fichiers presents correspondent au manifeste.")
    L.append("")
    exclus = [n + (" (donnees absentes)" if v["absent"] else "") for n, v in verdicts.items() if v["exclu"]]
    L.append("A EXCLURE AVANT LE TEST : %s" % (", ".join(exclus) if exclus else "aucun instrument"))
    return "\n".join(L), verdicts


# -- le tableau du protocole ----------------------------------------------------------
def _jour(t) -> str:
    return t.strftime("%d/%m/%Y")


def _pc(x: float) -> str:
    return ("%+.1f %%" % (100 * x)).replace(".", ",").replace("-", "−")


def decrire(x: dict) -> str:
    """Un defaut, en clair, suivi de ce qu'en dit la source de repli."""
    if x["type"] == "trou":
        texte = "trou du %s au %s (%d jours ouvrés sans cotation)" % (_jour(x["a"]), _jour(x["b"]), x["jours"])
    elif x["type"] == "fige":
        texte = "cours figé du %s au %s (%d cotations)" % (_jour(x["a"]), _jour(x["b"]), x["n"])
    elif x["type"] == "saut":
        texte = "saut du %s (%s, %.0f écarts-types)" % (_jour(x["date"]), _pc(x["rendement"]), x["sigmas"])
    elif x["type"] == "debut tardif":
        return "première cotation le %s, après janvier 2004" % _jour(x["date"])
    else:
        return "aucune cotation"
    if not x.get("repli"):
        return texte + ", pas de source de repli pour trancher"
    if x["type"] == "trou":
        return texte + (", le repli ne cote pas non plus : marché fermé" if x["resolu"]
                        else ", le repli cote pendant le trou")
    if x["type"] == "fige":
        return texte + (", le repli ne bouge pas non plus" if x["resolu"] else ", le repli bouge")
    if not x.get("mouvements_repli"):
        return texte + ", le repli ne couvre pas cette date"
    return texte + (", confirmé par le repli" if x["resolu"] else ", non confirmé par le repli")


def verdict(v: dict) -> str:
    if v["absent"]:
        return "**exclu** : fichier absent"
    non = [x for x in v["defauts"] if not x["resolu"]]
    if non:
        return "**exclu** : " + " ; ".join(decrire(x) for x in non)
    if not v["defauts"]:
        return "retenu, aucun défaut"
    return "retenu ; " + " ; ".join(decrire(x) for x in v["defauts"])


NOMS_TAUX = {"US": "américain ; sert aussi à Hong Kong (monnaie arrimée au dollar)",
             "XM": "de la zone euro, à partir de 1999",
             "DE": "allemand, pour 1998 (avant l'euro)",
             "GB": "britannique", "JP": "japonais", "CH": "suisse", "CA": "canadien",
             "AU": "australien", "NZ": "néo-zélandais"}


def tableau(verdicts: dict) -> str:
    """Le tableau de l'etape 2 : une ligne par fichier brut, avec son empreinte."""
    L = ["| fichier brut | empreinte SHA-256 | exclusion éventuelle et raison |", "|---|---|---|"]

    def ligne(src, texte):
        f = D.BRUT / D.nom_fichier(src)
        sha = "`%s`" % D.sha256(f) if f.exists() else "—"
        L.append("| `%s` | %s | %s |" % (f.name, sha, texte))

    for nom, (princ, repli) in D.SERIES.items():
        ligne(princ, "%s, source principale : %s" % (nom, verdict(verdicts[nom])))
        if (D.BRUT / D.nom_fichier(repli)).exists():
            ligne(repli, "%s, repli" % nom)
        elif not verdicts[nom]["absent"] and not verdicts[nom]["defauts"]:
            ligne(repli, "%s, repli : non téléchargé, aucun défaut à trancher" % nom)
        else:
            ligne(repli, "%s, repli : absent" % nom)
    for code, src in D.TAUX.items():
        ligne(src, "taux directeur %s" % NOMS_TAUX[code])
    return "\n".join(L)


def a_la_main(verdicts: dict) -> str:
    """Les fichiers Stooq qui manquent, avec la page ou les prendre."""
    lignes = []
    for role, nom, src in taches():
        if src[0] != "stooq" or (D.BRUT / D.nom_fichier(src)).exists():
            continue
        v = verdicts.get(nom, {})
        sans_repli = any(not x["resolu"] and not x.get("repli") for x in v.get("defauts", []))
        utile = ("indispensable" if role == "principale"
                 else "indispensable : un defaut attend le repli" if sans_repli
                 else "facultatif : aucun defaut a trancher")
        lignes.append("  %-7s %-10s %-38s %s" % (nom, role, D.page_stooq(src), utile))
    if not lignes:
        return ""
    return "\n".join([
        "Fichiers Stooq a telecharger A LA MAIN (Stooq demande une cle et verifie le navigateur) :",
        *lignes,
        "",
        "Pour chacun : ouvrir la page dans un navigateur ; en bas, cliquer \"Download data in",
        "csv file\" (\"Pobierz dane w pliku csv\" sur stooq.pl) ; si Stooq demande une cle ou une",
        "verification, la faire. Deposer le fichier tel quel, sans le renommer ni le modifier, dans",
        "  %s" % (RACINE / A_IMPORTER),
        "puis relancer ce script : il le recopie dans brut/ et le controle.",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--controle", action="store_true", help="controle seulement, sans rien recuperer")
    args = ap.parse_args()
    os.chdir(RACINE)
    manifeste = D.lire_manifeste() if D.MANIFESTE.exists() else {}
    if not args.controle:
        print("Recuperation (les fichiers deja presents sont gardes tels quels) :")
        manifeste = recuperer(manifeste)
        ecrire_manifeste(manifeste)
    texte, verdicts = controler()
    (D.DOSSIER / "controle.txt").write_text(texte + "\n", encoding="utf-8")
    (D.DOSSIER / "tableau_protocole.md").write_text(tableau(verdicts) + "\n", encoding="utf-8")
    print()
    print(texte)
    consigne = a_la_main(verdicts)
    if consigne:
        print()
        print(consigne)
    return 0


if __name__ == "__main__":
    sys.exit(main())
