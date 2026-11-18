# Protocole : le momentum intrajournalier sur les indices FTMO

**Écrit le 18 novembre 2026, AVANT tout calcul de la stratégie sur données réelles.**
**Brouillon tant qu'il n'est pas commité : la date du commit fait foi.**

> Verdict accepté d'avance, quel qu'il soit : une hypothèse testée une fois ne
> se re-teste pas avec d'autres réglages jusqu'à ce qu'elle passe.
>
> — `src/quantbot/volatilite.py`, écrit par Nathan

Trois idées ont déjà été testées dans ce projet : le score actions (**0 sur 5**,
`docs/protocole_defi_ftmo.md`), la tendance multi-actifs (**1 sur 5**,
`docs/protocole_tendance_multi_actifs.md`) et le tournant du mois (**1 sur 5**,
`docs/protocole_tournant_du_mois.md`). Comme le protocole de tendance l'a fixé,
toute idée testée après lui a son propre protocole et un seuil de **t > 2,5**.

Les deux derniers échecs disent la même chose : un effet publié depuis
longtemps s'use, et chez FTMO, garder une position la nuit coûte plus que ce
qu'il en reste. Le protocole du tournant du mois en a tiré la règle : la
prochaine idée doit reposer sur un mécanisme qui ne s'use pas en étant connu,
ou ne pas garder de position la nuit. Celle-ci fait les deux.

Après le commit, seules changent les cases prévues pour être remplies : coûts
(étape 1), données (étape 2), résultats (étape 4) et décision (étape 5),
chacune par un commit daté.

---

## L'hypothèse

**Pendant la dernière demi-heure de séance, un indice continue dans le sens de
sa journée.** Trente minutes avant la clôture du marché au comptant, on achète
l'indice s'il a monté depuis la clôture de la veille, on le vend s'il a baissé,
et on dénoue la position à la clôture. Aucune position ne passe la nuit.

### Pourquoi elle devrait marcher

- **Un effet publié, et mesuré presque partout.** Gao, Han, Li et Zhou (2018)
  le trouvent sur le S&P 500. Baltussen, Da, Lammers et Martens (2021)
  l'étudient sur 62 contrats à terme de 1974 à 2020, dont 17 indices
  d'actions : le rendement depuis la clôture de la veille jusqu'à 30 minutes
  avant la clôture annonce celui de la dernière demi-heure, dans les deux
  moitiés de leur période (1974-1999 et 2000-2020). Sur les actions, la
  stratégie atteint un ratio de Sharpe de 1,7 avant coûts, et reste rentable
  sur le contrat S&P 500 après un échelon de coût.
- **Des flux forcés.** Les teneurs de marché d'options vendeurs de gamma
  doivent acheter quand le marché monte et vendre quand il baisse, surtout en
  fin de séance ; les ETF à effet de levier rachètent ou revendent à la
  clôture dans le sens de la journée. D'après Baltussen et al., l'effet est
  fort quand les teneurs de marché sont vendeurs de gamma, et presque absent
  sinon. Ces flux ne disparaissent pas parce que l'effet est connu, tant que
  ces produits existent.
- **Il colle aux coûts FTMO.** Aucune position la nuit, donc aucun swap. Une
  seule transaction aller-retour par indice et par séance.

### Ce qui peut la faire échouer

- **L'usure.** Les données FTMO commencent fin 2017 : elles sont entièrement
  postérieures à la publication de Gao et al. On ne teste que ce qui reste de
  l'effet depuis qu'il est connu.
- **Les coûts.** Chaque transaction paie un spread entier, de 0,005 % à
  0,027 % du nominal selon l'indice (tableau des coûts du protocole de
  tendance). À la force publiée, l'avance par transaction serait de l'ordre de
  0,04 % (ordre de grandeur tiré de la prévisibilité publiée, à vérifier) : le
  spread en prendrait une petite part sur US500, GER40 et UK100, mais la plus
  grande sur JP225 et HK50. Si l'effet a diminué, il ne restera que les trois
  premiers, ou rien.
- **Le peu d'années.** Huit ans et demi de données (sept ans et demi pour
  AUS200 et HK50). Il faut un effet net pour dépasser t = 2,5.
- **Le régime de gamma.** Quand les teneurs de marché sont acheteurs de gamma,
  l'effet s'inverse. L'essor des options à échéance du jour depuis 2022 a
  changé leurs positions, dans un sens qu'on ne connaît pas.
- **Les heures fixes.** Les jours de clôture anticipée (veille de Noël,
  lendemain de Thanksgiving…), la fenêtre tombe après la clôture du marché au
  comptant. Quelques jours par an : du bruit, pas un biais.

---

## Ce qui est figé

Rien de cette section ne change après le commit, hormis les cases prévues pour
être remplies. Une erreur de code se corrige ; si elle est trouvée après le
test, elle doit être démontrée par un test unitaire sur données synthétiques,
et les résultats avant et après correction sont publiés tous les deux. Un
paramètre ne se change pas.

### Univers : 6 indices

US500, GER40, UK100, JP225, AUS200, HK50 : les CFD d'indices de FTMO
(`US500.cash`…), les mêmes que pour la tendance et le tournant du mois.

### Les données

- **Cours :** les barres de 30 minutes du serveur FTMO (cours acheteurs, bid),
  téléchargées une seule fois à l'étape 2 depuis le terminal MT5 (compte de
  démonstration, lecture seule), de la première barre disponible au
  31 octobre 2026, puis scellées par leurs empreintes SHA-256. D'après la
  sonde du 18 novembre 2026 : depuis le 28 ou 29 décembre 2017 pour US500,
  GER40, UK100 et JP225, depuis le 17 décembre 2018 pour HK50, depuis le
  8 février 2019 pour AUS200.
- **Heure :** l'heure du serveur FTMO est celle de New York plus 7 heures
  (GMT+3 pendant l'heure d'été américaine, GMT+2 sinon ; bulletin FTMO du
  22 octobre 2026). C'est vérifié à l'étape 2 sur tout l'historique : parmi
  les barres US500 de 8:00 à 12:00, heure de New York, celle au plus gros
  volume doit être la barre de 9:30, l'ouverture du marché au comptant, pour
  au moins 90 % des séances, et aussi pour au moins 90 % des séances des
  semaines où l'Europe et les États-Unis n'ont pas encore tous les deux changé
  d'heure. Sinon l'étape 2 s'arrête, et le test n'est pas lancé.
- **Calendrier des séances :** les jours de cotation de chaque indice dans ses
  cours quotidiens du protocole de tendance (fichiers bruts scellés à son
  étape 2, `5f3e492`), jusqu'au 30 octobre 2026. Un jour où le marché au
  comptant est fermé n'est jamais une séance, même si le CFD cote.

### La fenêtre et la position

| indice | clôture du marché au comptant (heure locale) | fenêtre |
|---|---|---|
| US500 | 16:00 New York | 15:30-16:00 |
| GER40 | 17:30 Francfort | 17:00-17:30 |
| UK100 | 16:30 Londres | 16:00-16:30 |
| JP225 | 15:00 Tokyo jusqu'au 4 novembre 2024, 15:30 depuis le 5 | 14:30-15:00, puis 15:00-15:30 |
| AUS200 | 16:00 Sydney | 15:30-16:00 |
| HK50 | 16:00 Hong Kong | 15:30-16:00 |

Pour chaque indice et chaque séance t, la barre de 30 minutes qui commence au
début de la fenêtre (heure locale convertie en heure du serveur, changements
d'heure compris) donne :

| élément | valeur figée |
|---|---|
| prix d'entrée E(t) | ouverture de la barre |
| prix de sortie C(t) | clôture de la barre |
| dernière demi-heure | r_DD(t) = C(t) / E(t) − 1 |
| reste de la journée | r_RJ(t) = E(t) / C(t−1) − 1, où t−1 est la séance précédente du calendrier |
| sens | achat si r_RJ(t) > 0, vente si r_RJ(t) < 0, rien s'il est nul |
| durée | ouverture et fermeture avec la barre : jamais de position la nuit |
| barre manquante | pas de position à t si la barre manque à t ou à t−1 |
| volatilité estimée σ(t) | moyenne exponentielle des r_DD², centre de gravité 60 séances, connue à la séance précédente |
| taille | nominal = 1 % ÷ σ(t) |
| portefeuille | moyenne des positions des indices disponibles |
| entrée d'un indice | dès 60 séances de r_DD |

La position ne dépend que du calendrier et de cours déjà connus à l'entrée.

**Aucune variante n'est calculée :** ni la première demi-heure comme
prédicteur (Gao et al.), ni une autre durée de fenêtre, ni une autre heure,
ni une autre liste d'indices.

### Les coûts FTMO

Les barres sont des cours acheteurs (bid) ; on achète au prix vendeur (ask).
Une position acheteuse paie donc le spread entier à l'entrée, une position
vendeuse le paie à la sortie, en rachetant. Une transaction aller-retour paie
un spread, celui du côté où l'on achète.

Le spread retenu, par indice, pour l'entrée et pour la sortie séparément, est
le plus élevé de :

1. la médiane, sur les 60 dernières séances de l'historique de ticks FTMO au
   moment du relevé, du spread relatif médian de la minute qui suit le début
   de la fenêtre (entrée) ou qui précède sa fin (sortie) ;
2. la valeur `transaction` du tableau des coûts du protocole de tendance
   (`docs/tendance_couts.csv`, `c33bba4`).

Si l'historique de ticks n'est pas disponible, la seconde valeur seule.

Aucune commission sur les indices FTMO (tableau des coûts du protocole de
tendance), et aucun swap : la position la plus tardive, US500, est fermée à
23:00 heure du serveur, avant le passage de minuit.

### La période

- Rendements jugés : **avril 2018 à octobre 2026**. Les données de décembre
  2017 à mars 2018 amorcent la volatilité estimée.
- Après publication : **janvier 2021 à octobre 2026**, après Baltussen et al.
  (2021), qui ont établi le prédicteur r_RJ.
- Avant le test, **aucun résultat de la stratégie n'est calculé sur données
  réelles**. Le code se met au point sur données synthétiques. Le premier
  calcul sur données réelles est le test. Les contrôles de l'étape 2 ne
  regardent que les cours, jamais le sens des positions ni le lien entre r_RJ
  et r_DD.

---

## Les cinq critères, au seuil t > 2,5

| # | critère | mesure | seuil | résultat |
|---|---|---|---|---|
| 1 | l'effet existe | régression, sur tous les couples indice-séance, de r_DD ÷ σ sur r_RJ ÷ σ_RJ (σ_RJ : moyenne exponentielle des r_RJ², même règle que σ) ; constante commune ; écarts-types regroupés par mois | pente > 0, t > 2,5 | |
| 2 | partout, pas sur un seul marché | la même pente, estimée indice par indice | positive pour au moins 5 des 6 indices | |
| 3 | mieux que l'exposition passive | rendements mensuels de la stratégie régressés sur ceux du portefeuille toujours acheteur pendant la même demi-heure (même dosage) ; Newey-West, 3 retards | alpha > 0, t > 2,5 | |
| 4 | test du hasard | 1 000 stratégies où le sens de chaque position est tiré au hasard (achat avec la même fréquence que dans la vraie stratégie, indice par indice), mêmes séances, même dosage, mêmes coûts ; Sharpe net | p < 0,01 | |
| 5 | rendement net des coûts FTMO | moyenne des rendements mensuels nets | t > 2,5 (Newey-West, 3 retards) sur avril 2018-octobre 2026, **et** moyenne > 0 sur janvier 2021-octobre 2026 | |

Critères 1 à 3 : avant les coûts FTMO ; ils jugent l'effet. Critères 4 et 5 :
après ces coûts. Critère 4 : p = (1 + nombre de tirages au moins aussi bons que
la vraie stratégie) ÷ 1 001 ; graine du hasard `20261118`. Si un indice est
exclu à l'étape 2, le critère 2 demande que la pente soit positive pour tous
les indices restants sauf un.

### Arrêt à l'étape 2, le 18 novembre 2026 : le test n'est pas lancé

Les barres ont été téléchargées et scellées le 18 novembre 2026 par
`scripts/donnees_intraday.py` (commit `8d928b8`), avec les empreintes du
tableau ci-dessous. Le contrôle de l'heure du serveur a échoué : la barre de
9:30 a le plus gros volume pour 64,9 % des 1 451 séances qui ont des barres
entre 8:00 et 12:00 à New York, et pour 71,2 % des 104 séances des semaines
de décalage, sous le seuil de 90 %. Comme écrit, l'étape 2 s'arrête là :
**le test n'est pas lancé**, et aucun résultat de la stratégie n'a été
calculé. La colonne « résultat » reste vide.

L'échec vient de deux erreurs de préparation, pas de l'hypothèse.

1. **L'historique de 30 minutes est bien plus court qu'annoncé.** La sonde a
   vérifié qu'il y avait des barres au début de l'historique, pas qu'elles
   duraient 30 minutes. Jusqu'au 20 janvier 2021, le serveur FTMO ne garde
   qu'une barre par jour (jusqu'au 17 mai 2021 pour GER40, jusqu'au
   2 décembre 2020 pour HK50), puis des barres d'une heure. Les barres de
   30 minutes ne commencent qu'à la mi-septembre 2021 (le 28 décembre 2021
   pour GER40) ; seul AUS200 les a dès février 2019. D'où 33 à 41 % de barres
   de fenêtre manquantes pour cinq indices, qui seraient exclus pour cette
   seule raison.
2. **Le seuil de 90 % était mal calibré.** Sur 2022-2026, où l'historique est
   complet, les volumes confirment l'heure annoncée : le volume médian triple
   à 9:30 (heure de New York si le serveur est à New York + 7 heures), en
   hiver comme en été, et retombe après 16:00. Mais la barre de 10:00 a
   souvent plus de ticks que celle de 9:30, qui ne l'emporte que dans 68 à
   74 % des séances. Pendant les semaines de décalage, elle l'emporte dans
   79 % des séances ; une heure du serveur réglée sur l'Europe aurait déplacé
   le pic vers 8:30 ou 10:30, qui ne l'emportent que dans 3 % et 1 % des
   séances. Le seuil supposait un profil de volumes qui n'avait pas été
   mesuré.

AUS200, le seul indice à l'historique complet, aurait été exclu de toute
façon : deux sauts dans la fenêtre, les 17 et 24 mars 2020, ne sont pas
corroborés par Yahoo. En plein krach, le CFD et l'indice ont différé de 1,8 %
et de 2,0 % à la clôture.

| fichier brut | empreinte SHA-256 | barres | première barre | dernière barre | barres de fenêtre manquantes | décision |
|---|---|---|---|---|---|---|
| `ftmo_US500_cash_M30.csv` | `74c6f0f17fdb7d74b593b876211f694e60904564145c97450422dd605a3fdb27` | 65 206 | 29/12/2017 | 30/10/2026 | 888 sur 2 159 séances jugées (41,1 %) | **exclu** : barre de fenêtre manquante pour 41,1 % des séances jugées |
| `ftmo_GER40_cash_M30.csv` | `cf09c298a4a8ba946e9e6f0171548c7ccd72def9ce3e26b5769719f581bb1698` | 60 616 | 28/12/2017 | 30/10/2026 | 787 sur 2 180 séances jugées (36,1 %) | **exclu** : barre de fenêtre manquante pour 36,1 % des séances jugées |
| `ftmo_UK100_cash_M30.csv` | `f0753dcb7e18998236f9870dd95974b915ac8e051953fff376978091bceb693e` | 63 826 | 28/12/2017 | 30/10/2026 | 715 sur 2 168 séances jugées (33,0 %) | **exclu** : barre de fenêtre manquante pour 33,0 % des séances jugées |
| `ftmo_JP225_cash_M30.csv` | `8ab75525b7b6e97662a7cba2ba0ee6f809274e7587c82a3c3135cb29cf8dc8af` | 65 290 | 28/12/2017 | 30/10/2026 | 850 sur 2 094 séances jugées (40,6 %) | **exclu** : barre de fenêtre manquante pour 40,6 % des séances jugées |
| `ftmo_AUS200_cash_M30.csv` | `0e71c6733730ba64f0bc88008119ae7a92b433a32d12a53c1dc1ab402aaeb9f4` | 85 419 | 08/02/2019 | 30/10/2026 | 35 sur 1 957 séances jugées (1,8 %) | **exclu** : saut dans la fenêtre du 17/03/2020 (+2,5 %, 15 écarts-types), non corroboré (écart avec Yahoo 1,80 %, avec la barre d'avant 0,00 %) ; saut dans la fenêtre du 24/03/2020 (+1,7 %, 10 écarts-types), non corroboré (écart avec Yahoo 2,01 %, avec la barre d'avant 0,00 %) |
| `ftmo_HK50_cash_M30.csv` | `9deaf7c1eb4c9abb19d1c2265d62ca8ee7e334e70f15caa08003ed430bde15fa` | 55 436 | 17/12/2018 | 30/10/2026 | 686 sur 1 935 séances jugées (35,5 %) | **exclu** : barre de fenêtre manquante pour 35,5 % des séances jugées |

Ce que l'étape 2 a montré des cours, à citer par tout protocole qui
reprendrait l'hypothèse : six mouvements de fenêtre de plus de 10
écarts-types, tous des jours de krach connus (GER40 −1,95 % et UK100 −2,14 %
le 7 avril 2025 ; JP225 −2,78 % le 5 août 2024 ; AUS200 +2,50 %, +2,61 % et
+1,65 % les 17, 18 et 24 mars 2020), et la corrélation de chaque indice avec
Yahoo, de clôture à clôture (0,962 à 0,998). Rien n'a été calculé sur le lien
entre r_RJ et r_DD.

**Décision : aucun paiement.** La règle était écrite d'avance et s'applique,
même si l'heure du serveur était la bonne. L'hypothèse, elle, n'a pas été
testée : rien ne la confirme ni ne l'infirme. La reprendre demanderait un
nouveau protocole, sur un historique de 30 minutes complet, avec un contrôle
d'horloge calibré avant d'être figé.

Pour les prochains protocoles : une sonde de données compte les barres par
jour, au lieu de vérifier seulement qu'il y en a ; et le seuil d'un contrôle
qui dépend du comportement du marché se mesure sur un échantillon récent
avant d'être figé, au lieu d'être supposé.

### Pourquoi ces critères

- **Critère 1.** Il mesure l'effet lui-même, sur toutes les séances, sans
  coûts ni dosage.
- **Critère 2.** Un effet présent sur un seul marché, ou porté par un seul,
  ressemble plus à un accident qu'à une régularité.
- **Critère 3.** Être acheteur en fin de séance peut rapporter tout seul. La
  stratégie doit faire mieux que cela.
- **Critère 4.** Le hasard garde tout sauf le sens : mêmes séances, mêmes
  tailles, mêmes coûts. Au tournant du mois, les fenêtres décalées se
  recouvraient d'un tirage à l'autre, ce qui rendait le test plus indulgent
  qu'annoncé. Ici, chaque tirage est une stratégie du même genre que la vraie,
  indépendante des autres : sans effet, la vraie n'est qu'un tirage de plus, et
  le test se trompe au taux annoncé. L'étape 3 le vérifie sur données
  synthétiques.
- **Critère 5.** Un effet qui ne rapporte plus rien depuis qu'il est publié ne
  se paie pas.

---

## Décision prévue d'avance

**5 sur 5** → l'effet est démontré au standard du projet. On passe à « Après le
test », toujours sans payer.

**Entre 1 et 4** → aucun paiement. On consigne lequel a échoué et pourquoi.
Cette idée n'est pas retouchée.

**0 sur 5** → aucun paiement.

## Ce qui NE compte pas comme critère

- le résultat de toute fenêtre, de tout prédicteur, de tout dosage ou de toute
  liste d'indices autres que ceux figés ici ;
- le résultat d'une sous-période choisie après coup : seules avril 2018-octobre
  2026 et janvier 2021-octobre 2026 comptent ;
- un mois spectaculaire, dans un sens ou dans l'autre ;
- le compte papier actions, qui teste une autre stratégie.

---

## Après le test (seulement si 5 sur 5)

1. **Régler le risque.** Rejouer le défi sur les rendements nets 2021-2026,
   avec les règles FTMO et les garde-fous du bot, pour plusieurs niveaux de
   risque. Retenir celui qui maximise la probabilité de réussir les deux phases
   en 12 mois au plus.
2. **Calculer l'espérance** du défi (prix, partage des gains) avec cette
   probabilité.
3. **Valider la machine** sur un compte de démonstration FTMO, pendant au
   moins 20 séances sans incident. Le robot doit passer douze ordres par jour à
   heure fixe sur trois continents, dont l'Asie pendant la nuit de Paris. Il
   doit respecter les règles de FTMO : moins de 2 000 requêtes au serveur par
   jour, aucune position pendant une fermeture du marché, et les restrictions
   autour des annonces économiques si le compte n'est pas un compte Swing.
4. **Mesurer l'écart d'exécution** sur ce compte : si les prix obtenus
   s'écartent des barres de plus que les coûts retenus ici, on refait le
   calcul avant tout paiement.
5. Seulement alors, **décider de payer**.

## Étapes

| # | étape | fait le |
|---|---|---|
| 0 | relire, valider, committer et pousser ce protocole | 18 novembre 2026 (`444c403`) |
| 1 | coûts : relever les spreads FTMO aux heures exactes d'entrée et de sortie (historique de ticks), consigner le tableau, committer | sans objet : l'étape 2 a arrêté le protocole |
| 2 | données : télécharger une fois les barres de 30 minutes, contrôler (heure du serveur, barres manquantes, cours figés, sauts), consigner empreintes et exclusions, committer | 18 novembre 2026 : arrêtée, contrôle de l'heure du serveur en échec |
| 3 | écrire le code et ses tests sur données synthétiques : sans effet, aucun avantage ; avec un effet implanté, les critères le détectent ; modifier les cours futurs ne change aucune position passée ; le test du hasard se trompe au taux annoncé | sans objet |
| 4 | lancer le test **une seule fois**, remplir la colonne « résultat », committer la sortie brute | sans objet : le test n'est pas lancé |
| 5 | appliquer la décision | 18 novembre 2026 : aucun paiement |

Exclusion d'un indice à l'étape 2 : si sa barre de fenêtre manque pour plus de
5 % de ses séances de la période jugée, ou si ses cours ont un défaut que rien
ne permet de trancher. La raison est consignée dans le tableau de l'étape 2.
