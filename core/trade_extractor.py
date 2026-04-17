# ============================================================
# core/trade_extractor.py
# ============================================================
#
# CORRECTIONS v3.1 :
#   - Logs de debug enrichis pour diagnostiquer les wallets à 0 trades
#   - Fallback sol_spent via accountData si nativeTransfers vide
#   - Validation que entry_price_target est > 0 (rejette les prix aberrants)
# ============================================================

import time
from services.helius_service import get_wallet_transactions, filter_pump_fun_buys
from services.gecko_service import get_price_at_entry


def extract_trades(wallet_address: str) -> list[dict]:
    """
    Pipeline complet d'extraction des trades d'un wallet.

    Returns:
        Liste de trades valides enrichis avec le prix d'entrée du wallet cible
    """
    print(f"[1/3] Récupération des transactions pour {wallet_address[:8]}...")
    raw_txs = get_wallet_transactions(wallet_address)
    print(f"      → {len(raw_txs)} transactions SWAP trouvées sur 30j")

    if not raw_txs:
        print("      ⚠️  Aucune transaction SWAP. Vérifier l'adresse ou l'activité du wallet.")
        return []

    print("[2/3] Filtrage Pump.fun BUY...")
    buys = filter_pump_fun_buys(raw_txs, wallet_address)
    print(f"      → {len(buys)} achats Pump.fun identifiés")

    if not buys:
        print("      ⚠️  0 BUY Pump.fun détecté.")
        print(f"         Exemple de tx analysée :")
        if raw_txs:
            tx = raw_txs[0]
            print(f"         source={tx.get('source')}, type={tx.get('type')}")
            print(f"         instructions programIds: {[i.get('programId','?') for i in tx.get('instructions',[])[:5]]}")
            print(f"         accountData accounts: {[a.get('account','?') for a in tx.get('accountData',[])[:5]]}")
        return []

    # Déduplication : conserver uniquement la première entrée par token
    seen_tokens: dict[str, dict] = {}
    for buy in buys:
        mint = buy["token_mint"]
        if mint not in seen_tokens:
            seen_tokens[mint] = buy
        elif buy["timestamp"] < seen_tokens[mint]["timestamp"]:
            seen_tokens[mint] = buy

    unique_buys = list(seen_tokens.values())
    print(f"      → {len(unique_buys)} tokens uniques après déduplication")

    print(f"[3/3] Récupération des prix d'entrée ({len(unique_buys)} tokens)...")
    trades = []
    skipped_no_price = 0

    for i, buy in enumerate(unique_buys):
        entry_price = get_price_at_entry(buy["token_mint"], buy["timestamp"])

        if entry_price is None or entry_price <= 0:
            skipped_no_price += 1
            continue

        trades.append({**buy, "entry_price_target": entry_price})

        if (i + 1) % 10 == 0:
            print(f"      → {i + 1}/{len(unique_buys)} prix récupérés...")

        time.sleep(0.1)

    if skipped_no_price:
        print(f"      ⚠️  {skipped_no_price} tokens sans prix disponible (trop récents ou sans liquidité)")

    print(f"      → {len(trades)} trades valides avec prix d'entrée")
    return trades