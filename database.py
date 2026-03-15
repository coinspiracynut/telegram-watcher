"""
Database layer for storing Telegram messages.
"""
import aiosqlite
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

DATABASE_PATH = Path(__file__).parent / "messages.db"


@dataclass
class StoredMessage:
    """Represents a message stored in the database."""
    id: int
    chat_id: int
    chat_title: str
    message_id: int
    sender_name: str
    message_text: Optional[str]
    timestamp: str
    raw_json: Optional[str]
    processed: bool
    created_at: str
    # Reply fields
    is_reply: bool = False
    reply_to_message_id: Optional[int] = None
    reply_to_text: Optional[str] = None
    reply_to_sender: Optional[str] = None
    # Thread/topic ID (if message is in a thread)
    thread_id: Optional[int] = None
    # Computed topic ID: the forum topic this message belongs to
    # = thread_id if set, else reply_to_message_id (the topic root)
    topic_id: Optional[int] = None
    # AI-tagged token association
    tagged_token_id: Optional[int] = None


@dataclass
class Token:
    """Represents a token extracted from a message."""
    id: int
    network: str
    address: str
    token_name: str
    token_ticker: str
    created_at: str


class Database:
    """Async SQLite database for message storage."""

    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Initialize database connection and create tables."""
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self):
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _create_tables(self):
        """Create tables if they don't exist."""
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                sender_name TEXT,
                message_text TEXT,
                timestamp TEXT NOT NULL,
                raw_json TEXT,
                processed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_reply INTEGER DEFAULT 0,
                reply_to_message_id INTEGER,
                reply_to_text TEXT,
                reply_to_sender TEXT,
                thread_id INTEGER,
                
                UNIQUE(chat_id, message_id)
            )
        """)
        
        # Migration: Add reply columns if they don't exist (for existing databases)
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN is_reply INTEGER DEFAULT 0")
        except:
            pass  # Column already exists
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN reply_to_message_id INTEGER")
        except:
            pass
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN reply_to_text TEXT")
        except:
            pass
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN reply_to_sender TEXT")
        except:
            pass
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN thread_id INTEGER")
        except:
            pass
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN tagged_token_id INTEGER")
        except:
            pass
        try:
            await self._connection.execute("ALTER TABLE messages ADD COLUMN topic_id INTEGER")
        except:
            pass
        
        # Backfill topic_id for existing rows that don't have it yet
        await self._connection.execute("""
            UPDATE messages 
            SET topic_id = COALESCE(thread_id, reply_to_message_id) 
            WHERE topic_id IS NULL AND (thread_id IS NOT NULL OR reply_to_message_id IS NOT NULL)
        """)
        await self._connection.commit()
        
        # Index for faster unprocessed message queries
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_processed 
            ON messages(processed) WHERE processed = 0
        """)
        
        # Index for chat lookups
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_chat_id 
            ON messages(chat_id)
        """)
        
        # Index for topic-scoped lookups (AI context window)
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_chat_topic 
            ON messages(chat_id, topic_id)
        """)
        
        # Tokens table
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                token_name TEXT,
                token_ticker TEXT,
                pool_id INTEGER,
                notification_sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                
                UNIQUE(network, address)
            )
        """)
        
        # Migration: Add notification_sent column if it doesn't exist
        try:
            await self._connection.execute("ALTER TABLE tokens ADD COLUMN notification_sent INTEGER DEFAULT 0")
        except:
            pass  # Column already exists
        
        # Migration: Add pool_id column if it doesn't exist
        try:
            await self._connection.execute("ALTER TABLE tokens ADD COLUMN pool_id INTEGER")
        except:
            pass  # Column already exists
        
        # Message-Token linking table (links tokens to the message Rick replied to)
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS message_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                token_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (message_id) REFERENCES messages(id),
                FOREIGN KEY (token_id) REFERENCES tokens(id),
                UNIQUE(message_id, token_id)
            )
        """)
        
        # Index for token lookups
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_tokens_network_address 
            ON tokens(network, address)
        """)
        
        # Positions table for tracking active token holdings
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                buy_amount_sol REAL NOT NULL,
                buy_price REAL,
                token_amount REAL NOT NULL,
                buy_tx_signature TEXT,
                current_value_sol REAL,
                profit_percent REAL,
                status TEXT DEFAULT 'active',
                sell_tx_signature TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (token_id) REFERENCES tokens(id)
            )
        """)
        
        # Index for active positions
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_status 
            ON positions(status) WHERE status = 'active'
        """)
        
        await self._connection.commit()

    async def save_message(
        self,
        chat_id: int,
        chat_title: str,
        message_id: int,
        sender_name: str,
        message_text: Optional[str],
        timestamp: str,
        raw_json: Optional[str] = None,
        is_reply: bool = False,
        reply_to_message_id: Optional[int] = None,
        reply_to_text: Optional[str] = None,
        reply_to_sender: Optional[str] = None,
        thread_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Save a message to the database.
        
        Returns the row ID if inserted, None if duplicate.
        """
        # Compute topic_id: which forum topic this message belongs to
        # thread_id is set when replying to a specific message within a topic
        # reply_to_message_id is the topic root when posting at the top level
        topic_id = thread_id if thread_id else reply_to_message_id
        
        try:
            cursor = await self._connection.execute(
                """
                INSERT INTO messages (
                    chat_id, chat_title, message_id, sender_name, message_text, 
                    timestamp, raw_json, is_reply, reply_to_message_id, 
                    reply_to_text, reply_to_sender, thread_id, topic_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (chat_id, chat_title, message_id, sender_name, message_text, 
                 timestamp, raw_json, 1 if is_reply else 0, reply_to_message_id,
                 reply_to_text, reply_to_sender, thread_id, topic_id),
            )
            await self._connection.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            # Duplicate message (chat_id + message_id already exists)
            return None

    async def get_unprocessed_messages(self, limit: int = 100, sender_filter: Optional[str] = None) -> List[StoredMessage]:
        """
        Fetch unprocessed messages, oldest first.
        
        Args:
            limit: Maximum number of messages to fetch
            sender_filter: Optional sender name filter (case-insensitive partial match)
        """
        if sender_filter:
            cursor = await self._connection.execute(
                """
                SELECT * FROM messages 
                WHERE processed = 0 
                AND LOWER(sender_name) LIKE ?
                ORDER BY id ASC 
                LIMIT ?
                """,
                (f"%{sender_filter.lower()}%", limit),
            )
        else:
            cursor = await self._connection.execute(
                """
                SELECT * FROM messages 
                WHERE processed = 0 
                ORDER BY id ASC 
                LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def mark_processed(self, message_id: int):
        """Mark a message as processed by its database ID."""
        await self._connection.execute(
            "UPDATE messages SET processed = 1 WHERE id = ?",
            (message_id,),
        )
        await self._connection.commit()

    async def mark_batch_processed(self, message_ids: List[int]):
        """Mark multiple messages as processed."""
        if not message_ids:
            return
        placeholders = ",".join("?" * len(message_ids))
        await self._connection.execute(
            f"UPDATE messages SET processed = 1 WHERE id IN ({placeholders})",
            message_ids,
        )
        await self._connection.commit()

    async def get_message_count(self, processed: Optional[bool] = None) -> int:
        """Get count of messages, optionally filtered by processed status."""
        if processed is None:
            cursor = await self._connection.execute("SELECT COUNT(*) FROM messages")
        else:
            cursor = await self._connection.execute(
                "SELECT COUNT(*) FROM messages WHERE processed = ?",
                (1 if processed else 0,),
            )
        row = await cursor.fetchone()
        return row[0]

    async def get_recent_messages(self, limit: int = 50) -> List[StoredMessage]:
        """Get most recent messages."""
        cursor = await self._connection.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def save_or_get_token(
        self,
        network: str,
        address: str,
        token_name: Optional[str] = None,
        token_ticker: Optional[str] = None,
        pool_id: Optional[int] = None,
    ) -> tuple[int, bool]:
        """
        Save a token or get existing one by network/address.
        Returns (token_id, is_new) tuple.
        """
        # Check if exists
        cursor = await self._connection.execute(
            "SELECT id, pool_id FROM tokens WHERE network = ? AND address = ?",
            (network, address),
        )
        row = await cursor.fetchone()
        
        if row:
            token_id = row["id"]
            existing_pool_id = row["pool_id"]
            # Update name/ticker/pool_id if provided and different
            if token_name or token_ticker or (pool_id is not None and existing_pool_id != pool_id):
                await self._connection.execute(
                    """
                    UPDATE tokens 
                    SET token_name = COALESCE(?, token_name),
                        token_ticker = COALESCE(?, token_ticker),
                        pool_id = COALESCE(?, pool_id)
                    WHERE id = ?
                    """,
                    (token_name, token_ticker, pool_id, token_id),
                )
                await self._connection.commit()
            return (token_id, False)
        
        # Create new
        cursor = await self._connection.execute(
            """
            INSERT INTO tokens (network, address, token_name, token_ticker, pool_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (network, address, token_name, token_ticker, pool_id),
        )
        await self._connection.commit()
        return (cursor.lastrowid, True)
    
    async def mark_notification_sent(self, token_id: int):
        """Mark that a notification has been sent for this token."""
        await self._connection.execute(
            "UPDATE tokens SET notification_sent = 1 WHERE id = ?",
            (token_id,),
        )
        await self._connection.commit()

    async def link_token_to_message(self, message_id: int, token_id: int):
        """
        Link a token to a message (the message Rick replied to).
        """
        try:
            await self._connection.execute(
                """
                INSERT INTO message_tokens (message_id, token_id)
                VALUES (?, ?)
                """,
                (message_id, token_id),
            )
            await self._connection.commit()
        except aiosqlite.IntegrityError:
            # Already linked
            pass

    async def get_tokens_for_message(self, message_id: int) -> List[Token]:
        """Get all tokens linked to a message."""
        cursor = await self._connection.execute(
            """
            SELECT t.* FROM tokens t
            INNER JOIN message_tokens mt ON t.id = mt.token_id
            WHERE mt.message_id = ?
            """,
            (message_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_token(row) for row in rows]

    async def find_message_db_id(self, chat_id: int, telegram_message_id: int) -> Optional[int]:
        """Find database ID of a message by chat_id and telegram message_id."""
        cursor = await self._connection.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, telegram_message_id),
        )
        row = await cursor.fetchone()
        return row["id"] if row else None
    
    async def get_pool_id_for_token(self, network: str, address: str) -> Optional[int]:
        """Get pool_id for a token by network and address."""
        cursor = await self._connection.execute(
            "SELECT pool_id FROM tokens WHERE network = ? AND address = ?",
            (network, address),
        )
        row = await cursor.fetchone()
        return row["pool_id"] if row and row["pool_id"] else None

    @staticmethod
    def _row_to_message(row: aiosqlite.Row) -> StoredMessage:
        """Convert a database row to a StoredMessage object."""
        return StoredMessage(
            id=row["id"],
            chat_id=row["chat_id"],
            chat_title=row["chat_title"],
            message_id=row["message_id"],
            sender_name=row["sender_name"],
            message_text=row["message_text"],
            timestamp=row["timestamp"],
            raw_json=row["raw_json"],
            processed=bool(row["processed"]),
            created_at=row["created_at"],
            is_reply=bool(row["is_reply"]) if row["is_reply"] is not None else False,
            reply_to_message_id=row["reply_to_message_id"],
            reply_to_text=row["reply_to_text"],
            reply_to_sender=row["reply_to_sender"],
            thread_id=row["thread_id"],
            topic_id=row["topic_id"] if "topic_id" in row.keys() else None,
            tagged_token_id=row["tagged_token_id"] if "tagged_token_id" in row.keys() else None,
        )

    async def create_position(
        self,
        token_id: int,
        buy_amount_sol: float,
        buy_price: float,
        token_amount: float,
        buy_tx_signature: Optional[str] = None,
    ) -> int:
        """Create a new position."""
        cursor = await self._connection.execute(
            """
            INSERT INTO positions (token_id, buy_amount_sol, buy_price, token_amount, buy_tx_signature)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_id, buy_amount_sol, buy_price, token_amount, buy_tx_signature),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_active_positions(self) -> List[Dict]:
        """Get all active positions."""
        cursor = await self._connection.execute(
            """
            SELECT p.*, t.network, t.address, t.token_name, t.token_ticker
            FROM positions p
            INNER JOIN tokens t ON p.token_id = t.id
            WHERE p.status = 'active'
            ORDER BY p.created_at DESC
            """
        )
        rows = await cursor.fetchall()
        # Convert Row objects to dicts
        return [dict(row) for row in rows]

    async def update_position_value(
        self,
        position_id: int,
        current_value_sol: float,
        profit_percent: float,
    ):
        """Update position current value and profit."""
        await self._connection.execute(
            """
            UPDATE positions 
            SET current_value_sol = ?, profit_percent = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (current_value_sol, profit_percent, position_id),
        )
        await self._connection.commit()

    async def close_position(
        self,
        position_id: int,
        sell_tx_signature: Optional[str] = None,
    ):
        """Mark position as closed."""
        await self._connection.execute(
            """
            UPDATE positions 
            SET status = 'closed', sell_tx_signature = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (sell_tx_signature, position_id),
        )
        await self._connection.commit()

    @staticmethod
    def _row_to_token(row: aiosqlite.Row) -> Token:
        """Convert a database row to a Token object."""
        return Token(
            id=row["id"],
            network=row["network"],
            address=row["address"],
            token_name=row["token_name"],
            token_ticker=row["token_ticker"],
            created_at=row["created_at"],
        )

    # ──────────────────────────────────────────────
    # AI CONTEXT FUNCTIONS
    # ──────────────────────────────────────────────

    async def get_recent_messages_for_context(
        self, chat_id: int, before_message_id: int, topic_id: Optional[int] = None, limit: int = 10
    ) -> List[Dict]:
        """
        Get the last N messages from the same chat (and same topic) before a given message,
        including any tagged token info.

        Args:
            chat_id: The Telegram chat ID.
            before_message_id: The database row ID of the target message (fetch messages before this).
            topic_id: If set, only return messages from this forum topic.
                      This filters to messages in the same thread/topic.
            limit: Maximum number of messages to return.

        Returns list of dicts with message fields + tagged token details.
        """
        if topic_id is not None:
            cursor = await self._connection.execute(
                """
                SELECT m.id, m.message_id, m.sender_name, m.message_text,
                       m.is_reply, m.reply_to_sender, m.reply_to_text,
                       m.thread_id, m.topic_id,
                       m.tagged_token_id,
                       t.address as tagged_address, t.token_name as tagged_name,
                       t.token_ticker as tagged_ticker
                FROM messages m
                LEFT JOIN tokens t ON m.tagged_token_id = t.id
                WHERE m.chat_id = ? AND m.id < ? AND m.topic_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (chat_id, before_message_id, topic_id, limit),
            )
        else:
            cursor = await self._connection.execute(
                """
                SELECT m.id, m.message_id, m.sender_name, m.message_text,
                       m.is_reply, m.reply_to_sender, m.reply_to_text,
                       m.thread_id, m.topic_id,
                       m.tagged_token_id,
                       t.address as tagged_address, t.token_name as tagged_name,
                       t.token_ticker as tagged_ticker
                FROM messages m
                LEFT JOIN tokens t ON m.tagged_token_id = t.id
                WHERE m.chat_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (chat_id, before_message_id, limit),
            )
        rows = await cursor.fetchall()
        # Reverse to chronological order and convert to dicts
        return [dict(row) for row in reversed(rows)]

    async def get_recent_tokens(self, limit: int = 20) -> List[Dict]:
        """
        Get the last N discovered tokens, formatted for AI context.

        Returns list of dicts with address, name, ticker, network.
        """
        cursor = await self._connection.execute(
            """
            SELECT t.id, t.address, t.token_name, t.token_ticker, t.network, t.created_at
            FROM tokens t
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        # Reverse to chronological order
        return [dict(row) for row in reversed(rows)]

    async def tag_message_with_token(self, message_db_id: int, token_id: int):
        """Tag a message with a token ID (AI-assigned association)."""
        await self._connection.execute(
            "UPDATE messages SET tagged_token_id = ? WHERE id = ?",
            (token_id, message_db_id),
        )
        await self._connection.commit()


# Convenience function for quick access
async def get_database() -> Database:
    """Create and connect to the database."""
    db = Database()
    await db.connect()
    return db
