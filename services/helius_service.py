# ============================================================
# services/helius_service.py
# ============================================================
#
# RÔLE :
#   Récupère et filtre les transactions d'un wallet Solana
#   via l'API Enhanced Transactions de Helius.
#
# API UTILISÉE :
#   GET https://api.helius.xyz/v0/addresses/{wallet}/transactions
#   Doc : https://docs.helius.dev/solana-apis/enhanced-transactions-api
#
# PLAN REQUIS :
#   Gratuit — 100 000 crédits/mois (~1 crédit par transaction)
#
# PAGINATION :
#   L'API retourne 100 transactions max par appel.
#   Pagination via le paramètre `before` (signature de la dernière tx).
#   On s'arrête dès qu'on dépasse la fenêtre de 30 jours.
#
# FILTRE PUMP.FUN :
#   On vérifie que le Program ID Pump.fun est dans les accountData
#   et que le wallet reçoit un token (= BUY).
# ============================================================

import httpx
import time
from datetime import datetime, timedelta
from config import HELIUS_API_URL, HELIUS_API_KEY, ANALYSIS_DAYS, PUMP_FUN_PROGRAM_ID

HEADERS = {"Content-Type": "application/json"}


def get_wallet_transactions(wallet_address: str) -> list[dict]:
    """
    Retourne toutes les transactions SWAP du wallet sur les ANALYSIS_DAYS derniers jours.

    Args:
        wallet_address: Adresse Solana du wallet (base58)

    Returns:
        Liste de transactions parsées par Helius

    Raises:
        RuntimeError: Si l'API Helius est inaccessible
    """
    cutoff    = datetime.utcnow() - timedelta(days=ANALYSIS_DAYS)
    cutoff_ts = int(cutoff.timestamp())

    url    = f"{HELIUS_API_URL}/addresses/{wallet_address}/transactions"
    params = {
        "api-key":    HELIUS_API_KEY,
        "limit":      100,
        "type":       "SWAP",
        "commitment": "confirmed",
    }

    all_txs    = []
    before_sig = None

    with httpx.Client(timeout=30) as client:
        while True:
            if before_sig:
                params["before"] = before_sig

            try:
                resp = client.get(url, params=params, headers=HEADERS)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"Helius API error {e.response.status_code}: {e.response.text}")
            except Exception as e:
                raise RuntimeError(f"Erreur réseau Helius: {str(e)}")

            if not data:
                break

            for tx in data:
                tx_ts = tx.get("timestamp", 0)
                if tx_ts < cutoff_ts:
                    return all_txs
                all_txs.append(tx)

            before_sig = data[-1].get("signature")
            if len(data) < 100:
                break

            time.sleep(0.1)

    return all_txs


def filter_pump_fun_buys(transactions: list[dict], wallet_address: str) -> list[dict]:
    """
    Filtre les transactions pour ne garder que les BUY Pump.fun.

    Un BUY est identifié par :
    1. Le Program ID Pump.fun présent dans les accountData
    2. Le wallet reçoit un token (toUserAccount == wallet)

    Args:
        transactions:   Liste de transactions Helius
        wallet_address: Adresse du wallet analysé

    Returns:
        Liste de dicts avec les infos d'achat enrichies
    """
    buys = []

    for tx in transactions:
        account_keys = [a.get("account", "") for a in tx.get("accountData", [])]
        if PUMP_FUN_PROGRAM_ID not in account_keys:
            continue

        token_transfers  = tx.get("tokenTransfers", [])
        native_transfers = tx.get("nativeTransfers", [])

        for tt in token_transfers:
            if tt.get("toUserAccount") == wallet_address and tt.get("mint"):
                sol_spent = sum(
                    abs(nt.get("amount", 0)) / 1e9
                    for nt in native_transfers
                    if nt.get("fromUserAccount") == wallet_address
                )

                buys.append({
                    "signature":    tx.get("signature"),
                    "timestamp":    tx.get("timestamp"),
                    "token_mint":   tt.get("mint"),
                    "token_amount": tt.get("tokenAmount", 0),
                    "sol_spent":    round(sol_spent, 6),
                    "wallet":       wallet_address,
                })
                break

    return buys
