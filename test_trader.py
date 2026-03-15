#!/usr/bin/env python3
"""
Test script for the trading implementation.

Usage:
    # Check balance of a token in your wallet
    python test_trader.py balance <token_address>

    # Get a quote (value in SOL) for your holdings of a token
    python test_trader.py quote <token_address>

    # Buy a token with SOL (default 0.01 SOL for safety)
    python test_trader.py buy <token_address> [amount_sol]

    # Sell a token (default: sell 100% of holdings)
    python test_trader.py sell <token_address> [percent]

    # Full round-trip: buy → balance → quote → sell
    python test_trader.py roundtrip <token_address> [amount_sol]

Examples:
    python test_trader.py balance So11111111111111111111111111111111111111112
    python test_trader.py quote GQaDVLoi9xe2eQcKqC5c11vRxJWu5askVty1dmzmoy8k
    python test_trader.py buy GQaDVLoi9xe2eQcKqC5c11vRxJWu5askVty1dmzmoy8k 0.01
    python test_trader.py sell GQaDVLoi9xe2eQcKqC5c11vRxJWu5askVty1dmzmoy8k 50
    python test_trader.py roundtrip GQaDVLoi9xe2eQcKqC5c11vRxJWu5askVty1dmzmoy8k 0.01
"""
import asyncio
import sys
import logging

from trader import SolanaTrader

# Setup logging — INFO level, no noisy debug output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet down noisy libraries
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("solana").setLevel(logging.WARNING)
logging.getLogger("solders").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def cmd_balance(trader: SolanaTrader, token_address: str):
    """Check token balance in wallet."""
    print(f"\n📦 Checking balance for {token_address}...")
    balance, decimals = await trader.get_token_balance(token_address)
    print(f"   Balance: {balance}")
    print(f"   Decimals: {decimals}")
    return balance, decimals


async def cmd_quote(trader: SolanaTrader, token_address: str):
    """Get current value of holdings in SOL."""
    balance, decimals = await trader.get_token_balance(token_address)
    if balance == 0:
        print(f"\n📊 No tokens held for {token_address} — nothing to quote")
        return None

    print(f"\n📊 Getting SOL value for {balance} tokens of {token_address}...")
    value = await trader.get_token_value_in_sol(token_address, balance)
    if value is not None:
        print(f"   {balance} tokens = {value:.6f} SOL")
    else:
        print(f"   ⚠️ Could not get quote")
    return value


async def cmd_buy(trader: SolanaTrader, token_address: str, amount_sol: float):
    """Buy a token with SOL."""
    print(f"\n🛒 Buying {amount_sol} SOL worth of {token_address}...")

    # Balance before
    balance_before, decimals = await trader.get_token_balance(token_address)
    print(f"   Balance before: {balance_before}")

    # Execute buy
    tx = await trader.buy_token(token_address, amount_sol)

    if tx:
        print(f"   ✅ TX: {tx}")
        print(f"   🔗 https://solscan.io/tx/{tx}")

        # Wait for settlement
        print(f"   ⏳ Waiting 3s for settlement...")
        await asyncio.sleep(3)

        # Balance after
        balance_after, _ = await trader.get_token_balance(token_address)
        received = balance_after - balance_before
        print(f"   Balance after: {balance_after}")
        print(f"   Tokens received: {received}")
        return tx
    else:
        print(f"   ❌ Buy failed")
        return None


async def cmd_sell(trader: SolanaTrader, token_address: str, percent: float = 100.0):
    """Sell a percentage of token holdings."""
    balance, decimals = await trader.get_token_balance(token_address)
    if balance == 0:
        print(f"\n💰 No tokens to sell for {token_address}")
        return None

    sell_amount = balance * (percent / 100.0)
    print(f"\n💰 Selling {percent}% of holdings ({sell_amount} of {balance} tokens)...")

    tx = await trader.sell_token(token_address, sell_amount)

    if tx:
        print(f"   ✅ TX: {tx}")
        print(f"   🔗 https://solscan.io/tx/{tx}")

        # Wait and check remaining
        print(f"   ⏳ Waiting 3s for settlement...")
        await asyncio.sleep(3)

        remaining, _ = await trader.get_token_balance(token_address)
        print(f"   Remaining balance: {remaining}")
        return tx
    else:
        print(f"   ❌ Sell failed")
        return None


async def cmd_roundtrip(trader: SolanaTrader, token_address: str, amount_sol: float):
    """Full round-trip: buy → balance → quote → sell."""
    print(f"\n🔄 ROUND-TRIP TEST for {token_address} with {amount_sol} SOL")
    print("=" * 60)

    # Step 1: Buy
    tx = await cmd_buy(trader, token_address, amount_sol)
    if not tx:
        print("\n❌ Round-trip aborted — buy failed")
        return

    # Step 2: Check balance
    await cmd_balance(trader, token_address)

    # Step 3: Get quote
    value = await cmd_quote(trader, token_address)

    # Step 4: Sell everything
    input_msg = input("\n   Press Enter to sell all, or 'skip' to keep: ").strip()
    if input_msg.lower() == "skip":
        print("   ⏭️ Skipping sell — tokens kept in wallet")
    else:
        await cmd_sell(trader, token_address, 100.0)

    print("\n" + "=" * 60)
    print("🔄 ROUND-TRIP COMPLETE")


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()
    token_address = sys.argv[2]

    # Initialize trader
    trader = SolanaTrader()
    if not trader.wallet_configured:
        print("❌ SOLANA_WALLET_PRIVATE_KEY not set in .env")
        sys.exit(1)

    print(f"🔑 Wallet: {trader.keypair.pubkey()}")
    print(f"🌐 RPC: {trader.rpc_url}")

    try:
        if command == "balance":
            await cmd_balance(trader, token_address)

        elif command == "quote":
            await cmd_quote(trader, token_address)

        elif command == "buy":
            amount_sol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.01
            await cmd_buy(trader, token_address, amount_sol)

        elif command == "sell":
            percent = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0
            await cmd_sell(trader, token_address, percent)

        elif command == "roundtrip":
            amount_sol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.01
            await cmd_roundtrip(trader, token_address, amount_sol)

        else:
            print(f"❌ Unknown command: {command}")
            print(__doc__)
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n⏹️ Interrupted")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
