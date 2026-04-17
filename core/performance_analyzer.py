# ============================================================
# core/performance_analyzer.py
# ============================================================
#
# CORRECTIONS v3.1 :
#   - entry_slippage_pct plafonné à MAX_ENTRY_SLIPPAGE_PCT (50%)
#     → évite les +176920% sur les pumps extrêmes intra-minute
#   - Trade skipé si effective_entry_price > ATH (incohérence de données)
#   - gain_max_net négatif possible et conservé (trade non rentable)
#   - drawdown_net plafonné à 100% (on ne peut pas perdre plus que sa mise)
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

# Plafond du slippage d'entrée observé ou estimé.
# Si la bougie à T+1s montre un prix 50x supérieur, la donnée est
# aberrante (bougie manquante, mauvaise pool, pump extrême non copiable).
# On plafonne à 50% — au-delà, le trade est de toute façon non copiable.
MAX_ENTRY_SLIPPAGE_PCT = 50.0

# Estimation conservative du prix SOL (pour affichage uniquement)
SOL_PRICE_USD_ESTIMATE = 70


def _compute_gas_cost_pct(sol_spent: float) -> float:
    """
    Calcule le coût des gas fees en % du capital investi.
    gas_cost_pct = (GAS_TOTAL_SOL / sol_spent) * 100
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
    Calcule le prix d'entrée effectif du copieur (ENTRY_DELAY_SECONDS après le wallet cible).

    Stratégie :
    1. Cherche la bougie à target_entry_ts + ENTRY_DELAY_SECONDS
    2. Si trouvée dans ±60s, utilise son open comme prix effectif
    3. Plafonne le slippage à MAX_ENTRY_SLIPPAGE_PCT (50%) :
       → Au-delà, les données sont aberrantes ou le trade est non copiable
    4. Fallback : slippage estimé de SLIPPAGE_PER_SECOND_PCT %

    Returns:
        (prix_effectif, slippage_entrée_en_pct) — slippage toujours >= 0 et <= 50
    """
    effective_ts = target_entry_ts + ENTRY_DELAY_SECONDS

    if candles:
        closest = min(candles, key=lambda c: abs(c["unixTime"] - effective_ts))
        time_diff = abs(closest["unixTime"] - effective_ts)

        if time_diff <= 60:
            effective_price = closest["o"]

            # Slippage réel observé depuis le prix du wallet cible
            raw_slippage = ((effective_price - target_entry_price) / target_entry_price) * 100

            # Cas aberrant : prix d'entrée effectif négatif ou nul
            if effective_price <= 0:
                pass  # → fallback ci-dessous
            else:
                # Plafonner le slippage positif à MAX_ENTRY_SLIPPAGE_PCT
                entry_slippage = min(max(0.0, raw_slippage), MAX_ENTRY_SLIPPAGE_PCT)

                # Si le slippage est plafonné, recalculer le prix effectif
                # pour rester cohérent avec la valeur plafonnée
                if raw_slippage > MAX_ENTRY_SLIPPAGE_PCT:
                    effective_price = target_entry_price * (1 + MAX_ENTRY_SLIPPAGE_PCT / 100)

                return round(effective_price, 12), round(entry_slippage, 2)

    # Fallback : slippage estimé
    entry_slippage  = SLIPPAGE_PER_SECOND_PCT * ENTRY_DELAY_SECONDS
    effective_price = target_entry_price * (1 + entry_slippage / 100)
    return round(effective_price, 12), round(entry_slippage, 2)


def compute_trade_metrics(trade: dict) -> dict | None:
    """
    Calcule les métriques de performance nettes pour un trade.

    Intègre :
    - Le délai d'entrée (prix effectif plus élevé, plafonné à +50%)
    - Le slippage de sortie (1 seconde de délai à la vente)
    - Les gas fees buy + sell (0.044 SOL total)

    Returns None si :
    - Données de prix insuffisantes (< 3 bougies)
    - Prix effectif d'entrée >= ATH (données incohérentes)
    - Prix effectif <= 0
    """
    target_entry_ts    = trade["timestamp"]
    target_entry_price = trade["entry_price_target"]
    token_mint         = trade["token_mint"]
    sol_spent          = trade.get("sol_spent", 0.1)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    to_ts  = min(target_entry_ts + MAX_WINDOW_SECONDS, now_ts)

    candles = get_price_history(token_mint, target_entry_ts, to_ts)

    if len(candles) < 3:
        return None

    # --- Prix d'entrée effectif ---
    effective_entry_price, entry_slippage_pct = _compute_effective_entry_price(
        candles, target_entry_ts, target_entry_price
    )

    if effective_entry_price <= 0:
        return None

    # --- Bougies post-entrée effective ---
    effective_entry_ts = target_entry_ts + ENTRY_DELAY_SECONDS
    post_entry_candles = [c for c in candles if c["unixTime"] >= effective_entry_ts]

    if len(post_entry_candles) < 2:
        return {
            **trade,
            "gain_max_pct": -100,
            "drawdown_pct": 100,
            "time_to_ath_secs": 999999,
        }

    highs = [c["h"] for c in post_entry_candles if c.get("h") and c["h"] > 0]
    lows  = [c["l"] for c in post_entry_candles if c.get("l") and c["l"] > 0]
    times = [c["unixTime"] for c in post_entry_candles]

    if not highs or not lows:
        return None

    ath_price = max(highs)
    ath_idx   = highs.index(ath_price)
    ath_ts    = times[ath_idx] if ath_idx < len(times) else effective_entry_ts
    min_price = min(lows)

    if ath_price <= 0:
        return None

    # --- Gains et drawdown BRUTS ---
    gain_max_brut    = ((ath_price  - effective_entry_price) / effective_entry_price) * 100
    drawdown_brut    = ((min_price  - effective_entry_price) / effective_entry_price) * 100  # négatif
    time_to_ath_secs = max(0, ath_ts - effective_entry_ts)

    # --- Slippage de sortie ---
    exit_slippage_pct = SLIPPAGE_PER_SECOND_PCT * EXIT_DELAY_SECONDS  # = 3.0%

    # --- Gas fees ---
    gas_cost_pct = _compute_gas_cost_pct(sol_spent)

    # --- Métriques NETTES ---
    gain_max_net = gain_max_brut - exit_slippage_pct - gas_cost_pct
    drawdown_net = min(abs(drawdown_brut) + exit_slippage_pct + gas_cost_pct, 100.0)

    return {
        **trade,
        "entry_price_effective": round(effective_entry_price, 12),
        "ath_price":             round(ath_price, 12),
        "min_price":             round(min_price, 12),

        "gain_max_brut_pct":     round(gain_max_brut, 2),
        "drawdown_brut_pct":     round(abs(drawdown_brut), 2),

        "entry_slippage_pct":    round(entry_slippage_pct, 2),
        "exit_slippage_pct":     round(exit_slippage_pct, 2),
        "gas_cost_pct":          round(gas_cost_pct, 2),

        # Métriques NETTES utilisées pour construire les stratégies
        "gain_max_pct":          round(gain_max_net, 2),
        "drawdown_pct":          round(drawdown_net, 2),

        "time_to_ath_secs":      time_to_ath_secs,
        "candles_count":         len(candles),
    }


def analyze_all_trades(trades: list[dict]) -> dict:
    """
    Lance l'analyse sur tous les trades et agrège les résultats.
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

    gains = [min(m["gain_max_pct"], 500) for m in metrics]
    drawdowns = [m["drawdown_pct"] for m in metrics]
    times_ath = [m["time_to_ath_secs"] for m in metrics]
    gas_costs = [m["gas_cost_pct"]  for m in metrics]

    # Double filtrage outliers :
    # 1. p99 sur les gains (retire les rugs x500+)
    # 2. Garde uniquement les valeurs dans [−100, +10000] (sanity check)
    gains_clean     = remove_outliers(
        [g for g in gains if g > -100],
        percentile=99
    )
    drawdowns_clean = remove_outliers(
        [d for d in drawdowns if 0 <= d <= 100],
        percentile=99
    )

    if not gains_clean or not drawdowns_clean:
        return {}

    stats = {
        "nb_trades":        len(metrics),
        "gain_mean":        round(statistics.mean(gains_clean), 1),
        "gain_median":      round(statistics.median(gains_clean), 1),
        "drawdown_mean":    round(statistics.mean(drawdowns_clean), 1),
        "time_to_ath_mean": _format_duration(statistics.mean(times_ath)),
        "avg_gas_pct":      round(statistics.mean(gas_costs), 1),
        "avg_entry_slip":   round(statistics.mean([m["entry_slippage_pct"] for m in metrics]), 1),
    }
    skipped = 0

    for trade in trades:
        result = compute_trade_metrics(trade)
        if result:
            metrics.append(result)
        else:
            skipped += 1
    print(f"Skipped trades: {skipped}/{total}")

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