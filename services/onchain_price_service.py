# services/onchain_price_service.py
#
# Reconstruit l'historique de prix à la seconde depuis les
# transactions on-chain Helius — fonctionne même pour les tokens
# avec 0 liquidité sur GeckoTerminal.
#
# Prix calculé : sol_spent / token_amount × SOL_PRICE_USD
# Granularité : 1 swap = 1 point de prix (timestamp exact)

import httpx
import time
from config import HELIUS_API_URL, HELIUS_API_KEY, PUMP_FUN_PROGRAM_ID

SOL_PRICE_USD = 70  # Mettre à jour ou récupérer dynamiquement

HEADERS = {"Content-Type": "application/json"}


def get_token_swaps_helius(token_mint: str, from_ts: int, to_ts: int) -> list[dict]:
    """
    Récupère tous les swaps d'un token via Helius Enhanced Transactions.
    Retourne une liste de points de prix avec timestamp précis.

    Chaque point : { unixTime, price_usd, sol_amount, token_amount, type }
    """
    url = f"{HELIUS_API_URL}/addresses/{token_mint}/transactions"
    params = {
        "api-key": HELIUS_API_KEY,
        "limit": 100,
        "type": "SWAP",
    }

    all_swaps = []
    before_sig = None

    with httpx.Client(timeout=30) as client:
        while True:
            if before_sig:
                params["before"] = before_sig

            try:
                resp = client.get(url, params=params, headers=HEADERS)
                resp.raise_for_status()
                txs = resp.json()
            except Exception:
                break

            if not txs:
                break

            for tx in txs:
                ts = tx.get("timestamp", 0)
                if ts < from_ts:
                    return all_swaps
                if ts > to_ts:
                    continue

                # Extraire sol_amount et token_amount depuis les transfers
                sol_out = sum(
                    abs(nt.get("amount", 0)) / 1e9
                    for nt in tx.get("nativeTransfers", [])
                )
                token_in = sum(
                    tt.get("tokenAmount", 0)
                    for tt in tx.get("tokenTransfers", [])
                    if tt.get("mint") == token_mint
                )

                if sol_out > 0 and token_in > 0:
                    price = (sol_out / token_in) * SOL_PRICE_USD
                    all_swaps.append({
                        "unixTime": ts,
                        "price_usd": price,
                        "sol_amount": sol_out,
                        "token_amount": token_in,
                    })

            before_sig = txs[-1].get("signature")
            if len(txs) < 100:
                break

            time.sleep(0.1)

    return sorted(all_swaps, key=lambda x: x["unixTime"])


def swaps_to_ohlcv(swaps: list[dict], interval_seconds: int = 1) -> list[dict]:
    """
    Convertit une liste de swaps en bougies OHLCV synthétiques.

    Avec interval_seconds=1 : chaque seconde devient une bougie.
    Un seul swap par seconde → O=H=L=C=prix du swap.

    Retourne le même format que gecko_service :
    { unixTime, o, h, l, c, v }
    """
    if not swaps:
        return []

    candles = {}
    for swap in swaps:
        # Arrondir au bucket de interval_seconds
        bucket = (swap["unixTime"] // interval_seconds) * interval_seconds
        p = swap["price_usd"]

        if bucket not in candles:
            candles[bucket] = {
                "unixTime": bucket,
                "o": p, "h": p, "l": p, "c": p,
                "v": swap["sol_amount"],
            }
        else:
            candles[bucket]["h"] = max(candles[bucket]["h"], p)
            candles[bucket]["l"] = min(candles[bucket]["l"], p)
            candles[bucket]["c"] = p
            candles[bucket]["v"] += swap["sol_amount"]

    return sorted(candles.values(), key=lambda x: x["unixTime"])


def get_entry_price_from_swaps(swaps: list[dict], timestamp: int) -> float | None:
    """
    Retourne le prix le plus proche du timestamp d'entrée.
    """
    if not swaps:
        return None
    closest = min(swaps, key=lambda s: abs(s["unixTime"] - timestamp))
    if abs(closest["unixTime"] - timestamp) <= 30:
        return closest["price_usd"]
    return None