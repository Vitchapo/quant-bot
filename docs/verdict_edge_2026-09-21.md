# Verdict : le score composite contient-il de l'information ?

**Date de la mesure :** 21 septembre 2026
**Univers :** 505 séries, `data/prices/us`, S&P 500 réel (pas la liste de repli de 64 titres)
**Période :** 2005-01-03 → 2026-09-18, 1112 dates de signal, rebalancement hebdomadaire
**Commandes :** `scripts/check_edge.py --config config/us.yaml` et `scripts/mesurer_biais.py`

> Ce document est l'étape 0 de la « Suite logique » du README :
> « Vérifier qu'il y a un signal avant tout le reste. Cette étape coûte deux
> minutes et fait gagner des mois. »
>
> **Réponse : non.** Le détail ci-dessous.

---

## 1. Information coefficient

Critère fixé dans le README : « Un IC moyen de 0,02 avec un t de 2 est un vrai
signal faible ; un IC de 0,001 avec un t de 0,05 est du bruit. »

| facteur | IC moyen | IC médian | t | p | % > 0 | n |
|---|---|---|---|---|---|---|
| momentum 252-21 | +0,0055 | +0,0132 | 0,84 | 0,402 | 52,0 % | 1080 |
| faible volatilité | −0,0150 | −0,0220 | **−2,05** | 0,041 | 45,9 % | 1126 |
| tendance MM200 | −0,0095 | +0,0006 | −1,56 | 0,119 | 50,2 % | 1112 |
| **COMPOSITE** | **−0,0033** | +0,0037 | **−0,51** | 0,609 | 50,8 % | 1112 |

Le composite est à t = −0,51 contre une barre à 2. C'est du bruit, et
légèrement du mauvais côté de zéro.

**Le seul facteur significatif l'est à l'envers.** `faible volatilite` atteint
t = −2,05, mais avec un IC *négatif* : sur cet univers et cette période, la
faible volatilité prédit des rendements plus **faibles**. Or `config/us.yaml`
lui donne `weight: 0.5` positif — « poids positif = on PRÉFÈRE la faible
volatilité ». L'inclinaison est prise dans le mauvais sens.

## 2. Décomposition

| étape | CAGR | Sharpe | perte max | Δ CAGR | Δ Sharpe |
|---|---|---|---|---|---|
| indice de référence (SPY) | 10,87 % | 0,54 | −55,19 % | — | — |
| univers entier, équipondéré | 14,81 % | 0,69 | −51,56 % | +3,95 | +0,16 |
| + sélection factorielle (top 20) | 21,30 % | 0,82 | −57,47 % | +6,48 | +0,13 |
| + pondération inv_vol | 16,71 % | 0,71 | −59,29 % | **−4,59** | −0,11 |
| + filtre de régime (complète) | 13,67 % | 0,69 | **−29,63 %** | −3,04 | −0,02 |

Lue seule, la ligne « sélection factorielle » suggère +6,48 points. Les
sections 3 et 4 montrent que c'est un artefact.

Deux constats qui tiennent, eux :
- la **stratégie complète fait moins bien que détenir tout l'univers** (13,67 %
  contre 14,81 %) à Sharpe identique (0,69) ;
- le **filtre de régime est réel** : il ramène la perte maximale de −51,6 % à
  −29,6 %. C'est du market timing, pas de la sélection de titres.

## 3. Profil de déciles

Rendement mensuel moyen par décile, en écart à l'univers entier (0,330 %/mois).

| décile | rdt/mois | écart | t |
|---|---|---|---|
| 1 (**pires** scores) | 0,550 % | **+0,220 %** | **3,37** |
| 2 | 0,374 % | +0,044 % | 1,36 |
| 3 | 0,374 % | +0,043 % | 1,95 |
| 4 | 0,312 % | −0,018 % | −1,14 |
| 5 | 0,310 % | −0,021 % | −1,30 |
| 6 | 0,301 % | −0,029 % | −1,70 |
| 7 | 0,288 % | −0,042 % | −2,03 |
| 8 | 0,257 % | −0,073 % | −3,12 |
| 9 | 0,232 % | −0,099 % | **−3,37** |
| 10 (**meilleurs** scores) | 0,304 % | −0,026 % | −0,58 |
| les 20 meilleurs | 0,390 % | +0,060 % | 0,94 |
| les 20 pires | 0,648 % | **+0,317 %** | **3,37** |

Écart meilleurs − pires : **−0,257 %/mois, t = −1,90, p = 0,058**.

Ce n'est pas le profil « en U » que le README anticipait. C'est un profil
**décroissant** : le classement est ordonné à l'envers sur neuf déciles sur
dix, avec une légère remontée au dernier. Le décile des meilleurs scores n'est
pas distinguable de l'univers (t = −0,58) ; celui des pires le bat nettement
(t = 3,37).

## 4. Score inversé

| | CAGR | Sharpe | perte max |
|---|---|---|---|
| score normal (meilleurs) | 13,67 % | 0,69 | −29,63 % |
| score **INVERSÉ** (pires) | **15,36 %** | 0,69 | −38,41 % |
| univers entier, sans sélection | 14,81 % | 0,69 | −51,56 % |

Acheter les pires scores rapporte **1,69 point de plus** que d'acheter les
meilleurs, à Sharpe identique. Un score porteur d'information s'effondre quand
on l'inverse. Celui-ci fait mieux.

## 5. Null aléatoire — et pourquoi il ne sauve rien

On remplace le score par du bruit de même persistance de rang (ρ = 0,975) et on
relance le backtest complet, 60 tirages.

| score utilisé | Sharpe réel | z | p(hasard fait mieux) | centile |
|---|---|---|---|---|
| normal | 0,690 | 1,92 | 0,050 | 95 |
| **inversé** | 0,687 | 1,86 | **0,050** | **95** |

Distribution des tirages : Sharpe moyen 0,533, écart-type 0,083.

Pris seul, le premier résultat se lirait comme une victoire : la stratégie bat
95 % des scores aléatoires. **Mais son inverse exact obtient le même score.**

Le null ne mesure donc pas le contenu informatif du score, il mesure la
**mécanique de construction** : concentrer sur 20 lignes choisies le long d'un
axe corrélé à la volatilité, puis pondérer en inverse-vol, bat un tirage au
hasard — quel que soit le sens dans lequel on lit l'axe. C'est un effet de
construction de portefeuille, pas un avantage de sélection.

C'est le test qui aurait pu faire conclure à tort, et c'est le fait de l'avoir
relancé sur le score inversé qui le désamorce.

## 6. Biais du survivant

`mesurer_biais.py`, fenêtre 2020-09-21 → 2026-09-18 (6 ans, chauffe complète
depuis 2005), appartenance à l'indice réellement connue sur cette période.

| | liste 2026 | liste d'époque | écart |
|---|---|---|---|
| rendement annualisé | 26,58 % | 13,36 % | **−13,22 pt** |
| volatilité | 23,00 % | 19,82 % | −3,18 pt |
| Sharpe | 1,15 | 0,74 | −0,41 |
| perte maximale | −19,80 % | −19,80 % | 0,00 pt |
| SPY sur la période | 16,68 % | | |

**D'où vient l'écart :** 20 % du capital en moyenne était investi dans des
titres **pas encore entrés dans l'indice** — jusqu'à 49 % des lignes en 2020.
Les plus détenus avant leur entrée : CVNA (565 séances), APP (435), FIX (421),
VRT (396), SMCI (283), CRWD (273), HOOD (224).

Autrement dit, la stratégie achetait les plus grands gagnants de 2020-2026
avant que quiconque ne sache qu'ils le deviendraient. Ce chiffre reste
**optimiste** : les sociétés radiées n'ont aucune série dans le cache, donc
leurs rendements — le plus souvent très négatifs — manquent toujours.

---

## Conclusion

Les cinq mesures convergent :

1. l'IC du composite est nul (t = −0,51), et son seul facteur significatif est
   pris à l'envers ;
2. le profil de déciles est décroissant : le classement est inversé ;
3. le score inversé fait mieux que le score normal ;
4. le null aléatoire donne le même résultat au score et à son inverse, donc il
   mesure la mécanique et non le signal ;
5. l'univers coûte 13,2 points de rendement annuel en information du futur.

**Il n'y a pas d'avantage de sélection dans ce score.** Ce qui reste, et qui est
réel, c'est le filtre de régime : il divise presque par deux la perte maximale.
C'est une couverture de marché, pas un classement de titres, et elle ne
nécessite ni les trois facteurs ni les 505 séries.

### Ce que cela implique pour la suite

- Les étapes 2 à 4 de la « Suite logique » (paper trading, données
  point-in-time, machine learning) n'ont pas d'objet en l'état. Le README le
  dit déjà : « Tant que l'étape 1 n'est pas franchie, le ML n'a rien à
  apprendre : il ne créera pas un avantage à partir de rien. »
- Aucun défi de prop firm n'est envisageable : il n'y a rien à mettre en
  levier, et la perte maximale mesurée (−29,6 %) dépasse de deux à cinq fois
  les seuils d'élimination du secteur (−6 % à −10 %).
- Les pistes qui restent ouvertes portent sur le **score**, pas sur
  l'infrastructure : retourner le signe de `low_volatility`, chercher des
  facteurs hors prix (valorisation, qualité, révisions de bénéfices), ou
  acheter des données point-in-time avant de chercher quoi que ce soit.

### Ce qui n'est pas en cause

L'infrastructure a fait exactement son travail. L'IC, la décomposition, le
profil de déciles, le score inversé et le null aléatoire se sont contredits
entre eux, et c'est en les croisant que le verdict est devenu net — un seul de
ces tests, lu seul, menait à la mauvaise conclusion. La chronologie, les
garde-fous, le walk-forward et les 381 tests restent valables et réutilisables
sur n'importe quel autre signal.
