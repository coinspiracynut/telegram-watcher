"""
AI Token Tagger

Uses OpenRouter to analyze each incoming message and tag it with the token
being discussed. Handles multi-threaded conversations by providing the model
with recent messages (including existing tags) and the known token list.
"""
import json
import logging
from typing import Optional, List, Dict

from database import Database, StoredMessage
from openrouter import OpenRouterClient

logger = logging.getLogger(__name__)

# ─── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a token tagger for a Telegram crypto trading group. Your job is to determine which token (if any) a message is ACTUALLY discussing.

You will receive:
1. KNOWN TOKENS — a numbered list of recently discovered tokens.
2. RECENT MESSAGES — the last several messages from the same thread, with existing tags shown.
3. NEW MESSAGE — the message you need to tag.

TAGGING RULES:

TAG the message when:
- Message contains a token ticker ($NAME), contract address, or token name.
- Message directly discusses buying, selling, charting, price, or market cap of a specific token.
- Rick/Quickscope bot messages contain token data — always tag these.
- A short trading reaction ("aping", "rugging", "mooning", "this is it", "maybe smt") immediately following token discussion.
- Someone who just called/posted a token is giving follow-up opinions, descriptions, or evaluations of it. E.g. after posting a contract address, the same person says "i like the name", "some github thing", "could be something" — these are about the token they just called.
- The message is clearly an opinion, evaluation, or description of the most recently discussed token (e.g. "interesting concept", "trash ticker", "decent chart").

Do NOT tag (return 0) when:
- The conversation has genuinely drifted to a completely unrelated topic — lifestyle, personal stories, "gm", "gneet", "touching grass", off-topic banter.
- The topic shift is clear: people stop talking about tokens entirely and start chatting about daily life, memes, or non-crypto subjects.
- You truly cannot tell what token is being discussed, even from context.

KEY DISTINCTION: There is a difference between:
  ✅ Follow-up evaluation of a token: "maybe smt", "i just like the name", "decent concept" (TAG these)
  ❌ Conversation drift away from tokens: "touching grass", "neet life irl", "chill outside" (do NOT tag)

The test is: is the person still talking ABOUT the token (even vaguely), or have they moved on to an unrelated subject?

Do NOT blindly continue a tag chain — but DO recognize that short evaluative messages right after a token call are still about that token.

Respond with ONLY a JSON object:
{"token": <number>, "reason": "<brief reason>"}

<number> = token number from KNOWN TOKENS, or 0 if not about any token.

Examples:
{"token": 14, "reason": "mentions $COCK ticker directly"}
{"token": 3, "reason": "evaluating the token they just called - says it could be something"}
{"token": 7, "reason": "describing what the token is about, follow-up to their contract post"}
{"token": 0, "reason": "conversation drifted to personal topics, no longer about any token"}
{"token": 0, "reason": "general lifestyle chat, not about any token"}"""


def _format_token_list(tokens: List[Dict]) -> str:
    """Format token list for the prompt."""
    if not tokens:
        return "(no tokens discovered yet)"

    lines = []
    for i, t in enumerate(tokens, 1):
        name = t.get("token_name") or "Unknown"
        ticker = t.get("token_ticker") or ""
        address = t.get("address", "")
        ticker_str = f" ${ticker}" if ticker else ""
        lines.append(f"{i}. {name}{ticker_str} — {address}")
    return "\n".join(lines)


def _format_recent_messages(messages: List[Dict]) -> str:
    """Format recent messages for the prompt, including tag info.
    
    Only shows reply context for real replies (thread_id is set),
    not for top-level topic posts where reply_to is just the topic root.
    """
    if not messages:
        return "(no recent messages)"

    lines = []
    for m in messages:
        sender = m.get("sender_name", "Unknown")
        text = m.get("message_text", "")
        # Truncate long messages (Rick/bot messages can be very long)
        if len(text) > 300:
            text = text[:300] + "..."

        # Reply context — only show for real replies within the topic
        # (thread_id is set when the message is a reply to a specific message,
        #  not just a top-level post in the topic)
        reply_str = ""
        is_real_reply = m.get("thread_id") is not None and m.get("reply_to_sender")
        if is_real_reply:
            reply_text = m.get("reply_to_text") or ""
            if len(reply_text) > 100:
                reply_text = reply_text[:100] + "..."
            reply_str = f" [replying to {m['reply_to_sender']}: \"{reply_text}\"]" if reply_text else f" [replying to {m['reply_to_sender']}]"

        # Tag info
        tag_str = ""
        if m.get("tagged_token_id"):
            tag_name = m.get("tagged_name") or m.get("tagged_ticker") or m.get("tagged_address", "")
            tag_str = f" [TAGGED: {tag_name}]"

        lines.append(f"- {sender}{reply_str}: \"{text}\"{tag_str}")

    return "\n".join(lines)


def _format_new_message(message: StoredMessage) -> str:
    """Format the new message to be tagged.
    
    Only shows reply context for real replies (thread_id is set),
    not for top-level topic posts.
    """
    sender = message.sender_name or "Unknown"
    text = message.message_text or ""
    if len(text) > 500:
        text = text[:500] + "..."

    # Only show reply context for real replies within a topic
    reply_str = ""
    is_real_reply = message.thread_id is not None and message.reply_to_sender
    if is_real_reply:
        reply_text = message.reply_to_text or ""
        if len(reply_text) > 150:
            reply_text = reply_text[:150] + "..."
        reply_str = f"\nReplying to {message.reply_to_sender}: \"{reply_text}\"" if reply_text else f"\nReplying to {message.reply_to_sender}"

    return f"From: {sender}{reply_str}\nMessage: \"{text}\""


class AITagger:
    """Tags incoming messages with the token they are discussing."""

    def __init__(self, db: Database):
        self.db = db
        self.client = OpenRouterClient()
        # Build a quick lookup: list-position → token_id
        self._token_index: List[int] = []

    async def tag_message(self, message: StoredMessage) -> Optional[int]:
        """
        Determine which token (if any) this message is about.

        Args:
            message: The new message to tag.

        Returns:
            token_id if a match is found, None otherwise.
        """
        if not self.client.enabled:
            return None

        # 1. Fetch context (scoped to the same forum topic)
        tokens = await self.db.get_recent_tokens(limit=20)
        recent = await self.db.get_recent_messages_for_context(
            chat_id=message.chat_id,
            before_message_id=message.id,
            topic_id=message.topic_id,
            limit=10,
        )

        # Build index: position (1-based) → token_id
        self._token_index = [t["id"] for t in tokens]

        # 2. Build prompt
        token_block = _format_token_list(tokens)
        messages_block = _format_recent_messages(recent)
        new_msg_block = _format_new_message(message)

        user_content = (
            f"KNOWN TOKENS:\n{token_block}\n\n"
            f"RECENT MESSAGES:\n{messages_block}\n\n"
            f"NEW MESSAGE:\n{new_msg_block}"
        )

        # 3. Call the model
        response = await self.client.chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            max_tokens=64,
        )

        if not response:
            return None

        # 4. Parse JSON response
        import re
        choice = None
        reason = ""

        # Try to parse as JSON first
        try:
            # Handle responses wrapped in markdown code blocks
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)
            data = json.loads(cleaned)
            choice = int(data.get("token", 0))
            reason = data.get("reason", "")
        except (json.JSONDecodeError, TypeError, ValueError):
            # Fallback: extract first number from response
            match = re.match(r'(\d+)', response.strip())
            if match:
                choice = int(match.group(1))
                logger.debug(f"🏷️ Extracted number {choice} from non-JSON response: {response[:80]!r}")
            else:
                logger.warning(f"⚠️ AI returned unparseable response: {response!r}")
                return None

        if choice == 0 or choice is None:
            logger.debug(f"🏷️ AI tagged message {message.id} as: no token — {reason}")
            return None

        # Map 1-based index back to token_id
        if 1 <= choice <= len(self._token_index):
            token_id = self._token_index[choice - 1]
            token = tokens[choice - 1]
            token_label = token.get("token_ticker") or token.get("token_name") or token.get("address", "")
            logger.info(f"🏷️ AI tagged message {message.id} → {token_label} (token_id={token_id}) — {reason}")
            return token_id
        else:
            logger.warning(f"⚠️ AI returned out-of-range index: {choice} (max {len(self._token_index)})")
            return None
