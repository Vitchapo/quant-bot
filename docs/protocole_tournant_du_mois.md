# Protocole : le tournant du mois sur les indices FTMO

**Écrit le 6 novembre 2026, AVANT tout calcul de la stratégie sur données réelles.**
**Brouillon tant qu'il n'est pas commité : la date du commit fait foi.**

> Verdict accepté d'avance, quel qu'il soit : une hypothèse testée une fois ne
> se re-teste pas avec d'autres réglages jusqu'à ce qu'elle passe.
>
> — `src/quantbot/volatilite.py`, écrit par Nathan

Deux idées ont déjà été testées dans ce projet. Le score actions a obtenu
**0 sur 5** (`docs/protocole_defi_ftmo.md`), la tendance multi-actifs
**1 sur 5** (`docs/protocole_tendance_multi_actifs.md`). Comme ce dernier
protocole l'a fixé, toute idée testée après lui a son propre protocole et un
seuil de **t > 2,5**. Ce document fixe, avant de la tester, une troisième
idée, la façon exacte de la tester et ce qu'on fera du résultat.

Après le commit, seules changent les cases prévues pour être remplies : les
résultats (étape 4) et la décision (étape 5), chacun par un commit daté.

---

## L'hypothèse

**Les indices boursiers montent davantage au tournant du mois : le dernier
jour de cotation d'un mois et les trois premiers du mois suivant.** On achète
chaque indice à la clôture de l'avant-dernier jour du mois, on revend à la
clôture du troisième jour du mois suivant, et on reste sans position le reste
du temps.

### Pourquoi elle devrait marcher

- **Un effet ancien et répandu.** Lakonishok et Smidt (1988) le trouvent sur
  90 ans de Dow Jones. McConnell et Xu (2008) concluent que, de 1926 à 2005,
  les actions américaines n'ont en moyenne rien rapporté au-delà du taux sans
  risque *en dehors* du tournant du mois, et retrouvent l'effet dans 31 des
  35 pays étudiés.
- **Une raison avancée : les flux d'argent de fin de mois** (salaires,
  versements des fonds de pension, réinvestissements). McConnell et Xu ne la
  confirment pas avec leurs mesures de flux, et parlent d'une énigme.
- **Elle colle aux coûts FTMO.** Environ 4 séances d'exposition par mois : 2
  transactions et 5 à 7 nuits de swap par mois, contre un mois entier pour la
  tendance, qui a échoué sur ces coûts.

### Ce qui peut la faire échouer

- **L'usure.** Une étude récente (QuantSeeker, 9 février 2025) trouve que la
  fenêtre classique s'est presque éteinte sur les indices américains depuis le
  début des années 2000, et qu'elle tient mieux hors des États-Unis (Hong Kong
  est cité). Une fenêtre plus large tiendrait mieux, mais en baisse elle aussi.
  On garde la fenêtre classique, définie par les études d'origine : choisir la
  fenêtre qui marche encore d'après des résultats récents reviendrait à
  choisir d'après la période même qu'on teste.
- **Les coûts.** Par fenêtre, d'après les relevés FTMO du 30 octobre au
  6 novembre 2026 : deux spreads (0,01 à 0,05 % du nominal) et environ six
  nuits de marge sur les swaps (0,06 à 0,09 %). À pleine force, l'effet des
  études rapporte de l'ordre de 0,1 % par jour de fenêtre de plus que les
  autres jours, soit environ 0,4 % par fenêtre : les coûts en prendraient le
  quart ou le tiers. Si l'effet a diminué de moitié, ils en prennent l'essentiel.
- **Le peu de jours.** Avec 4 jours par mois, il faut un effet net pour que
  27 ans suffisent à dépasser t = 2,5.

---

## Ce qui est figé

Rien de cette section ne change après le commit, hormis les cases prévues pour
être remplies. Une erreur de code se corrige ; si elle est trouvée après le
test, elle doit être démontrée par un test unitaire sur données synthétiques,
et les résultats avant et après correction sont publiés tous les deux. Un
paramètre ne se change pas.

### Univers : 6 indices

US500, GER40, UK100, JP225, AUS200, HK50 : les indices du protocole de
tendance, avec les **mêmes fichiers bruts**, déjà contrôlés et scellés par
leurs empreintes SHA-256 (étape 2 de ce protocole, commit `5f3e492`), et les
**mêmes coûts FTMO** (`docs/tendance_couts.csv`, étape 1, commit `c33bba4`).
Rien n'est retéléchargé, rien n'est relevé de nouveau.

### Ce que rapporte une position

Comme pour la tendance : rendement **en excès**, c'est-à-dire variation de
l'indice moins le taux court local (BRI ; Hong Kong : taux américain), au
prorata des jours calendaires, base 365, au taux connu la veille.

- **Dividendes.** On prend l'indice tel que publié. FTMO verse les dividendes
  aux acheteurs (relevés du protocole de tendance) : la stratégie n'étant
  qu'acheteuse, le test est exact pour GER40, dont l'indice les réinvestit
  déjà, et prudent pour les cinq autres.

### La fenêtre et les positions

| élément | valeur figée |
|---|---|
| fenêtre | dernier jour de cotation du mois (−1) et trois premiers du mois suivant (+1, +2, +3), au calendrier de chaque indice |
| entrée | à la clôture de l'avant-dernier jour de cotation du mois (−2) |
| sortie | à la clôture du troisième jour de cotation du mois suivant (+3) |
| hors fenêtre | aucune position |
| volatilité estimée | moyenne exponentielle des carrés des rendements quotidiens en excès, centre de gravité 60 jours, annualisée sur 261 jours, connue à la clôture d'entrée |
| taille | nominal = 40 % ÷ volatilité estimée, fixe pendant la fenêtre |
| portefeuille | moyenne des positions des indices disponibles |
| entrée d'un indice | dès 60 jours de rendements |

La fenêtre ne dépend que du calendrier, connu à l'avance : aucune décision ne
lit un cours qu'elle n'aurait pas eu.

**Aucune variante n'est calculée** : ni autre fenêtre (−2 à +3, −3 à +3, +1 à
+4…), ni autre heure d'entrée, ni autre liste d'indices.

### Les coûts FTMO

Ceux du tableau de l'étape 1 du protocole de tendance (`docs/tendance_couts.csv`) :
le spread entier et la commission à chaque entrée et à chaque sortie, et la
marge à l'achat sur les swaps pour chaque jour calendaire où la position est
ouverte.

### La période et les données

- Rendements jugés : **janvier 2000 à septembre 2026**, comme pour la tendance.
  Données dès janvier 1998, pour amorcer la volatilité.
- Après publication : **janvier 2009 à septembre 2026**, après McConnell et Xu
  (2008).
- Avant le test, **aucun résultat de la stratégie n'est calculé sur données
  réelles**. Le code se met au point sur données synthétiques. Le premier
  calcul sur données réelles est le test.

---

## Les cinq critères, au seuil t > 2,5

| # | critère | mesure | seuil | résultat |
|---|---|---|---|---|
| 1 | l'effet existe | régression, sur tous les couples indice-jour, du rendement en excès du jour divisé par la volatilité estimée la veille, sur une variable valant 1 les jours de la fenêtre ; constante commune ; écarts-types regroupés par mois | coefficient > 0, t > 2,5 | |
| 2 | partout, pas sur un seul marché | le même coefficient, estimé indice par indice | positif pour au moins 5 des 6 indices | |
| 3 | mieux que l'exposition passive | rendements mensuels de la stratégie régressés sur ceux du portefeuille toujours acheteur des 6 indices (même dosage) ; Newey-West, 3 retards | alpha > 0, t > 2,5 | |
| 4 | test du hasard | 1 000 stratégies où, chaque mois, la fenêtre de 4 séances est décalée d'un même nombre de séances pour tous les indices, tiré au hasard entre 4 et 14 ; Sharpe net de coûts | p < 0,01 | |
| 5 | rendement net des coûts FTMO | moyenne des rendements mensuels nets | t > 2,5 (Newey-West, 3 retards) sur 2000-2026, **et** moyenne > 0 sur 2009-2026 | |

Critères 1 à 3 : avant les coûts FTMO ; ils jugent l'effet. Critères 4 et 5 :
après ces coûts. Critère 4 : p = (1 + nombre de tirages au moins aussi bons que
la vraie stratégie) ÷ 1 001 ; graine du hasard `20261106`.

### Pourquoi ces critères

- **Critère 2.** Un effet présent sur un seul marché, ou porté par un seul,
  ressemble plus à un accident qu'à une régularité.
- **Critère 3.** La stratégie n'est acheteuse que quelques jours par mois. Sans
  ce critère, elle passerait pour habile sur des marchés qui ont simplement
  monté.
- **Critère 4.** Le hasard garde tout sauf le moment : même nombre de jours,
  mêmes indices, même dosage, mêmes coûts, même synchronisation entre marchés.
  C'est le calendrier qui est jugé. Seuil 0,01 au lieu de 0,05, pour la même
  raison que t > 2,5 : c'est la troisième idée testée.
- **Critère 5.** La même exigence après publication que pour la tendance : un
  effet qui ne rapporte plus rien depuis qu'il est connu ne se paie pas.

---

## Décision prévue d'avance

**5 sur 5** → l'effet est démontré au standard du projet. On passe à « Après le
test », toujours sans payer.

**Entre 1 et 4** → aucun paiement. On consigne lequel a échoué et pourquoi.
Cette idée n'est pas retouchée.

**0 sur 5** → aucun paiement.

## Ce qui NE compte pas comme critère

- le résultat de toute fenêtre, de tout dosage ou de toute liste d'indices
  autres que ceux figés ici ;
- le résultat d'une sous-période choisie après coup : seules 2000-2026 et
  2009-2026 comptent ;
- un mois spectaculaire, dans un sens ou dans l'autre ;
- le compte papier actions, qui teste une autre stratégie.

---

## Après le test (seulement si 5 sur 5)

1. **Régler le risque.** Rejouer le défi sur les rendements nets 2009-2026,
   avec les règles FTMO et les garde-fous du bot, pour plusieurs niveaux de
   risque. Retenir celui qui maximise la probabilité de réussir les deux phases
   en 12 mois au plus.
2. **Calculer l'espérance** du défi (prix, partage des gains) avec cette
   probabilité.
3. **Valider la machine** sur un compte de démonstration FTMO, pendant au
   moins deux fenêtres sans incident. Les entrées et sorties d'Asie tombent la
   nuit, heure de Paris : la machine doit tourner à ces heures-là.
4. **Respecter les règles de forme.** FTMO exige 4 jours de trading par phase,
   et cette stratégie n'en compte qu'un ou deux par mois, selon que la sortie
   compte ou non : une phase durera au moins deux à quatre mois. Compte Swing,
   puisque les fenêtres enjambent souvent un week-end.
5. Seulement alors, **décider de payer**.

## Étapes

| # | étape | fait le |
|---|---|---|
| 0 | relire, valider, committer et pousser ce protocole | 7 novembre 2026 (`58def69`) |
| 1 | coûts FTMO : ceux du protocole de tendance, déjà relevés et commités (`c33bba4`) | 7 novembre 2026, avec le protocole |
| 2 | données : celles du protocole de tendance, déjà contrôlées et scellées (`5f3e492`) ; le test vérifie leurs empreintes | 7 novembre 2026, avec le protocole |
| 3 | écrire le code et ses tests sur données synthétiques : sans effet, aucun avantage ; avec un effet implanté, les critères le détectent ; modifier les cours futurs ne change aucune position passée | 7 novembre 2026 |
| 4 | lancer le test **une seule fois**, remplir la colonne « résultat », committer la sortie brute | |
| 5 | appliquer la décision | |
