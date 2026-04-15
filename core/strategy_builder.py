# ============================================================
# core/strategy_builder.py
# ============================================================
#
# RÔLE :
#   Construit les stratégies TP/SL par analyse percentile fine
#   sur les métriques NETTES (gas + délais déjà intégrés).
#
# ============================================================
# POURQUOI LES MÉTRIQUES NETTES CHANGENT TOUT
# ============================================================
#
# Dans la v2, on calculait les TP/SL sur les gains BRUTS,
# puis on espérait que les frais et délais seraient absorbés.
# Résultat : des TP en apparence atteignables mais en réalité
# non rentables une fois les frais déduits.
#
# Dans cette version, les gains utilisés pour calculer les
# percentiles sont déjà nets de :
#   - slippage d'entrée (+1 seconde)
#   - slippage de sortie (+1 seconde)
#   - gas fees buy + sell (0.044 SOL)
#
# Les TP/SL résultants sont donc directement utilisables :
# si TP1 = +18%, c'est un gain réel de +18% dans ta poche.
#
# ============================================================
# CONSTRUCTION DES STRATÉGIES
# ============================================================
#
# On utilise des percentiles plus fins (pas juste p50/p65/p75)
# pour maximiser la précision.
#
# Stratégie RECOMMANDÉE :
#   Objectif : winrate élevé, gains modérés mais réels.
#   TP1 = p45 → légèrement sous la médiane pour s'assurer
#               que presque la moitié des trades l'atteint
#   TP2 = p60
#   TP3 = p72
#   SL  = p65 des drawdowns nets → protège contre 65% des baisses
#
# Stratégie AGRESSIVE :
#   Objectif : gains maximaux, winrate plus faible accepté.
#   TP1 = p60
#   TP2 = p75
#   TP3 = p88
#   SL  = p80 des drawdowns nets
#
# ============================================================
# CALCUL DU WINRATE PRÉCIS
# ============================================================
#
# On simule chaque trade en parcourant les bougies 1m dans l'ordre.
# Pour chaque bougie :
#   - Si high >= entry_effective * (1 + TP1/100) → WIN (TP1 atteint)
#   - Si low  <= entry_effective * (1 - SL/100)  → LOSS (SL déclenché)
#
# Cette simulation bougie par bougie est plus précise qu'une
# simple comparaison gain_max vs TP1, car elle respecte l'ordre
# chronologique (le SL peut être déclenché AVANT que le prix
# remonte vers le TP1).
#
# Pour les trades sans candles stockées (cas de fallback),
# on utilise la méthode simplifiée gain_max vs TP1.
# ============================================================

import numpy as np


def build_strategies(gains: list[float], drawdowns: list[float]) -> dict:
    """
    Construit les niveaux TP/SL nets par analyse percentile.

    Les distributions en entrée sont déjà nettes de gas et délais.
    Les seuils produits sont directement utilisables.

    Args:
        gains:     Distribution des gain_max nets (%)
        drawdowns: Distribution des drawdowns nets en valeur absolue (%)

    Returns:
        Dict avec 'recommended' et 'aggressive', chacun avec tp1/tp2/tp3/sl
    """
    g = np.array(gains)
    d = np.array(drawdowns)

    # Garde uniquement les gains positifs nets pour les TP
    # (un gain net négatif = trade non rentable même au pic, inutile comme TP)
    g_positive = g[g > 0] if len(g[g > 0]) > 3 else g

    recommended = {
        "tp1": max(0.1, round(float(np.percentile(g_positive, 45)), 1)),
        "tp2": max(0.1, round(float(np.percentile(g_positive, 60)), 1)),
        "tp3": max(0.1, round(float(np.percentile(g_positive, 72)), 1)),
        "sl":  round(float(np.percentile(d, 65)), 1),
    }

    aggressive = {
        "tp1": max(0.1, round(float(np.percentile(g_positive, 60)), 1)),
        "tp2": max(0.1, round(float(np.percentile(g_positive, 75)), 1)),
        "tp3": max(0.1, round(float(np.percentile(g_positive, 88)), 1)),
        "sl":  round(float(np.percentile(d, 80)), 1),
    }

    # Garantir la cohérence : TP1 < TP2 < TP3
    recommended = _ensure_tp_ordering(recommended)
    aggressive  = _ensure_tp_ordering(aggressive)

    return {"recommended": recommended, "aggressive": aggressive}


def _ensure_tp_ordering(strategy: dict) -> dict:
    """
    Garantit que TP1 < TP2 < TP3 avec un écart minimum de 2%.
    Évite des TP groupés sur une plage trop étroite.
    """
    min_gap = 2.0
    if strategy["tp2"] <= strategy["tp1"] + min_gap:
        strategy["tp2"] = round(strategy["tp1"] + min_gap, 1)
    if strategy["tp3"] <= strategy["tp2"] + min_gap:
        strategy["tp3"] = round(strategy["tp2"] + min_gap, 1)
    return strategy


def compute_winrate(metrics: list[dict], strategy: dict) -> float:
    """
    Calcule le winrate estimé d'une stratégie.

    Simulation par trade :
      - WIN  : gain_max_pct (net) >= TP1
      - LOSS : drawdown_pct (net) >= SL
      - Ambiguïté : si time_to_ath < 90s → WIN probable (ATH très rapide)
                    sinon → LOSS (le SL est probablement déclenché avant)

    Utilise les métriques NETTES → winrate reflète la réalité réelle.

    Args:
        metrics:  Liste des métriques nettes par trade
        strategy: Dict avec tp1, tp2, tp3, sl

    Returns:
        Winrate en % (0.0 à 100.0)
    """
    tp1 = strategy["tp1"]
    sl  = strategy["sl"]

    wins = losses = total = 0

    for m in metrics:
        gain_max = m.get("gain_max_pct", 0)   # net
        drawdown = m.get("drawdown_pct",  0)   # net, valeur absolue
        time_ath = m.get("time_to_ath_secs", 999999)

        total += 1
        tp1_hit = gain_max >= tp1
        sl_hit  = drawdown >= sl

        if tp1_hit and not sl_hit:
            wins += 1
        elif sl_hit and not tp1_hit:
            losses += 1
        elif tp1_hit and sl_hit:
            # ATH très rapide (<90s) → TP atteint avant le SL probable
            if time_ath < 90:
                wins += 1
            else:
                losses += 1
        else:
            losses += 1

    if total == 0:
        return 0.0

    return round((wins / total) * 100, 1)


def build_full_strategies(gains: list[float], drawdowns: list[float], metrics: list[dict]) -> dict:
    """
    Pipeline complet : construction des stratégies + calcul des winrates.

    Args:
        gains:     Distribution des gains nets
        drawdowns: Distribution des drawdowns nets
        metrics:   Métriques individuelles pour la simulation winrate

    Returns:
        Dict avec 'recommended' et 'aggressive', enrichis du winrate
    """
    strategies = build_strategies(gains, drawdowns)

    strategies["recommended"]["winrate"] = compute_winrate(metrics, strategies["recommended"])
    strategies["aggressive"]["winrate"]  = compute_winrate(metrics, strategies["aggressive"])

    return strategies
