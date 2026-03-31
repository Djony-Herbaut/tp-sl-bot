# ============================================================
# utils/formatter.py
# ============================================================
#
# RÔLE :
#   Formate les résultats d'analyse en messages Telegram.
#   Utilise le format exact défini dans le cahier des charges,
#   enrichi d'une ligne de contexte sur les ajustements appliqués.
#
# FORMAT TELEGRAM :
#   ParseMode.MARKDOWN_V2 — les caractères . + - ( ) ! doivent
#   être échappés avec un backslash.
# ============================================================


def format_analysis(
    wallet: str,
    strategies: dict,
    stats: dict,
    simulation_rec: dict | None = None,
    simulation_agg: dict | None = None,
) -> str:
    """
    Formate le message final Telegram.

    Les TP et SL affichés sont nets de :
      - Gas fees buy + sell (0.044 SOL)
      - Slippage d'entrée (~1 seconde de délai)
      - Slippage de sortie (~1 seconde de délai)

    Args:
        wallet:         Adresse du wallet analysé
        strategies:     Dict avec 'recommended' et 'aggressive'
        stats:          Statistiques globales
        simulation_rec: Simulation USD stratégie recommandée (optionnel)
        simulation_agg: Simulation USD stratégie agressive (optionnel)

    Returns:
        Message Telegram en Markdown V2
    """
    addr_short = wallet[:6] + "\\.\\.\\." + wallet[-4:]

    rec = strategies["recommended"]
    agg = strategies["aggressive"]

    lines = [
        f"Analyse du wallet : `{addr_short}`",
        "",
        "Stratégie recommandée :",
        f"TP1 : \\+{rec['tp1']}%",
        f"TP2 : \\+{rec['tp2']}%",
        f"TP3 : \\+{rec['tp3']}%",
        f"SL : \\-{rec['sl']}%",
        f"Winrate : {rec['winrate']}%",
        "",
        "Stratégie agressive :",
        f"TP1 : \\+{agg['tp1']}%",
        f"TP2 : \\+{agg['tp2']}%",
        f"TP3 : \\+{agg['tp3']}%",
        f"SL : \\-{agg['sl']}%",
        f"Winrate : {agg['winrate']}%",
    ]

    if simulation_rec and simulation_agg:
        amount = simulation_rec["amount_per_trade"]
        lines += [
            "",
            f"Simulation \\({amount}$\\) :",
            f"Recommandée : {_sign(simulation_rec['net_result'])}{simulation_rec['net_result']}$",
            f"Agressive : {_sign(simulation_agg['net_result'])}{simulation_agg['net_result']}$",
        ]

    lines += [
        "",
        "Statistiques :",
        f"Trades : {stats['nb_trades']}",
        f"Gain médian net : \\+{stats['gain_median']}%",
        f"Gain max moyen net : \\+{stats['gain_mean']}%",
        f"Drawdown moyen net : \\-{stats['drawdown_mean']}%",
        f"Temps moy\\. ATH : {stats['time_to_ath_mean']}",
        f"Frais moy\\. par trade : \\-{stats['avg_gas_pct']}%",
        f"Slippage entrée moy\\. : \\+{stats['avg_entry_slip']}%",
    ]

    return "\n".join(lines)


def format_error(message: str) -> str:
    """Formate un message d'erreur simple (sans Markdown V2)."""
    return f"❌ Erreur : {message}"


def format_loading(wallet: str) -> str:
    """Message affiché pendant l'analyse."""
    addr_short = wallet[:6] + "\\.\\.\\." + wallet[-4:]
    return (
        f"⏳ *Analyse en cours\\.\\.\\.*\n\n"
        f"Wallet : `{addr_short}`\n\n"
        f"_Récupération des transactions \\(30j\\)\\.\\.\\.\n"
        f"Cela peut prendre 1 à 3 minutes selon l'activité du wallet\\._"
    )


def _sign(val: float) -> str:
    return "\\+" if val >= 0 else ""
