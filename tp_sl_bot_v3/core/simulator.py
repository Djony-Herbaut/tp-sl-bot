# ============================================================
# core/simulator.py
# ============================================================
#
# RÔLE :
#   Simule le P&L en USD si l'utilisateur avait appliqué
#   la stratégie calculée sur chaque trade historique.
#
# IMPORTANT :
#   Les TP et SL utilisés ici sont déjà nets de gas fees et délais.
#   Le P&L simulé reflète donc le gain réellement encaissable.
#
# LOGIQUE DE SIMULATION PAR TRADE :
#
#   Si TP3 atteint :
#     → Sortie partielle : 33% à TP1, 33% à TP2, 34% à TP3
#     profit = amount * (0.33*TP1 + 0.33*TP2 + 0.34*TP3) / 100
#
#   Si TP2 atteint (mais pas TP3) :
#     → Sortie : 50% à TP1, 50% à TP2
#     profit = amount * (0.5*TP1 + 0.5*TP2) / 100
#
#   Si TP1 atteint uniquement :
#     profit = amount * TP1 / 100
#
#   Si SL déclenché avant TP1 :
#     perte = amount * SL / 100
#
#   Si ni TP ni SL (trade inactif) :
#     perte partielle = amount * min(drawdown, SL) / 100
#
# NOTE : les gas fees sont déjà inclus dans les TP/SL nets.
#        Pas besoin de les soustraire à nouveau ici.
# ============================================================


def simulate_strategy(metrics: list[dict], strategy: dict, amount_usd: float) -> dict:
    """
    Simule le P&L USD d'une stratégie sur l'historique des trades.

    Args:
        metrics:    Liste des métriques nettes par performance_analyzer
        strategy:   Dict avec tp1, tp2, tp3, sl (valeurs NETTES)
        amount_usd: Montant investi par trade en USD

    Returns:
        Dict avec total_profit, total_loss, net_result, nb_trades,
        amount_per_trade et le détail trade par trade
    """
    tp1 = strategy["tp1"]
    tp2 = strategy["tp2"]
    tp3 = strategy["tp3"]
    sl  = strategy["sl"]

    total_profit  = 0.0
    total_loss    = 0.0
    trade_results = []

    for m in metrics:
        gain_max = m.get("gain_max_pct", 0)  # net
        drawdown = m.get("drawdown_pct",  0)  # net, valeur absolue

        if gain_max >= tp3:
            profit  = amount_usd * (0.33 * tp1 + 0.33 * tp2 + 0.34 * tp3) / 100
            outcome = "TP3"
        elif gain_max >= tp2:
            profit  = amount_usd * (0.5 * tp1 + 0.5 * tp2) / 100
            outcome = "TP2"
        elif gain_max >= tp1:
            profit  = amount_usd * tp1 / 100
            outcome = "TP1"
        elif drawdown >= sl:
            profit  = -(amount_usd * sl / 100)
            outcome = "SL"
        else:
            profit  = -(amount_usd * min(drawdown, sl) / 100)
            outcome = "timeout"

        if profit >= 0:
            total_profit += profit
        else:
            total_loss += abs(profit)

        trade_results.append({
            "token":   m.get("token_mint", "")[:8] + "...",
            "outcome": outcome,
            "pnl_usd": round(profit, 2),
        })

    return {
        "total_profit":     round(total_profit, 2),
        "total_loss":       round(total_loss, 2),
        "net_result":       round(total_profit - total_loss, 2),
        "nb_trades":        len(metrics),
        "amount_per_trade": amount_usd,
        "trade_results":    trade_results,
    }
