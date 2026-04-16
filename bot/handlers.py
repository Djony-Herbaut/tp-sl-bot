# ============================================================
# bot/handlers.py
# ============================================================
#
# RÔLE :
#   Gestion des commandes Telegram.
#
# COMMANDES :
#   /start   → Message de bienvenue
#   /help    → Aide détaillée
#   /analyze → Pipeline d'analyse complet
#
# FLOW DE /analyze :
#   1. Validation des arguments
#   2. Message de chargement immédiat
#   3. Pipeline dans un thread séparé (run_in_executor)
#   4. Édition du message avec le résultat final
#
# THREAD EXECUTOR :
#   Le pipeline est synchrone (appels httpx bloquants).
#   run_in_executor() le déporte dans un thread OS pour ne pas
#   bloquer la boucle asyncio — le bot reste réactif en parallèle.
# ============================================================

import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import MIN_TRADES
from utils.filters import is_valid_solana_address
from utils.formatter import format_analysis, format_error, format_loading
from core.trade_extractor import extract_trades
from core.performance_analyzer import analyze_all_trades
from core.strategy_builder import build_full_strategies
from core.simulator import simulate_strategy


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Bot d'analyse TP/SL — Wallets Solana Pump\\.fun*\n\n"
        "Les TP et SL générés sont *nets de gas fees et de délais d'exécution*\\.\n\n"
        "📌 *Commande :*\n"
        "`/analyze <adresse\\_wallet> \\[montant\\_usd\\]`\n\n"
        "📌 *Exemples :*\n"
        "`/analyze 8fj3kABC\\.\\.\\.xyz`\n"
        "`/analyze 8fj3kABC\\.\\.\\.xyz 500`\n\n"
        "Tape /help pour l'aide complète\\."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *AIDE COMPLÈTE*\n\n"
        "*Commande :*\n"
        "`/analyze <wallet> \\[usd\\]`\n\n"
        "*Paramètres :*\n"
        "• `wallet` — adresse Solana du wallet à analyser\n"
        "• `usd` \\(optionnel\\) — montant en $ par trade pour la simulation\n\n"
        "*Ce que retourne le bot :*\n"
        "• TP1/TP2/TP3 et SL nets de gas \\+ délais\n"
        "• Winrate estimé par simulation historique\n"
        "• Simulation P&L USD si montant fourni\n"
        "• Stats : frais moyens, slippage, temps ATH\n\n"
        "*Ajustements appliqués automatiquement :*\n"
        "• Gas buy : 0\\.001 \\+ 0\\.001 \\+ 0\\.02 SOL\n"
        "• Gas sell : 0\\.001 \\+ 0\\.001 \\+ 0\\.02 SOL\n"
        "• Délai entrée : \\+1 seconde\n"
        "• Délai sortie : \\+1 seconde\n\n"
        "*Temps d'analyse :* 1 à 3 minutes\n"
        "*Minimum requis :* 5 trades Pump\\.fun sur 30 jours"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Commande principale : /analyze <wallet> [montant_usd]"""
    args = context.args

    if not args:
        await update.message.reply_text(
            format_error("Adresse wallet manquante. Usage : /analyze <wallet> [usd]")
        )
        return

    wallet = args[0].strip()
    if not is_valid_solana_address(wallet):
        await update.message.reply_text(format_error("Adresse Solana invalide."))
        return

    amount_usd = None
    if len(args) >= 2:
        try:
            amount_usd = float(args[1])
            if amount_usd <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                format_error("Montant USD invalide. Exemple : /analyze <wallet> 1000")
            )
            return

    loading_msg = await update.message.reply_text(
        format_loading(wallet),
        parse_mode=ParseMode.HTML,
    )

    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_analysis, wallet, amount_usd)
        await loading_msg.edit_text(result, parse_mode=ParseMode.HTML)

    except InsufficientDataError as e:
        await loading_msg.edit_text(format_error(str(e)))
    except RuntimeError as e:
        await loading_msg.edit_text(format_error(f"Erreur API : {str(e)[:120]}"))
    except Exception as e:
        await loading_msg.edit_text(format_error(f"Erreur inattendue : {str(e)[:100]}"))


def _run_analysis(wallet: str, amount_usd: float | None) -> str:
    """
    Pipeline synchrone complet.

    1. Extraction des trades (Helius + GeckoTerminal)
    2. Analyse des performances nettes (gas + délais intégrés)
    3. Construction des stratégies (percentiles fins + winrate)
    4. Simulation USD (optionnelle)
    5. Formatage du message Telegram

    Args:
        wallet:     Adresse Solana
        amount_usd: Montant par trade pour la simulation (ou None)

    Returns:
        Message Telegram en Markdown V2
    """
    trades = extract_trades(wallet)
    if len(trades) < MIN_TRADES:
        raise InsufficientDataError(
            f"Seulement {len(trades)} trade(s) valide(s) sur 30 jours. "
            f"Minimum requis : {MIN_TRADES}."
        )

    analysis = analyze_all_trades(trades)
    if not analysis or len(analysis.get("gains", [])) < MIN_TRADES:
        raise InsufficientDataError(
            "Données de prix insuffisantes. "
            "Les tokens peuvent être trop récents ou sans liquidité."
        )

    gains     = analysis["gains"]
    drawdowns = analysis["drawdowns"]
    metrics   = analysis["metrics"]
    stats     = analysis["stats"]

    strategies = build_full_strategies(gains, drawdowns, metrics)

    sim_rec = sim_agg = None
    if amount_usd:
        sim_rec = simulate_strategy(metrics, strategies["recommended"], amount_usd)
        sim_agg = simulate_strategy(metrics, strategies["aggressive"],  amount_usd)

    return format_analysis(wallet, strategies, stats, sim_rec, sim_agg)


class InsufficientDataError(Exception):
    pass
