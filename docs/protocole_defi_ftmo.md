# Protocole : à quelles conditions payer un défi FTMO

**Écrit le 22 septembre 2026, AVANT de connaître le résultat du paper trading.**

> Verdict accepté d'avance, quel qu'il soit : une hypothèse testée une fois ne
> se re-teste pas avec d'autres réglages jusqu'à ce qu'elle passe.
>
> — `src/quantbot/volatilite.py`, écrit par Nathan

Ce document existe pour une raison précise. Le 21 septembre 2026, après **une
séance** à +0,96 % sur le compte papier, la conclusion tirée était que le défi
passerait en deux mois au lieu de huit. L'écart entre cette conclusion et les
mesures du 21 septembre ne vient pas d'un désaccord sur les chiffres — il vient
de ce qu'un chiffre seul se lit toujours comme une tendance.

Les critères ci-dessous sont donc fixés **maintenant**, pendant qu'aucun
résultat n'est en jeu.

---

## L'offre visée

FTMO Challenge 2-Step, compte 100 000 $, **439 €** (remise de 19 % sur 540 €).

| règle | offre | config du bot | marge |
|---|---|---|---|
| objectif phase 1 | 10 % | 10 % | identique |
| objectif phase 2 | 5 % | 5 % | identique |
| perte journalière | 5 % | **4 %** | 1 point |
| perte maximale | 10 % statique | **8 %** | 2 points |
| jours minimum | 4 | — | — |
| durée | **illimitée** | — | — |
| EA autorisés | oui | — | — |

La config `config/ftmo.yaml` est déjà calibrée sur ces règles. **La conformité
aux règles n'a jamais été le point bloquant** : un bot peut satisfaire les cinq
lignes de cette carte et perdre quand même. Les règles disent quand on est
éliminé, pas si on gagne.

Note : **« illimitée »**. Le défi n'expire pas. Acheter en janvier coûte la même
chose qu'acheter demain. La seule chose qui expire est la remise de 19 % —
soit 101 € d'économie, à mettre en face d'un pari dont la probabilité est
calculée plus bas.

---

## Les cinq critères

Tous viennent du harnais de validation du projet, et tous se recalculent par
une commande :

```bash
python scripts/check_edge.py --config config/us.yaml --draws 300
python scripts/mesurer_biais.py --config config/us.yaml
```

| # | critère | seuil | mesure du 21/09/2026 | |
|---|---|---|---|---|
| 1 | IC du composite | \|t\| > 2 | **t = −0,51** | ✗ |
| 2 | profil de déciles | croissant | **décroissant** (décile 1 : +0,220 %/mois, t = 3,37) | ✗ |
| 3 | score inversé | nettement pire que le normal | **meilleur de 1,69 point de CAGR** | ✗ |
| 4 | null aléatoire | p < 0,05 **et** résultat différent sur le score inversé | **identique des deux côtés** (p = 0,05 / 0,050) | ✗ |
| 5 | CAGR corrigé du survivant | positif, hors du bruit | **13,67 − 13,22 = +0,45 %** | ✗ |

**Score au 22 septembre 2026 : 0 sur 5.**

Le seuil de 2 sur l'IC n'est pas arbitraire : il est écrit dans le README du
projet — « un IC moyen de 0,02 avec un t de 2 est un vrai signal faible ; un IC
de 0,001 avec un t de 0,05 est du bruit ».

### Pourquoi le critère 4 est formulé ainsi

Le null aléatoire seul donne p = 0,05, ce qui se lirait comme une victoire. Mais
relancé sur le score **inversé**, il donne exactement le même résultat. Il
mesure donc la mécanique de construction — concentrer sur un axe corrélé à la
volatilité puis pondérer en inverse-vol — et non le contenu informatif du score.

C'est le test qui aurait fait conclure à tort. Le critère exige donc les deux
moitiés.

---

## Décision prévue d'avance

**5 sur 5** → l'avantage est démontré au standard que le projet s'est fixé. Le
défi devient un pari défendable, et il reste à en calculer l'espérance.

**Entre 1 et 4** → on regarde lequel a bougé et pourquoi. Aucun paiement.

**0 sur 5** → aucun paiement. Ce qui doit changer est le **score**, pas la
plateforme ni la date.

---

## Ce qui NE compte pas comme critère

**Le résultat du paper trading.** Il n'a aucun pouvoir statistique sur cette
question, et l'inclure reviendrait à valider le raisonnement que ce document
existe pour empêcher.

| durée | bande de bruit du rendement (95 %) |
|---|---|
| 1 séance | ± 1,6 % |
| 1 semaine | ± 3,5 % |
| 6 semaines | **± 8,6 %** |
| 6 mois | ± 17,7 % |

Pour conclure à 95 % en six semaines, il faudrait finir **au-dessus de +8,6 %**,
soit 100 % annualisé. Ce serait un signal d'alarme — aucune stratégie actions ne
fait ça — pas une bonne nouvelle.

Durée nécessaire pour distinguer un avantage du bruit, selon le Sharpe réel :

| Sharpe | durée |
|---|---|
| 0,5 | 16 ans |
| 1,0 | 4 ans |
| 2,0 | 1 an |

**Aucune durée de paper trading raisonnable ne peut valider cette stratégie.**
Le paper trading valide la machine. C'est l'objet de l'autre protocole.

---

## L'espérance, si les critères passaient un jour

Sur 20 000 simulations, dérive 12,7 %/an (elle-même biaisée de +13,2 points),
volatilité 12,5 %, garde-fous 4 %/8 %, minimum 4 jours par phase :

| | |
|---|---|
| P(passer les deux phases) | 53,1 % |
| durée médiane si ça passe | **37 semaines (8,5 mois)** |
| P(fini en 2 mois) | **0,1 %** |
| P(fini en 3 mois) | 1,5 % |
| P(fini en 6 mois) | 14,7 % |

Le plancher mécanique est de **8 semaines** : 4 jours de négociation par phase,
à un rebalancement hebdomadaire.

Et le plafond de gain, dans l'hypothèse la plus généreuse — CAGR 12,7 %, partage
90 % — est de **952 $/mois**. Avec la correction du biais du survivant, il
devient négatif.

---

## Recalculer ce document

```bash
python scripts/check_edge.py --config config/us.yaml --draws 300 > docs/check_edge_$(date +%F).log
python scripts/mesurer_biais.py --config config/us.yaml
```

Puis reporter les cinq lignes du tableau et redater. Si un critère passe, il
passe ; s'il ne passe pas, on ne change pas le critère.
