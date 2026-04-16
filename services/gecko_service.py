# ============================================================
# services/gecko_service.py
# ============================================================
#
# RÔLE :
#   Récupère les données de prix OHLCV pour les tokens Solana
#   via l'API publique GeckoTerminal.
#
# API UTILISÉE :
#   https://api.geckoterminal.com/api/v2
#   Doc : https://www.geckoterminal.com/api
#
# PLAN REQUIS :
#   Gratuit — aucune clé API nécessaire
#   Limite : 30 requêtes / minute (gérée par time.sleep)
#
# RÉSOLUTION MINT → POOL :
#   GeckoTerminal raisonne par POOL et non par token mint.
#   On résout d'abord le mint vers l'adresse de pool principale
#   (la plus liquide), puis on interroge l'OHLCV du pool.
#   Un cache mémoire évite de re-résoudre le même mint.
#
# RÉSOLUTION 1m :
#   On utilise l'intervalle 1m pour maximiser la précision du
#   calcul de l'ATH post-entry et du drawdown.
#   Avec des bougies de 15m, on pourrait rater un pic de 3 min
#   ou sous-estimer le vrai drawdown intra-bougie.
#
# FORMAT OHLCV GeckoTerminal :
#   [timestamp, open, high, low, close, volume]
#
# FORMAT de sortie attendu par performance_analyzer.py :
#   { unixTime, o, h, l, c, v }
# ============================================================

import httpx
import time
from config import GECKOTERMINAL_API_URL, PRICE_INTERVAL

HEADERS = {"Accept": "application/json;version=20230302"}

TIMEFRAME_MAP = {
    "1m":  ("minute", 1),
    "5m":  ("minute", 5),
    "15m": ("minute", 15),
    "30m": ("minute", 30),
    "1H":  ("hour",   1),
    "4H":  ("hour",   4),
    "1D":  ("day",    1),
}

# Cache en mémoire pour éviter de re-résoudre les pools
_pool_cache: dict[str, str | None] = {}


def _get_pool_address(token_mint: str) -> str | None:
    """
    Résout un token_mint Solana en adresse de pool GeckoTerminal.
    Prend le premier pool retourné (le plus liquide).
    Résultat mis en cache.

    Args:
        token_mint: Adresse mint du token (base58)

    Returns:
        Adresse du pool ou None si introuvable
    """
    if token_mint in _pool_cache:
        return _pool_cache[token_mint]

    url = f"{GECKOTERMINAL_API_URL}/networks/solana/tokens/{token_mint}/pools"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=HEADERS)
            resp.raise_for_status()
            pools = resp.json().get("data", [])

        if not pools:
            _pool_cache[token_mint] = None
            return None

        pool_address = pools[0]["attributes"]["address"]
        _pool_cache[token_mint] = pool_address
        return pool_address

    except Exception:
        _pool_cache[token_mint] = None
        return None


def get_price_history(token_mint: str, from_ts: int, to_ts: int) -> list[dict]:
    """
    Retourne le prix de clôture du token au moment de l'entrée du wallet cible.

    Note : ce prix correspond à l'entrée du wallet ANALYSÉ.
    L'entrée réelle du copieur est modélisée dans performance_analyzer
    via ENTRY_DELAY_SECONDS et SLIPPAGE_PER_SECOND_PCT.

    Args:
        token_mint: Adresse mint du token
        timestamp:  Timestamp unix de l'entrée (secondes)

    Returns:
        Prix en USD ou None si indisponible
    """
    pool = _get_pool_address(token_mint)

    if not pool:
        # Fallback : reconstruire depuis les swaps on-chain Helius
        from services.onchain_price_service import get_token_swaps_helius, swaps_to_ohlcv
        swaps = get_token_swaps_helius(token_mint, from_ts, to_ts)
        return swaps_to_ohlcv(swaps, interval_seconds=1)

    timeframe, aggregate = TIMEFRAME_MAP.get(PRICE_INTERVAL, ("minute", 1))
    url = (
        f"{GECKOTERMINAL_API_URL}/networks/solana/pools/{pool}"
        f"/ohlcv/{timeframe}"
        f"?aggregate={aggregate}"
        f"&before_timestamp={timestamp + 120}"
        f"&limit=5"
        f"&currency=usd"
    )

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=HEADERS)
            resp.raise_for_status()
            candles = resp.json()["data"]["attributes"]["ohlcv_list"]

        if not candles:
            return None

        closest = min(candles, key=lambda x: abs(x[0] - timestamp))
        return float(closest[4])  # close

    except Exception:
        return None

def get_price_at_entry(token_mint: str, timestamp: int) -> float | None:
    pool = _get_pool_address(token_mint)

    if not pool:
        from services.onchain_price_service import get_token_swaps_helius, get_entry_price_from_swaps
        swaps = get_token_swaps_helius(token_mint, timestamp - 60, timestamp + 60)
        return get_entry_price_from_swaps(swaps, timestamp)

    timeframe, aggregate = TIMEFRAME_MAP.get(PRICE_INTERVAL, ("minute", 1))
    before_ts = timestamp + 120

    url = (
        f"{GECKOTERMINAL_API_URL}/networks/solana/pools/{pool}"
        f"/ohlcv/{timeframe}"
        f"?aggregate={aggregate}"
        f"&before_timestamp={before_ts}"
        f"&limit=5"
        f"&currency=usd"
    )

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=HEADERS)
            resp.raise_for_status()
            candles = resp.json()["data"]["attributes"]["ohlcv_list"]

        if not candles:
            return None

        closest = min(candles, key=lambda x: abs(x[0] - timestamp))
        return float(closest[4])

    except Exception:
        return None