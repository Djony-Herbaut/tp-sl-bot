# ============================================================
# core/performance_analyzer.py
# ============================================================
#
# RÔLE :
#   Calcule les métriques de performance réelles pour chaque trade,
#   en intégrant les gas fees et les délais d'exécution.
#
# ============================================================
# MODÉLISATION DE L'ENTRÉE RÉELLE (copieur)
# ============================================================
#
# Le wallet cible achète au timestamp T à un prix P_target.
# Le copieur achète ~1 seconde plus tard (ENTRY_DELAY_SECONDS).
#
# Prix d'entrée effectif du copieur :
#   On cherche la bougie à T + ENTRY_DELAY_SECONDS dans l'historique.
#   Si indisponible, on applique un slippage estimé :
#   P_effective = P_target * (1 + SLIPPAGE_PER_SECOND_PCT/100 * ENTRY_DELAY_SECONDS)
#
# Exemple : P_target = 0.001$, délai 1s, slippage 3%/s
#   P_effective = 0.001 * 1.03 = 0.00103$
#   → On entre 3% plus haut que le wallet cible
#
# ============================================================
# MODÉLISATION DES GAS FEES
# ============================================================
#
# Coût total d'un round-trip (buy + sell) : GAS_TOTAL_SOL = 0.044 SOL
#
# Pour convertir ce coût en % du capital investi, on a besoin
# du montant investi en SOL (sol_spent du trade).
#
# gas_cost_pct = (GAS_TOTAL_SOL / sol_spent) * 100
#
# Exemple : sol_spent = 0.1 SOL
#   gas_cost_pct = (0.044 / 0.1) * 100 = 44%
#   → Il faut que le trade rapporte +44% juste pour couvrir les frais !
#
# Exemple : sol_spent = 0.5 SOL
#   gas_cost_pct = (0.044 / 0.5) * 100 = 8.8%
#   → Seuil de rentabilité à +8.8%
#
# IMPACT SUR LES MÉTRIQUES :
#   gain_max_net = gain_max_brut - gas_cost_pct - entry_slippage_pct - exit_slip_pct
#   drawdown_net = drawdown_brut + gas_cost_pct + entry_slippage_pct
#
# ============================================================
# MODÉLISATION DE LA SORTIE (délai de 1 seconde)
# ============================================================
#
# Quand le TP ou SL est déclenché, l'ordre de vente s'exécute
# ~1 seconde plus tard (EXIT_DELAY_SECONDS).
#
# Sur un dump rapide (SL), le prix peut avoir baissé de X%
# supplémentaires pendant cette seconde.
# On applique un malus exit_slip_pct sur le gain final.
#
# exit_slippage_pct = SLIPPAGE_PER_SECOND_PCT * EXIT_DELAY_SECONDS
#
# ============================================================
# MÉTRIQUES FINALES PAR TRADE (nettes de tout)
# ============================================================
#
# gain_max_net (%) :
#   = ((ATH_post_effective_entry - P_effective) / P_effective) * 100
#   - gas_cost_pct
#   - exit_slippage_pct
#
# drawdown_net (%) :
#   = |((min_post_effective_entry - P_effective) / P_effective) * 100|
#   + gas_cost_pct
#   + exit_slippage_pct
#
# Ces métriques nettes sont ensuite utilisées pour construire
# les stratégies TP/SL qui seront directement applicables
# sans ajustement manuel.
# ============================================================

import time
import statistics
from datetime import datetime, timezone
from services.gecko_service import get_price_history
from utils.filters import remove_outliers
from config import (
    MAX_WINDOW_SECONDS,
    ENTRY_DELAY_SECONDS,
    EXIT_DELAY_SECONDS,
    SLIPPAGE_PER_SECOND_PCT,
    GAS_TOTAL_SOL,
)

# SOL price en USD utilisé pour convertir les gas fees
# On utilise une estimation conservative (ajuster si SOL change drastiquement)
SOL_PRICE_USD_ESTIMATE = 150.0


def _compute_gas_cost_pct(sol_spent: float, sol_price_usd: float = SOL_PRICE_USD_ESTIMATE) -> float:
    """
    Calcule le coût des gas fees en % du capital investi.

    gas_cost_pct = (GAS_TOTAL_SOL * sol_price_usd) / (sol_spent * sol_price_usd) * 100
                 = (GAS_TOTAL_SOL / sol_spent) * 100

    Le sol_price_usd s'annule — le calcul est en % SOL pur.

    Args:
        sol_spent:    Montant investi en SOL sur ce trade
        sol_price_usd: Prix du SOL en USD (non utilisé dans le calcul % mais
                       gardé pour extension future si gas est en USD)

    Returns:
        Coût des gas fees en % du capital investi
    """
    if sol_spent <= 0:
        return 0.0
    return round((GAS_TOTAL_SOL / sol_spent) * 100, 2)


def _compute_effective_entry_price(
    candles: list[dict],
    target_entry_ts: int,
    target_entry_price: float,
) -> tuple[float, float]:
    """
    Calcule le prix d'entrée effectif du copieur (1 seconde après le wallet cible).

    Stratégie :
    1. On cherche la bougie à target_entry_ts + ENTRY_DELAY_SECONDS
    2. Si trouvée, on utilise son open (prix au début de cette bougie)
    3. Sinon, on applique un slippage estimé sur le prix du wallet cible

    Args:
        candles:             Historique OHLCV post-entry
        target_entry_ts:     Timestamp d'achat du wallet cible
        target_entry_price:  Prix d'achat du wallet cible

    Returns:
        (prix_effectif, slippage_entrée_en_pct)
    """
    effective_ts = target_entry_ts + ENTRY_DELAY_SECONDS

    # Chercher la bougie la plus proche du timestamp effectif
    if candles:
        closest = min(candles, key=lambda c: abs(c["unixTime"] - effective_ts))
        time_diff = abs(closest["unixTime"] - effective_ts)

        # Si on a une bougie dans les 60 secondes → on l'utilise
        if time_diff <= 60:
            effective_price = closest["o"]  # open de cette bougie
            entry_slippage  = ((effective_price - target_entry_price) / target_entry_price) * 100
            return effective_price, max(0.0, round(entry_slippage, 2))

    # Fallback : slippage estimé
    entry_slippage  = SLIPPAGE_PER_SECOND_PCT * ENTRY_DELAY_SECONDS
    effective_price = target_entry_price * (1 + entry_slippage / 100)
    return round(effective_price, 12), round(entry_slippage, 2)


def compute_trade_metrics(trade: dict) -> dict | None:
    """
    Calcule les métriques de performance nettes pour un trade.

    Intègre :
    - Le délai d'entrée de 1 seconde (prix effectif plus élevé)
    - Le slippage de sortie de 1 seconde (gain réduit / perte amplifiée)
    - Les gas fees buy + sell (0.044 SOL total)

    Args:
        trade: Dict avec au moins :
               { timestamp, entry_price_target, token_mint, sol_spent }

    Returns:
        Dict enrichi avec les métriques nettes, ou None si données insuffisantes
    """
    target_entry_ts    = trade["timestamp"]
    target_entry_price = trade["entry_price_target"]
    token_mint         = trade["token_mint"]
    sol_spent          = trade.get("sol_spent", 0.1)  # fallback 0.1 SOL si absent

    now_ts = int(datetime.now(timezone.utc).timestamp())
    to_ts  = min(target_entry_ts + MAX_WINDOW_SECONDS, now_ts)

    # On récupère l'historique depuis le signal (timestamp cible)
    # pour pouvoir calculer le prix d'entrée effectif (1s après)
    candles = get_price_history(token_mint, target_entry_ts, to_ts)

    if len(candles) < 3:
        return None

    # --- Prix d'entrée effectif (copieur, 1s de délai) ---
    effective_entry_price, entry_slippage_pct = _compute_effective_entry_price(
        candles, target_entry_ts, target_entry_price
    )

    if effective_entry_price <= 0:
        return None

    # --- Filtrer les bougies APRÈS l'entrée effective ---
    effective_entry_ts = target_entry_ts + ENTRY_DELAY_SECONDS
    post_entry_candles = [c for c in candles if c["unixTime"] >= effective_entry_ts]

    if len(post_entry_candles) < 2:
        return None

    highs = [c["h"] for c in post_entry_candles if c.get("h") and c["h"] > 0]
    lows  = [c["l"] for c in post_entry_candles if c.get("l") and c["l"] > 0]
    times = [c["unixTime"] for c in post_entry_candles]

    if not highs or not lows:
        return None

    ath_price = max(highs)
    ath_idx   = highs.index(ath_price)
    ath_ts    = times[ath_idx] if ath_idx < len(times) else effective_entry_ts
    min_price = min(lows)

    # --- Gain et drawdown BRUTS (depuis prix effectif d'entrée) ---
    gain_max_brut    = ((ath_price  - effective_entry_price) / effective_entry_price) * 100
    drawdown_brut    = ((min_price  - effective_entry_price) / effective_entry_price) * 100  # négatif
    time_to_ath_secs = max(0, ath_ts - effective_entry_ts)

    # --- Slippage de sortie (1 seconde de délai à la vente) ---
    exit_slippage_pct = SLIPPAGE_PER_SECOND_PCT * EXIT_DELAY_SECONDS  # = 3.0%

    # --- Gas fees en % ---
    gas_cost_pct = _compute_gas_cost_pct(sol_spent)

    # --- Métriques NETTES ---
    # Le gain net = gain brut - exit slippage - gas fees
    # (le entry slippage est déjà intégré dans le prix effectif)
    gain_max_net = gain_max_brut - exit_slippage_pct - gas_cost_pct

    # Le drawdown net = drawdown brut (déjà négatif) amplifié par exit slip + gas
    # On le retourne en valeur absolue pour la suite
    drawdown_net = abs(drawdown_brut) + exit_slippage_pct + gas_cost_pct

    return {
        **trade,
        # Prix effectifs
        "entry_price_effective": round(effective_entry_price, 12),
        "ath_price":             round(ath_price, 12),
        "min_price":             round(min_price, 12),

        # Métriques brutes
        "gain_max_brut_pct":     round(gain_max_brut, 2),
        "drawdown_brut_pct":     round(abs(drawdown_brut), 2),

        # Ajustements
        "entry_slippage_pct":    round(entry_slippage_pct, 2),
        "exit_slippage_pct":     round(exit_slippage_pct, 2),
        "gas_cost_pct":          round(gas_cost_pct, 2),

        # Métriques NETTES (utilisées pour construire les stratégies)
        "gain_max_pct":          round(gain_max_net, 2),
        "drawdown_pct":          round(drawdown_net, 2),

        # Timing
        "time_to_ath_secs":      time_to_ath_secs,
        "candles_count":         len(candles),
    }


def analyze_all_trades(trades: list[dict]) -> dict:
    """
    Lance l'analyse sur tous les trades et agrège les résultats.

    Args:
        trades: Liste de trades extraits par trade_extractor

    Returns:
        Dict avec :
          - metrics   : métriques individuelles nettes
          - gains     : distribution des gain_max_pct nets (filtrée p99)
          - drawdowns : distribution des drawdown_pct nets (filtrée p99)
          - stats     : statistiques globales formatées
          - avg_gas_pct : coût moyen des gas en % (info affichée)
    """
    metrics = []
    total   = len(trades)

    print(f"\nAnalyse des performances ({total} trades)...")

    for i, trade in enumerate(trades):
        result = compute_trade_metrics(trade)
        if result:
            metrics.append(result)

        if (i + 1) % 5 == 0:
            print(f"  → {i + 1}/{total} analysés...")

        time.sleep(0.15)

    if not metrics:
        return {}

    gains     = [m["gain_max_pct"] for m in metrics]
    drawdowns = [m["drawdown_pct"]  for m in metrics]
    times_ath = [m["time_to_ath_secs"] for m in metrics]
    gas_costs = [m["gas_cost_pct"]  for m in metrics]

    # Filtrer les outliers extrêmes (rugs, anomalies)
    gains_clean     = remove_outliers(gains,     percentile=99)
    drawdowns_clean = remove_outliers(drawdowns, percentile=99)

    stats = {
        "nb_trades":        len(metrics),
        "gain_mean":        round(statistics.mean(gains_clean), 1),
        "gain_median":      round(statistics.median(gains_clean), 1),
        "drawdown_mean":    round(statistics.mean(drawdowns_clean), 1),
        "time_to_ath_mean": _format_duration(statistics.mean(times_ath)),
        "avg_gas_pct":      round(statistics.mean(gas_costs), 1),
        "avg_entry_slip":   round(statistics.mean([m["entry_slippage_pct"] for m in metrics]), 1),
    }

    return {
        "metrics":   metrics,
        "gains":     gains_clean,
        "drawdowns": drawdowns_clean,
        "stats":     stats,
    }


def _format_duration(seconds: float) -> str:
    """Convertit des secondes en format lisible 'XhYm'."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"
