# quantbot — bot de trading actions multi-facteurs

Première version d'un bot de trading systématique sur actions, fondé sur des
statistiques et non sur des paris. Horizon de quelques semaines à quelques
mois, rebalancement mensuel, données journalières gratuites.

**Ce projet est un outil de recherche à but éducatif. Il ne constitue pas un
conseil en investissement.** Lis la section « Ce que ce projet ne fait pas »
avant d'y mettre le moindre euro.

---

## Prérequis : Python 3.11 ou 3.12

Le projet fonctionne dès Python 3.8 (les 66 tests y passent, aux mêmes chiffres
à la décimale près), mais **3.11 ou 3.12 est fortement recommandé**. Vérifie ta
version :

```bash
python --version
```

Si tu es en 3.8 ou 3.9, va voir la section « Problèmes d'installation » en bas :
Python 3.8 n'est plus maintenu depuis octobre 2024, et surtout `yfinance` — le
module qui télécharge les cours — n'y fonctionne qu'avec une version figée, qui
finira par ne plus rien télécharger le jour où Yahoo changera son interface.

## Démarrage en 3 minutes (sans réseau)

```bash
pip install -r requirements.txt

python scripts/fetch_data.py   --config config/us.yaml --synthetic
python scripts/run_backtest.py --config config/us.yaml --synthetic
```

Le mode `--synthetic` fabrique un univers de cours artificiels : tu vois
tourner toute la chaîne sans clé d'API ni quota. Ouvre ensuite
`reports/backtest_us.html`.

## Avec de vraies données

```bash
python scripts/fetch_data.py   --config config/us.yaml   # ~10 min la 1re fois
python scripts/run_backtest.py --config config/us.yaml
python scripts/walk_forward.py --config config/us.yaml   # le chiffre honnête
python scripts/check_edge.py   --config config/us.yaml   # y a-t-il un signal, oui ou non ?
python scripts/dashboard.py    --config config/us.yaml   # tableau de bord interactif
python scripts/daily_signals.py --config config/us.yaml --positions data/positions.csv
```

Remplace `config/us.yaml` par `config/fr.yaml` pour Euronext Paris.
Relancer `fetch_data.py` ne retélécharge que les jours manquants.

---

## Le tableau de bord

```bash
python scripts/dashboard.py --config config/us.yaml
python scripts/dashboard.py --config config/us.yaml --export reports/dashboard_us.html
```

Sans `--export`, un serveur local s'ouvre dans le navigateur. Chaque réglage —
nombre de lignes, pondération, poids de chaque facteur, fenêtres, filtre de
régime, fréquence de rebalancement, frais, délai d'exécution, période —
**relance réellement le backtest** : environ 0,4 s sur 64 titres, 2 à 3 s sur
500. Trois mesures sont disponibles à la demande : la décomposition, le test du
hasard et l'information coefficient. L'écoute est limitée à `127.0.0.1`, rien
ne sort de la machine, et aucune bibliothèque tierce n'est nécessaire (serveur
de la bibliothèque standard, graphiques dessinés sur `canvas`).

Avec `--export`, la même interface est figée dans un seul fichier HTML avec une
grille de variantes précalculée : ouvrable hors ligne, transmissible tel quel,
sans serveur ni dépendance. Les réglages qui exigeraient un recalcul y sont
masqués plutôt que présentés sans effet.

La courbe **univers entier** est celle qu'il faut battre : elle détient tous
les titres de l'univers, équipondérés, sans aucune sélection ni filtre. Comme
elle subit exactement le même biais du survivant que la stratégie, l'écart
entre les deux est la seule mesure non biaisée de ce que la sélection apporte.

### Deux vues : Analyse et Opérations

Le sélecteur en haut de page passe de l'**Analyse** — backtests, mesure
d'edge, tout ce qui précède — aux **Opérations**, qui remplacent la ligne de
commande pour le quotidien :

- état du compte, positions détenues, portefeuille visé, régime de marché ;
- les **sept contrôles** avant envoi, chacun avec sa pastille verte ou rouge :
  données à jour, jour de rebalancement, marché ouvert, échange sous le
  plafond, aucun achat à crédit, valorisation cohérente, compte non bloqué ;
- le plan d'ordres, et un bouton d'envoi **en deux temps** (le second affiche
  le nombre d'ordres et le montant avant de confirmer) ;
- la qualité d'exécution, et le rafraîchissement des cours en tâche de fond
  avec son avancement.

Deux garde-fous méritent d'être connus. La case *« passer outre le calendrier
et l'horaire »* ne lève que ces deux contraintes-là : les données périmées, le
crédit, le plafond et la cohérence des valorisations restent bloquants — ce
sont des conditions de justesse, pas de confort.

Et le tableau de bord ne travaille **que sur le compte de simulation**. Un clic
dans un navigateur n'a pas le poids d'une phrase tapée à la main : l'argent
réel reste sur `scripts/trade.py --reel`, avec ses trois verrous et sa
confirmation au clavier.

Le contrôle *« valorisation cohérente »* mérite un mot : le courtier valorise
tes positions au marché, le plan les valorise au dernier cours en cache — c'est
ce que modélise le backtest. Un écart de plus de 2 % entre les deux signale un
cache périmé, et donc un dimensionnement d'ordres faussé.

### Deux niveaux de lecture

Le sélecteur **Simple / Complet**, en haut à droite, ne cache ni ne simplifie
les calculs : il replie le vocabulaire technique, les tableaux de chiffres
bruts et les réglages fins. Le choix est retenu d'une visite à l'autre.

En **Simple** : la carte *En résumé* répond à trois questions dans l'ordre où
elles comptent — fait-on mieux que l'indice, fait-on mieux qu'acheter tout sans
réfléchir, et le score contient-il vraiment de l'information — avec une
pastille verte, rouge ou en attente, et une conclusion en une phrase. Un bouton
lance les trois tests d'un coup. Chaque mesure porte son nom en français
courant, son nom technique en second, et une phrase qui dit ce qu'elle veut
dire. Chaque graphique est précédé d'une ligne « à regarder » qui dit quoi
chercher, le détail restant replié sous « en savoir plus ». Cinq réglages au
lieu de dix-huit. Un glossaire ferme la page.

En **Complet** : tout ci-dessus, plus les huit mesures, les tableaux de
chiffres, la mesure statistique du signal (information coefficient) et les
dix-huit réglages.

L'ordre de lecture est délibéré. Les deux premières questions portent sur la
performance, la troisième sur sa réalité — et c'est la troisième qui commande :
une belle courbe sans signal est de la chance ou un artefact de données, jamais
un avantage. La carte de synthèse le dit explicitement tant que les tests n'ont
pas été lancés.

---

## La stratégie

À chaque fin de mois, on classe tous les titres de l'univers selon un score
composite, on achète les meilleurs, on pondère par le risque, et on ne touche
plus à rien jusqu'au mois suivant.

### Les trois facteurs

| Facteur | Calcul | Pourquoi |
|---|---|---|
| **Momentum 12-1** | rendement de t-252 à t-21 jours | L'anomalie la mieux documentée en finance académique (Jegadeesh & Titman, 1993). On saute le dernier mois, qui présente au contraire un effet de retournement. |
| **Faible volatilité** | écart-type annualisé sur 63 jours, **signe négatif** | La volatilité est l'une des rares grandeurs réellement persistantes en finance : celle d'hier prédit assez bien celle de demain, contrairement au rendement. |
| **Tendance** | écart au cours moyen 200 jours | Écarte les titres en descente d'escalier que le momentum long peut encore bien classer. |

Chaque facteur est converti en **z-score cross-sectionnel** : on compare les
titres entre eux à une date donnée, pas un titre à son propre passé. Le score
devient ainsi indépendant du niveau général du marché. Les z-scores sont bornés
à ±3 écarts-types pour qu'une valeur aberrante ne pilote pas le classement,
puis combinés par moyenne pondérée.

### Interruptions de cotation

Un jour sans cours n'est pas un jour à rendement nul — c'est une des sources
d'illusion les plus discrètes d'un backtest. Deux cas sont traités séparément :

- **Suspension puis reprise** : les cours sont prolongés sans limite de durée,
  si bien que le jour de la reprise porte la variation **complète** depuis la
  dernière cotation. Un titre suspendu quinze jours qui rouvre à −50 % coûte
  bien −50 % au portefeuille, au lieu de voir sa chute disparaître.
- **Arrêt définitif** (radiation, faillite) : passé `data.stale_days` séances
  sans cours réel, le titre devient intraitable. S'il est en portefeuille, il
  est soldé au dernier cours connu, frais compris, et ne peut plus être
  racheté même si son score gelé reste le meilleur.

Solder au dernier cours connu est **optimiste** pour une faillite et
pessimiste pour un rachat avec prime — et les données de prix seules ne
permettent pas de distinguer les deux. `execution.delisting_haircut_bps` sert
à tester la sensibilité du résultat à cette hypothèse : mets `2000` (soit
−20 %) et regarde ce qu'il reste de la performance.

### Le filtre de régime

Si l'indice de référence clôture **sous** sa moyenne mobile 200 jours, on passe
en liquidités. Ce filtre unique explique l'essentiel de la réduction de perte
maximale d'une stratégie momentum : il coupe l'exposition pendant les krachs.

Contrepartie, mesurée et testée dans ce projet : sur un marché sans direction,
il vend après la baisse et rachète après la hausse — l'effet de scie. Il
n'apporte quelque chose que si les baisses sont **durables**. Le test
`test_effet_de_scie_documente` formalise cette limite.

### La pondération

`inv_vol` par défaut : le poids de chaque ligne est proportionnel à l'inverse
de sa volatilité. On égalise la contribution au **risque** plutôt que les
montants investis, ce qui évite qu'un seul titre très volatil pilote la
performance. Aucune ligne ne dépasse `max_weight`.

---

## Chronologie : le point critique

```
jour t            clôture connue  →  calcul du score et des poids cibles
jour t + lag      passage des ordres à la clôture, frais prélevés
jour t + lag + 1  le portefeuille commence à produire des rendements
```

Aucun rendement n'est jamais attribué à une position décidée le même jour.
Avec `execution_lag: 1`, il s'écoule une journée complète entre le signal et
l'exécution : le rythme réel d'un bot qui tourne après la clôture et passe ses
ordres le lendemain.

Cette chronologie est **vérifiée par les tests**, pas seulement affirmée
(`tests/test_no_lookahead.py`).

---

## Comment savoir si le résultat est réel

Trois garde-fous, dans l'ordre d'importance :

**1. Le test du bruit.** `tests/test_no_lookahead.py` lance la stratégie sur
des marches aléatoires pures, où il n'y a rien à trouver. Elle doit y perdre
face à l'achat-conservation, puisqu'elle paie de la rotation pour rien. Une
belle performance sur du bruit est la signature d'une fuite d'information
future. Un test miroir vérifie l'inverse : quand un signal *existe*, la chaîne
doit le détecter — sinon une stratégie cassée qui ne trouve jamais rien
passerait le premier test haut la main.

**2. La validation walk-forward.** `scripts/walk_forward.py` choisit les
paramètres sur une fenêtre, les applique à la fenêtre **suivante** jamais vue,
puis avance. La courbe obtenue en recollant les fenêtres de test est le seul
résultat à peu près honnête du projet. Le script affiche l'écart entre le
Sharpe plein échantillon et le Sharpe hors échantillon : **c'est le prix du
surajustement**. Au-delà de 0,5 point d'écart, la performance vient du choix
des paramètres, pas d'un avantage réel.

**3. La validation de la configuration.** Écrire `top_n` au lieu de
`portfolio.top_n` dans la grille crée une clé que personne ne lit, sans la
moindre erreur : la grille n'explore alors rien, et la sensibilité affiche une
robustesse parfaitement fictive. `Config.with_overrides` refuse désormais tout
chemin inexistant. Ce bug était présent dans la première version de ce projet
et n'a été trouvé qu'en revue — d'où le garde-fou.

**4. La sensibilité aux paramètres.** `run_backtest.py --sensitivity` évalue
toute la grille et affiche la dispersion. Une stratégie robuste reste correcte
sur une large plage de réglages. Si un seul réglage surnage et que ses voisins
immédiats s'effondrent, c'est du surajustement.

Les quatre points ci-dessus vérifient que la **méthode** est honnête. Ils ne
disent rien de l'existence d'un **signal** : une méthode irréprochable
appliquée à un score sans information produit une courbe irréprochablement
inutile. D'où `scripts/check_edge.py`, qui pose la seconde question.

**5. L'information coefficient.** Corrélation de rang entre le score du jour de
signal et le rendement réalisé jusqu'au rebalancement suivant. Cette mesure
utilise **tous** les titres à chaque date, là où une courbe de performance ne
retient que les lignes sélectionnées : elle est bien plus efficace
statistiquement. Un IC moyen de 0,02 avec un t de 2 est un vrai signal faible ;
un IC de 0,001 avec un t de 0,05 est du bruit, quelle que soit l'allure de la
courbe qu'il produit.

**6. Le test du hasard.** On remplace le score par du bruit ayant la **même
persistance mensuelle de rang**, et on relance le backtest complet des
centaines de fois. Si le Sharpe réel tombe au milieu de cette distribution, la
stratégie ne fait pas mieux qu'un tirage. Le contrôle de la persistance est
indispensable : un bruit sans mémoire ferait tourner le portefeuille beaucoup
plus vite et paierait des frais que la vraie stratégie ne paie pas, ce qui
truquerait la comparaison en sa faveur.

**7. La décomposition.** On empile les briques une par une — indice, univers
entier équipondéré, sélection factorielle, pondération, filtre de régime — et
on regarde ce que chacune apporte. C'est la mesure qui répond à « ai-je
construit un tracker cher ? ».

**8. Le profil de déciles — le piège que les sept précédents ne voient pas.**
L'IC et la décomposition peuvent se contredire, et ce n'est pas un bug : ils ne
mesurent pas la même chose. L'IC est une corrélation de rang sur **tout** le
classement, il capte une relation monotone moyenne ; un portefeuille de 20
titres sur 500 ne vit que dans la queue droite. Un score peut donc avoir un IC
rigoureusement nul et produire un top 20 spectaculaire.

Reste à savoir pourquoi. On regarde le rendement moyen **par décile de score**.
Profil croissant : le score classe, le top 20 se mérite. Profil **en U** — les
deux bouts battent le milieu — : le score ne classe pas, il repère les titres
extrêmes. Dans ce cas acheter les 20 **pires** scores marche aussi bien, et le
test est immédiat : `inverted_score` relance le même moteur avec le score
retourné. La ligne qui tranche est l'écart meilleurs − pires ; s'il n'est pas
significatif, il n'y a pas de classement.

Ce piège est spécifique aux univers de survivants, et il y est maximal : les
titres extrêmes qui ont survécu jusqu'à aujourd'hui sont, par construction,
ceux qui sont montés. Une stratégie de momentum penche exactement vers eux.
C'est pourquoi « battre l'univers entier équipondéré » **ne suffit pas** comme
preuve pour une stratégie de momentum : le benchmark équipondéré neutralise le
biais du survivant pour une stratégie neutre, pas pour une stratégie qui incline
vers les gagnants.

---

## Ce que ce projet ne fait pas

- **Le biais du survivant n'est pas corrigé.** L'univers ne contient que des
  sociétés encore cotées aujourd'hui : les faillites et les retraits de cote
  ont disparu. Les rendements affichés sont donc **optimistes**, de l'ordre de
  1 à 4 points par an selon les études. Corriger cela demande des données
  point-in-time payantes (CRSP, Sharadar, Norgate). C'est le premier achat à
  envisager si le prototype tient la route.
- **Les frais sont modélisés forfaitairement.** L'impact de marché réel, la
  fourchette achat-vente sur les titres peu liquides et la fiscalité ne le
  sont pas.
- **Aucun passage d'ordre automatique.** `daily_signals.py` produit la liste
  des ordres ; tu les passes chez ton courtier. C'est volontaire pour une v1.
- **La liste des tickers français est statique** et doit être vérifiée.
  `fetch_data.py` signale automatiquement ceux qui ne renvoient rien.
- **Les radiations sont soldées au dernier cours connu**, faute de pouvoir
  distinguer une faillite d'un rachat avec prime à partir des seuls prix.
- **Un backtest n'est pas un résultat**, c'est une simulation sur le passé.

---

## Le robot : autonomie

```powershell
python scripts\robot.py --config config/us.yaml --a-blanc   # déroule tout, n'envoie rien
python scripts\robot.py --installer                         # tâche planifiée Windows
```

`robot.py` est fait pour être appelé **tous les jours** par le planificateur de
tâches, et pour ne rien faire la plupart du temps. Un programme qui décide
chaque jour s'il doit agir est plus sûr qu'un programme qui tourne en
permanence et qu'on oublie de surveiller.

Sa séquence : mise à jour des cours, recherche du dernier signal **révolu**
(fin de période passée, jamais la période en cours), puis quatre règles dans
`decider()` — une fonction pure, sans réseau ni disque, donc vérifiable ligne
à ligne :

| situation | décision |
|---|---|
| aucun signal révolu | rien |
| signal déjà traité | rien, même au dixième lancement du jour |
| signal tombé à la dernière séance | on attend le lendemain |
| signal vieux de plus de `--rattrapage` séances | on saute |
| sinon | on exécute |

L'état est sur disque (`data/robot_etat.json`), donc **l'idempotence survit aux
redémarrages** : deux lancements ne rebalancent jamais deux fois. La fenêtre de
rattrapage (3 séances par défaut) permet d'absorber une machine éteinte ou une
coupure réseau, sans jamais exécuter une décision périmée.

Aucun équivalent de `--force` : si un contrôle échoue, rien ne part, et le
signal reste à traiter pour le lendemain.

Chaque passage écrit un compte rendu lisible dans `data/robot_journal.txt` —
personne ne regardera l'écran d'une tâche planifiée.

### Ce que le robot ne fera jamais

Il ne touche **que le compte de simulation**. `--reel` n'existe pas et ne sera
pas ajouté : un programme qui envoie des ordres pendant que personne ne
regarde ne doit pas pouvoir engager de l'argent. Deux tests le vérifient, dont
un qui lance réellement `robot.py --reel` et attend un refus. Le chemin réel
reste `trade.py --reel`, avec ses trois verrous et sa confirmation au clavier.

---

## Le mode défi : limites de perte au format prop firm

```bash
python scripts/veille_defi.py --config config/us.yaml --etat-defi   # où en est-on
python scripts/veille_defi.py --config config/us.yaml --a-blanc     # évalue, n'envoie rien
python scripts/veille_defi.py --installer --minutes 30              # tâche Windows
python scripts/veille_defi.py --lever-verrou                        # repartir, après analyse
```

Le bloc `defi` de la configuration active des **limites de perte**, réglées
sous les seuils contractuels d'un défi : 4 % de perte journalière quand le
contrat dit 5 %, 8 % de perte totale quand il dit 10 %. Un garde-fou qui se
déclenche pile à la limite ne sert à rien — entre la décision du bot et
l'exécution de l'ordre, le marché continue de bouger.

Ce ne sont **pas des signaux**. Elles ne disent rien sur ce qu'il faut acheter
et n'influencent jamais la sélection : ce sont des interrupteurs. Les mélanger
à la stratégie donnerait des décisions dépendantes du chemin parcouru, donc
incomparables à un backtest.

### Statique ou glissante

| référence | plancher | conséquence |
|---|---|---|
| `statique` | −10 % du solde de **départ**, fixe | les gains accumulés deviennent un matelas définitif |
| `glissante` | −10 % du **plus haut** atteint | on peut être éliminé en étant encore en gain |

Le défaut est `statique`. Se tromper de règle dans un sens arrête le bot sans
aucune raison contractuelle ; dans l'autre, le laisse franchir une limite
réelle. Départ et plus-haut sont tous deux sur disque
(`data/defi_etat.json`) : le courtier ne connaît ni l'un ni l'autre, et sans
persistance ils repartiraient de la valeur du jour à chaque redémarrage.

### La perte journalière se mesure sur le capital de départ

Le contrat dit « 5 % of initial balance » : un montant fixe en dollars. Le
dénominateur est donc le capital de **départ**, pas la valeur de la veille.
Les deux formules coïncident le premier jour et divergent ensuite. Parti de
100 000 et monté à 200 000, une séance à −4 % coûte 8 000 : le contrat compte
8 000 sur une enveloppe de 5 000 — éliminé — là où rapporter la perte à la
veille affichait −4 % et laissait passer. Mesurer une limite fixe avec une
règle élastique donne le mauvais verdict dans les deux sens.

### Le verrou, et le piège qu'il évite

À la brèche, le bot **annule ses ordres en vol, vend tout, puis se
verrouille**. Geler les achats en gardant vingt lignes longues dans le marché
qui vient de déclencher la limite, c'est attendre les deux points qui restent
avant l'élimination. `defi.solder_sur_verrou: false` donne le comportement
« gel seul », où l'opérateur solde à la main.

Le verrou est écrit sur disque, ne se lève **jamais** tout seul — même si
l'equity remonte au-dessus de tout — et bloque aussi l'**amorçage**. Cette
dernière clause est la plus importante du mécanisme :

```
perte → le bot vend tout → compte vide → « tiens, un compte neuf »
      → rachat du portefeuille entier au passage suivant
```

Solder laisse le compte vide, et un compte vide est exactement la condition
d'amorçage. Sans verrou, la liquidation d'urgence se faisait racheter le
lendemain matin, dans le marché même qui l'avait déclenchée.

**L'objectif atteint verrouille aussi**, avec un motif distinct. C'est le seul
moment du défi où le rapport risque/gain est strictement défavorable : la phase
est acquise, chaque séance de plus ne peut que la reprendre.

### Pourquoi un second script planifié

`robot.py` tourne à 21:00 Paris, soit **après** la clôture de New York. Il y
vérifiait la limite journalière — qui se franchit en séance, sur du non
réalisé. La constater le soir, c'est en prendre acte : le compte était éliminé
depuis six heures. Une limite qu'on ne mesure qu'après la clôture n'est pas un
garde-fou, c'est un compte rendu d'autopsie.

`veille_defi.py` est donc séparé, et volontairement minuscule : un appel REST
au compte, un calcul pur, et la liquidation si besoin. Aucun cours téléchargé,
aucun score recalculé, aucun verrou de moteur pris — `operations.etat()`
recalculerait tout l'univers et gèlerait le tableau de bord vingt fois par
séance. Il n'achète jamais rien : il ne sait que sortir. Hors séance, la
consigne de liquidation est **conservée** plutôt qu'exécutée, et part à
l'ouverture suivante.

### Ce que le mode défi ne résout pas

Les limites sont respectées ; cela ne rend pas la stratégie compatible avec un
défi. La perte maximale hors échantillon de cette stratégie est de **−26 %**,
soit deux fois et demie le seuil qui élimine. Un garde-fou empêche de franchir
une limite, il ne réduit pas la volatilité de ce qu'il surveille. Et les défis
de prop firm sont en pratique des comptes **CFD sur MT5/cTrader**, avec une
vingtaine d'actions disponibles — là où cette stratégie classe 500 titres les
uns par rapport aux autres. Le mode défi est un garde-fou de risque utile en
soi, pas un billet d'entrée.

---

## Connecter le bot a un courtier

Le projet parle a **Alpaca** en REST direct (pas de SDK, pas de dependance
supplementaire : `requests` suffisait). Deux mondes strictement separes :
la **simulation**, qui est le defaut partout, et le **reel**, qui exige trois
verrous indépendants plus une confirmation tapée à la main.

### 1. Ouvrir un compte de simulation

Sur [app.alpaca.markets](https://app.alpaca.markets), inscription puis
activation de l'authentification à deux facteurs. La simulation ne demande
**aucun document, aucun justificatif de domicile, aucun versement** — elle est
ouverte immédiatement, avec 100 000 $ fictifs.

### 2. Générer les clés

Dans le tableau de bord, section **Home**, bouton de génération des clés. La
clé secrète **ne s'affiche qu'une seule fois** : copie-la tout de suite. Les
clés de simulation et les clés du compte réel sont **différentes et non
interchangeables** ; se tromper produit un `401`, pas un ordre au mauvais
endroit.

### 3. Les donner au projet — jamais dans le dépôt

```powershell
# PowerShell, pour la session en cours
$env:ALPACA_KEY_ID     = "PK..."
$env:ALPACA_SECRET_KEY = "..."
```

Ou, en repli, un fichier `secrets/alpaca.json` (le dossier `secrets/` est
exclu par `.gitignore`) :

```json
{ "paper": { "key_id": "PK...", "secret_key": "..." },
  "live":  { "key_id": "AK...", "secret_key": "..." } }
```

Les identifiants ne sont jamais journalisés, jamais affichés, et n'apparaissent
ni dans un `repr` ni dans un message d'erreur — c'est testé.

### 4. Le solde du compte

**En simulation.** Le compte démarre à 100 000 $. Il n'existe **pas
d'endpoint REST** pour changer ce montant : il faut passer par le tableau de
bord, cliquer sur le numéro du compte papier, puis *Open New Paper Account*, et
fixer le solde de départ du nouveau compte. Le nouveau compte a ses propres
clés. Choisis le montant que tu mettrais réellement : simuler 100 000 $ quand
on en placerait 5 000 masque complètement le poids des frais fixes et des
lignes trop petites pour être achetées.

**En réel.** C'est une ouverture de compte de courtage : vérification
d'identité, justificatif d'adresse, formulaire **W-8BEN** (généré pendant
l'inscription pour un résident fiscal non américain), puis versement depuis la
section *Banking* du tableau de bord — à partir de 1 $ pour les non-résidents
américains. La disponibilité dépend du pays de résidence fiscale : Alpaca
demande de les contacter pour confirmer.

### 5. Les commandes

```bash
python scripts/trade.py --config config/us.yaml --compte      # etat du compte, positions
python scripts/trade.py --config config/us.yaml               # plan d'ordres, SANS rien envoyer
python scripts/trade.py --config config/us.yaml --envoyer     # envoie (simulation)
python scripts/trade.py --config config/us.yaml --annuler     # annule les ordres en attente
python scripts/verifier_executions.py --config config/us.yaml # glissement reellement subi
```

`verifier_executions.py` compare le cours qui a servi à **dimensionner**
chaque ordre au cours réellement **obtenu**, et le rapporte en points de base.
C'est la seule mesure qu'une courte période de simulation produise vraiment :
vingt ordres font vingt observations, là où le rendement du portefeuille sur
quelques semaines n'est que du bruit. Le script compare le résultat à
l'hypothèse de frais du backtest et dit de combien la corriger.

Sans `--envoyer`, **rien ne part** : le script affiche le plan et s'arrête.
Il refuse par ailleurs de travailler si aujourd'hui n'est pas un jour de
rebalancement (`--force`), si le marché est fermé (`--hors-seance`), si les
données en cache datent de plus de 5 jours, si un titre visé n'est pas
négociable chez le courtier, ou si l'échange total dépasse le plafond de
`broker.max_echange_par_seance` — 200 % par défaut, c'est-à-dire tout vendre
puis tout racheter, le maximum légitime. Au-delà, c'est un bug de calcul.

Tout ce qui est envoyé est écrit dans `data/journal_ordres.csv`.

### 6. Les verrous de l'argent réel

Trois verrous **indépendants**, placés à trois endroits différents exprès —
aucun ne peut être ouvert par mégarde par les deux autres, et aucune tâche
planifiée héritée ne peut les réunir toute seule :

1. `broker.allow_live: true` dans le YAML de configuration ;
2. la variable d'environnement `QUANTBOT_LIVE` valant exactement
   `oui-argent-reel` sur la machine ;
3. l'option `--reel` sur la ligne de commande.

Puis, à l'exécution, il faut taper les 4 derniers caractères du numéro de
compte. Un `--envoyer` reste nécessaire par-dessus tout ça.

### Ce que cette couche ne fait pas

Ordres **au marché uniquement** — pas de cours limite, pas de stop. Les prix
utilisés pour dimensionner les ordres sont les **clôtures de la veille** en
cache, pas des cours en temps réel : le montant réellement exécuté diffère
donc de celui affiché. Aucune reprise sur erreur : un ordre refusé est
journalisé et le script passe au suivant. Aucune vérification que les ordres
ont bien été exécutés — relance `--compte` le lendemain.

---

## Bugs corrigés — à ne pas réintroduire

Chacun a produit des résultats **faux et plausibles**, ce qui est le pire cas.
Aucun n'a levé d'exception ; tous ont été trouvés en revue.

1. **Grille de paramètres inerte.** La grille déclarait `top_n` là où le moteur
   lit `portfolio.top_n` : une clé que personne ne lit, créée sans erreur.
   12 combinaisons ne produisaient que 2 résultats distincts, le walk-forward
   était décoratif et la sensibilité annonçait une robustesse fictive.
   `Config.with_overrides` refuse désormais tout chemin inexistant.
2. **Radiations.** Un titre qui cessait de coter restait en portefeuille à un
   cours gelé et sa chute disparaissait. Les cours sont désormais prolongés
   sans limite (une reprise après suspension porte la variation complète) et,
   passé `data.stale_days` séances sans cotation réelle, le titre est soldé.
3. **Formule de Sortino fausse** (écart-type des rendements négatifs autour de
   leur propre moyenne, au lieu du moment d'ordre 2 autour de zéro).
4. **`daily_signals.py` ne détectait jamais un jour d'exécution.**
5. **Indice de référence en rendement de prix.** Les titres sont téléchargés
   dividendes réinvestis, mais la référence était `^GSPC`, un indice de prix.
   L'écart — environ 2 points par an aux États-Unis, 3 à Paris — était offert à
   la stratégie. La référence est désormais `SPY` (et `CAC.PA` pour Paris), des
   ETF dont la série ajustée est bien une série total return.
   *Corollaire, moins évident :* l'exclusion du benchmark de l'univers
   investissable reposait sur « le ticker commence par `^` ». `SPY` est un
   ticker ordinaire — sans exclusion explicite, la stratégie pouvait détenir
   l'indice qu'elle est censée battre. Testé dans `test_garde_fous.py`.
6. **Univers de repli silencieux.** `pd.read_html(url)` part avec le
   User-Agent par défaut de Python, que Wikipedia refuse par un 403. La
   récupération des constituants du S&P 500 échouait donc **systématiquement**,
   et le code retombait sur `US_FALLBACK_TICKERS` — 64 grandes
   capitalisations — avec un simple `logger.warning`. Des mois de backtests
   annoncés « S&P 500 » ont en réalité tourné sur 64 mégacapitalisations.
   Trois corrections : requête via `requests` avec un en-tête explicite,
   `universe.allow_fallback: false` par défaut (le repli lève au lieu de se
   taire), et `universe.min_tickers` qui refuse de démarrer sur un univers
   tronqué. Un benchmark configuré mais absent du cache lève également, au lieu
   de basculer en silence le filtre de régime sur la moyenne de l'univers.
7. **Python 3.8 :** `duckdb` retiré (jamais importé, cassait l'installation
   sans Visual C++) et `multitasking` épinglé en 0.0.11 — au-delà, `yfinance`
   ne s'importe plus sous 3.8.
8. **`ops.compte_vide()` n'existait pas.** Le seul de cette liste qui levait
   bien une exception — et qui a quand même tourné dans le vide, parce que
   personne ne lisait la sortie d'une tâche planifiée. `compte_vide` était une
   **variable locale** de `Operations.etat()` ; `robot.py` l'appelait comme une
   méthode. Chaque passage levait `AttributeError` juste après la mise à jour
   des cours, avant toute décision, et rien n'était jamais envoyé. Le trou
   n'était pas dans la logique — `decider()` est testée ligne à ligne — mais à
   la **couture** entre deux modules tous les deux bien testés. D'où
   `TestLAppelQuiNExistaitPas`, qui vérifie le contrat d'appel plutôt qu'un
   comportement.
9. **La perte journalière était rapportée à la veille, pas au départ.** Le
   contrat dit « 5 % of initial balance » : un montant fixe. Les deux formules
   coïncident le premier jour, ce qui explique que le bug ait pu passer. Parti
   de 100 000 et monté à 200 000, une séance à −4 % coûte 8 000 — soit 8 000
   sur une enveloppe de 5 000, donc éliminé — quand l'ancienne formule affichait
   −4 % et laissait passer. Et symétriquement, après une baisse, elle arrêtait
   le bot pour une perte que le contrat ne comptait pas.
10. **Une brèche de défi ne survivait pas au rebond, et l'amorçage rachetait
    tout.** Le garde-fou bloquait l'envoi, ne soldait rien, et oubliait la
    brèche dès que l'equity remontait. Pire : solder laisse le compte **vide**,
    or un compte vide est exactement la condition d'amorçage. La séquence
    complète était perte → liquidation → compte vide → « un compte neuf » →
    rachat du portefeuille entier au passage suivant, dans le marché même qui
    venait de déclencher la limite. Le verrou est désormais sur disque, ne se
    lève jamais seul, et bloque l'amorçage.
11. **Une limite journalière contrôlée après la clôture.** `robot.py` tourne à
    21:00 Paris ; la limite se franchit en séance, sur du non réalisé. Elle
    était donc constatée six heures trop tard — un compte rendu d'autopsie, pas
    un garde-fou. D'où `veille_defi.py`, planifié en séance.
12. **Le coût du levier annoncé comme absent, facturé au mauvais taux.** Le
    commentaire de `us.yaml` disait « le backtest ne facture aucun intérêt sur
    l'argent emprunté ». Faux dans les deux sens : `cash * rf_daily` facturait
    bien un intérêt, mais au taux **sans risque**, que personne ne pratique.
    `execution.financing_spread_bps` ajoute la marge du courtier sur la seule
    part empruntée. Sans levier — le défaut — rien ne change, ce qu'un test
    vérifie au dernier chiffre près.

---

## Structure

```
config/us.yaml, fr.yaml     tous les réglages, aucun paramètre en dur
src/quantbot/
  config.py       config accessible par chemin pointé ("factors.momentum.lookback")
  universe.py     listes de tickers + avertissement biais du survivant
  datafeed.py     téléchargement, cache Parquet incrémental, matrices larges
  synthetic.py    générateur de cours artificiels (tests + démo sans réseau)
  factors.py      momentum, volatilité, tendance, z-scores, score composite
  portfolio.py    sélection, pondération, plafonnement, calendrier
  backtest.py     moteur jour par jour : dérive des poids, frais, radiations
  metrics.py      CAGR, Sharpe, Sortino, perte maximale, rotation, alpha, béta
  validation.py   walk-forward et sensibilité aux paramètres — la MÉTHODE
  edge.py         IC, test du hasard, décomposition, profil de déciles,
                  inversion du score — le SIGNAL
  explore.py      couche de calcul du tableau de bord (une seule source de chiffres)
  webui.py        page du tableau de bord : CSS, graphiques canvas, application
  live.py         portefeuille cible du jour — le MEME calcul pour l'affichage,
                  l'envoi des ordres et le robot, jamais trois. La décision est
                  prise à la clôture du SIGNAL, pas à la séance en cours.
  orders.py       cible + positions -> ordres. Fonction pure, sans reseau
                  (dont seuil_minimal : le seuil suit la taille du compte)
  broker.py       client REST Alpaca + les trois verrous de l'argent reel
  operations.py   etat du compte, controles, envoi — vue Operations et CLI
  executions.py   qualite d'execution : trois references, une seule conclut
  defi.py         limites de perte au format prop firm. CONSTATE, n'agit pas :
                  la liquidation est dans operations.appliquer_defi()
  volatilite.py   pilotage de l'exposition a volatilite cible (inactif par defaut)
  diversification.py  contrainte de correlation a la selection (inactive par defaut)
  pointintime.py  univers point-in-time, pour mesurer le biais du survivant
  surveillance.py reconciliation, sante du robot, rebalancement incomplet
  report.py       graphiques et rapport HTML autonome
scripts/          fetch_data, run_backtest, walk_forward, check_edge,
                  dashboard, daily_signals, trade, verifier_executions,
                  robot, veille_defi, archiver, verifier_sante,
                  mesurer_biais, mesurer_volatilite, nouveau_compte
secrets/          identifiants du courtier — hors depot, jamais versionne
tests/            381 tests : anti-fuite temporelle, garde-fous, mesure d'edge,
                  limites de defi, coutures entre modules
```

## Réglages utiles

Tout se pilote depuis le YAML, sans toucher au code :

```yaml
portfolio:
  top_n: 20            # moins de lignes = plus de risque, plus de rotation
  weighting: inv_vol   # equal | inv_vol
  max_weight: 0.10
execution:
  rebalance: monthly   # weekly | monthly | quarterly
  commission_bps: 5.0  # sous-estimer les frais est la 1re cause d'illusion
  slippage_bps: 5.0
  delisting_haircut_bps: 0.0   # mets 2000 pour tester l'hypothèse de faillite
data:
  stale_days: 10       # sans cours réel pendant N séances -> titre intraitable
regime:
  enabled: true
  ma_window: 200
```

Quelques surcharges en ligne de commande :

```bash
python scripts/run_backtest.py --config config/fr.yaml --no-regime --top-n 10
python scripts/run_backtest.py --config config/us.yaml --sensitivity
```

## Tests

```bash
python -m pytest tests/ -v
```

381 tests : exactitude des facteurs, scénarios de backtest à résultat connu
d'avance, calcul des frais, robustesse aux trous de données et aux
introductions en bourse récentes, la batterie anti-fuite temporelle, les
garde-fous contre les replis silencieux (`test_garde_fous.py`) et la mesure
d'edge dans les deux sens — détecter un signal implanté, ne rien détecter sur
du bruit (`test_edge.py`), le profil de déciles dans les deux sens également,
la planification des ordres (`test_orders.py`), les verrous du courtier
(`test_broker.py`), la discipline du robot (`test_robot.py`) et surtout
l'**équivalence entre la décision en direct et celle du backtest**
(`test_live.py`) — mêmes poids, à la neuvième décimale.

Deux familles ajoutées depuis : les limites de défi (`test_defi.py`,
`TestVerrouEtLiquidation`) — dont le test du verrou qui survit au rebond et
celui de l'amorçage qui ne rachète pas un compte soldé — et les **coutures**
entre modules (`TestLAppelQuiNExistaitPas`). Cette dernière famille existe
parce que le bug le plus coûteux du projet n'était pas dans une logique mal
écrite mais dans un appel à une méthode qui n'existait pas, entre deux modules
tous les deux bien testés. Un test par module ne voit pas ce genre de trou.

---

## Problèmes d'installation

### `error: Microsoft Visual C++ 14.0 or greater is required` (Windows)

C'est `duckdb` qui déclenche ça : il n'a pas de version précompilée pour ta
combinaison Python/Windows, donc pip essaie de le compiler depuis les sources.

**Le projet n'importe jamais duckdb** — il ne servait qu'à explorer le cache
Parquet en SQL à la main. Il a été retiré de `requirements.txt` ; relance
simplement :

```bash
pip install -r requirements.txt
```

Si tu veux quand même DuckDB plus tard : `pip install duckdb` avec un Python
3.9+, où des versions précompilées existent.

### Je suis en Python 3.8

Tout fonctionne sauf `yfinance`, qui échoue dès l'import : une de ses
dépendances (`multitasking`) utilise la syntaxe `type[X]`, apparue en Python
3.9. Deux options.

**Option recommandée — installer Python 3.12.** Télécharge-le sur
[python.org](https://www.python.org/downloads/), coche « Add python.exe to
PATH » à l'installation, puis :

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Le `venv` isole le projet : ton Python 3.8 et tout ce qui en dépend restent
intacts.

**Option de dépannage — rester en 3.8.** Un fichier de versions épinglées est
fourni, avec le contournement du problème `multitasking` :

```bash
pip install -r requirements-py38.txt
```

Testé : les 66 tests passent et la chaîne complète tourne. Mais tu restes
figé sur pandas 2.0.3 et yfinance 0.2.65 — et comme Yahoo change régulièrement
son interface non officielle, cette version finira par ne plus télécharger.

### `pip` est en version 21.1.1

Sans rapport avec l'erreur ci-dessus, mais autant le faire :

```bash
python -m pip install --upgrade pip
```

---

## Suite logique

Dans l'ordre où ça vaut le coup :

0. **Vérifier qu'il y a un signal avant tout le reste.** `check_edge.py`, ou
   le tableau de bord. Si l'IC du composite est nul et que le test du hasard
   place la stratégie sous la médiane des tirages, les étapes suivantes n'ont
   pas d'objet : il n'y a rien à valider, rien à paper-trader, rien à
   apprendre. Cette étape coûte deux minutes et fait gagner des mois.

   > **FAIT le 21 septembre 2026, sur les 505 séries réelles. Résultat : non.**
   > IC du composite **t = −0,51** (barre fixée à 2), profil de déciles
   > **décroissant** (les pires scores battent les meilleurs de 0,26 %/mois),
   > **le score inversé rapporte 1,69 point de plus**, et le null aléatoire
   > donne le même verdict au score et à son inverse — il mesure donc la
   > mécanique de construction, pas le signal. L'univers coûte par ailleurs
   > **13,2 points de rendement annuel** de biais du survivant.
   >
   > Ce qui reste et qui est réel : le filtre de régime, qui divise presque par
   > deux la perte maximale (−29,6 % contre −51,6 %). C'est une couverture de
   > marché, pas un classement de titres.
   >
   > **Les étapes 1 à 4 ci-dessous sont donc sans objet en l'état.** Détail
   > complet des cinq mesures, avec les commandes pour les rejouer :
   > [`docs/verdict_edge_2026-09-21.md`](docs/verdict_edge_2026-09-21.md).
1. **Faire tourner le walk-forward sur de vraies données.** Si l'écart entre
   Sharpe plein échantillon et hors échantillon dépasse 0,5, il n'y a pas
   d'avantage : inutile d'aller plus loin.
2. **Paper trading pendant 3 à 6 mois.** Génère les signaux chaque mois, note
   les ordres, ne mets pas d'argent. C'est le seul test qui compte vraiment,
   parce qu'il est hors échantillon par construction.
3. **Acheter des données point-in-time** si les deux étapes précédentes sont
   concluantes. C'est ce qui supprime le biais du survivant.
4. **Alors seulement, envisager le machine learning.** Un `LightGBM` qui
   apprend à filtrer les signaux de la stratégie de base, entraîné en
   walk-forward strict, avec les facteurs actuels comme variables d'entrée.
   Le point d'entrée naturel est `factors.py` : le score composite y est
   calculé par une moyenne pondérée fixe, qu'un modèle peut remplacer.
   Tant que l'étape 1 n'est pas franchie, le ML n'a rien à apprendre : il
   ne créera pas un avantage à partir de rien.
