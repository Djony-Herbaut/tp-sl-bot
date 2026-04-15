# ============================================================
# utils/filters.py
# ============================================================
#
# RÔLE :
#   Fonctions de nettoyage et validation des données brutes.
#
# POURQUOI FILTRER LES OUTLIERS :
#   Sur Pump.fun, certains tokens font x500 en quelques secondes
#   puis s'effondrent (rugs). Même nets de frais, ces valeurs
#   fausseraient les percentiles et rendraient les TP inaccessibles
#   pour les trades normaux.
#   On coupe au 99e percentile par défaut.
# ============================================================

import numpy as np


def remove_outliers(values: list[float], percentile: float = 99) -> list[float]:
    """
    Supprime les valeurs au-delà du percentile donné.

    Args:
        values:     Liste de valeurs numériques
        percentile: Seuil de coupure (défaut : 99e percentile)

    Returns:
        Liste filtrée sans les valeurs extrêmes
    """
    if not values:
        return values
    upper = float(np.percentile(values, percentile))
    return [v for v in values if v <= upper]


def remove_negative_gains(values: list[float]) -> list[float]:
    """
    Garde uniquement les gains positifs nets.
    Utilisé pour ne pas générer de TP négatifs (non rentables).
    """
    return [v for v in values if v > 0]


def is_valid_solana_address(address: str) -> bool:
    """
    Valide basiquement une adresse Solana (base58, 32 à 44 caractères).

    Args:
        address: Chaîne à valider

    Returns:
        True si l'adresse semble valide
    """
    if not address or not isinstance(address, str):
        return False
    if not (32 <= len(address) <= 44):
        return False
    base58_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    return all(c in base58_chars for c in address)
