# Telegram Watcher

Real-time message streaming from private Telegram channels, built for AI-assisted processing.

## Setup

### 1. Get Telegram API Credentials

1. Go to [my.telegram.org/apps](https://my.telegram.org/apps)
2. Log in with your phone number
3. Create a new application (or use existing)
4. Copy your **API ID** and **API Hash**

### 2. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment

Create a `.env` file in the project root:

```env
# Telegram API Credentials
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here

# Optional: Phone number for login hint
TELEGRAM_PHONE=+1234567890

# Channels to monitor (comma-separated)
# Can be: usernames, channel IDs, or invite links
WATCHED_CHANNELS=duaborongboss,-1001234567890,https://t.me/+InviteLink
```

### 4. Run the Watcher

```bash
python watcher.py
```

On first run, you'll be prompted to:
1. Enter your phone number (if not in `.env`)
2. Enter the verification code sent to Telegram
3. Enter 2FA password (if enabled)

Session data is saved locally, so you only need to authenticate once.

## Finding Channel IDs

To get a channel's ID:

1. **For public channels**: Use the username (e.g., `channelname`)
2. **For private channels**: 
   - Forward a message from the channel to [@userinfobot](https://t.me/userinfobot)
   - Or use the web version: the URL shows the ID (e.g., `web.telegram.org/a/#-1001234567890`)

## Project Structure

```
tg-watcher/
├── config.py              # Configuration management
├── watcher.py             # Telegram message listener (saves to DB)
├── processor.py           # AI message processor (reads from DB)
├── database.py            # SQLite database layer
├── cli.py                 # Interactive channel manager
├── messages.db            # SQLite database (auto-created)
├── watched_channels.json  # Persisted watch list (auto-created)
├── requirements.txt
└── .env                   # Your credentials (create this)
```

## Managing Channels

Use the interactive CLI to browse and manage your watched channels:

```bash
python cli.py
```

This gives you a menu to:
1. **List all channels/groups** you're a member of
2. **View currently watched** channels
3. **Add channels** to the watch list (by number or ID)
4. **Remove channels** from the watch list

The watch list is saved to `watched_channels.json` and shared with the watcher.

## Architecture: Two-Process Design

The system runs as two separate processes:

### 1. Watcher Process (`watcher.py`)
- Connects to Telegram and listens for new messages
- Saves all messages to SQLite database with `processed=false`
- Runs continuously

```bash
python watcher.py
```

### 2. Processor Process (`processor.py`)
- Polls database for unprocessed messages
- Runs AI analysis on each message
- Marks messages as `processed=true` when done
- Runs independently from the watcher

```bash
python processor.py
```

**Run both in separate terminals:**

```bash
# Terminal 1 - Message collection
python watcher.py

# Terminal 2 - AI processing
python processor.py
```

### Database Schema

Messages are stored in SQLite (`messages.db`):

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| chat_id | INTEGER | Telegram chat ID |
| chat_title | TEXT | Channel/group name |
| message_id | INTEGER | Telegram message ID |
| sender_name | TEXT | Who sent the message |
| message_text | TEXT | Message content |
| timestamp | TEXT | When it was sent |
| raw_json | TEXT | Full Telethon message object |
| **processed** | INTEGER | 0=pending, 1=processed |
| created_at | TEXT | When we saved it |

## Extending for AI Processing

Edit the `process_message()` method in `processor.py` to add your AI logic:

```python
async def process_message(self, message: StoredMessage) -> bool:
    from openai import AsyncOpenAI
    client = AsyncOpenAI()
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Analyze this message..."},
            {"role": "user", "content": message.message_text or ""},
        ]
    )
    
    analysis = response.choices[0].message.content
    # Save analysis, send alert, etc.
    
    return True
```

## License

MIT
