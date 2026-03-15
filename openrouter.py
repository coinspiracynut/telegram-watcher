"""
OpenRouter API client.

Provides a composable interface to test against multiple models.
Uses the OpenAI-compatible API at https://openrouter.ai/api/v1.
"""
import logging
from typing import Optional, List, Dict, Any

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    """Async client for OpenRouter API (OpenAI-compatible)."""

    def __init__(self, model: Optional[str] = None):
        self.api_key = Config.OPENROUTER_API_KEY
        self.model = model or Config.OPENROUTER_MODEL
        self.enabled = bool(self.api_key)

        if not self.enabled:
            logger.warning("⚠️ OPENROUTER_API_KEY not set — AI tagging disabled")

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 256,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a chat completion request to OpenRouter.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
            temperature: 0.0 = deterministic, 1.0 = creative
            max_tokens: Max response tokens
            model: Override the default model for this call

        Returns:
            The assistant's response text, or None on failure.
        """
        if not self.enabled:
            return None

        use_model = model or self.model

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/tg-watcher",
            "X-Title": "tg-watcher",
        }

        payload = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"❌ OpenRouter {resp.status}: {body[:200]}")
                        return None

                    data = await resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        logger.warning(f"⚠️ OpenRouter returned no choices: {str(data)[:300]}")
                        return None

                    message = choices[0].get("message") if choices[0] else None
                    if not message:
                        logger.warning(f"⚠️ OpenRouter choice has no message: {str(choices[0])[:300]}")
                        return None

                    content = message.get("content") or ""
                    logger.debug(f"OpenRouter response ({use_model}): {content[:100]}...")
                    return content.strip()

        except Exception as e:
            logger.error(f"❌ OpenRouter request failed: {type(e).__name__}: {e}")
            return None
