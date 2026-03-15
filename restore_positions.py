#!/usr/bin/env python3
"""
Audit closed positions and restore any that still have tokens in the wallet.

Some positions were incorrectly closed when the sqlite3.Row .get() bug
caused balance checks to return 0.  This script checks actual on-chain
balances and re-opens positions where tokens are still held.

Usage:
    python restore_positions.py          # dry run
    python restore_positions.py --apply  # actually restore
"""
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

# Activate dotenv so Config picks up .env
from dotenv import load_dotenv
load_dotenv()

from config import Config
from trader import SolanaTrader

DATABASE_PATH = Path(__file__).parent / "messages.db"

# Rate-limit: ~4 RPC calls/sec to stay safe on Helius free tier
RPC_DELAY = 0.3  # seconds between balance checks


async def main():
    apply = "--apply" in sys.argv
    trader = SolanaTrader()

    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row

    # Get all closed positions with their token address
    rows = conn.execute("""
        SELECT p.id, p.token_id, p.buy_amount_sol, p.buy_price, p.token_amount,
               p.buy_tx_signature, p.current_value_sol, p.sell_tx_signature,
               t.address, t.token_name, t.token_ticker, t.network
        FROM positions p
        INNER JOIN tokens t ON p.token_id = t.id
        WHERE p.status = 'closed'
        ORDER BY p.id ASC
    """).fetchall()

    print(f"📊 Found {len(rows)} closed positions to audit\n")

    to_restore = []

    for i, row in enumerate(rows):
        pos_id = row["id"]
        address = row["address"]
        name = row["token_name"] or "Unknown"
        ticker = row["token_ticker"] or "?"

        print(f"  [{i+1}/{len(rows)}] #{pos_id} {name} (${ticker}) — {address[:16]}...", end="  ")

        try:
            balance, decimals = await trader.get_token_balance(address)
        except Exception as e:
            print(f"❌ RPC error: {e}")
            await asyncio.sleep(RPC_DELAY)
            continue

        if balance > 0:
            print(f"✅ Balance: {balance:,.4f} tokens — RESTORE")
            to_restore.append({
                "id": pos_id,
                "address": address,
                "name": name,
                "ticker": ticker,
                "balance": balance,
                "buy_amount_sol": row["buy_amount_sol"],
            })
        else:
            print(f"— empty")

        await asyncio.sleep(RPC_DELAY)

    print(f"\n{'='*60}")
    print(f"📋 {len(to_restore)} positions have tokens still in wallet:\n")

    for r in to_restore:
        print(f"  #{r['id']:4d}  {r['name']} (${r['ticker']})  —  {r['balance']:,.4f} tokens  (bought {r['buy_amount_sol']:.4f} SOL)")

    if not to_restore:
        print("✅ Nothing to restore — all closed positions are truly empty")
        conn.close()
        return

    if not apply:
        print(f"\n⚠️  Dry run — nothing was changed.")
        print(f"    Run with --apply to restore these {len(to_restore)} positions.")
    else:
        restored = 0
        for r in to_restore:
            try:
                conn.execute(
                    """
                    UPDATE positions
                    SET status = 'active',
                        token_amount = ?,
                        sell_tx_signature = NULL,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (r["balance"], r["id"]),
                )
                restored += 1
            except Exception as e:
                print(f"  ❌ Failed to restore #{r['id']}: {e}")
        conn.commit()
        print(f"\n✅ Restored {restored} positions to active status")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
