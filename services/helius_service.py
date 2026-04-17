# ============================================================
# services/helius_service.py
# ============================================================
#
# RÔLE :
#   Récupère et filtre les transactions d'un wallet Solana
#   via l'API Enhanced Transactions de Helius.
#
# CORRECTIONS v3.1 :
#   - Détection Pump.fun via instructions[].programId (pas accountData)
#   - Détection BUY : SOL sort du wallet + token entre dans le wallet
#   - Fallback sur description "SWAP" si instructions non dispo
#   - Log de debug pour faciliter le diagnostic
# ============================================================

import httpx
import time
from datetime import datetime, timedelta
from config import HELIUS_API_URL, HELIUS_API_KEY, ANALYSIS_DAYS, PUMP_FUN_PROGRAM_ID

HEADERS = {"Content-Type": "application/json"}

# Program IDs alternatifs Pump.fun (migration, AMM, etc.)
PUMP_FUN_PROGRAM_IDS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # Programme principal
    "BSfD6SHZigAfDWSjzD5Q41jw8LmKwtmjskPH9XW1mrRW",  # Pump.fun AMM
    "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",  # Variante connue
}


def get_wallet_transactions(wallet_address: str) -> list[dict]:
    """
    Retourne toutes les transactions SWAP du wallet sur les ANALYSIS_DAYS derniers jours.
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


def _is_pump_fun_tx(tx: dict) -> bool:
    """
    Vérifie si une transaction implique un programme Pump.fun.

    Helius peut exposer le program ID via :
    1. instructions[].programId  (Enhanced TX)
    2. instructions[].innerInstructions[].programId
    3. accountData[].account  (moins fiable, mais fallback)

    Returns True si au moins un programme Pump.fun est trouvé.
    """
    # Méthode 1 : instructions directes
    for instr in tx.get("instructions", []):
        prog_id = instr.get("programId", "")
        if prog_id in PUMP_FUN_PROGRAM_IDS:
            return True
        # Inner instructions
        for inner in instr.get("innerInstructions", []):
            if inner.get("programId", "") in PUMP_FUN_PROGRAM_IDS:
                return True

    # Méthode 2 : accountData (fallback, moins précis)
    for account_data in tx.get("accountData", []):
        if account_data.get("account", "") in PUMP_FUN_PROGRAM_IDS:
            return True

    # Méthode 3 : source / platform metadata si présent
    source = tx.get("source", "").upper()
    if "PUMP" in source:
        return True

    return False


def filter_pump_fun_buys(transactions: list[dict], wallet_address: str) -> list[dict]:
    """
    Filtre les transactions pour ne garder que les BUY Pump.fun.

    Un BUY est identifié par :
    1. Programme Pump.fun présent dans les instructions (ou accountData)
    2. Le wallet ENVOIE des SOL (nativeTransfers fromUserAccount == wallet)
    3. Le wallet REÇOIT un token (tokenTransfers toUserAccount == wallet)

    Note : on ne filtre PAS sur PUMP_FUN_PROGRAM_ID dans accountData uniquement,
    car Helius place le program ID dans instructions[].programId.

    Args:
        transactions:   Liste de transactions Helius (type SWAP)
        wallet_address: Adresse du wallet analysé

    Returns:
        Liste de dicts avec les infos d'achat enrichies
    """
    buys = []
    skipped_no_pump = 0
    skipped_no_token = 0
    skipped_no_sol = 0

    for tx in transactions:
        # Étape 1 : vérifier que c'est bien une tx Pump.fun
        if not _is_pump_fun_tx(tx):
            skipped_no_pump += 1
            continue

        token_transfers  = tx.get("tokenTransfers", [])
        native_transfers = tx.get("nativeTransfers", [])

        # Étape 2 : trouver le token reçu par le wallet
        received_token = None
        for tt in token_transfers:
            if tt.get("toUserAccount") == wallet_address and tt.get("mint"):
                # Exclure les tokens SOL wrappés (WSOL)
                if tt["mint"] == "So11111111111111111111111111111111111111112":
                    continue
                received_token = tt
                break

        if not received_token:
            skipped_no_token += 1
            continue

        # Étape 3 : calculer les SOL dépensés
        # On additionne tous les transferts natifs sortants du wallet
        sol_spent = sum(
            abs(nt.get("amount", 0)) / 1e9
            for nt in native_transfers
            if nt.get("fromUserAccount") == wallet_address
        )

        # Fallback : si nativeTransfers vide, chercher dans accountData
        if sol_spent == 0:
            for ad in tx.get("accountData", []):
                if ad.get("account") == wallet_address:
                    native_delta = ad.get("nativeBalanceChange", 0)
                    if native_delta < 0:
                        sol_spent = abs(native_delta) / 1e9
                        break

        if sol_spent == 0:
            skipped_no_sol += 1
            continue

        # Trade BUY valide ✓
        buys.append({
            "signature":    tx.get("signature"),
            "timestamp":    tx.get("timestamp"),
            "token_mint":   received_token["mint"],
            "token_amount": received_token.get("tokenAmount", 0),
            "sol_spent":    round(sol_spent, 6),
            "wallet":       wallet_address,
        })

    print(f"      → Filtre Pump.fun : {skipped_no_pump} ignorées (pas Pump.fun), "
          f"{skipped_no_token} sans token reçu, {skipped_no_sol} sans SOL dépensé")

    return buys