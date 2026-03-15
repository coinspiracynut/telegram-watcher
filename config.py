"""
Configuration management for the Telegram Watcher.
"""
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram API credentials from https://my.telegram.org/apps
    API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    
    # Optional phone number hint for login
    PHONE: Optional[str] = os.getenv("TELEGRAM_PHONE")
    
    # Session file name (stores auth state)
    SESSION_NAME: str = "tg_watcher_session"
    
    # Telegram Bot for notifications (get from @BotFather)
    BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    NOTIFICATION_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_NOTIFICATION_CHAT_ID")
    
    # Solana trading configuration
    SOLANA_WALLET_PRIVATE_KEY: Optional[str] = os.getenv("SOLANA_WALLET_PRIVATE_KEY")
    SOLANA_RPC_URL: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    SOLANA_SWAP_API_KEY: Optional[str] = os.getenv("SOLANA_SWAP_API_KEY")
    # Optional: Custom send endpoint for swaps (solana-swap may require their endpoint)
    SOLANA_CUSTOM_SEND_ENDPOINT: Optional[str] = os.getenv("SOLANA_CUSTOM_SEND_ENDPOINT")
    
    AUTO_BUY_AMOUNT_SOL: float = float(os.getenv("AUTO_BUY_AMOUNT_SOL", "0.5"))
    PROFIT_TARGET_PERCENT: float = float(os.getenv("PROFIT_TARGET_PERCENT", "250.0"))
    SELL_PERCENTAGE: float = float(os.getenv("SELL_PERCENTAGE", "50.0"))
    
    # OpenRouter AI configuration
    OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
    
    # Channels to monitor
    # Can be usernames, IDs (as strings), or invite links
    @staticmethod
    def get_watched_channels() -> list[str]:
        channels_str = os.getenv("WATCHED_CHANNELS", "")
        if not channels_str:
            return []
        return [ch.strip() for ch in channels_str.split(",") if ch.strip()]
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present."""
        if not cls.API_ID or cls.API_ID == 0:
            print("❌ TELEGRAM_API_ID is required")
            return False
        if not cls.API_HASH:
            print("❌ TELEGRAM_API_HASH is required")
            return False
        return True
