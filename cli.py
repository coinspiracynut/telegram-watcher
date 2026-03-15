#!/usr/bin/env python3
"""
Interactive CLI for managing Telegram Watcher channels.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

from config import Config

# File to persist watched channels
WATCHED_CHANNELS_FILE = Path(__file__).parent / "watched_channels.json"


class ChannelManager:
    """Manages channel listing and watch list operations."""

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.watched_ids: Set[int] = set()
        self._load_watched_channels()

    def _load_watched_channels(self):
        """Load watched channels from JSON file."""
        if WATCHED_CHANNELS_FILE.exists():
            try:
                data = json.loads(WATCHED_CHANNELS_FILE.read_text())
                self.watched_ids = set(data.get("channel_ids", []))
            except (json.JSONDecodeError, KeyError):
                self.watched_ids = set()
        
        # Also load from env if present
        for ch in Config.get_watched_channels():
            if ch.lstrip("-").isdigit():
                self.watched_ids.add(int(ch))

    def _save_watched_channels(self):
        """Persist watched channels to JSON file."""
        data = {"channel_ids": list(self.watched_ids)}
        WATCHED_CHANNELS_FILE.write_text(json.dumps(data, indent=2))

    async def connect(self) -> bool:
        """Connect to Telegram."""
        if not Config.validate():
            return False

        self.client = TelegramClient(
            Config.SESSION_NAME,
            Config.API_ID,
            Config.API_HASH,
        )
        await self.client.start(phone=Config.PHONE)

        me = await self.client.get_me()
        print(f"\n✅ Logged in as: {me.first_name} (@{me.username})\n")
        return True

    async def disconnect(self):
        """Disconnect from Telegram."""
        if self.client:
            await self.client.disconnect()

    async def list_all_channels(self) -> List[Dict]:
        """Get all channels/groups the user is a member of."""
        channels = []
        
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            
            if isinstance(entity, (Channel, Chat)):
                is_channel = isinstance(entity, Channel) and entity.broadcast
                is_group = isinstance(entity, Channel) and entity.megagroup
                is_chat = isinstance(entity, Chat)
                
                channel_type = "channel" if is_channel else "group" if (is_group or is_chat) else "other"
                
                channels.append({
                    "id": entity.id,
                    "title": getattr(entity, "title", "Unknown"),
                    "username": getattr(entity, "username", None),
                    "type": channel_type,
                    "watched": entity.id in self.watched_ids,
                })
        
        return channels

    def add_channel(self, channel_id: int):
        """Add a channel to the watch list."""
        self.watched_ids.add(channel_id)
        self._save_watched_channels()

    def remove_channel(self, channel_id: int):
        """Remove a channel from the watch list."""
        self.watched_ids.discard(channel_id)
        self._save_watched_channels()

    def get_watched_ids(self) -> List[int]:
        """Get list of watched channel IDs."""
        return list(self.watched_ids)


def print_channels_table(channels: List[Dict], show_index: bool = True):
    """Pretty print channels as a table."""
    if not channels:
        print("  No channels found.")
        return

    # Header
    print()
    if show_index:
        print(f"  {'#':<4} {'Status':<10} {'Type':<10} {'Title':<35} {'ID':<15} {'Username'}")
        print(f"  {'-'*4} {'-'*10} {'-'*10} {'-'*35} {'-'*15} {'-'*20}")
    else:
        print(f"  {'Status':<10} {'Type':<10} {'Title':<35} {'ID':<15} {'Username'}")
        print(f"  {'-'*10} {'-'*10} {'-'*35} {'-'*15} {'-'*20}")

    for i, ch in enumerate(channels, 1):
        status = "👀 watching" if ch["watched"] else ""
        username = f"@{ch['username']}" if ch["username"] else "-"
        title = ch["title"][:33] + ".." if len(ch["title"]) > 35 else ch["title"]
        
        if show_index:
            print(f"  {i:<4} {status:<10} {ch['type']:<10} {title:<35} {ch['id']:<15} {username}")
        else:
            print(f"  {status:<10} {ch['type']:<10} {title:<35} {ch['id']:<15} {username}")
    print()


async def interactive_menu(manager: ChannelManager):
    """Run the interactive CLI menu."""
    channels_cache: list[dict] = []

    while True:
        print("\n" + "="*60)
        print("  TELEGRAM WATCHER - Channel Manager")
        print("="*60)
        print("\n  Commands:")
        print("    [1] List all my channels/groups")
        print("    [2] Show watched channels only")
        print("    [3] Add channel to watch list (by number from list)")
        print("    [4] Remove channel from watch list")
        print("    [5] Add channel by ID or username")
        print("    [q] Quit")
        print()

        choice = input("  Enter choice: ").strip().lower()

        if choice == "1":
            print("\n  📋 Fetching your channels...")
            channels_cache = await manager.list_all_channels()
            print_channels_table(channels_cache)

        elif choice == "2":
            watched = [ch for ch in channels_cache if ch["watched"]]
            if not watched and not channels_cache:
                print("\n  ⚠️  Run option [1] first to load channels.")
            else:
                # Refresh watched status
                for ch in channels_cache:
                    ch["watched"] = ch["id"] in manager.watched_ids
                watched = [ch for ch in channels_cache if ch["watched"]]
                if watched:
                    print("\n  👀 Currently watching:")
                    print_channels_table(watched, show_index=False)
                else:
                    print("\n  No channels being watched yet.")
                    print(f"  Watched IDs from file: {manager.get_watched_ids()}")

        elif choice == "3":
            if not channels_cache:
                print("\n  ⚠️  Run option [1] first to load channels.")
                continue
            
            try:
                num = input("  Enter channel number to add: ").strip()
                idx = int(num) - 1
                if 0 <= idx < len(channels_cache):
                    ch = channels_cache[idx]
                    manager.add_channel(ch["id"])
                    ch["watched"] = True
                    print(f"\n  ✅ Added '{ch['title']}' (ID: {ch['id']}) to watch list")
                else:
                    print(f"\n  ❌ Invalid number. Enter 1-{len(channels_cache)}")
            except ValueError:
                print("\n  ❌ Please enter a valid number")

        elif choice == "4":
            if not channels_cache:
                print("\n  ⚠️  Run option [1] first to load channels.")
                continue
            
            watched = [ch for ch in channels_cache if ch["watched"]]
            if not watched:
                print("\n  No channels being watched.")
                continue
                
            print("\n  Currently watching:")
            for i, ch in enumerate(watched, 1):
                print(f"    [{i}] {ch['title']} (ID: {ch['id']})")
            
            try:
                num = input("\n  Enter number to remove: ").strip()
                idx = int(num) - 1
                if 0 <= idx < len(watched):
                    ch = watched[idx]
                    manager.remove_channel(ch["id"])
                    ch["watched"] = False
                    print(f"\n  ✅ Removed '{ch['title']}' from watch list")
                else:
                    print(f"\n  ❌ Invalid number. Enter 1-{len(watched)}")
            except ValueError:
                print("\n  ❌ Please enter a valid number")

        elif choice == "5":
            identifier = input("  Enter channel ID or @username: ").strip()
            if not identifier:
                continue
            
            try:
                # Try to resolve the entity
                if identifier.lstrip("-").isdigit():
                    entity = await manager.client.get_entity(int(identifier))
                else:
                    entity = await manager.client.get_entity(identifier)
                
                if isinstance(entity, (Channel, Chat)):
                    manager.add_channel(entity.id)
                    title = getattr(entity, "title", str(entity.id))
                    print(f"\n  ✅ Added '{title}' (ID: {entity.id}) to watch list")
                    
                    # Update cache if present
                    for ch in channels_cache:
                        if ch["id"] == entity.id:
                            ch["watched"] = True
                            break
                else:
                    print(f"\n  ❌ '{identifier}' is not a channel or group")
            except Exception as e:
                print(f"\n  ❌ Could not find '{identifier}': {e}")

        elif choice == "q":
            print("\n  👋 Goodbye!\n")
            break

        else:
            print("\n  ❌ Unknown command. Enter 1-5 or 'q'")


async def main():
    """Main entry point for CLI."""
    manager = ChannelManager()

    if not await manager.connect():
        return

    try:
        await interactive_menu(manager)
    finally:
        await manager.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
