# ============================================================
# config.py — Configuration globale du projet
# ============================================================
#
# VARIABLES D'ENVIRONNEMENT REQUISES :
#   TELEGRAM_BOT_TOKEN  → Obtenu via @BotFather sur Telegram
#   HELIUS_API_KEY      → Obtenu sur https://helius.dev (plan gratuit)
#
# GeckoTerminal est utilisé pour les prix (gratuit, sans clé API).
#
# En local : créer un fichier .env à la racine du projet
# En prod  : ajouter ces variables dans Railway > Settings > Variables
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv()

# --- Clés API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
HELIUS_API_KEY     = os.getenv("HELIUS_API_KEY")

# --- Paramètres d'analyse ---
ANALYSIS_DAYS       = 30          # Fenêtre d'analyse en jours
PRICE_INTERVAL      = "1m"        # Résolution OHLCV : 1m pour la précision maximale
MIN_TRADES          = 1           # Réduit : wallets bundles pump.fun ont peu de trades
MAX_WINDOW_SECONDS  = 7 * 24 * 3600  # Fenêtre max post-entry : 7 jours

# Program ID Pump.fun on-chain (stable, ne pas modifier)
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# ============================================================
# GAS FEES (en SOL) — Paramètres réels de trading
# ============================================================
# Ces valeurs sont utilisées pour ajuster les TP et SL :
# le coût total d'un aller-retour (buy + sell) est soustrait
# du gain brut afin que les seuils calculés soient nets de frais.
#
# BUY GAS :
#   Priority Fee : 0.001 SOL
#   Tip          : 0.001 SOL
#   Bribe        : 0.02  SOL
#   Total BUY    : 0.022 SOL
#
# SELL GAS :
#   Priority Fee : 0.001 SOL
#   Tip          : 0.001 SOL
#   Bribe        : 0.02  SOL
#   Total SELL   : 0.022 SOL
#
# TOTAL ROUND-TRIP : 0.044 SOL
# ============================================================
GAS_BUY_SOL  = 0.001 + 0.001 + 0.02   # = 0.022 SOL
GAS_SELL_SOL = 0.001 + 0.001 + 0.02   # = 0.022 SOL
GAS_TOTAL_SOL = GAS_BUY_SOL + GAS_SELL_SOL  # = 0.044 SOL

# ============================================================
# DÉLAIS D'EXÉCUTION (en secondes)
# ============================================================
# Ces délais modélisent la latence entre le moment où le wallet
# cible achète/vend et le moment où notre propre ordre s'exécute.
#
# ENTRY_DELAY_SECONDS :
#   On entre ~1 seconde après le wallet cible.
#   Sur des tokens très volatils, 1 seconde peut représenter
#   +5% à +20% de slippage sur le prix d'entrée.
#
# EXIT_DELAY_SECONDS :
#   Notre vente s'exécute ~1 seconde après le déclenchement du TP/SL.
#   Sur un dump rapide, le prix peut avoir baissé de 5% à 15%
#   entre le déclenchement et l'exécution réelle.
# ============================================================
ENTRY_DELAY_SECONDS = 1   # Délai d'entrée post-signal (secondes)
EXIT_DELAY_SECONDS  = 1   # Délai de sortie post-déclenchement TP/SL (secondes)

# ============================================================
# SLIPPAGE ESTIMÉ PAR DÉLAI
# ============================================================
# Sur Pump.fun, la volatilité moyenne dans la première minute
# post-création est de l'ordre de 5% à 20% par seconde.
# On utilise une estimation conservatrice de 3% par seconde
# pour modéliser l'impact du délai sur le prix réel d'entrée.
#
# Cette valeur est appliquée comme un malus sur le prix d'entrée
# effectif, ce qui rend les TP plus difficiles à atteindre et
# recalibre les SL à la hausse (déclenchement plus précoce).
# ============================================================
SLIPPAGE_PER_SECOND_PCT = 3.0   # % de slippage estimé par seconde de délai

# --- Endpoints ---
HELIUS_API_URL        = "https://api.helius.xyz/v0"
HELIUS_RPC_URL        = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
GECKOTERMINAL_API_URL = "https://api.geckoterminal.com/api/v2"
