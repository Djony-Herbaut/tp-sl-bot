# ============================================================
# core/trade_extractor.py
# ============================================================
#
# RÔLE :
#   Orchestre l'extraction et la préparation des trades bruts.
#   Premier maillon du pipeline d'analyse.
#
# PIPELINE :
#   1. get_wallet_transactions()  → toutes les tx SWAP du wallet (30j)
#   2. filter_pump_fun_buys()     → filtre sur les BUY Pump.fun uniquement
#   3. Déduplication par token    → on garde la 1ère entrée par mint
#   4. get_price_at_entry()       → enrichit avec le prix d'entrée du wallet cible
#
# NOTE SUR LE PRIX D'ENTRÉE :
#   Le prix récupéré ici est celui du WALLET CIBLE au moment de son achat.
#   Le prix d'entrée RÉEL du copieur (toi) est calculé dans
#   performance_analyzer.py en appliquant le délai et le slippage.
# ============================================================

import time
from services.helius_service import get_wallet_transactions, filter_pump_fun_buys
from services.gecko_service import get_price_at_entry


def extract_trades(wallet_address: str) -> list[dict]:
    """
    Pipeline complet d'extraction des trades d'un wallet.

    Args:
        wallet_address: Adresse Solana du wallet à analyser

    Returns:
        Liste de trades valides enrichis avec le prix d'entrée du wallet cible

    Raises:
        RuntimeError: Si l'API Helius est inaccessible
    """
    print(f"[1/3] Récupération des transactions pour {wallet_address[:8]}...")
    raw_txs = get_wallet_transactions(wallet_address)
    print(f"      → {len(raw_txs)} transactions trouvées")

    print("[2/3] Filtrage Pump.fun BUY...")
    buys = filter_pump_fun_buys(raw_txs, wallet_address)
    print(f"      → {len(buys)} achats Pump.fun identifiés")

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

    for i, buy in enumerate(unique_buys):
        # Prix au moment exact de l'achat du wallet cible
        entry_price = get_price_at_entry(buy["token_mint"], buy["timestamp"])

        if entry_price is None or entry_price <= 0:
            continue

        trades.append({**buy, "entry_price_target": entry_price})

        if (i + 1) % 10 == 0:
            print(f"      → {i + 1}/{len(unique_buys)} prix récupérés...")

        time.sleep(0.1)

    print(f"      → {len(trades)} trades valides avec prix d'entrée")
    return trades
