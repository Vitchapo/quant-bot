# Protocole : la tendance multi-actifs sur les instruments FTMO

**Écrit le 10 octobre 2026, AVANT tout calcul de la stratégie sur données réelles.**
**Brouillon tant qu'il n'est pas commité : la date du commit fait foi.**

> Verdict accepté d'avance, quel qu'il soit : une hypothèse testée une fois ne
> se re-teste pas avec d'autres réglages jusqu'à ce qu'elle passe.
>
> — `src/quantbot/volatilite.py`, écrit par Nathan

Le score actions a obtenu **0 sur 5** aux critères de
`docs/protocole_defi_ftmo.md`. Ce document fixe, avant de la tester, **une**
nouvelle idée, la façon exacte de la tester et ce qu'on fera du résultat.

Après le commit, seules changent les cases prévues pour être remplies : les
coûts (étape 1), les exclusions de données (étape 2), les résultats
(étape 4). Chacune par un commit daté.

Ce travail ne touche ni au robot actions ni à sa configuration : le gel
jusqu'au 2 novembre reste respecté. Le code du test vit dans un module à part,
qu'aucun code du robot n'importe.

---

## L'hypothèse

**Un marché qui a monté sur les 12 derniers mois a tendance à continuer de
monter le mois suivant ; un marché qui a baissé, à continuer de baisser.**
On achète les marchés en hausse sur 12 mois, on vend ceux en baisse, chacun
dosé pour peser le même risque, et on révise une fois par mois.

### Pourquoi elle devrait marcher

- C'est l'effet le mieux documenté en dehors des actions individuelles.
  Moskowitz, Ooi et Pedersen (*Journal of Financial Economics*, 2012) le
  trouvent sur 58 marchés à terme (indices, devises, matières premières,
  obligations) de 1985 à 2009. Hurst, Ooi et Pedersen (*Journal of Portfolio
  Management*, 2017) remontent de 1880 à 2016 : des gains dans chaque
  décennie.
- L'explication avancée : les prix réagissent d'abord trop lentement aux
  nouvelles, puis s'emballent. Les deux phases font les tendances.
- La stratégie a souvent gagné pendant les baisses longues (2008 notamment),
  ce qui compte face à une perte maximale.

### Ce qui peut la faire échouer

Écrit maintenant, pour ne pas le « découvrir » après :

- l'effet a été bien plus faible dans les années 2010 qu'avant ; d'où le
  jugement à part de 2013-2026, après la publication (critère 5) ;
- l'univers FTMO est pauvre pour cette stratégie : 15 instruments au lieu de
  58, pas d'obligations, et comme matières premières seulement l'or et
  l'argent (voir « Univers »). Moins de diversification, donc un résultat
  plus bruité et un Sharpe plus faible que dans les études ;
- les swaps des CFD peuvent manger un avantage de quelques points par an. On
  ne les connaîtra qu'à l'étape 1 ;
- même si tout passe, le défi sera lent (voir « À quoi s'attendre »).

---

## Ce qui est figé

Rien de cette section ne change après le commit, hormis les cases prévues pour
être remplies. Une erreur de code se corrige ; si elle est trouvée après le
test, elle doit être démontrée par un test unitaire sur données synthétiques,
et les résultats avant et après correction sont publiés tous les deux. Un
paramètre ne se change pas.

### Univers : 15 instruments

| famille | instruments FTMO | série | source (repli) |
|---|---|---|---|
| indices (6) | US500, GER40, UK100, JP225, AUS200, HK50 | `^GSPC`, `^GDAXI`, `^FTSE`, `^N225`, `^AXJO`, `^HSI` | Yahoo (Stooq) |
| devises (7) | EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD | cours de midi de la Fed (H.10) : `DEXUSEU`, `DEXUSUK`, `DEXJPUS`, `DEXSZUS`, `DEXCAUS`, `DEXUSAL`, `DEXUSNZ` | FRED (Yahoo `=X`) |
| métaux (2) | XAUUSD, XAGUSD | cours au comptant | Stooq (Yahoo `GC=F`, `SI=F`) |
| taux courts | — | taux directeurs : États-Unis, zone euro (Allemagne avant 1999), Royaume-Uni, Japon, Suisse, Canada, Australie, Nouvelle-Zélande, Hong Kong | BRI (Hong Kong : taux américain, monnaie arrimée au dollar) |

Exclus d'avance :

- **les doublons** : US100, US30 et US2000 bougent presque comme US500 ; EU50,
  FRA40, SPN35 et N25 presque comme GER40 ; DXY est une combinaison des
  devises retenues. Ils compteraient plusieurs fois le même pari ;
- **pétrole, gaz, fioul, produits agricoles, cuivre** : un CFD sur ces marchés
  rapporte ce que rapporte un contrat à terme renouvelé, pas le cours au
  comptant, et l'écart entre les deux (le coût de renouvellement) dépasse
  10 % certaines années. Aucune source gratuite ne fournit ces contrats
  jusqu'en 2026 : l'EIA ne publie plus leurs cours depuis avril 2024 ;
- **platine et palladium** : marchés étroits, coût de portage parfois extrême
  et non publié ;
- **cryptomonnaies** : historique trop court ;
- **obligations** : absentes chez FTMO.

### Ce que rapporte une position

Comme dans les études, on mesure le rendement **en excès** : ce que rapporte
une position financée, en monnaie locale (sur un CFD, seul le gain est
converti, pas le nominal). Les taux s'appliquent au prorata des jours
calendaires, base 365.

| famille | rendement d'une position acheteuse |
|---|---|
| devises | variation du cours + taux de la devise achetée − taux de la devise vendue |
| indices | variation de l'indice − taux court local |
| métaux | variation du cours − taux court américain |

Une position vendeuse rapporte l'opposé.

- **Dividendes.** On prend l'indice tel que publié. Pour GER40 c'est exact :
  le DAX réinvestit déjà ses dividendes. Pour les cinq autres, c'est exact si
  FTMO ne verse pas les dividendes sur ses CFD, et prudent s'il les verse :
  les positions acheteuses y perdent alors 1,5 à 4,5 % de nominal par an.
- **Métaux.** Le taux de location de l'or et de l'argent est ignoré, ce qui
  pénalise un peu les positions acheteuses.

### Le signal et les positions

| élément | valeur figée |
|---|---|
| signal | signe du rendement en excès des 12 derniers mois |
| décision | à la clôture du dernier jour de cotation du mois |
| exécution | à la clôture du jour de cotation suivant |
| volatilité estimée | moyenne exponentielle des carrés des rendements quotidiens en excès, centre de gravité 60 jours, annualisée sur 261 jours |
| taille | nominal = 40 % ÷ volatilité estimée (chaque position vise 40 % de volatilité par an) |
| portefeuille | moyenne des positions de tous les instruments disponibles |
| entrée d'un instrument | dès 12 mois de cours et 60 jours de volatilité |

Tout vient de Moskowitz et al. (2012), sauf le décalage d'un jour, ajouté pour
qu'aucun ordre ne soit exécuté au cours qui l'a décidé.

Le niveau de risque n'a pas d'importance pour le test : doubler ou diviser
toutes les positions ne change aucun des cinq critères. Il se règle après
(voir « Après le test »).

**Aucune variante n'est calculée** : ni autre durée que 12 mois, ni autre
fenêtre de volatilité, ni autre jour de révision, ni autre liste
d'instruments.

### Les coûts FTMO, relevés avant le test

À l'étape 1, dans MetaTrader 5 (essai gratuit FTMO, compte Swing), pour chaque
instrument : nom exact, taille du contrat, spread, commission, swap acheteur et
vendeur avec leur unité, jour du swap triple, levier ; pour les indices, si
FTMO verse ou prélève les dividendes. Spread retenu : le plus élevé de trois
relevés, faits trois jours différents, marché sous-jacent ouvert.

Dans le test :

- **spread entier et commission à chaque transaction.** Choix prudent : les
  cours de clôture des sources ne sont pas des milieux de fourchette ;
- **marge de FTMO sur les swaps**, prélevée chaque jour sur le nominal :
  marge = portage donné par notre modèle le jour du relevé − swap FTMO
  annualisé, pour l'achat et pour la vente, jamais négative. Le portage
  historique est déjà dans les rendements en excès ; on ne le compte pas deux
  fois.

| instrument | nom MT5 | contrat | spread | commission | swap achat | swap vente | levier | dividendes |
|---|---|---|---|---|---|---|---|---|
| *(étape 1)* | | | | | | | | |

### La période et les données

- Rendements jugés : **janvier 2000 à septembre 2026**, dernier mois complet
  avant l'écriture. Les données sont prises dès janvier 1998, ou dès le début
  de la série, pour amorcer les signaux.
- Après publication : **janvier 2013 à septembre 2026**.
- Une série est retenue si elle commence au plus tard en janvier 2004 et passe
  les contrôles de l'étape 2 : pas de trou de plus de 5 jours ouvrés, pas de
  cours figé plus de 5 jours, aucun rendement quotidien de plus de 25 % ou de
  10 écarts-types sans confirmation par la source de repli. Un défaut que la
  source de repli ne résout pas exclut l'instrument **avant** le test.
- Avant le test, **aucun résultat de la stratégie n'est calculé sur données
  réelles**. Le code se met au point sur données synthétiques
  (`synthetic.py`). Les vraies données ne servent qu'aux contrôles de qualité,
  qui ne calculent ni signal ni rendement de stratégie. Le premier calcul sur
  données réelles **est** le test.

| fichier brut | empreinte SHA-256 | exclusion éventuelle et raison |
|---|---|---|
| `yahoo__GSPC.json` | `7a5c1cd69e2ea56e2cfef93a79992beda089265ebd8de6e6cc46381c37229ea6` | US500, source principale : retenu, aucun défaut |
| `stooq__SPX.csv` | — | US500, repli : non téléchargé, aucun défaut à trancher |
| `yahoo__GDAXI.json` | `3e9f9f0679ba8e62b575afc83d443d53308a9be6a61a6cba55fdf181ab309574` | GER40, source principale : retenu, aucun défaut |
| `stooq__DAX.csv` | — | GER40, repli : non téléchargé, aucun défaut à trancher |
| `yahoo__FTSE.json` | `c58ce686c1e5076bb8b8d2a0c9d1230a56ca72a85dc22c0951a1e35fdd9c628c` | UK100, source principale : retenu, aucun défaut |
| `stooq__UKX.csv` | — | UK100, repli : non téléchargé, aucun défaut à trancher |
| `yahoo__N225.json` | `ae963771e5a7ceacd30200597e714d58fc9419ce4cac3c8e3636573ae72d87c7` | JP225, source principale : retenu ; trou du 26/04/2019 au 07/05/2019 (6 jours ouvrés sans cotation), le repli ne cote pas non plus : marché fermé |
| `stooq__NKX.csv` | `31ffd435ab10bd0eb64e5f2ee62f9884cb92843312fb8673a7ae249ead298174` | JP225, repli |
| `yahoo__AXJO.json` | `6de236daaceeee8a54ffb4a0429155f5f915931d1d37b19d7a38d2418362efde` | AUS200, source principale : retenu, aucun défaut |
| `stooq__AXJO.csv` | — | AUS200, repli : non téléchargé, aucun défaut à trancher |
| `yahoo__HSI.json` | `e5e6c3e3903dbed82b9d00368e74e98ca425cb5cdc94e9c0f6e4343e05524d3b` | HK50, source principale : retenu, aucun défaut |
| `stooq__HSI.csv` | — | HK50, repli : non téléchargé, aucun défaut à trancher |
| `fred_DEXUSEU.csv` | `69896d0b36255eecdfb9a8be80517f1fdeadcdab5e2707f2047d585cb290c439` | EURUSD, source principale : retenu, aucun défaut |
| `yahoo_EURUSD_X.json` | `74a9e58de8b929032d46f85b6ff8b04c5acf7ddac7ceddca95e000daaac2beca` | EURUSD, repli |
| `fred_DEXUSUK.csv` | `9bdf3affad22a28d974d7bf184edabed7a912f4170460c2738ca93b07a10dc15` | GBPUSD, source principale : retenu ; saut du 24/06/2016 (−7,8 %, 14 écarts-types), confirmé par le repli |
| `yahoo_GBPUSD_X.json` | `5096a4fa02a33a64dc4b4719bc432a4567ec358d32c415d800f06f1b9bf0c6db` | GBPUSD, repli |
| `fred_DEXJPUS.csv` | `c359b1700c8175b5ca3f857439d965c022e9cc5c222e08f747760a3aa4b61155` | USDJPY, source principale : retenu, aucun défaut |
| `yahoo_JPY_X.json` | `d661a12b30ee134f4629b7cabdee2a1e318efeda35d1200f851e7df06991c64a` | USDJPY, repli |
| `fred_DEXSZUS.csv` | `02b28ca263021a282d401a034c18759682ca14b8d540e482a0fc80d694faa812` | USDCHF, source principale : retenu ; saut du 06/09/2011 (+9,3 %, 15 écarts-types), confirmé par le repli ; saut du 15/01/2015 (−12,2 %, 19 écarts-types), confirmé par le repli |
| `yahoo_CHF_X.json` | `01a334b96c83d6a655626303058cb6b60389ac82e191c073ffa06344190c51a6` | USDCHF, repli |
| `fred_DEXCAUS.csv` | `a1bf82fd07da7fcb44eb52eccb8b847811194ae136f604a0af4f360e469fc936` | USDCAD, source principale : retenu, aucun défaut |
| `yahoo_CAD_X.json` | `2735511eb2f0f9b6d8f99c0b6380afcfaa4a0cdcb38f835089e2bff7250513ae` | USDCAD, repli |
| `fred_DEXUSAL.csv` | `b11f9478ed64c676d2a8f0cff25cce83ad696b528a6e2ebb577874b9030a6673` | AUDUSD, source principale : retenu ; saut du 06/10/2008 (−7,9 %, 10 écarts-types), confirmé par le repli ; saut du 14/10/2008 (+8,0 %, 10 écarts-types), confirmé par le repli |
| `yahoo_AUDUSD_X.json` | `f2e21308deae0f81c9bc1253062d04fac26053b1d05e0868e0426401c8435eae` | AUDUSD, repli |
| `fred_DEXUSNZ.csv` | `cf7628782c7d5996fe4326a9eeaf4c50c3b4466cbc48f7bbcecf6b1dee6a5472` | NZDUSD, source principale : retenu, aucun défaut |
| `yahoo_NZDUSD_X.json` | `15b95047abe4e6787e60fabc9bab01f05d8d9d01fbeaad75864be732d802ae70` | NZDUSD, repli |
| `stooq_XAUUSD.csv` | `a624371ffe5e9170897965ab57a8f10aaa107fd48507262e709431ac3ff8b072` | XAUUSD, source principale : retenu, aucun défaut |
| `yahoo_GC_F.json` | `56a5cd81b91745673a8584be8b402f32b76173a1032a2fd2f329ea0b328ee9da` | XAUUSD, repli |
| `stooq_XAGUSD.csv` | `49b6bb0aa359bc7b189a596c07054dfc7d8d0a4932f5a4bf229bd6c38d41356e` | XAGUSD, source principale : retenu ; saut du 30/01/2026 (−28,0 %, 14 écarts-types), confirmé par le repli |
| `yahoo_SI_F.json` | `983f02e170c7c42e69f0f0b77ec906e35d3a91a6860ee68abaa418917919ed00` | XAGUSD, repli |
| `bri_US.csv` | `8187d3e30033003f29bdffee13bd61c2605640ca5ebb65bc5fd0c4729f3fa7f6` | taux directeur américain ; sert aussi à Hong Kong (monnaie arrimée au dollar) |
| `bri_XM.csv` | `a60d9acb21dff6b4028e01123783be837586cdcb82351b6f49268d30672f2cbd` | taux directeur de la zone euro, à partir de 1999 |
| `bri_DE.csv` | `a0b9897ddae8f001a2652bb33f7eda9ad8d358ca3a3c6f5036cb357617aa87d0` | taux directeur allemand, pour 1998 (avant l'euro) |
| `bri_GB.csv` | `72065704f8a551b31663cdafb532aba29827216df1d60a4985badd1d76644558` | taux directeur britannique |
| `bri_JP.csv` | `7c17d885f1b05aa243c2585eba5665b7bf73bfc42a9a765e45ccd8319e92d5d9` | taux directeur japonais |
| `bri_CH.csv` | `c9ab75197a76434eab6ce2aaada5c76e023a66437c2eb41b045496385fec01eb` | taux directeur suisse |
| `bri_CA.csv` | `79189fdf439d9e6e04b492a99dff1f881552b3448a5e2690d58bafec57a5f3db` | taux directeur canadien |
| `bri_AU.csv` | `677b45addcfd835f189e23adaa6c8ed107d9a8c915a7c95ff4b6715e81632026` | taux directeur australien |
| `bri_NZ.csv` | `0863a2141269bec5d1bf5f76dd76a23165dc1b9028062844336e2fc47a0d3721` | taux directeur néo-zélandais |

Relevé du 30 octobre 2026 par `scripts/donnees_tendance.py`. Les fichiers
bruts sont dans `data/tendance/brut/` (hors du dépôt), leurs empreintes dans
`data/tendance/manifeste.csv`. **Aucun instrument n'est exclu.** Les six sauts
signalés sont des événements connus : le Brexit, le plancher de la Banque
nationale suisse puis sa levée, la crise d'octobre 2008 et la chute de
l'argent du 30 janvier 2026. Le trou de JP225 est la Golden Week de 2019 :
dix jours de fermeture pour l'avènement de l'empereur Naruhito.

- **Comment le repli confirme un saut.** Il doit bouger dans le même sens,
  d'au moins la moitié, entre les mêmes dates ou à une ou deux séances près.
  Les sources ne relèvent pas leurs cours à la même heure : FRED prend le
  cours de midi à New York, et Yahoo date ses barres de change autrement.
  Cette tolérance a été fixée **après** avoir vu le cas de GBPUSD. Entre les
  mêmes dates seulement, le Brexit n'était pas confirmé, car la clôture Yahoo
  du 24/06/2016 est fausse (1,4558, pour un plus bas du jour à 1,3239) : GBPUSD
  aurait été exclu. La règle vaut pour tous les instruments et a été fixée
  avant tout calcul de la stratégie. Un cours aberrant n'est confirmé dans
  aucune fenêtre, ce que vérifie un test sur données fabriquées.
- **Stooq** exige depuis mars 2026 une clé et une vérification du navigateur.
  Ses trois fichiers nécessaires (`^nkx`, `xauusd`, `xagusd`) ont été
  téléchargés à la main le 30 octobre 2026, puis recopiés tels quels. Les
  replis Stooq des cinq autres indices n'ont pas été téléchargés : ils
  n'avaient aucun défaut à trancher.

---

## Les cinq critères, traduits pour la tendance

Les cinq critères de `docs/protocole_defi_ftmo.md` posent une seule question :
le signal contient-il de l'information, ou le résultat vient-il d'autre chose ?
Ils sont gardés, mais traduits pour un signal acheteur/vendeur.

| # | critère | mesure | seuil | résultat |
|---|---|---|---|---|
| 1 | pouvoir prédictif | régression, sur tous les couples instrument-mois, du rendement du mois suivant sur celui des 12 derniers mois, tous deux divisés par la volatilité estimée ; constante commune, pas d'écrêtage ; écarts-types regroupés par mois | pente > 0, t > 2 | |
| 2 | profil par groupes | mêmes couples rangés en 5 groupes égaux selon le rendement passé ; rendement moyen du mois suivant dans chaque groupe (tous deux divisés par la volatilité) | croissant du groupe 1 au groupe 5, au plus une inversion entre voisins, **et** groupe 5 − groupe 1 > 0 avec t > 2 (regroupé par mois) | |
| 3 | mieux que l'exposition passive | rendements mensuels de la stratégie régressés sur ceux du portefeuille toujours acheteur (indices et métaux, même dosage) ; Newey-West, 3 retards | alpha > 0, t > 2 | |
| 4 | test du hasard | 1 000 stratégies où, pour chaque instrument, les périodes acheteuses et vendeuses de la vraie stratégie (mêmes nombres, mêmes durées) sont replacées dans un ordre tiré au hasard ; Sharpe net de coûts | p < 0,05 | |
| 5 | rendement net des coûts FTMO | moyenne des rendements mensuels nets | t > 2 (Newey-West, 3 retards) sur 2000-2026, **et** moyenne > 0 sur 2013-2026 | |

Critères 1 à 3 : avant les coûts FTMO (spread, commission, marge sur les
swaps) ; ils jugent l'information du signal. Critères 4 et 5 : après ces coûts.
Critère 4 : p = (1 + nombre de tirages au moins aussi bons que la vraie
stratégie) ÷ 1 001 ; graine du hasard `20261010`.

### Pourquoi ces traductions

- **Critère 3.** Pour les actions, c'était « le score inversé doit faire
  nettement pire ». Inverser un signal acheteur/vendeur donne exactement
  l'opposé : le test ne dirait rien. La vraie question devient : le signal
  fait-il mieux que d'être toujours acheteur ? Sans elle, être acheteur sur
  des marchés qui ont surtout monté passerait pour du talent. Les devises sont
  hors du portefeuille passif : elles n'ont pas de sens « acheteur » naturel.
- **Critère 4.** Même esprit que `random_null` dans `edge.py` : du hasard qui
  a la même persistance que le signal. Les tirages gardent les instruments,
  le dosage, le nombre de retournements et le temps passé acheteur ; seul le
  moment change. C'est le timing qui est jugé.
- **Critère 5.** Indices et devises n'ont pas de biais du survivant. Les biais
  à craindre ici sont les coûts et l'usure de l'effet depuis sa publication,
  d'où l'exigence sur 2013-2026.

---

## Décision prévue d'avance

**5 sur 5** → l'avantage est démontré au standard que le projet s'est fixé. On
passe à « Après le test », toujours sans payer.

**Entre 1 et 4** → aucun paiement. On consigne lequel a échoué et pourquoi.
Cette idée n'est pas retouchée.

**0 sur 5** → aucun paiement.

Ce n'est pas la première idée testée dans ce projet. Le seuil t > 2 est gardé
parce que l'effet vient de la littérature et non des données du projet, et
parce que réussir cinq critères à la fois par pur hasard est très improbable.
Toute idée testée après celle-ci aura son propre protocole et un seuil de
t > 2,5.

## Ce qui NE compte pas comme critère

- le résultat de tout réglage autre que ceux figés ici ;
- le résultat d'une sous-période choisie après coup : seules 2000-2026 et
  2013-2026 comptent ;
- une bonne année des fonds de suivi de tendance dans la presse ;
- le compte papier actions, qui teste une autre stratégie.

---

## À quoi s'attendre, même à 5 sur 5

Simulation simplifiée du défi : rendements quotidiens gaussiens, volatilité
10 % par an, garde-fous du bot à 4 % et 8 %, 4 jours minimum par phase,
5 ans au plus, 6 000 tirages par ligne.

| avantage réel | P(réussir les deux phases en 5 ans) | durée médiane si réussite | P(réussir en 12 mois) |
|---|---|---|---|
| aucun (Sharpe 0) | 26 % | 13 mois | 11 % |
| Sharpe 0,3 | 40 % | 13 mois | 18 % |
| Sharpe 0,5 | 52 % | 13 mois | 23 % |

Les études trouvent des Sharpe nettement plus élevés, mais avec 58 marchés et
avant 2010. Avec 15 instruments, des coûts de CFD et la période récente, 0,3 à
0,5 serait déjà un bon résultat. À 10 % de volatilité, un Sharpe de 0,5
rapporte en moyenne 5 % par an : le défi se joue alors sur plus d'un an et
réussit à peu près une fois sur deux.

---

## Après le test (seulement si 5 sur 5)

1. **Régler le risque.** Rejouer le défi sur les rendements nets
   **2013-2026**, pas sur toute la période, plus flatteuse, avec les règles
   FTMO et les garde-fous du bot, pour une volatilité de 5 ; 7,5 ; 10 ; 12,5
   ou 15 % par an. Retenir celle qui maximise la probabilité de réussir les
   deux phases en 12 mois au plus.
2. **Calculer l'espérance** du défi (prix, partage des gains) avec cette
   probabilité.
3. **Valider la machine** sur un compte de démonstration FTMO, pendant au
   moins deux révisions mensuelles sans incident. La passerelle
   `mt5broker.py` existe déjà, mais la librairie MetaTrader 5 ne tourne que
   sous Windows : la tour ou un serveur Windows, pas le VPS Ubuntu actuel.
4. **Respecter les règles de forme.** FTMO ne compte comme jour de trading
   qu'un jour où une position est **ouverte**, et en exige 4 par phase :
   étaler la première révision de chaque phase sur 4 jours. Compte Swing
   obligatoire, puisque les positions restent ouvertes le week-end.
5. Seulement alors, **décider de payer**.

## Étapes

| # | étape | fait le |
|---|---|---|
| 0 | relire, valider, committer et pousser ce protocole | 27 octobre 2026 (`f0afabb`) |
| 1 | relever les conditions FTMO dans MT5, remplir le tableau des coûts, committer | |
| 2 | télécharger et contrôler les données, consigner empreintes et exclusions, committer | 30 octobre 2026 |
| 3 | écrire le code et ses tests sur données synthétiques : sans signal, aucun avantage ; avec une tendance implantée, les critères la détectent ; modifier les cours futurs ne change aucune position passée | 30 octobre 2026 (`5b0b596`) |
| 4 | lancer le test **une seule fois**, remplir la colonne « résultat », committer la sortie brute | |
| 5 | appliquer la décision | |
