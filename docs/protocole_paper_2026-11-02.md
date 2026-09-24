# Protocole : le gel du 22 septembre au 2 novembre 2026

**Écrit le 22 septembre 2026, avant de connaître le résultat.**

## L'engagement

Du **22 septembre au 2 novembre 2026**, on ne touche à rien : ni la config, ni
les facteurs, ni les seuils, ni l'univers. Le robot tourne, le relevé
enregistre, on regarde.

Six semaines, soit environ **30 séances** et **6 rebalancements** hebdomadaires.

Compte : Alpaca simulation `PA3S61TZRXG3`, 505 titres, départ 100 000 $ le
18 septembre 2026.

## Ce que ce gel peut trancher

Quatre questions, toutes sur **la machine**, toutes vérifiables :

### 1. Le robot tient-il sans surveillance ?

- **critère** : zéro traceback dans `data/robot_sortie.log` sur la période
- **vérification** : `grep -c Traceback data/robot_sortie.log`
- **contexte** : il a planté le 17 septembre sur un `AttributeError`, et
  personne ne s'en est aperçu parce que personne ne lit la sortie d'une tâche
  planifiée. Il tourne correctement depuis le 18.

### 2. Le robot donne-t-il signe de vie tous les jours ?

- **critère** : `dernier_passage` jamais plus vieux que 78 h en semaine
- **vérification** : `python scripts/verifier_sante.py --config config/us.yaml`
- **contexte** : corrigé le 22 septembre. L'état n'était écrit que sur les
  passages qui agissent — six soirs sur sept le robot ne fait rien, donc la
  veille le croyait mort. Un battement de cœur est maintenant écrit à chaque
  passage.

### 3. Le verrou solde-t-il pour de vrai ?

- **critère** : déclenché au moins une fois, volontairement, et les positions
  sont effectivement fermées chez le courtier
- **méthode** : mettre `defi.perte_jour_max` à `0.001` un matin, lancer
  `python scripts/veille_defi.py`, vérifier sur Alpaca que tout est soldé,
  remettre `0.04`, puis `--lever-verrou`
- **contexte** : ce chemin n'a jamais tourné que contre `tests/faux_mt5.py` et
  `FauxApi`. **C'est le critère le plus important des quatre** : un
  coupe-circuit non testé contre un vrai courtier est une hypothèse, pas une
  protection.

### 4. Quel est le glissement RÉELLEMENT subi ?

- **critère** : pas de seuil fixé d'avance — c'est une **mesure**, pas un test
- **vérification** : `python scripts/verifier_executions.py --config config/us.yaml`
- **contexte** : le backtest suppose 5 bps de glissement. Avec 530 % de rotation
  annuelle, si le réel est à 20 bps, la performance annoncée est fausse de
  plusieurs points par an.

  **Attention à la référence.** Les fourchettes relevées le 18 septembre vont de
  2,8 à 1 046 bps, médiane 279 — mais elles viennent du flux **IEX**, une seule
  place qui fait ~2 % du volume américain. Ces chiffres mesurent le flux de
  données, pas le marché. La seule mesure valable est **le prix d'exécution réel
  contre le prix de décision**, ce que fait `verifier_executions.py` avec sa
  référence A. Ne pas conclure sur les fourchettes IEX.

## Ce que ce gel ne peut PAS trancher

**L'existence d'un avantage.** Voir `docs/protocole_defi_ftmo.md`.

En résumé : la bande de bruit à six semaines est de **± 8,6 %**. Tout résultat
dans cette fourchette ne dit rien. Et pour qu'un résultat dise quelque chose, il
faudrait dépasser +8,6 %, soit 100 % annualisé — ce qui signalerait un bug
plutôt qu'un succès.

Le rendement du 2 novembre **n'est pas un critère**, et c'est écrit ici pour
qu'on ne soit pas tenté de le lire comme tel le jour venu.

`scripts/releve_quotidien.py` affiche d'ailleurs systématiquement le rendement à
côté de sa bande, précisément pour rendre cette lecture impossible.

## La décision du 2 novembre

**4 critères sur 4** → la machine est digne de confiance. Elle peut servir à
exécuter une stratégie — laquelle reste une question ouverte, tranchée par
l'autre protocole.

**Un critère manqué** → on répare, et on recompte six semaines à partir de la
réparation. Pas de demi-mesure : une machine qui plante une fois sur six
semaines plantera pendant un défi.

**Ce qui n'est pas au programme du 2 novembre** : payer un défi. Cette décision
a ses propres critères, et six semaines de paper trading n'en font bouger aucun.

## Ce qui tourne pendant le gel

| tâche | fréquence | rôle |
|---|---|---|
| `quantbot` | tous les jours 21:00 | le robot : décide, rebalance le lundi |
| `quantbot-releve` | tous les jours 22:30 | enregistre l'equity et sa bande de bruit |

`quantbot-coupe-circuit` reste **désinstallée** : elle ne sert qu'aux comptes
FTMO/MT5, et sur Alpaca c'est `veille_defi.py` qui joue ce rôle.

## Relevé

`data/releve_quotidien.csv` — une ligne par jour, avec le rendement, le nombre
de séances, la bande de bruit et un drapeau `dans_le_bruit`.

```bash
python scripts/releve_quotidien.py --historique
```

## Journal des incidents

À remplir au fil de l'eau. Un incident n'invalide pas le gel — il le documente.

| date | quoi | suite donnée |
|---|---|---|
| 21/09 | rebalancement du lundi manqué : aucun passage du robot ce jour-là dans `robot_sortie.log` | à élucider avant le 2 novembre |
