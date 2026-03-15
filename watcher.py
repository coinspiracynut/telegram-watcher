#!/usr/bin/env python3
"""
Telegram Channel Watcher

Streams new messages from configured private channels in real-time.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from typing import Dict, List, Optional, Set

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User, Message

from config import Config
from database import Database
from processor import MessageProcessor
from monitor import PositionMonitor

# Shared file with CLI for watched channels
WATCHED_CHANNELS_FILE = Path(__file__).parent / "watched_channels.json"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class TelegramWatcher:
    """
    Watches specified Telegram channels and streams incoming messages.
    """

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.db: Optional[Database] = None
        self.watched_channels: List[int] = []  # Will store resolved channel IDs
        self.channel_names: Dict[int, str] = {}  # ID -> name mapping
        self.processor: Optional[MessageProcessor] = None
        self.monitor: Optional[PositionMonitor] = None

    async def start(self):
        """Initialize and start the Telegram client."""
        if not Config.validate():
            raise ValueError("Invalid configuration. Check your .env file.")

        self.client = TelegramClient(
            Config.SESSION_NAME,
            Config.API_ID,
            Config.API_HASH,
        )

        logger.info("🚀 Starting Telegram Watcher...")
        await self.client.start(phone=Config.PHONE)

        # Initialize database
        self.db = Database()
        await self.db.connect()
        logger.info("💾 Database connected")

        # Verify we're logged in
        me = await self.client.get_me()
        logger.info(f"✅ Logged in as: {me.first_name} (@{me.username})")

        # Resolve and validate channels
        await self._resolve_channels()

        # Register message handler
        self._register_handlers()

        # Initialize processor with shared client and database
        self.processor = MessageProcessor()
        self.processor.client = self.client  # Share the Telethon client
        self.processor.db = self.db  # Share the database connection
        await self.processor.start_processing_only()  # Start without initializing client/db

        # Initialize position monitor with shared database
        self.monitor = PositionMonitor()
        await self.monitor.start_shared(self.db)

        logger.info("👀 Watching for new messages... (Press Ctrl+C to stop)")

    def _load_watched_ids_from_file(self) -> Set[int]:
        """Load watched channel IDs from JSON file (shared with CLI)."""
        if WATCHED_CHANNELS_FILE.exists():
            try:
                data = json.loads(WATCHED_CHANNELS_FILE.read_text())
                return set(data.get("channel_ids", []))
            except (json.JSONDecodeError, KeyError):
                pass
        return set()

    async def _resolve_channels(self):
        """Resolve channel identifiers to actual channel entities."""
        # Load from both .env and JSON file
        channels_config = Config.get_watched_channels()
        watched_ids_from_file = self._load_watched_ids_from_file()
        
        # Combine sources
        all_identifiers = set(channels_config)
        for cid in watched_ids_from_file:
            all_identifiers.add(str(cid))

        if not all_identifiers:
            logger.warning("⚠️  No channels configured. Run 'python cli.py' to add channels")
            return

        logger.info(f"📋 Resolving {len(all_identifiers)} configured channels...")

        for channel_identifier in all_identifiers:
            try:
                # Handle numeric IDs
                if channel_identifier.lstrip("-").isdigit():
                    channel_id = int(channel_identifier)
                    entity = await self.client.get_entity(channel_id)
                else:
                    # Handle usernames or invite links
                    entity = await self.client.get_entity(channel_identifier)

                if isinstance(entity, (Channel, Chat)):
                    self.watched_channels.append(entity.id)
                    self.channel_names[entity.id] = getattr(entity, "title", str(entity.id))
                    logger.info(f"  ✓ {self.channel_names[entity.id]} (ID: {entity.id})")
                else:
                    logger.warning(f"  ✗ '{channel_identifier}' is not a channel/group")

            except Exception as e:
                logger.error(f"  ✗ Failed to resolve '{channel_identifier}': {e}")

        logger.info(f"📡 Monitoring {len(self.watched_channels)} channels")

    def _register_handlers(self):
        """Register event handlers for new messages."""

        @self.client.on(events.NewMessage())
        async def handle_new_message(event: events.NewMessage.Event):
            """Process incoming messages from watched channels."""
            message: Message = event.message

            # Get chat info
            chat = await event.get_chat()
            chat_id = event.chat_id
            
            # Normalize chat_id for comparison (handle -100 prefix for channels)
            # event.chat_id can be -1003043451883 while entity.id is 3043451883
            normalized_chat_id = abs(chat_id)
            if normalized_chat_id > 1000000000000:  # Has -100 prefix
                normalized_chat_id = normalized_chat_id - 1000000000000

            # Filter to only watched channels (if any are configured)
            if self.watched_channels and normalized_chat_id not in self.watched_channels:
                return

            # Extract message details
            chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown")
            sender = await event.get_sender()
            sender_name = self._get_sender_name(sender)

            # Format timestamp
            timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")

            # Extract reply information
            is_reply = message.reply_to is not None
            reply_to_message_id = None
            reply_to_text = None
            reply_to_sender = None
            thread_id = None  # Thread/topic ID if message is in a thread
            
            if is_reply and message.reply_to:
                reply_to_message_id = message.reply_to.reply_to_msg_id
                # Check if message is in a thread (reply_to_top_id indicates thread/topic)
                # This is the ID of the message that started the thread
                thread_id = getattr(message.reply_to, 'reply_to_top_id', None)
                # Try to fetch the original message for context
                try:
                    reply_msg = await self.client.get_messages(chat, ids=reply_to_message_id)
                    if reply_msg:
                        reply_to_text = reply_msg.text
                        reply_sender = await reply_msg.get_sender()
                        reply_to_sender = self._get_sender_name(reply_sender)
                except Exception as e:
                    logger.debug(f"Could not fetch reply message: {e}")
            
            # Also check if message itself has a thread_id (for messages not replying but in a thread)
            if not thread_id:
                thread_id = getattr(message, 'reply_to', None) and getattr(message.reply_to, 'reply_to_top_id', None)

            # Log the message
            reply_indicator = " ↩️" if is_reply else ""
            logger.info(f"📨{reply_indicator} [{chat_title}] {sender_name}: {self._truncate(message.text or '[media]', 100)}")

            # Process the message (extend this for AI processing)
            await self.process_message(
                chat_id=chat_id,
                chat_title=chat_title,
                sender_name=sender_name,
                message_text=message.text,
                message_id=message.id,
                timestamp=timestamp,
                raw_message=message,
                is_reply=is_reply,
                reply_to_message_id=reply_to_message_id,
                reply_to_text=reply_to_text,
                reply_to_sender=reply_to_sender,
                thread_id=thread_id,
            )

    def _get_sender_name(self, sender) -> str:
        """Extract a readable name from a sender entity."""
        if sender is None:
            return "Unknown"
        if isinstance(sender, User):
            name = sender.first_name or ""
            if sender.last_name:
                name += f" {sender.last_name}"
            if sender.username:
                name += f" (@{sender.username})"
            return name.strip() or "Unknown User"
        if isinstance(sender, Channel):
            return sender.title or "Channel"
        return str(sender.id)

    @staticmethod
    def _truncate(text: str, max_length: int) -> str:
        """Truncate text with ellipsis if too long."""
        if not text:
            return ""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    async def process_message(
        self,
        chat_id: int,
        chat_title: str,
        sender_name: str,
        message_text: Optional[str],
        message_id: int,
        timestamp: str,
        raw_message: Message,
        is_reply: bool = False,
        reply_to_message_id: Optional[int] = None,
        reply_to_text: Optional[str] = None,
        reply_to_sender: Optional[str] = None,
        thread_id: Optional[int] = None,
    ):
        """
        Save incoming message to database for later AI processing.
        
        Args:
            chat_id: Telegram chat ID
            chat_title: Name of the channel/group
            sender_name: Formatted sender name
            message_text: Text content of the message (None for media-only)
            message_id: Telegram message ID
            timestamp: Formatted timestamp string
            raw_message: The raw Telethon Message object for advanced processing
            is_reply: Whether this message is a reply to another
            reply_to_message_id: The message ID being replied to
            reply_to_text: Text of the original message
            reply_to_sender: Who sent the original message
        """
        # Serialize raw message to JSON for later processing
        raw_json = raw_message.to_json() if raw_message else None
        
        # Save to database
        row_id = await self.db.save_message(
            chat_id=chat_id,
            chat_title=chat_title,
            message_id=message_id,
            sender_name=sender_name,
            message_text=message_text,
            timestamp=timestamp,
            raw_json=raw_json,
            is_reply=is_reply,
            reply_to_message_id=reply_to_message_id,
            reply_to_text=reply_to_text,
            reply_to_sender=reply_to_sender,
            thread_id=thread_id,
        )
        
        if row_id:
            logger.debug(f"💾 Saved message {message_id} to database (row {row_id})")

    async def run_forever(self):
        """Run the watcher, processor, and monitor until interrupted."""
        await self.start()
        
        # Run processor in background
        processor_task = None
        if self.processor:
            processor_task = asyncio.create_task(self.processor.run_forever())
        
        # Run position monitor in background (checks active positions every 15s)
        monitor_task = None
        if self.monitor:
            monitor_task = asyncio.create_task(self.monitor.run_forever(min_cycle_pause=5.0))
        
        try:
            await self.client.run_until_disconnected()
        finally:
            # Stop monitor when watcher stops
            if monitor_task:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
            # Stop processor when watcher stops
            if processor_task:
                processor_task.cancel()
                try:
                    await processor_task
                except asyncio.CancelledError:
                    pass
                await self.processor.stop()

    async def stop(self):
        """Gracefully stop the watcher."""
        logger.info("🛑 Stopping Telegram Watcher...")
        if self.db:
            await self.db.close()
        if self.client:
            await self.client.disconnect()


async def main():
    """Main entry point."""
    watcher = TelegramWatcher()

    try:
        await watcher.run_forever()
    except KeyboardInterrupt:
        logger.info("⏹️  Interrupted by user")
    finally:
        await watcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
