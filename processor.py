#!/usr/bin/env python3
"""
AI Message Processor

Runs as a separate process/thread to process unprocessed messages from the database.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from database import Database, StoredMessage
from token_extractor import extract_dexscreener_urls, parse_token_name_and_ticker
from notifier import TelegramNotifier
from trader import SolanaTrader
from ai_tagger import AITagger
from config import Config
from telethon import TelegramClient
from telethon.tl.types import Message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class MessageProcessor:
    """
    Processes messages from the database in a separate thread/process.
    
    This is designed to run independently from the watcher, picking up
    unprocessed messages and running them through AI analysis.
    """

    def __init__(self, poll_interval: float = 2.0, batch_size: int = 10):
        """
        Args:
            poll_interval: Seconds between database polls for new messages
            batch_size: Number of messages to fetch per poll
        """
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.db: Optional[Database] = None
        self.notifier = TelegramNotifier()
        self.trader = SolanaTrader()
        self.tagger: Optional[AITagger] = None
        self.client: Optional[TelegramClient] = None
        self._running = False

    async def start(self):
        """Initialize the processor (standalone mode)."""
        self.db = Database()
        await self.db.connect()
        self._db_owned = True
        self.tagger = AITagger(self.db)
        
        # Initialize Telethon client for forwarding messages (user account)
        # Use a separate session file to avoid database locking conflicts
        if Config.NOTIFICATION_CHAT_ID:
            try:
                # Use separate session to avoid SQLite locking with watcher
                self.client = TelegramClient(
                    Config.SESSION_NAME + "_forwarder",
                    Config.API_ID,
                    Config.API_HASH,
                )
                await self.client.start()
                self._client_owned = True
                logger.info("✅ Telethon client connected for message forwarding")
            except Exception as e:
                logger.warning(f"⚠️ Could not initialize Telethon client: {e}")
                logger.warning("   Message forwarding will be disabled")
                self.client = None
        else:
            self._client_owned = False
        
        logger.info("🤖 AI Processor started")
        
        # Show initial stats
        total = await self.db.get_message_count()
        unprocessed = await self.db.get_message_count(processed=False)
        logger.info(f"📊 Database: {total} total messages, {unprocessed} unprocessed")
    
    async def start_processing_only(self):
        """Initialize processor when client and db are already provided (shared mode)."""
        self._client_owned = False  # Don't disconnect shared client
        self._db_owned = False  # Don't close shared db
        self.tagger = AITagger(self.db)
        logger.info("🤖 AI Processor started (shared mode)")
        
        # Show initial stats
        if self.db:
            total = await self.db.get_message_count()
            unprocessed = await self.db.get_message_count(processed=False)
            logger.info(f"📊 Database: {total} total messages, {unprocessed} unprocessed")

    async def stop(self):
        """Stop the processor."""
        self._running = False
        # Only disconnect client if we created it (not shared)
        if self.client and hasattr(self, '_client_owned') and self._client_owned:
            await self.client.disconnect()
        # Only close db if we created it (not shared)
        if self.db and hasattr(self, '_db_owned') and self._db_owned:
            await self.db.close()
        logger.info("🛑 AI Processor stopped")

    # Names/usernames to monitor for special processing
    MONITORED_USERS = ["rick", "rickburpbot"]
    
    def is_from_monitored_user(self, message: StoredMessage) -> bool:
        """Check if message is from a monitored user."""
        sender = message.sender_name.lower() if message.sender_name else ""
        for name in self.MONITORED_USERS:
            # Check for exact name match or @username match
            # sender_name can be "Rick" or "Rick (@rick)" or just the name
            if name.lower() == sender or f"@{name.lower()}" in sender or sender.startswith(name.lower()):
                return True
        return False

    async def process_message(self, message: StoredMessage) -> bool:
        """
        Process a single message with AI.
        
        Args:
            message: The stored message to process
            
        Returns:
            True if processing succeeded, False otherwise
        """
        # Check if from monitored users (@rick or @rickburpbot)
        is_from_rick = self.is_from_monitored_user(message)
        
        if is_from_rick:
            logger.info(f"🎯 RICK MESSAGE: [{message.chat_title}] {message.sender_name}")
            if message.message_text:
                logger.info(f"   📝 {message.message_text[:200]}")
            
            # Check if it's a reply
            if message.is_reply:
                logger.info(f"   ↩️ Replying to: {message.reply_to_sender}")
                if message.reply_to_text:
                    logger.info(f"   💬 Original: {message.reply_to_text[:100]}...")
            
            # Extract tokens from DexScreener URLs
            dexscreener_urls = extract_dexscreener_urls(message.raw_json)
            
            logger.info(f"   🔍 Found {len(dexscreener_urls)} DexScreener URL(s)")
            
            if dexscreener_urls:
                # Get raw message text for parsing
                raw_text = ""
                if message.raw_json:
                    try:
                        data = json.loads(message.raw_json)
                        raw_text = data.get("message", "")
                    except json.JSONDecodeError:
                        pass
                
                # Parse token name and ticker from first line
                token_name, token_ticker = parse_token_name_and_ticker(raw_text)
                
                # Process each token
                for network, address in dexscreener_urls:
                    logger.info(f"   💎 Token: {network}/{address}")
                    if token_name:
                        logger.info(f"      Name: {token_name}")
                    if token_ticker:
                        logger.info(f"      Ticker: ${token_ticker}")
                    
                    # Save or get token
                    token_id, is_new = await self.db.save_or_get_token(
                        network=network,
                        address=address,
                        token_name=token_name,
                        token_ticker=token_ticker,
                    )
                    
                    # Send notification if this is a new token
                    if is_new:
                        dexscreener_url = f"https://dexscreener.com/{network}/{address}"
                        # Get the name of who called Rick bot (from replied-to message)
                        called_by = message.reply_to_sender if message.is_reply else None
                        
                        # Build link to original message
                        # Telegram message link format:
                        # - Regular: https://t.me/c/{chat_id}/{message_id}
                        # - Thread: https://t.me/c/{chat_id}/{thread_id}/{message_id}
                        chat_id_for_link = abs(message.chat_id)
                        if chat_id_for_link > 1000000000000:
                            chat_id_for_link = chat_id_for_link - 1000000000000
                        
                        # Build message link (try to build it, but continue even if it fails)
                        message_link = None
                        try:
                            if message.thread_id:
                                message_link = f"https://t.me/c/{chat_id_for_link}/{message.thread_id}/{message.message_id}"
                            else:
                                message_link = f"https://t.me/c/{chat_id_for_link}/{message.message_id}"
                        except Exception as e:
                            logger.warning(f"      ⚠️ Failed to build message link: {e}")
                        
                        # Send Rick message text to notification chat via user account
                        # This triggers Rick bot in that chat, then we follow up with /lore
                        if self.client and Config.NOTIFICATION_CHAT_ID:
                            try:
                                dest_chat = await self.client.get_entity(int(Config.NOTIFICATION_CHAT_ID))
                                
                                # Build link to the original message (the one Rick replied to)
                                original_link = None
                                if message.reply_to_message_id:
                                    try:
                                        if message.thread_id:
                                            original_link = f"https://t.me/c/{chat_id_for_link}/{message.thread_id}/{message.reply_to_message_id}"
                                        else:
                                            original_link = f"https://t.me/c/{chat_id_for_link}/{message.reply_to_message_id}"
                                    except Exception:
                                        pass
                                
                                # Build the message:
                                # [original message](link) from username
                                # {message text}
                                header = ""
                                link_text = "[original message]"
                                if original_link:
                                    link_text = f"[original message]({original_link})"
                                caller_name = called_by or "unknown"
                                header = f"{link_text} from {caller_name}\n\n"
                                
                                original_text = message.reply_to_text or ""
                                outgoing_text = f"{header}{original_text}"
                                
                                logger.info(f"      📤 Sending Rick message to notification chat...")
                                await self.client.send_message(dest_chat, outgoing_text, parse_mode="md")
                                logger.info(f"      ✅ Rick message sent")
                                
                                # Wait 1 second for Rick to respond, then send /lore
                                await asyncio.sleep(1)
                                await self.client.send_message(dest_chat, f"/lore {address}")
                                logger.info(f"      ✅ Sent /lore {address}")
                            except Exception as e:
                                logger.warning(f"      ⚠️ Failed to send to notification chat: {e}")
                                # Continue anyway - bot notification still sends below
                        
                        # Mark notification as sent (notification chat handles it now)
                        await self.db.mark_notification_sent(token_id)
                        logger.info(f"      📢 Token notification complete")
                        
                        # Auto-buy if Solana token
                        if network.lower() == "solana":
                            if Config.AUTO_BUY_AMOUNT_SOL > 0:
                                logger.info(f"      💰 Auto-buying {Config.AUTO_BUY_AMOUNT_SOL} SOL worth...")
                                
                                # Check if we already have a position in this token
                                existing_positions = await self.db.get_active_positions()
                                has_existing = any(p["address"] == address for p in existing_positions)
                                
                                if has_existing:
                                    logger.info(f"      ⚠️ Already have position in this token, skipping buy")
                                else:
                                    try:
                                        # Get balance before buy (to calculate what we actually received)
                                        logger.debug(f"      Getting balance before buy...")
                                        balance_before, decimals = await self.trader.get_token_balance(address)
                                        logger.debug(f"      Balance before: {balance_before}")
                                        
                                        logger.info(f"      Executing buy transaction...")
                                        buy_tx = await self.trader.buy_token(address, Config.AUTO_BUY_AMOUNT_SOL)
                                        
                                        if buy_tx:
                                            logger.info(f"      ✅ Buy transaction sent: {buy_tx}")
                                            
                                            # Poll for balance change (RPC may lag behind tx confirmation)
                                            token_amount = 0
                                            balance_after = balance_before
                                            for attempt in range(6):
                                                wait_secs = 3 + attempt * 2  # 3, 5, 7, 9, 11, 13
                                                logger.info(f"      ⏳ Waiting {wait_secs}s for balance update (attempt {attempt+1}/6)...")
                                                await asyncio.sleep(wait_secs)
                                                balance_after, decimals = await self.trader.get_token_balance(address)
                                                token_amount = balance_after - balance_before
                                                if token_amount > 0:
                                                    break
                                            
                                            if token_amount > 0:
                                                buy_price = Config.AUTO_BUY_AMOUNT_SOL / token_amount
                                                await self.db.create_position(
                                                    token_id=token_id,
                                                    buy_amount_sol=Config.AUTO_BUY_AMOUNT_SOL,
                                                    buy_price=buy_price,
                                                    token_amount=token_amount,
                                                    buy_tx_signature=buy_tx,
                                                )
                                                logger.info(f"      ✅ Position created: {token_amount:.4f} tokens @ {buy_price:.8f} SOL/token")
                                            else:
                                                # Still no balance — create position anyway so monitor can pick it up later
                                                logger.warning(f"      ⚠️ Balance not yet visible (before: {balance_before}, after: {balance_after})")
                                                logger.info(f"      📝 Creating position with estimated values — monitor will sync actual balance")
                                                await self.db.create_position(
                                                    token_id=token_id,
                                                    buy_amount_sol=Config.AUTO_BUY_AMOUNT_SOL,
                                                    buy_price=0,
                                                    token_amount=0,
                                                    buy_tx_signature=buy_tx,
                                                )
                                                logger.info(f"      ✅ Position created (pending balance sync)")
                                        else:
                                            logger.error(f"      ❌ Auto-buy failed - no transaction signature returned")
                                    except Exception as e:
                                        logger.error(f"      ❌ Error during auto-buy: {e}")
                                        import traceback
                                        logger.debug(traceback.format_exc())
                            else:
                                logger.debug(f"      Auto-buy disabled (AUTO_BUY_AMOUNT_SOL = 0)")
                    
                    # Link token to the message Rick replied to
                    if message.reply_to_message_id:
                        # Find the database ID of the replied-to message
                        # Try both normalized and original chat_id
                        replied_to_db_id = await self.db.find_message_db_id(
                            message.chat_id,
                            message.reply_to_message_id
                        )
                        
                        # If not found, try normalized chat_id (handle -100 prefix)
                        if not replied_to_db_id:
                            normalized_chat_id = abs(message.chat_id)
                            if normalized_chat_id > 1000000000000:
                                normalized_chat_id = normalized_chat_id - 1000000000000
                            replied_to_db_id = await self.db.find_message_db_id(
                                normalized_chat_id,
                                message.reply_to_message_id
                            )
                        
                        if replied_to_db_id:
                            await self.db.link_token_to_message(replied_to_db_id, token_id)
                            logger.info(f"      ✅ Linked token to replied message (DB ID: {replied_to_db_id})")
                        else:
                            logger.warning(f"      ⚠️ Could not find replied-to message in database (chat_id: {message.chat_id}, msg_id: {message.reply_to_message_id})")
                    else:
                        logger.warning(f"      ⚠️ Message is not a reply, cannot link token")
            
            # TODO: Add your AI processing for Rick's messages here
            # Example: analyze sentiment, extract info, send alerts, etc.
            
        else:
            # Non-Rick message - just log briefly
            if message.message_text:
                preview = message.message_text[:60] + "..." if len(message.message_text) > 60 else message.message_text
                logger.debug(f"⏭️ Non-Rick message: [{message.chat_title}] {preview}")
        
        # ── AI Token Tagging (runs for every message) ─────────────────────
        if self.tagger and message.message_text:
            try:
                token_id = await self.tagger.tag_message(message)
                if token_id:
                    await self.db.tag_message_with_token(message.id, token_id)
            except Exception as e:
                logger.warning(f"⚠️ AI tagging failed for message {message.id}: {e}")

        return True

    async def process_batch(self) -> int:
        """
        Fetch and process a batch of unprocessed messages.
        
        Returns:
            Number of messages processed
        """
        messages = await self.db.get_unprocessed_messages(limit=self.batch_size)
        
        if not messages:
            return 0

        processed_ids = []
        
        for message in messages:
            try:
                success = await self.process_message(message)
                if success:
                    processed_ids.append(message.id)
            except Exception as e:
                logger.error(f"❌ Error processing message {message.id}: {e}")
                # Still mark as processed to avoid infinite loop on bad messages
                # You might want different behavior depending on your use case
                processed_ids.append(message.id)

        # Mark batch as processed
        if processed_ids:
            await self.db.mark_batch_processed(processed_ids)

        return len(processed_ids)

    async def run_forever(self):
        """
        Main processing loop.
        
        Continuously polls for unprocessed messages and processes them.
        """
        # Only start if not already initialized (shared mode)
        if not self.db:
            await self.start()
        self._running = True

        logger.info(f"⏳ Polling every {self.poll_interval}s for new messages...")

        try:
            while self._running:
                try:
                    processed_count = await self.process_batch()
                    
                    if processed_count > 0:
                        logger.info(f"✅ Processed {processed_count} messages")
                    
                    # If we got a full batch, immediately check for more
                    if processed_count >= self.batch_size:
                        continue
                        
                    # Otherwise, wait before polling again
                    await asyncio.sleep(self.poll_interval)
                    
                except Exception as e:
                    logger.error(f"❌ Error in processing loop: {e}")
                    await asyncio.sleep(self.poll_interval)
                    
        except asyncio.CancelledError:
            logger.info("⏹️  Processing cancelled")
        finally:
            await self.stop()


async def main():
    """Main entry point."""
    processor = MessageProcessor(
        poll_interval=2.0,  # Check for new messages every 2 seconds
        batch_size=10,      # Process 10 messages at a time
    )

    try:
        await processor.run_forever()
    except KeyboardInterrupt:
        logger.info("⏹️  Interrupted by user")
        await processor.stop()


if __name__ == "__main__":
    asyncio.run(main())
