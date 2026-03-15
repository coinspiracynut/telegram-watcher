"""
Python trading wrapper using solana-swap-python (SolanaTracker).

Available methods from solanatracker.py:
  - get_swap_instructions(from_token, to_token, from_amount, slippage, payer, priority_fee, force_legacy)
      → Calls the Swap API, returns dict with 'txn' (base64 transaction) + rate info
  - perform_swap(swap_response, options)
      → Signs, sends, and confirms the transaction. Returns txid string or Exception.

IMPORTANT: The SolanaTracker API expects amounts in HUMAN-READABLE units:
  - For SOL: 0.5 means 0.5 SOL (NOT 500000000 lamports)
  - For tokens: 1000 means 1000 tokens (NOT 1000 * 10^decimals)

There is NO get_rate() method. To check token value without swapping,
we call get_swap_instructions() (Token → SOL) which returns rate info
in the response WITHOUT executing the swap.

For token balances, we use standard Solana RPC calls (getTokenAccountsByOwner).
"""
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple

import aiohttp
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TokenAccountOpts

from solanatracker import (
    SolanaTracker,
    TransactionExpiredError,
    TransactionConfirmationTimeoutError,
    TransactionFailedError,
)
from config import Config

logger = logging.getLogger(__name__)

# SOL mint address (native wrapped SOL)
SOL_MINT = "So11111111111111111111111111111111111111112"

# How many times to re-fetch swap instructions and retry if the tx expires or times out
MAX_SWAP_RETRIES = 3


def _validate_solana_address(address: str) -> bool:
    """Quick check that a string looks like a valid base58 Solana address."""
    import re
    return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address))

# Public Solana RPC for read-only queries (balance checks).
# The SolanaTracker RPC requires a paid API key and may 401.
# Standard RPC calls don't need SolanaTracker — any endpoint works.
PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"


class SolanaTrader:
    """Python wrapper for solana-swap trading using solanatracker.py."""

    def __init__(self):
        self.wallet_configured = bool(Config.SOLANA_WALLET_PRIVATE_KEY)
        if not self.wallet_configured:
            logger.warning("⚠️ SOLANA_WALLET_PRIVATE_KEY not configured - trading disabled")
            self.tracker = None
            self.keypair = None
            self.rpc_url = None
            return

        try:
            # Load wallet keypair from base58 private key
            self.keypair = Keypair.from_base58_string(Config.SOLANA_WALLET_PRIVATE_KEY)

            # Build RPC URL
            self.rpc_url = Config.SOLANA_RPC_URL

            # Initialize SolanaTracker (swap library)
            self.tracker = SolanaTracker(
                keypair=self.keypair,
                rpc_url=self.rpc_url,
                logging_level="INFO",
            )

            logger.info(f"✅ SolanaTrader initialized (wallet: {str(self.keypair.pubkey())[:8]}...)")
        except Exception as e:
            logger.error(f"❌ Failed to initialize SolanaTrader: {e}")
            self.tracker = None
            self.keypair = None
            self.rpc_url = None

    # ──────────────────────────────────────────────
    # SWAP OPTIONS (shared between buy/sell)
    # ──────────────────────────────────────────────
    @staticmethod
    def _default_swap_options() -> dict:
        """Default options for swap execution.
        
        Strategy: send → poll for confirmation → periodically resend the
        same signed tx to increase landing probability.  If the blockhash
        expires or we time out, the caller can retry with fresh instructions.
        
        Helius free-tier allows 10 RPC calls/s.  With 2s check intervals
        we stay well under that (status + occasional resend ≈ 1-2 RPC/s).
        """
        return {
            "send_options": {
                "skip_preflight": False,   # simulate first send to catch obvious errors
                "max_retries": 5,
            },
            "confirmation_retries": 30,           # 30 checks × 2s = 60s max
            "confirmation_retry_timeout": 1000,
            "last_valid_block_height_buffer": 150,
            "commitment": "confirmed",
            "resend_interval": 5000,              # resend the tx every 5s
            "confirmation_check_interval": 2000,  # check status every 2s
            "skip_confirmation_check": False,     # ACTUALLY CONFIRM the tx
        }

    # ──────────────────────────────────────────────
    # BUY TOKEN (SOL → Token)
    # ──────────────────────────────────────────────
    async def buy_token(self, token_address: str, amount_sol: float) -> Optional[str]:
        """
        Buy a token with SOL.  Retries with fresh swap instructions if the
        transaction expires or times out (up to MAX_SWAP_RETRIES attempts).

        Args:
            token_address: Token mint address
            amount_sol: Amount of SOL to spend (human-readable, e.g. 0.5 = 0.5 SOL)

        Returns:
            Transaction signature string, or None on failure
        """
        if not self.tracker or not self.keypair:
            logger.error("❌ Trader not initialized")
            return None

        if not _validate_solana_address(token_address):
            logger.error(f"❌ Invalid Solana address (bad base58): {token_address}")
            return None

        for attempt in range(MAX_SWAP_RETRIES):
            try:
                attempt_label = f"[attempt {attempt + 1}/{MAX_SWAP_RETRIES}] " if MAX_SWAP_RETRIES > 1 else ""
                logger.info(f"🛒 {attempt_label}Buying {amount_sol} SOL worth of {token_address[:8]}...")

                # Get swap instructions (SOL → Token) — always fresh for each attempt
                swap_response = await self.tracker.get_swap_instructions(
                    from_token=SOL_MINT,
                    to_token=token_address,
                    from_amount=amount_sol,
                    slippage=30,  # 30% slippage for volatile tokens
                    payer=str(self.keypair.pubkey()),
                    priority_fee=0.005,  # 0.005 SOL priority fee
                )

                logger.debug(f"Swap response keys: {list(swap_response.keys())}")

                # Execute the swap (send + confirm + resend loop)
                result = await self.tracker.perform_swap(
                    swap_response, options=self._default_swap_options()
                )

                # perform_swap returns either a txid string or an Exception
                if isinstance(result, Exception):
                    # Retryable errors: tx expired or timed out — get fresh instructions
                    if isinstance(result, (TransactionExpiredError, TransactionConfirmationTimeoutError)):
                        logger.warning(f"⚠️ {attempt_label}Transaction not confirmed: {result}")
                        if attempt < MAX_SWAP_RETRIES - 1:
                            logger.info("🔄 Retrying with fresh swap instructions...")
                            await asyncio.sleep(1)
                            continue
                    # Non-retryable: on-chain failure or other error
                    logger.error(f"❌ Swap failed: {result}")
                    return None

                logger.info(f"✅ Buy transaction confirmed: {result}")
                logger.info(f"   https://solscan.io/tx/{result}")
                return str(result)

            except Exception as e:
                logger.error(f"❌ {attempt_label}Failed to buy token: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                if attempt < MAX_SWAP_RETRIES - 1:
                    logger.info("🔄 Retrying with fresh swap instructions...")
                    await asyncio.sleep(1)
                    continue
                return None

        return None

    # ──────────────────────────────────────────────
    # SELL TOKEN (Token → SOL)
    # ──────────────────────────────────────────────
    async def sell_token(self, token_address: str, token_amount: float) -> Optional[str]:
        """
        Sell a token for SOL.  Retries with fresh swap instructions if the
        transaction expires or times out (up to MAX_SWAP_RETRIES attempts).

        Args:
            token_address: Token mint address
            token_amount: Amount of tokens to sell (human-readable, e.g. 1000.5 tokens)

        Returns:
            Transaction signature string, or None on failure
        """
        if not self.tracker or not self.keypair:
            logger.error("❌ Trader not initialized")
            return None

        if not _validate_solana_address(token_address):
            logger.error(f"❌ Invalid Solana address (bad base58): {token_address}")
            return None

        for attempt in range(MAX_SWAP_RETRIES):
            try:
                attempt_label = f"[attempt {attempt + 1}/{MAX_SWAP_RETRIES}] " if MAX_SWAP_RETRIES > 1 else ""
                logger.info(f"💰 {attempt_label}Selling {token_amount} tokens ({token_address[:8]}...)")

                # Get swap instructions (Token → SOL) — always fresh for each attempt
                swap_response = await self.tracker.get_swap_instructions(
                    from_token=token_address,
                    to_token=SOL_MINT,
                    from_amount=token_amount,
                    slippage=30,
                    payer=str(self.keypair.pubkey()),
                    priority_fee=0.005,
                )

                # Execute the swap (send + confirm + resend loop)
                result = await self.tracker.perform_swap(
                    swap_response, options=self._default_swap_options()
                )

                if isinstance(result, Exception):
                    if isinstance(result, (TransactionExpiredError, TransactionConfirmationTimeoutError)):
                        logger.warning(f"⚠️ {attempt_label}Transaction not confirmed: {result}")
                        if attempt < MAX_SWAP_RETRIES - 1:
                            logger.info("🔄 Retrying with fresh swap instructions...")
                            await asyncio.sleep(1)
                            continue
                    logger.error(f"❌ Sell swap failed: {result}")
                    return None

                logger.info(f"✅ Sell transaction confirmed: {result}")
                logger.info(f"   https://solscan.io/tx/{result}")
                return str(result)

            except Exception as e:
                logger.error(f"❌ {attempt_label}Failed to sell token: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                if attempt < MAX_SWAP_RETRIES - 1:
                    logger.info("🔄 Retrying with fresh swap instructions...")
                    await asyncio.sleep(1)
                    continue
                return None

        return None

    # ──────────────────────────────────────────────
    # GET TOKEN VALUE IN SOL (quote without executing)
    # ──────────────────────────────────────────────
    async def get_token_value_in_sol(
        self, token_address: str, token_amount: float
    ) -> Optional[float]:
        """
        Get the current SOL value of a token amount WITHOUT executing a swap.

        Calls get_swap_instructions(Token → SOL) to get a quote.
        The API returns rate info in the response.

        Args:
            token_address: Token mint address
            token_amount: Amount of tokens (human-readable, e.g. 1000.5 tokens)

        Returns:
            Value in SOL, or None on failure
        """
        if not self.tracker or not self.keypair:
            logger.error("❌ Trader not initialized")
            return None

        if not _validate_solana_address(token_address):
            logger.error(f"❌ Invalid Solana address (bad base58): {token_address}")
            return None

        try:
            # Call swap API for Token → SOL (we only read the response, never execute)
            # token_amount is human-readable — the API handles unit conversion
            swap_response = await self.tracker.get_swap_instructions(
                from_token=token_address,
                to_token=SOL_MINT,
                from_amount=token_amount,
                slippage=30,
                payer=str(self.keypair.pubkey()),
                priority_fee=0.0001,  # Minimal fee since we won't execute
            )

            # Log the full response so we can see the structure
            logger.debug(f"Quote response keys: {list(swap_response.keys())}")
            logger.debug(f"Full quote response: {swap_response}")

            # Extract the SOL amount we'd receive
            sol_value = self._extract_sol_value_from_response(swap_response)

            if sol_value is not None:
                logger.info(f"📊 {token_amount:.4f} tokens = {sol_value:.6f} SOL")
            else:
                logger.warning(f"⚠️ Could not extract SOL value from quote response")
                logger.warning(f"   Response keys: {list(swap_response.keys())}")
                # Log rate sub-object if present
                if "rate" in swap_response:
                    logger.warning(f"   Rate object: {swap_response['rate']}")

            return sol_value

        except Exception as e:
            logger.error(f"❌ Failed to get token value: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _extract_sol_value_from_response(self, response: Dict[str, Any]) -> Optional[float]:
        """
        Extract the output SOL amount from a swap instructions response.

        The response structure may vary, so we try multiple possible field locations.
        The output amount is typically in lamports (1 SOL = 1e9 lamports).
        """
        # Try common response structures

        # 1. Direct 'rate' field (most common for SolanaTracker API)
        if "rate" in response:
            rate = response["rate"]
            if isinstance(rate, dict):
                # Try known field names for output amount
                for field in ["amountOut", "outAmount", "outputAmount", "out"]:
                    if field in rate:
                        val = float(rate[field])
                        # If it looks like lamports (> 1000), convert to SOL
                        if val > 1000:
                            return val / 1e9
                        return val
                # Also check for "amount" fields with "Out" or "output" in the key
                for key, val in rate.items():
                    if "out" in key.lower() and isinstance(val, (int, float)):
                        val = float(val)
                        if val > 1000:
                            return val / 1e9
                        return val
            elif isinstance(rate, (int, float)):
                return float(rate)

        # 2. Direct amountOut field at top level
        for field in ["amountOut", "outAmount", "outputAmount"]:
            if field in response:
                val = float(response[field])
                if val > 1000:
                    return val / 1e9
                return val

        # 3. Nested in 'data' or 'quote'
        for wrapper in ["data", "quote"]:
            if wrapper in response and isinstance(response[wrapper], dict):
                inner = response[wrapper]
                for field in ["amountOut", "outAmount", "outputAmount"]:
                    if field in inner:
                        val = float(inner[field])
                        if val > 1000:
                            return val / 1e9
                        return val

        return None

    # ──────────────────────────────────────────────
    # GET TOKEN BALANCE (from wallet via RPC)
    # ──────────────────────────────────────────────
    async def get_token_balance(self, token_address: str) -> Tuple[float, int]:
        """
        Get the token balance in our wallet using Solana RPC.

        Uses getTokenAccountsByOwner to find all accounts for this mint,
        then reads the parsed balance.

        Args:
            token_address: Token mint address

        Returns:
            (balance_human_readable, decimals) tuple. Returns (0.0, 9) on failure.
        """
        if not self.keypair or not self.rpc_url:
            logger.error("❌ Wallet/RPC not configured")
            return (0.0, 9)

        try:
            # Use configured RPC (Helius) for balance queries — same network as tx sender
            rpc_for_balance = Config.SOLANA_RPC_URL or PUBLIC_RPC_URL
            async with AsyncClient(rpc_for_balance, commitment=Confirmed) as connection:
                wallet_pubkey = self.keypair.pubkey()
                mint_pubkey = Pubkey.from_string(token_address)

                # Use getParsedTokenAccountsByOwner for easy balance extraction
                opts = TokenAccountOpts(mint=mint_pubkey)
                resp = await connection.get_token_accounts_by_owner_json_parsed(
                    wallet_pubkey,
                    opts,
                )

                if not resp.value:
                    # No token account found — balance is 0
                    logger.debug(f"No token account for {token_address[:8]}... in wallet")
                    return (0.0, 9)

                # Parse the first (usually only) token account
                account = resp.value[0]
                parsed_data = account.account.data.parsed

                if isinstance(parsed_data, dict) and "info" in parsed_data:
                    info = parsed_data["info"]
                    token_amount = info.get("tokenAmount", {})
                    balance = float(token_amount.get("uiAmount", 0) or 0)
                    decimals = int(token_amount.get("decimals", 9))
                    logger.debug(f"Token balance: {balance} (decimals: {decimals})")
                    return (balance, decimals)

                logger.warning(f"⚠️ Unexpected parsed data format: {parsed_data}")
                return (0.0, 9)

        except Exception as e:
            logger.error(f"❌ Failed to get token balance: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return (0.0, 9)
