# ============================================================
# services/gecko_service.py (REFactor CLEAN)
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

_pool_cache: dict[str, str | None] = {}


# ============================================================
# POOL RESOLUTION
# ============================================================

def _get_pool_address(token_mint: str) -> str | None:
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


# ============================================================
# PRICE AT ENTRY (POINT IN TIME)
# ============================================================

def get_price_at_entry(token_mint: str, timestamp: int) -> float | None:
    pool = _get_pool_address(token_mint)

    # 🔁 FALLBACK ONCHAIN
    if not pool:
        from services.onchain_price_service import (
            get_token_swaps_helius,
            get_entry_price_from_swaps
        )
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


# ============================================================
# FULL PRICE HISTORY (OHLCV)
# ============================================================

def get_price_history(token_mint: str, from_ts: int, to_ts: int) -> list[dict]:
    pool = _get_pool_address(token_mint)

    # 🔁 FALLBACK ONCHAIN
    if not pool:
        from services.onchain_price_service import (
            get_token_swaps_helius,
            swaps_to_ohlcv
        )
        swaps = get_token_swaps_helius(token_mint, from_ts, to_ts)
        return swaps_to_ohlcv(swaps, interval_seconds=1)

    timeframe, aggregate = TIMEFRAME_MAP.get(PRICE_INTERVAL, ("minute", 1))

    all_candles = []
    before_ts = to_ts

    with httpx.Client(timeout=20) as client:
        while True:
            url = (
                f"{GECKOTERMINAL_API_URL}/networks/solana/pools/{pool}"
                f"/ohlcv/{timeframe}"
                f"?aggregate={aggregate}"
                f"&before_timestamp={before_ts}"
                f"&limit=1000"
                f"&currency=usd"
            )

            try:
                resp = client.get(url, headers=HEADERS)
                resp.raise_for_status()
                candles = resp.json()["data"]["attributes"]["ohlcv_list"]
            except Exception:
                break

            if not candles:
                break

            in_range = [c for c in candles if c[0] >= from_ts]
            all_candles.extend(in_range)

            if len(in_range) < len(candles):
                break

            before_ts = candles[-1][0] - 1
            time.sleep(0.2)  # rate limit

    # 🔁 CLEAN + FORMAT
    seen = set()
    result = []

    for c in sorted(all_candles, key=lambda x: x[0]):
        ts = c[0]
        if ts in seen:
            continue

        seen.add(ts)
        result.append({
            "unixTime": ts,
            "o": c[1],
            "h": c[2],
            "l": c[3],
            "c": c[4],
            "v": c[5],
        })

    return result