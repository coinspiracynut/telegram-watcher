#!/usr/bin/env python3
"""
Position Monitor

Monitors active token positions and sells when profit targets are reached.
Uses get_token_value_in_sol() to check current value via the swap API quote.
"""
import asyncio
import logging
import time
from typing import Optional

from database import Database
from trader import SolanaTrader
from config import Config

# Helius free tier: 10 RPC calls/sec. Budget conservatively at 6-7
# to leave headroom for the watcher/processor and burst sells.
MAX_RPC_CALLS_PER_SECOND = 6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class PositionMonitor:
    """Monitors and manages token positions."""

    def __init__(self):
        self.db: Optional[Database] = None
        self.trader = SolanaTrader()
        self.profit_target = Config.PROFIT_TARGET_PERCENT
        self.sell_percentage = Config.SELL_PERCENTAGE
        self._db_owned = True  # Whether we own the db connection (standalone mode)

    async def start(self):
        """Initialize monitor (standalone mode)."""
        self.db = Database()
        await self.db.connect()
        self._db_owned = True
        logger.info("📊 Position Monitor started")
        logger.info(f"   Profit Target: {self.profit_target}% (sell when value ≥ {self.profit_target/100:.1f}x buy)")
        logger.info(f"   Sell Percentage: {self.sell_percentage}%")

    async def start_shared(self, db: Database):
        """Initialize monitor with a shared database connection (integrated mode)."""
        self.db = db
        self._db_owned = False
        logger.info("📊 Position Monitor started (shared mode)")
        logger.info(f"   Profit Target: {self.profit_target}% (sell when value ≥ {self.profit_target/100:.1f}x buy)")
        logger.info(f"   Sell Percentage: {self.sell_percentage}%")

    async def stop(self):
        """Stop monitor."""
        if self.db and self._db_owned:
            await self.db.close()
        logger.info("🛑 Position Monitor stopped")

    async def check_position(self, position: dict) -> bool:
        """
        Check a single position and sell if target reached.

        Returns:
            True if position was sold, False otherwise
        """
        token_address = position["address"]
        position_id = position["id"]
        buy_amount_sol = position["buy_amount_sol"]
        token_amount = position["token_amount"]  # Amount we think we have

        try:
            token_name = position["token_name"] if position["token_name"] else "Unknown"
        except (KeyError, TypeError):
            token_name = "Unknown"
        logger.info(f"🔍 Checking position #{position_id}: {token_name} ({token_address[:8]}...)")

        # Step 1: Get ACTUAL token balance from wallet via RPC
        actual_balance, decimals = await self.trader.get_token_balance(token_address)

        if actual_balance == 0:
            logger.warning(f"   ⚠️ No tokens in wallet, position may be closed")
            await self.db.close_position(position_id, None)
            return False

        current_token_amount = actual_balance

        # Step 2: Get current value in SOL using swap quote (Token → SOL)
        try:
            current_value_sol = await self.trader.get_token_value_in_sol(
                token_address, current_token_amount
            )

            if current_value_sol is None:
                logger.warning(f"   ⚠️ Could not get value quote, using buy amount as fallback")
                current_value_sol = buy_amount_sol
            else:
                logger.info(f"   📊 Quote: {current_token_amount:.4f} tokens ≈ {current_value_sol:.6f} SOL")

        except Exception as e:
            logger.error(f"   ❌ Error getting value: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            current_value_sol = buy_amount_sol  # Fallback

        # Step 3: Calculate profit and check sell threshold
        # Sell threshold: current value >= (PROFIT_TARGET_PERCENT / 100) * buy_amount
        # e.g. 250% target, 0.5 SOL buy → sell when value >= 2.5 * 0.5 = 1.25 SOL
        sell_threshold = (self.profit_target / 100.0) * buy_amount_sol
        profit_percent = ((current_value_sol - buy_amount_sol) / buy_amount_sol) * 100
        multiplier = current_value_sol / buy_amount_sol if buy_amount_sol > 0 else 0

        # Update position in database
        await self.db.update_position_value(position_id, current_value_sol, profit_percent)

        # Also sync token_amount to match actual wallet balance
        if abs(current_token_amount - token_amount) > 0.0001:
            await self.db._connection.execute(
                "UPDATE positions SET token_amount = ? WHERE id = ?",
                (current_token_amount, position_id),
            )
            await self.db._connection.commit()

        logger.info(f"   💰 Balance: {current_token_amount:.4f} tokens")
        logger.info(f"   💰 Current Value: {current_value_sol:.6f} SOL (bought for {buy_amount_sol:.4f} SOL)")
        logger.info(f"   📈 Profit: {profit_percent:.2f}% ({multiplier:.2f}x) | Sell target: {sell_threshold:.4f} SOL ({self.profit_target/100:.1f}x)")

        # Step 4: Check if sell threshold reached
        if current_value_sol >= sell_threshold:
            logger.info(f"   🎯 Target reached! Value {current_value_sol:.4f} SOL ≥ {sell_threshold:.4f} SOL — Selling {self.sell_percentage}%...")

            # Re-fetch actual balance for sell amount
            actual_balance, decimals = await self.trader.get_token_balance(token_address)
            sell_amount = actual_balance * (self.sell_percentage / 100.0)

            # Execute sell (amount is human-readable, API handles conversion)
            tx_signature = await self.trader.sell_token(token_address, sell_amount)

            if tx_signature:
                logger.info(f"   ✅ Sold: {tx_signature}")
                await self.db.close_position(position_id, tx_signature)
                logger.info(f"   📝 Position closed")
                return True
            else:
                logger.error(f"   ❌ Failed to sell")
                return False
        else:
            logger.debug(f"   ⏳ Target not reached ({profit_percent:.2f}% < {self.profit_target}%)")
            return False

    async def check_all_positions(self):
        """
        Check all active positions, rate-limited to stay within Helius RPC budget.
        
        Each position check makes ~1 RPC call (get_token_balance) to Helius.
        The quote call goes to SolanaTracker (separate endpoint, no RPC limit).
        We space out positions to stay under MAX_RPC_CALLS_PER_SECOND.
        """
        positions = await self.db.get_active_positions()

        if not positions:
            logger.debug("No active positions to check")
            return 0.0

        n = len(positions)
        # Delay between positions: 1 / budget (e.g. 1/6 ≈ 167ms)
        delay_between = 1.0 / MAX_RPC_CALLS_PER_SECOND
        estimated_time = n * delay_between
        logger.info(f"📊 Checking {n} active positions (~{estimated_time:.1f}s at {MAX_RPC_CALLS_PER_SECOND} RPC/s)...")

        cycle_start = time.monotonic()

        for i, position in enumerate(positions):
            call_start = time.monotonic()
            try:
                await self.check_position(position)
            except Exception as e:
                try:
                    pos_id = position["id"]
                except (KeyError, TypeError):
                    pos_id = "unknown"
                logger.error(f"❌ Error checking position {pos_id}: {e}")

            # Rate-limit: ensure at least `delay_between` seconds between RPC calls
            elapsed = time.monotonic() - call_start
            sleep_needed = delay_between - elapsed
            if sleep_needed > 0 and i < n - 1:  # Don't sleep after the last one
                await asyncio.sleep(sleep_needed)

        cycle_time = time.monotonic() - cycle_start
        logger.info(f"📊 Cycle complete: {n} positions checked in {cycle_time:.1f}s")
        return cycle_time

    async def run_forever(self, min_cycle_pause: float = 5.0):
        """
        Run monitoring loop continuously.

        The loop checks every position at a rate-limited pace, then pauses
        briefly before starting the next cycle. With many tokens the cycle
        itself provides the spacing; with few tokens the pause prevents
        hammering.

        Args:
            min_cycle_pause: Minimum seconds to wait between cycles (default 5s)
        """
        # Only start standalone if not already initialized via start_shared()
        if not self.db:
            await self.start()

        logger.info(f"⏳ Monitor loop: {MAX_RPC_CALLS_PER_SECOND} RPC/s budget, {min_cycle_pause}s min pause between cycles")

        try:
            while True:
                cycle_time = await self.check_all_positions()
                # Wait at least min_cycle_pause before next sweep
                await asyncio.sleep(min_cycle_pause)
        except asyncio.CancelledError:
            logger.info("⏹️  Monitor cancelled")
        except KeyboardInterrupt:
            logger.info("⏹️  Interrupted by user")
        finally:
            await self.stop()


async def main():
    """Main entry point (standalone mode)."""
    monitor = PositionMonitor()

    try:
        await monitor.run_forever(min_cycle_pause=5.0)
    except KeyboardInterrupt:
        logger.info("⏹️  Interrupted by user")
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())
