#!/usr/bin/env python3
"""
Test script for the AI tagger.

Usage:
    python test_tagger.py <message_db_id>
    python test_tagger.py <message_db_id> --dry   # show context only, don't call AI

Examples:
    python test_tagger.py 112155
    python test_tagger.py 112155 --dry
"""
import asyncio
import sys
import logging

from database import Database
from ai_tagger import (
    AITagger,
    SYSTEM_PROMPT,
    _format_token_list,
    _format_recent_messages,
    _format_new_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def test_message(message_db_id: int, dry_run: bool = False):
    db = Database()
    await db.connect()

    # Fetch the target message
    cursor = await db._connection.execute(
        "SELECT * FROM messages WHERE id = ?", (message_db_id,)
    )
    row = await cursor.fetchone()
    if not row:
        print(f"❌ Message ID {message_db_id} not found in database")
        await db.close()
        return

    message = db._row_to_message(row)

    print("=" * 80)
    print(f"TARGET MESSAGE (DB id={message.id})")
    print(f"  Chat:    {message.chat_title}")
    print(f"  Sender:  {message.sender_name}")
    print(f"  Reply:   {message.reply_to_sender or '(not a reply)'}")
    print(f"  Thread:  {message.thread_id}  Topic: {message.topic_id}")
    if message.reply_to_text:
        preview = message.reply_to_text[:120] + "..." if len(message.reply_to_text) > 120 else message.reply_to_text
        print(f"  ReplyTx: {preview}")
    print(f"  Text:    {(message.message_text or '')[:200]}")
    print(f"  Tagged:  {message.tagged_token_id or '(none)'}")
    print("=" * 80)

    # Fetch context (scoped to same forum topic)
    tokens = await db.get_recent_tokens(limit=20)
    recent = await db.get_recent_messages_for_context(
        chat_id=message.chat_id,
        before_message_id=message.id,
        topic_id=message.topic_id,
        limit=10,
    )

    token_block = _format_token_list(tokens)
    messages_block = _format_recent_messages(recent)
    new_msg_block = _format_new_message(message)

    user_content = (
        f"KNOWN TOKENS:\n{token_block}\n\n"
        f"RECENT MESSAGES:\n{messages_block}\n\n"
        f"NEW MESSAGE:\n{new_msg_block}"
    )

    print("\n📋 SYSTEM PROMPT:")
    print("-" * 40)
    print(SYSTEM_PROMPT)
    print("-" * 40)

    print("\n📨 USER PROMPT:")
    print("-" * 40)
    print(user_content)
    print("-" * 40)

    # Token index for interpreting the response
    token_index = {i + 1: t for i, t in enumerate(tokens)}

    print(f"\n📊 CONTEXT STATS:")
    print(f"  Tokens:   {len(tokens)}")
    print(f"  Messages: {len(recent)}")

    if dry_run:
        print("\n🔒 DRY RUN — skipping AI call")
        print("\nToken index for reference:")
        for i, t in enumerate(tokens, 1):
            name = t.get("token_name") or "Unknown"
            ticker = t.get("token_ticker") or ""
            print(f"  {i}. {name} {f'${ticker}' if ticker else ''} — {t['address'][:20]}...")
        await db.close()
        return

    # Call AI
    print("\n🤖 Calling OpenRouter...")
    tagger = AITagger(db)
    from openrouter import OpenRouterClient
    import re

    client = OpenRouterClient()

    response = await client.chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.0,
        max_tokens=64,
    )

    print(f"\n🔮 RAW RESPONSE: {response!r}")

    if response:
        import json as _json
        choice = None
        reason = ""
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)
            data = _json.loads(cleaned)
            choice = int(data.get("token", 0))
            reason = data.get("reason", "")
        except (ValueError, _json.JSONDecodeError, TypeError):
            match = re.match(r'(\d+)', response.strip())
            if match:
                choice = int(match.group(1))
            else:
                print(f"⚠️ RESULT: Unparseable response")

        if choice is not None:
            if choice == 0:
                print(f"🏷️ RESULT: No token (general chat)")
            elif 1 <= choice <= len(tokens):
                t = tokens[choice - 1]
                name = t.get("token_name") or "Unknown"
                ticker = t.get("token_ticker") or ""
                print(f"🏷️ RESULT: #{choice} → {name} {f'${ticker}' if ticker else ''}")
                print(f"   Address: {t['address']}")
                print(f"   Token ID: {t['id']}")
            else:
                print(f"⚠️ RESULT: Out of range ({choice})")
            if reason:
                print(f"   Reason: {reason}")

    await db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_tagger.py <message_db_id> [--dry]")
        sys.exit(1)

    msg_id = int(sys.argv[1])
    dry = "--dry" in sys.argv

    asyncio.run(test_message(msg_id, dry_run=dry))
