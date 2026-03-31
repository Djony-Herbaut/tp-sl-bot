# Bot TP/SL Analyzer — Wallets Solana / Pump.fun

Bot Telegram d'analyse de stratégies Take Profit / Stop Loss basé sur l'historique
réel d'un wallet Solana sur les 30 derniers jours de trading Pump.fun.

**Version 3 — TP/SL nets de gas fees et délais d'exécution réels.**

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Ce qui change dans la v3](#2-ce-qui-change-dans-la-v3)
3. [Architecture du projet](#3-architecture-du-projet)
4. [Description des modules](#4-description-des-modules)
5. [APIs utilisées](#5-apis-utilisées)
6. [Logique algorithmique complète](#6-logique-algorithmique-complète)
7. [Paramètres configurables](#7-paramètres-configurables)
8. [Installation locale](#8-installation-locale)
9. [Déploiement Railway](#9-déploiement-railway)
10. [Utilisation du bot](#10-utilisation-du-bot)
11. [Format de sortie](#11-format-de-sortie)
12. [Coûts et limites](#12-coûts-et-limites)
13. [Évolutions possibles](#13-évolutions-possibles)

---

## 1. Vue d'ensemble

### Problème résolu

Sur Pump.fun, copier un wallet performant ne suffit pas. Même avec un bon wallet
cible, des TP/SL mal calibrés entraînent :

- Des sorties trop tôt (TP atteint sur le papier, mais pas dans la réalité à cause des frais)
- Des stops trop larges (le trade est en perte nette avant même d'être clôturé)
- Un décalage d'entrée systématique (on entre 1 seconde après le signal = prix plus élevé)

Ce bot résout ces trois problèmes en intégrant directement dans le calcul des TP/SL :
les gas fees réels, le délai d'entrée de 1 seconde, et le délai de sortie de 1 seconde.

### Ce que fait le bot

1. Récupère toutes les transactions SWAP du wallet sur 30 jours (Helius)
2. Filtre uniquement les achats Pump.fun (BUY)
3. Pour chaque token, récupère l'historique de prix 1m après l'entrée (GeckoTerminal)
4. Calcule le prix d'entrée effectif du copieur (1s de délai + slippage)
5. Calcule le gain maximum et le drawdown net de frais et délais pour chaque trade
6. Construit deux stratégies par analyse percentile sur les métriques nettes
7. Calcule le winrate par simulation chronologique sur l'historique
8. Optionnellement simule le P&L USD

---

## 2. Ce qui change dans la v3

### 2.1 Gas fees intégrés dans les métriques

Les gas fees sont convertis en pourcentage du capital investi et déduits
de chaque gain avant de construire les stratégies.

```
Coût total round-trip : 0.044 SOL
  Buy  : Priority Fee 0.001 + Tip 0.001 + Bribe 0.02 = 0.022 SOL
  Sell : Priority Fee 0.001 + Tip 0.001 + Bribe 0.02 = 0.022 SOL

Pour un trade de 0.1 SOL :
  gas_cost_pct = (0.044 / 0.1) * 100 = 44%
  → Le token doit monter de +44% avant d'être rentable

Pour un trade de 0.5 SOL :
  gas_cost_pct = (0.044 / 0.5) * 100 = 8.8%
  → Seuil de rentabilité à +8.8%
```

**Implication directe :** les TP de la v3 sont systématiquement plus élevés que
ceux de la v2, car ils doivent d'abord absorber les frais avant de générer un gain net.

### 2.2 Délai d'entrée de 1 seconde

Le wallet cible achète au temps T au prix P_cible.
Notre ordre s'exécute à T+1 seconde.

En 1 seconde sur Pump.fun, le prix peut avoir monté de 3% à 20% selon la
volatilité du token. On modélise ce slippage de deux façons :

**Méthode principale (bougies 1m disponibles) :**
On cherche la bougie à T+1 dans l'historique et on utilise son open comme
prix d'entrée effectif. C'est le reflet le plus proche du prix réel.

**Méthode de fallback (pas de bougie proche) :**
On applique un slippage estimé de 3% par seconde :
```
P_effectif = P_cible × (1 + 3% × 1s) = P_cible × 1.03
```

Ce prix effectif est utilisé comme base pour tous les calculs de gain et drawdown.
Le gain brut est donc calculé depuis un prix déjà plus élevé que celui du wallet cible.

### 2.3 Délai de sortie de 1 seconde

Quand le TP ou le SL est déclenché, notre vente s'exécute ~1 seconde plus tard.
On applique un malus de 3% sur le gain final :

```
exit_slippage_pct = 3% × 1s = 3%
gain_max_net = gain_max_brut - exit_slippage_pct - gas_cost_pct
```

Sur un SL, ce délai aggrave la perte :
```
drawdown_net = drawdown_brut + exit_slippage_pct + gas_cost_pct
```

### 2.4 Percentiles plus fins pour les stratégies

| Paramètre | v2 Recommandée | v3 Recommandée | v2 Agressive | v3 Agressive |
|---|---|---|---|---|
| TP1 | p50 | p45 | p65 | p60 |
| TP2 | p65 | p60 | p80 | p75 |
| TP3 | p75 | p72 | p90 | p88 |
| SL  | p70 | p65 | p85 | p80 |

Les percentiles v3 sont légèrement plus bas sur les TP car les métriques sont
déjà nettes — moins besoin de marge de sécurité supplémentaire.

### 2.5 Résolution OHLCV passée à 1m

La v2 utilisait des bougies 15m. Un pic de 2 minutes aurait pu être manqué.
La v3 utilise des bougies 1m pour capturer :
- Les ATH intra-bougies avec plus de précision
- Les drawdowns réels (un dump de 30 secondes est visible en 1m)
- Le prix exact à T+1 seconde pour le calcul du prix d'entrée effectif

### 2.6 Winrate calculé avec seuil d'ambiguïté réduit

La v2 considérait un trade ambigu (TP et SL tous les deux atteints) comme WIN
si time_to_ath < 2h (7200s).

La v3 réduit ce seuil à 90 secondes, car sur Pump.fun les trades qui ATH
rapidement le font généralement dans les 90 premières secondes.
Au-delà, le SL est très probablement déclenché avant.

---

## 3. Architecture du projet

```
tp_sl_bot/
│
├── bot/                         # Interface Telegram
│   ├── __init__.py
│   ├── main.py                  # Point d'entrée, démarrage du polling
│   └── handlers.py              # Commandes /start /help /analyze
│
├── services/                    # Couche d'accès aux APIs externes
│   ├── __init__.py
│   ├── helius_service.py        # Transactions Solana (API Helius)
│   └── gecko_service.py         # Prix OHLCV tokens (API GeckoTerminal, gratuit)
│
├── core/                        # Logique métier
│   ├── __init__.py
│   ├── trade_extractor.py       # Pipeline extraction + déduplication
│   ├── performance_analyzer.py  # Métriques nettes (gas + délais intégrés)
│   ├── strategy_builder.py      # Construction TP/SL + calcul winrate précis
│   └── simulator.py             # Simulation P&L USD
│
├── utils/                       # Utilitaires transverses
│   ├── __init__.py
│   ├── filters.py               # Suppression outliers, validation adresse
│   ├── stats.py                 # Fonctions numpy réutilisables
│   └── formatter.py             # Formatage messages Telegram Markdown V2
│
├── config.py                    # Variables d'environnement + constantes gas/délais
├── requirements.txt
├── Procfile                     # Commande Railway
├── .env.example                 # Template des variables
├── .gitignore
└── README.md
```

---

## 4. Description des modules

### `config.py`

Centralise toutes les constantes. Les paramètres gas et délai sont ici
pour pouvoir être ajustés sans toucher au code métier.

| Constante | Valeur | Description |
|---|---|---|
| `GAS_BUY_SOL` | 0.022 | Priority Fee + Tip + Bribe à l'achat |
| `GAS_SELL_SOL` | 0.022 | Priority Fee + Tip + Bribe à la vente |
| `GAS_TOTAL_SOL` | 0.044 | Total round-trip |
| `ENTRY_DELAY_SECONDS` | 1 | Délai entre signal et entrée réelle |
| `EXIT_DELAY_SECONDS` | 1 | Délai entre déclenchement et exécution vente |
| `SLIPPAGE_PER_SECOND_PCT` | 3.0 | Slippage estimé par seconde de délai |
| `PRICE_INTERVAL` | "1m" | Résolution OHLCV (bougies 1 minute) |

---

### `services/helius_service.py`

Deux fonctions :

**`get_wallet_transactions(wallet_address)`**
Récupère tous les SWAP sur 30 jours. Pagination automatique.

**`filter_pump_fun_buys(transactions, wallet_address)`**
Filtre sur le Program ID Pump.fun. Un BUY = le wallet reçoit un token.
Retourne `sol_spent` (SOL dépensé) qui sera utilisé pour calculer le gas_cost_pct.

---

### `services/gecko_service.py`

**`_get_pool_address(token_mint)`**
Résout le mint vers l'adresse de pool principale (la plus liquide).
Résultat mis en cache en mémoire (`_pool_cache`) pour éviter les appels redondants.

**`get_price_at_entry(token_mint, timestamp)`**
Retourne le prix au moment de l'entrée du wallet cible.
Note : ce prix est celui du wallet cible. Le prix effectif du copieur
est calculé dans performance_analyzer avec le délai de 1 seconde.

**`get_price_history(token_mint, from_ts, to_ts)`**
Historique OHLCV 1m complet. Pagine si nécessaire.
`time.sleep(0.2)` respecte la limite de 30 req/min de GeckoTerminal.

---

### `core/trade_extractor.py`

Pipeline en 3 étapes :
1. `get_wallet_transactions` → transactions brutes
2. `filter_pump_fun_buys` → BUY Pump.fun uniquement
3. Déduplication (première entrée par mint uniquement)
4. `get_price_at_entry` → prix du wallet cible (champ `entry_price_target`)

Note : le champ est nommé `entry_price_target` pour le distinguer clairement
du prix d'entrée effectif du copieur calculé dans l'étape suivante.

---

### `core/performance_analyzer.py`

Cœur algorithmique de la v3. Intègre les trois ajustements.

**`_compute_gas_cost_pct(sol_spent)`**
```
gas_cost_pct = (GAS_TOTAL_SOL / sol_spent) × 100
             = (0.044 / sol_spent) × 100
```
Ce calcul est en % SOL pur, indépendant du prix du SOL.

**`_compute_effective_entry_price(candles, target_entry_ts, target_entry_price)`**
Cherche la bougie à `target_entry_ts + 1s` dans l'historique 1m.
Si une bougie est trouvée dans ±60 secondes, utilise son open.
Sinon, applique le slippage estimé de 3%.

**`compute_trade_metrics(trade)`**
Calcule pour chaque trade :
```
gain_max_net = gain_brut_depuis_prix_effectif
             - exit_slippage_pct (3%)
             - gas_cost_pct (0.044/sol_spent × 100)

drawdown_net = drawdown_brut_depuis_prix_effectif
             + exit_slippage_pct (3%)
             + gas_cost_pct
```

---

### `core/strategy_builder.py`

**`build_strategies(gains, drawdowns)`**
Construit les niveaux TP/SL depuis les distributions nettes.
Applique `_ensure_tp_ordering` pour garantir un écart minimum de 2% entre paliers.

**`compute_winrate(metrics, strategy)`**
Simulation par trade avec seuil d'ambiguïté à 90 secondes (vs 2h en v2).

**`build_full_strategies(gains, drawdowns, metrics)`**
Pipeline complet : stratégies + winrates.

---

### `core/simulator.py`

Simulation P&L USD avec sortie partielle sur 3 paliers.
Les TP/SL en entrée étant déjà nets, le P&L calculé est directement encaissable.

---

### `utils/formatter.py`

Le message de résultat inclut deux lignes supplémentaires en statistiques :
- `Frais moy. par trade` : gas_cost_pct moyen observé sur les trades analysés
- `Slippage entrée moy.` : slippage d'entrée moyen observé

Ces informations donnent une visibilité sur le coût réel du profil de trading
du wallet analysé (petits trades = frais élevés en %, gros trades = frais faibles).

---

## 5. APIs utilisées

### Helius (transactions Solana)

- **Site :** https://helius.dev
- **Plan :** Gratuit (100 000 crédits/mois)
- **Endpoint :** `GET /v0/addresses/{wallet}/transactions`
- **Paramètres :** `type=SWAP`, `limit=100`, pagination `before`
- **Doc :** https://docs.helius.dev/solana-apis/enhanced-transactions-api

### GeckoTerminal (prix des tokens)

- **Site :** https://geckoterminal.com
- **Plan :** Gratuit, sans clé API
- **Limite :** 30 requêtes/minute (gérée automatiquement)
- **Endpoints :**
  - `GET /networks/solana/tokens/{mint}/pools` → résolution mint → pool
  - `GET /networks/solana/pools/{pool}/ohlcv/minute?aggregate=1` → OHLCV 1m
- **Doc :** https://www.geckoterminal.com/api

---

## 6. Logique algorithmique complète

### Pipeline général

```
Wallet address
      ↓
[Helius] get_wallet_transactions()
      → 30 jours de SWAP
      ↓
[Helius] filter_pump_fun_buys()
      → BUY Pump.fun + sol_spent par trade
      ↓
Déduplication par mint
      → 1 entrée initiale par token
      ↓
[GeckoTerminal] get_price_at_entry()
      → Prix du wallet cible au moment T
      ↓
[GeckoTerminal] get_price_history() [bougies 1m]
      → Historique de prix de T à T+7j
      ↓
_compute_effective_entry_price()
      → Prix effectif à T+1s (bougie 1m ou slippage estimé 3%)
      ↓
_compute_gas_cost_pct()
      → (0.044 SOL / sol_spent) × 100
      ↓
compute_trade_metrics()
      → gain_max_net = gain_brut - 3% (exit slip) - gas_cost_pct
      → drawdown_net = drawdown_brut + 3% + gas_cost_pct
      ↓
remove_outliers() [p99]
      → Suppression des rugs
      ↓
build_strategies() [percentiles fins sur métriques nettes]
      → TP1/TP2/TP3 et SL directement utilisables
      ↓
compute_winrate() [seuil ambiguïté 90s]
      ↓
simulate_strategy() [optionnel]
      ↓
format_analysis()
      → Message Telegram
```

### Modélisation du délai d'entrée

```
Temps :    T         T+1s        T+60s
           │           │            │
Prix :    P_cible    P_effectif   ATH possible
           │           │
Wallet cible achète   Notre ordre s'exécute

P_effectif (méthode principale) :
  → Open de la bougie 1m à T+1s

P_effectif (fallback) :
  → P_cible × (1 + 3%) = P_cible × 1.03
```

### Impact des gas fees selon la taille du trade

| SOL investi | Gas total (SOL) | Gas en % du trade | Seuil de rentabilité |
|---|---|---|---|
| 0.05 SOL | 0.044 | 88% | Le token doit +88% pour couvrir les frais |
| 0.10 SOL | 0.044 | 44% | +44% pour couvrir |
| 0.25 SOL | 0.044 | 17.6% | +17.6% pour couvrir |
| 0.50 SOL | 0.044 | 8.8% | +8.8% pour couvrir |
| 1.00 SOL | 0.044 | 4.4% | +4.4% pour couvrir |

> **Recommandation F Project :** ne pas trader avec moins de 0.05 SOL
> (les frais représentent presque la totalité du trade).
> Pour des stratégies TP1 raisonnables, viser 0.25 SOL minimum.

### Filtrage des outliers

Les tokens Pump.fun incluent des rugs qui font x500 puis s'effondrent.
Même après déduction des frais, un gain de +49 900% fausserait les percentiles.
On coupe au 99e percentile avant construction des stratégies.

---

## 7. Paramètres configurables

Tous dans `config.py`. Modifiables sans toucher au code métier.

### Ajuster les gas fees

Si tes paramètres gas changent, modifier dans `config.py` :
```python
GAS_BUY_SOL  = 0.001 + 0.001 + 0.02   # Priority Fee + Tip + Bribe
GAS_SELL_SOL = 0.001 + 0.001 + 0.02
GAS_TOTAL_SOL = GAS_BUY_SOL + GAS_SELL_SOL
```

### Ajuster le délai d'exécution

```python
ENTRY_DELAY_SECONDS = 1    # Si ton infra est plus rapide, réduire à 0.5
EXIT_DELAY_SECONDS  = 1
```

### Ajuster le slippage estimé

```python
SLIPPAGE_PER_SECOND_PCT = 3.0  # 3% par seconde — conservateur
                                 # Réduire à 1.5 si marché moins volatile
```

### Ajuster la résolution OHLCV

```python
PRICE_INTERVAL = "1m"   # Valeurs possibles : 1m, 5m, 15m, 1H, 4H, 1D
```

La résolution 1m est recommandée pour la précision maximale mais
consomme plus de quota GeckoTerminal. Passer à "5m" si les analyses
sont trop longues sur des wallets avec 50+ trades.

---

## 8. Installation locale

### Prérequis

- Python 3.11+
- Compte Helius (gratuit)
- Bot Telegram créé via @BotFather

### Étapes

```bash
# 1. Dézipper et entrer dans le dossier
cd tp_sl_bot

# 2. Créer l'environnement virtuel
python -m venv venv

# 3. Activer
source venv/bin/activate       # Mac / Linux
venv\Scripts\activate          # Windows

# 4. Installer les dépendances
pip install -r requirements.txt

# 5. Configurer
cp .env.example .env
# Éditer .env avec tes clés

# 6. Lancer
python -m bot.main
```

### Vérification

```
INFO | Démarrage du bot TP/SL Analyzer v3...
INFO | Bot démarré — en attente de commandes Telegram.
```

Tester sur Telegram avec `/start` → message de bienvenue ✅

---

## 9. Déploiement Railway

### Étape 1 — GitHub

```bash
git init
git add .
git commit -m "v3 — gas fees + délais intégrés"
git remote add origin https://github.com/TON_USER/tp-sl-bot.git
git branch -M main
git push -u origin main
```

### Étape 2 — Railway

1. https://railway.app → Login with GitHub
2. New Project → Deploy from GitHub repo → sélectionner le repo
3. Settings → Variables → ajouter :

| Variable | Valeur |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `1234567890:AAFxxxxxxxx...` |
| `HELIUS_API_KEY` | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |

4. Railway redémarre automatiquement

### Vérification

Logs Railway :
```
INFO | Démarrage du bot TP/SL Analyzer v3...
INFO | Bot démarré — en attente de commandes Telegram.
```

### Redéploiement

Chaque `git push main` → redéploiement automatique Railway.

---

## 10. Utilisation du bot

### Commandes

| Commande | Description |
|---|---|
| `/start` | Bienvenue + résumé des ajustements appliqués |
| `/help` | Aide complète avec détail des gas fees |
| `/analyze <wallet>` | Analyse sans simulation |
| `/analyze <wallet> <usd>` | Analyse + simulation USD |

### Exemples

```
/analyze 8fj3kLM5vXc2NqPwR9Ab7Yd4TeK1JhGf6sUoI3Zn
/analyze 8fj3kLM5vXc2NqPwR9Ab7Yd4TeK1JhGf6sUoI3Zn 250
```

### Temps de traitement

| Trades | Temps estimé |
|---|---|
| 5 à 15 | ~1 à 2 min |
| 15 à 30 | ~2 à 3 min |
| 30 à 50 | ~3 à 5 min |
| 50+ | ~5 à 8 min |

Le temps est principalement lié aux appels GeckoTerminal (30 req/min)
et à la résolution 1m (plus de bougies à récupérer qu'en 15m).

---

## 11. Format de sortie

```
Analyse du wallet : 8fj3k...3Zn

Stratégie recommandée :
TP1 : +22%
TP2 : +31%
TP3 : +41%
SL : -19%
Winrate : 61%

Stratégie agressive :
TP1 : +31%
TP2 : +44%
TP3 : +58%
SL : -26%
Winrate : 38%

Simulation (250$) :
Recommandée : +1840$
Agressive : +2200$

Statistiques :
Trades : 41
Gain médian net : +28%
Gain max moyen net : +43%
Drawdown moyen net : -22%
Temps moy. ATH : 1h18m
Frais moy. par trade : -17%
Slippage entrée moy. : +3%
```

### Lecture des résultats

Les TP et SL affichés sont **directement utilisables** dans ton bot de sniping.
Ils intègrent déjà :
- Le coût des gas buy + sell (0.044 SOL total)
- Le slippage dû au délai d'entrée de 1 seconde
- Le slippage dû au délai de sortie de 1 seconde

`Frais moy. par trade : -17%` signifie que sur les trades analysés, les gas fees
représentaient en moyenne 17% du capital investi. Si ce chiffre est très élevé
(>30%), le wallet cible trade avec des montants trop faibles pour être profitable à copier.

`Slippage entrée moy. : +3%` est le slippage moyen observé ou estimé au moment
de l'entrée. Si les bougies 1m étaient disponibles, c'est le delta entre le prix
du wallet cible et le open de la bougie suivante.

---

## 12. Coûts et limites

### Coûts mensuels

| Service | Plan | Coût |
|---|---|---|
| Telegram Bot | Gratuit | 0$ |
| Helius | Free | 0$ |
| GeckoTerminal | Gratuit | 0$ |
| Railway | Hobby | ~5$/mois |
| **Total** | | **~5$/mois** |

### Limites techniques

**GeckoTerminal :**
- 30 req/min → analyses de wallets très actifs (50+ trades) prennent 5-8 min
- Données absentes pour les tokens créés il y a moins de 1 heure
- Les pools avec très peu de liquidité peuvent ne pas être indexés

**Helius :**
- 100 000 crédits/mois sur le plan gratuit
- Un wallet actif consomme 500 à 2 000 crédits par analyse

**Slippage estimé :**
- Le 3% par seconde est une estimation conservative sur des marchés normaux
- En période de très forte volatilité, le slippage réel peut être 2 à 5x plus élevé
- Si ton infrastructure est plus rapide (< 300ms), réduire `ENTRY_DELAY_SECONDS` à 0.5

**Gas fees :**
- Le calcul suppose que `sol_spent` reflète le vrai capital investi
- Si Helius ne retourne pas les native_transfers correctement, un fallback de 0.1 SOL est utilisé

---

## 13. Évolutions possibles

**Précision améliorée :**
- Récupérer le prix SOL/USD en temps réel pour convertir les gas en % USD exact
- Modéliser le slippage d'entrée par profil de token (micro-cap vs mid-cap)
- Intégrer les frais de plateforme Pump.fun (1% de chaque trade)

**Fonctionnalités :**
- `/compare <wallet1> <wallet2>` — comparaison de rentabilité nette
- `/score <wallet>` — scoring global (rentabilité nette, fréquence, consistance)
- Export CSV des trades avec métriques détaillées

**Performance :**
- Cache Redis pour les prix déjà fetchés
- Analyses parallèles pour réduire le temps de traitement

**Alertes :**
- Notification quand un wallet suivi fait un nouveau trade
- Alerte si les métriques récentes divergent de l'historique (wallet qui change de style)
