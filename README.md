# TG Watcher

AI-assisted Telegram message processor for crypto trading groups. Streams messages from private channels, extracts token discoveries from Rick/Quickscope bot alerts, auto-trades on Solana, and uses LLMs to tag every message with the token being discussed.

## Architecture

The system runs as a **single process** with three concurrent asyncio tasks:

1. **Watcher** — Streams messages from Telegram via Telethon (user account), saves them to SQLite.
2. **Processor** — Polls for unprocessed messages, extracts tokens from Rick bot alerts, sends notifications, executes auto-buys, and runs AI tagging on every message.
3. **Monitor** — Periodically checks active token positions against profit targets and sells when thresholds are met.

A separate **Flask dashboard** provides a read-only web UI for inspecting messages, AI tags, and token discoveries.

```
┌──────────────────────────────────────────────────────┐
│                    watcher.py (main)                  │
│                                                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │  Telethon   │  │  Processor   │  │   Monitor    │ │
│  │  listener   │  │  (AI tagger, │  │  (position   │ │
│  │  (messages) │  │   notifier,  │  │   checker,   │ │
│  │             │  │   auto-buy)  │  │   auto-sell) │ │
│  └──────┬─────┘  └──────┬───────┘  └──────┬───────┘ │
│         │               │                 │          │
│         └───────┬───────┘─────────────────┘          │
│                 ▼                                     │
│          messages.db (SQLite)                         │
└──────────────────────────────────────────────────────┘

┌────────────────┐       ┌──────────────────────┐
│  dashboard.py  │──────▶│  messages.db (r/o)   │
│  (Flask :5050) │       └──────────────────────┘
└────────────────┘
```

## Quick Start

### 1. Prerequisites

- Python 3.9+
- A Telegram user account (not a bot account) with API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)

### 2. Install

```bash
git clone https://github.com/coinspiracynut/telegram-watcher.git
cd telegram-watcher

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp env.example .env
# Edit .env with your credentials (see Configuration section below)
```

### 4. Add Channels to Watch

```bash
python cli.py
```

Follow the interactive menu to list your Telegram channels/groups and add them to the watchlist.

### 5. Run

```bash
# Start the main watcher (includes processor + monitor)
python watcher.py

# In another terminal, start the dashboard
python dashboard.py
```

The dashboard is available at [http://localhost:5050](http://localhost:5050).

## Configuration

All configuration is via environment variables in `.env`. See `env.example` for the full template.

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_API_ID` | ✅ | Telegram API ID from my.telegram.org |
| `TELEGRAM_API_HASH` | ✅ | Telegram API hash from my.telegram.org |
| `TELEGRAM_PHONE` | | Phone number hint for login |
| `WATCHED_CHANNELS` | | Comma-separated channel IDs/usernames (also managed via `cli.py`) |
| `TELEGRAM_BOT_TOKEN` | | Bot token from @BotFather (for bot-based notifications) |
| `TELEGRAM_NOTIFICATION_CHAT_ID` | | Chat ID to send notifications and trigger Rick in |
| `SOLANA_WALLET_PRIVATE_KEY` | | Base58 private key for trading |
| `SOLANA_RPC_URL` | | RPC endpoint (default: public mainnet; recommend Helius) |
| `SOLANA_SWAP_API_KEY` | | API key for SolanaTracker swap API |
| `AUTO_BUY_AMOUNT_SOL` | | SOL to spend per new token (set `0` to disable buying) |
| `PROFIT_TARGET_PERCENT` | | Sell when value ≥ this % of buy amount (e.g. `250` = 2.5×) |
| `SELL_PERCENTAGE` | | % of holdings to sell when target hit (e.g. `50`) |
| `OPENROUTER_API_KEY` | | OpenRouter API key for AI tagging |
| `OPENROUTER_MODEL` | | Model ID (default: `anthropic/claude-sonnet-4`) |

## Files

### Core Application

| File | Description |
|---|---|
| `watcher.py` | **Main entry point.** Connects to Telegram via Telethon, streams new messages from watched channels, saves them to the database, and launches the processor and monitor as background tasks. Extracts reply chains, thread IDs, and sender info for each message. |
| `processor.py` | **Message processor.** Polls for unprocessed messages and handles two flows: (1) **Rick bot detection** — identifies messages from `@rick`/`@rickburpbot`, extracts token addresses from Photon/DexScreener URLs, saves tokens, sends notifications to the user's notification chat, and auto-buys Solana tokens. (2) **AI tagging** — runs every message through the AI tagger to associate it with a known token. |
| `monitor.py` | **Position monitor.** Checks active token positions on a loop, fetching real wallet balances via Solana RPC and getting SOL-value quotes from the SolanaTracker swap API. Sells when a position's current value exceeds the configured profit target. Rate-limited to stay within Helius free-tier RPC limits (~6 calls/sec). |
| `database.py` | **Database layer.** Async SQLite via `aiosqlite`. Manages four tables: `messages` (all Telegram messages with reply/thread metadata and AI tags), `tokens` (discovered token contracts), `message_tokens` (Rick-message-to-token links), and `positions` (active/closed trading positions). Includes schema migrations for adding columns to existing databases. |
| `config.py` | **Configuration.** Loads all settings from `.env` via `python-dotenv` and exposes them as class attributes on `Config`. Validates required Telegram credentials on startup. |

### Trading

| File | Description |
|---|---|
| `trader.py` | **Solana trading wrapper.** Wraps the `SolanaTracker` class to provide `buy_token()`, `sell_token()`, `get_token_value_in_sol()` (quote without executing), and `get_token_balance()`. Implements a retry loop (up to 3 attempts) for expired/timed-out transactions. Uses human-readable amounts (e.g. `0.5` SOL, not lamports). |
| `solanatracker.py` | **SolanaTracker swap library.** Vendored from [solana-swap-python](https://github.com/YZYLAB/solana-swap-python). Handles swap instruction fetching from the SolanaTracker API, transaction signing, sending, confirmation polling, and periodic resending. Includes custom exceptions for failed/expired/timed-out transactions. |
| `test_trader.py` | **Trading test script.** CLI tool for manual testing: `balance`, `quote`, `buy`, `sell`, and full `roundtrip` operations against any token address. |

### AI Tagging

| File | Description |
|---|---|
| `ai_tagger.py` | **AI token tagger.** For each incoming message, constructs a prompt with the last 20 known tokens and the last 10 messages from the same forum topic (with existing tags). Sends this to an LLM via OpenRouter and parses the response to determine which token (if any) the message discusses. Handles JSON and fallback number-only responses. The system prompt distinguishes between token evaluation messages (tag them) and lifestyle drift (don't tag). |
| `openrouter.py` | **OpenRouter API client.** Async HTTP client for the OpenRouter chat completions endpoint (OpenAI-compatible). Supports model override per call for easy A/B testing. Gracefully disables itself when no API key is configured. |
| `test_tagger.py` | **AI tagger test script.** Tests the tagger on a specific message ID: shows the full system prompt, user prompt (with token list, recent messages, and target message), calls the AI, and displays the parsed result. Supports `--dry` mode to inspect context without calling the API. |

### Token Extraction & Notifications

| File | Description |
|---|---|
| `token_extractor.py` | **Token extraction from Rick messages.** Parses raw message JSON to extract: token mint addresses from Photon URLs (correct base58 casing), network from DexScreener URLs, and token name/ticker from the message's first line. Prioritizes Photon addresses over DexScreener pair addresses. |
| `notifier.py` | **Telegram Bot API notifications.** Sends formatted "New Token Discovered" messages via the Bot API, including token name, ticker, network, contract address, caller name, DexScreener link, and original message link. |

### Channel Management

| File | Description |
|---|---|
| `cli.py` | **Interactive channel manager.** Terminal UI for listing all your Telegram channels/groups, adding them to the watchlist, and removing them. Persists the watchlist to `watched_channels.json`. |
| `watched_channels.json` | Stores the list of monitored channel IDs. Managed by `cli.py`, consumed by `watcher.py`. |

### Dashboard

| File | Description |
|---|---|
| `dashboard.py` | **Flask web dashboard** (port 5050). Displays: stat cards (total messages, AI-tagged count, tag rate, tokens known), most discussed tokens bar, latest messages with AI tag pills, recent token cards with inline tagged messages and external links (DexScreener, Photon, Solscan). Includes a token detail page and a JSON API endpoint. Auto-refreshes every 20 seconds. |

### Utilities

| File | Description |
|---|---|
| `restore_positions.py` | **Position recovery script.** Audits all closed positions, checks actual on-chain token balances, and restores any that still hold tokens (caused by earlier bugs). Supports `--apply` flag; dry-run by default. |

### Configuration Files

| File | Description |
|---|---|
| `requirements.txt` | Python dependencies. Core: `telethon`, `aiosqlite`, `python-dotenv`, `requests`, `flask`, `aiohttp`. Trading: `solana`, `solders`, `base58`. AI: `openai` (optional). |
| `env.example` | Template `.env` file with all configuration variables and descriptions. |
| `.gitignore` | Ignores `.env`, `venv/`, `*.session`, `messages.db`, `__pycache__/`, `node_modules/`, OS files, and temporary data files. |

## Database Schema

```
messages
├── id (PK)
├── chat_id, chat_title, message_id, sender_name, message_text
├── timestamp, raw_json, processed
├── is_reply, reply_to_message_id, reply_to_text, reply_to_sender
├── thread_id          — Telegram thread/topic root message ID
├── topic_id           — Computed: thread_id or reply_to_message_id
└── tagged_token_id    — FK → tokens.id (AI-assigned)

tokens
├── id (PK)
├── network, address (UNIQUE together)
├── token_name, token_ticker
├── pool_id, notification_sent
└── created_at

message_tokens          — Links Rick's replied-to message → token
├── message_id (FK → messages.id)
└── token_id (FK → tokens.id)

positions
├── id (PK)
├── token_id (FK → tokens.id)
├── buy_amount_sol, buy_price, token_amount
├── buy_tx_signature, sell_tx_signature
├── current_value_sol, profit_percent
├── status ('active' | 'closed')
└── created_at, updated_at
```

## Message Flow

1. **Telegram → Watcher** — New message arrives in a watched channel. Watcher extracts sender, reply chain, thread ID, and raw JSON, then saves to `messages` table.

2. **Processor picks up unprocessed messages** — Every 2 seconds, the processor fetches a batch of unprocessed messages.

3. **Rick bot detection** — If the sender is `@rick` or `@rickburpbot`:
   - Extract token addresses from Photon URLs and network from DexScreener URLs.
   - Parse token name and ticker from the message text.
   - Save or retrieve the token in the `tokens` table.
   - If the token is new:
     - Send the original caller's message to the notification chat (triggers Rick there).
     - Follow up with `/lore {address}` after a 1-second delay.
     - Mark notification as sent.
     - If `AUTO_BUY_AMOUNT_SOL > 0` and the network is Solana: execute a buy, poll for balance, create a position.

4. **AI tagging** — For every message (Rick or not), construct a context window of the last 10 messages from the same forum topic and the last 20 known tokens. Send to OpenRouter LLM. Parse the response and update `tagged_token_id` on the message.

5. **Position monitoring** — Every ~5 seconds, the monitor cycles through all active positions:
   - Fetch actual wallet balance via Solana RPC.
   - Get a SOL-value quote from SolanaTracker's swap API.
   - If value ≥ profit target × buy amount: sell the configured percentage and close the position.

## AI Tagging Details

The AI tagger uses a carefully tuned system prompt that instructs the model to:
- **Tag** messages that mention a token ticker, address, or name; discuss trading activity; or contain short follow-up evaluations of a recently discussed token.
- **Not tag** messages where conversation has drifted to unrelated topics (lifestyle, personal stories, off-topic banter).
- Respond with JSON: `{"token": <number>, "reason": "<brief reason>"}`.

Context is scoped to the same **forum topic** (`topic_id`) to prevent cross-thread noise. The `topic_id` is computed as `thread_id` (if set) or `reply_to_message_id` (for top-level topic posts).

Tested models: `openai/gpt-4o`, `anthropic/claude-sonnet-4`, `x-ai/grok-4.20-multi-agent-beta` (best results); `openai/gpt-4o-mini`, `qwen/qwen3-32b` (acceptable for simpler cases).

## License

Private repository.
