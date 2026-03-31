# ============================================================
# utils/stats.py — Fonctions statistiques réutilisables
# ============================================================

import numpy as np


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    return round(float(np.percentile(values, p)), 2)


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(np.mean(values)), 2)


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(np.median(values)), 2)


def std(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(np.std(values)), 2)
