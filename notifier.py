"""
Telegram Bot notification utilities.
"""
import logging
from typing import Optional

import requests

from config import Config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends notifications via Telegram Bot API."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or Config.BOT_TOKEN
        self.chat_id = chat_id or Config.NOTIFICATION_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def is_configured(self) -> bool:
        """Check if bot is configured."""
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        Send a message via Telegram Bot API.
        
        Args:
            text: Message text
            parse_mode: Markdown or HTML
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_configured():
            logger.warning("⚠️ Telegram bot not configured (BOT_TOKEN or NOTIFICATION_CHAT_ID missing)")
            return False

        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info("✅ Notification sent successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to send notification: {e}")
            return False

    def send_token_notification(
        self,
        network: str,
        address: str,
        token_name: Optional[str] = None,
        token_ticker: Optional[str] = None,
        dexscreener_url: Optional[str] = None,
        called_by: Optional[str] = None,
        message_link: Optional[str] = None,
    ) -> bool:
        """
        Send a notification about a new token discovery.
        
        Args:
            network: Blockchain network (e.g., "solana")
            address: Token contract address
            token_name: Token name
            token_ticker: Token ticker/symbol
            dexscreener_url: DexScreener URL for the token
            called_by: Name of the user who called Rick bot
            message_link: Link to the original Telegram message
        """
        if not dexscreener_url:
            dexscreener_url = f"https://dexscreener.com/{network}/{address}"
        
        # Format message
        lines = ["🎯 *New Token Discovered*"]
        lines.append("")
        
        if called_by:
            lines.append(f"*Called by:* {called_by}")
            lines.append("")
        
        if token_name:
            lines.append(f"*Name:* {token_name}")
        if token_ticker:
            lines.append(f"*Ticker:* ${token_ticker}")
        
        lines.append(f"*Network:* {network.upper()}")
        lines.append(f"*Address:* `{address}`")
        lines.append("")
        lines.append(f"[View on DexScreener]({dexscreener_url})")
        
        if message_link:
            lines.append(f"[📨 Original Message]({message_link})")
        
        message = "\n".join(lines)
        
        return self.send_message(message)
