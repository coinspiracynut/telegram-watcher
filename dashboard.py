#!/usr/bin/env python3
"""
TG Watcher Dashboard – AI Tagging & Token Intelligence

Focused on:
  • Latest messages with their AI-tagged tokens
  • Recent tokens with associated messages
  • Quick overview stats

Run with: python dashboard.py
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

DATABASE_PATH = Path(__file__).parent / "messages.db"


def get_db():
    """Get a synchronous sqlite3 connection (read-only for the dashboard)."""
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def query_db(sql, args=(), one=False):
    """Execute a query and return results as list of dicts."""
    conn = get_db()
    try:
        cur = conn.execute(sql, args)
        rows = [dict(row) for row in cur.fetchall()]
        return rows[0] if one and rows else rows if not one else None
    finally:
        conn.close()


@app.route("/")
def index():
    """Main dashboard – messages + tokens."""

    # ── Recent messages with AI tag info ───────────────────────
    recent_messages = query_db("""
        SELECT m.id, m.chat_title, m.sender_name, m.message_text,
               m.timestamp, m.is_reply, m.reply_to_sender,
               m.thread_id, m.topic_id, m.tagged_token_id,
               t.token_name  AS tag_name,
               t.token_ticker AS tag_ticker,
               t.address      AS tag_address,
               t.network      AS tag_network
        FROM messages m
        LEFT JOIN tokens t ON m.tagged_token_id = t.id
        ORDER BY m.id DESC
        LIMIT 80
    """)

    # ── Recent tokens + their tagged messages ─────────────────
    tokens = query_db("""
        SELECT t.id, t.network, t.address, t.token_name, t.token_ticker,
               t.notification_sent, t.created_at,
               COUNT(m.id) AS tagged_msg_count
        FROM tokens t
        LEFT JOIN messages m ON m.tagged_token_id = t.id
        GROUP BY t.id
        ORDER BY t.created_at DESC
        LIMIT 50
    """)

    # For each of the most recent 30 tokens, grab the last 5 tagged messages
    token_messages = {}
    for tk in tokens[:30]:
        msgs = query_db("""
            SELECT m.id, m.sender_name, m.message_text, m.timestamp
            FROM messages m
            WHERE m.tagged_token_id = ?
            ORDER BY m.id DESC
            LIMIT 5
        """, (tk["id"],))
        if msgs:
            token_messages[tk["id"]] = msgs

    # ── Stats ─────────────────────────────────────────────────
    stats = {}
    stats["total_messages"] = query_db(
        "SELECT COUNT(*) as c FROM messages", one=True
    )["c"]
    stats["tagged_messages"] = query_db(
        "SELECT COUNT(*) as c FROM messages WHERE tagged_token_id IS NOT NULL", one=True
    )["c"]
    stats["untagged_messages"] = stats["total_messages"] - stats["tagged_messages"]
    stats["total_tokens"] = query_db(
        "SELECT COUNT(*) as c FROM tokens", one=True
    )["c"]
    stats["tokens_with_tags"] = query_db(
        "SELECT COUNT(DISTINCT tagged_token_id) as c FROM messages WHERE tagged_token_id IS NOT NULL", one=True
    )["c"]

    # tag rate
    stats["tag_rate"] = (
        round(stats["tagged_messages"] / stats["total_messages"] * 100, 1)
        if stats["total_messages"] > 0
        else 0
    )

    # Top tagged tokens (most messages)
    top_tokens = query_db("""
        SELECT t.id, t.token_name, t.token_ticker, t.address,
               COUNT(m.id) AS msg_count
        FROM tokens t
        INNER JOIN messages m ON m.tagged_token_id = t.id
        GROUP BY t.id
        ORDER BY msg_count DESC
        LIMIT 10
    """)

    return render_template_string(
        TEMPLATE,
        recent_messages=recent_messages,
        tokens=tokens,
        token_messages=token_messages,
        stats=stats,
        top_tokens=top_tokens,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        request=request,
    )


@app.route("/token/<int:token_id>")
def token_detail(token_id):
    """All messages tagged with a specific token."""
    token = query_db(
        "SELECT * FROM tokens WHERE id = ?", (token_id,), one=True
    )
    if not token:
        return "Token not found", 404

    messages = query_db("""
        SELECT m.id, m.sender_name, m.message_text, m.timestamp,
               m.chat_title, m.is_reply, m.reply_to_sender
        FROM messages m
        WHERE m.tagged_token_id = ?
        ORDER BY m.id DESC
        LIMIT 200
    """, (token_id,))

    return render_template_string(
        TOKEN_DETAIL_TEMPLATE,
        token=token,
        messages=messages,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


@app.route("/api/messages")
def api_messages():
    """JSON endpoint for recent messages with tags."""
    limit = request.args.get("limit", 50, type=int)
    rows = query_db("""
        SELECT m.id, m.sender_name, m.message_text, m.timestamp,
               m.tagged_token_id,
               t.token_name AS tag_name, t.token_ticker AS tag_ticker
        FROM messages m
        LEFT JOIN tokens t ON m.tagged_token_id = t.id
        ORDER BY m.id DESC
        LIMIT ?
    """, (limit,))
    return jsonify(rows)


# ─────────────────────────────────────────────────────────────
# Main Dashboard Template
# ─────────────────────────────────────────────────────────────
TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="20">
    <title>TG Watcher – AI Tags</title>
    <style>
        :root {
            --bg: #0a0a0f;
            --surface: #12121a;
            --surface2: #1a1a26;
            --border: #2a2a3a;
            --text: #e4e4ef;
            --muted: #6b6b80;
            --accent: #7c6bf5;
            --accent-dim: rgba(124, 107, 245, 0.12);
            --green: #4ade80;
            --green-dim: rgba(74, 222, 128, 0.1);
            --yellow: #facc15;
            --yellow-dim: rgba(250, 204, 21, 0.1);
            --blue: #60a5fa;
            --red: #f87171;
            --pink: #f472b6;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            min-height: 100vh;
        }

        .container { max-width: 1400px; margin: 0 auto; padding: 24px; }

        /* ─── Header ─── */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 0 24px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 28px;
        }
        .header h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
        .header h1 span { color: var(--accent); }
        .header .meta { color: var(--muted); font-size: 12px; }

        /* ─── Stat Cards ─── */
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px;
            margin-bottom: 32px;
        }
        .stat {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px 18px;
        }
        .stat .label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--muted);
            margin-bottom: 6px;
        }
        .stat .value {
            font-size: 26px;
            font-weight: 700;
            letter-spacing: -1px;
        }
        .stat .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
        .accent-text { color: var(--accent); }
        .green-text { color: var(--green); }

        /* ─── Layout ─── */
        .grid-2 {
            display: grid;
            grid-template-columns: 1fr 420px;
            gap: 24px;
            align-items: start;
        }
        @media (max-width: 1100px) {
            .grid-2 { grid-template-columns: 1fr; }
        }

        /* ─── Section ─── */
        .section {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 24px;
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 18px;
            border-bottom: 1px solid var(--border);
            background: var(--surface2);
        }
        .section-header h2 {
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .badge {
            background: var(--accent-dim);
            color: var(--accent);
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 8px;
            font-weight: 600;
        }
        .badge-green {
            background: var(--green-dim);
            color: var(--green);
        }

        /* ─── Messages Table ─── */
        .msg-list { padding: 0; }
        .msg-row {
            display: grid;
            grid-template-columns: 50px 1fr auto;
            gap: 12px;
            padding: 12px 18px;
            border-bottom: 1px solid var(--border);
            align-items: start;
            transition: background 0.1s;
        }
        .msg-row:hover { background: rgba(124, 107, 245, 0.03); }
        .msg-row:last-child { border-bottom: none; }

        .msg-id {
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 11px;
            color: var(--muted);
            padding-top: 2px;
        }
        .msg-body { min-width: 0; }
        .msg-sender {
            font-weight: 600;
            font-size: 13px;
            margin-bottom: 2px;
        }
        .msg-sender .reply-info {
            color: var(--yellow);
            font-weight: 400;
            font-size: 11px;
        }
        .msg-text {
            color: var(--muted);
            font-size: 12.5px;
            line-height: 1.4;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            word-break: break-word;
        }
        .msg-meta {
            text-align: right;
            white-space: nowrap;
            padding-top: 2px;
        }
        .msg-time {
            font-size: 11px;
            color: var(--muted);
        }
        .msg-tag {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            margin-top: 6px;
            padding: 3px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
        }
        .msg-tag.tagged {
            background: var(--accent-dim);
            color: var(--accent);
        }
        .msg-tag.untagged {
            background: rgba(107, 107, 128, 0.1);
            color: var(--muted);
            font-weight: 400;
        }
        .msg-channel {
            font-size: 10px;
            color: var(--muted);
            opacity: 0.7;
        }

        /* ─── Token Cards ─── */
        .token-list { padding: 8px; }
        .token-card {
            background: var(--surface2);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 8px;
            transition: border-color 0.15s;
        }
        .token-card:hover { border-color: var(--accent); }
        .token-card:last-child { margin-bottom: 0; }

        .token-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .token-name {
            font-weight: 700;
            font-size: 14px;
        }
        .token-ticker {
            color: var(--accent);
            font-weight: 600;
            margin-left: 6px;
        }
        .token-count {
            font-size: 11px;
            color: var(--muted);
        }
        .token-count strong { color: var(--green); font-weight: 600; }
        .token-addr {
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 10.5px;
            color: var(--muted);
            margin-bottom: 8px;
            word-break: break-all;
        }
        .token-msgs {
            border-top: 1px solid var(--border);
            padding-top: 8px;
        }
        .token-msg-item {
            display: flex;
            gap: 8px;
            font-size: 11.5px;
            padding: 3px 0;
            color: var(--muted);
        }
        .token-msg-item .tm-sender { color: var(--text); font-weight: 500; min-width: 80px; }
        .token-msg-item .tm-text {
            flex: 1;
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .token-date { font-size: 10px; color: var(--muted); opacity: 0.6; }
        .token-links { margin-top: 8px; display: flex; gap: 8px; }
        .token-links a {
            font-size: 11px;
            color: var(--blue);
            text-decoration: none;
            opacity: 0.8;
        }
        .token-links a:hover { opacity: 1; text-decoration: underline; }

        /* ─── Top Tokens Bar ─── */
        .top-bar {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 24px;
        }
        .top-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 12px;
            text-decoration: none;
            color: var(--text);
            transition: border-color 0.15s;
        }
        .top-chip:hover { border-color: var(--accent); text-decoration: none; }
        .top-chip .chip-ticker { color: var(--accent); font-weight: 600; }
        .top-chip .chip-count {
            background: var(--accent-dim);
            color: var(--accent);
            font-size: 10px;
            font-weight: 700;
            padding: 1px 6px;
            border-radius: 6px;
        }

        /* ─── Empty state ─── */
        .empty {
            text-align: center;
            color: var(--muted);
            padding: 32px;
            font-size: 13px;
        }

        /* ─── Scrollbar ─── */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    </style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>📡 TG Watcher <span>· AI Tags</span></h1>
    <div class="meta">{{ now }} · auto-refreshes every 20s</div>
</div>

<!-- ── Stats ── -->
<div class="stats">
    <div class="stat">
        <div class="label">Messages</div>
        <div class="value">{{ "{:,}".format(stats.total_messages) }}</div>
    </div>
    <div class="stat">
        <div class="label">AI Tagged</div>
        <div class="value green-text">{{ "{:,}".format(stats.tagged_messages) }}</div>
        <div class="sub">{{ stats.tag_rate }}% of all messages</div>
    </div>
    <div class="stat">
        <div class="label">Untagged</div>
        <div class="value">{{ "{:,}".format(stats.untagged_messages) }}</div>
    </div>
    <div class="stat">
        <div class="label">Tokens Known</div>
        <div class="value accent-text">{{ stats.total_tokens }}</div>
    </div>
    <div class="stat">
        <div class="label">Tokens w/ Tags</div>
        <div class="value accent-text">{{ stats.tokens_with_tags }}</div>
        <div class="sub">tokens referenced in messages</div>
    </div>
</div>

<!-- ── Top Tokens ── -->
{% if top_tokens %}
<div style="margin-bottom: 24px;">
    <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); margin-bottom: 10px; font-weight: 600;">🔥 Most Discussed Tokens</div>
    <div class="top-bar">
        {% for tt in top_tokens %}
        <a href="/token/{{ tt.id }}" class="top-chip">
            <span>{{ tt.token_name or '?' }}</span>
            <span class="chip-ticker">${{ tt.token_ticker or '?' }}</span>
            <span class="chip-count">{{ tt.msg_count }}</span>
        </a>
        {% endfor %}
    </div>
</div>
{% endif %}

<!-- ── Main Grid ── -->
<div class="grid-2">

    <!-- Left: Messages -->
    <div class="section">
        <div class="section-header">
            <h2>💬 Latest Messages</h2>
            <span class="badge">{{ recent_messages|length }}</span>
        </div>
        <div class="msg-list">
        {% if recent_messages %}
            {% for m in recent_messages %}
            <div class="msg-row">
                <div class="msg-id">#{{ m.id }}</div>
                <div class="msg-body">
                    <div class="msg-sender">
                        {{ m.sender_name or 'Unknown' }}
                        {% if m.is_reply and m.reply_to_sender %}
                            <span class="reply-info">↩ {{ m.reply_to_sender }}</span>
                        {% endif %}
                    </div>
                    <div class="msg-text">{{ m.message_text or '[media/empty]' }}</div>
                    <div class="msg-channel">{{ m.chat_title }}</div>
                </div>
                <div class="msg-meta">
                    <div class="msg-time">{{ m.timestamp[11:16] if m.timestamp and m.timestamp|length > 16 else m.timestamp }}</div>
                    {% if m.tagged_token_id %}
                    <a href="/token/{{ m.tagged_token_id }}" style="text-decoration:none;">
                        <div class="msg-tag tagged">
                            🏷 ${{ m.tag_ticker or '?' }}
                        </div>
                    </a>
                    {% else %}
                    <div class="msg-tag untagged">—</div>
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        {% else %}
            <div class="empty">No messages yet</div>
        {% endif %}
        </div>
    </div>

    <!-- Right: Tokens with messages -->
    <div>
        <div class="section">
            <div class="section-header">
                <h2>🪙 Recent Tokens</h2>
                <span class="badge">{{ tokens|length }}</span>
            </div>
            <div class="token-list">
            {% if tokens %}
                {% for tk in tokens %}
                <div class="token-card">
                    <div class="token-head">
                        <div>
                            <span class="token-name">{{ tk.token_name or 'Unknown' }}</span>
                            {% if tk.token_ticker %}
                            <span class="token-ticker">${{ tk.token_ticker }}</span>
                            {% endif %}
                        </div>
                        <div class="token-count">
                            <strong>{{ tk.tagged_msg_count }}</strong> msgs
                        </div>
                    </div>
                    <div class="token-addr">{{ tk.address }}</div>

                    {% if token_messages.get(tk.id) %}
                    <div class="token-msgs">
                        {% for tmsg in token_messages[tk.id] %}
                        <div class="token-msg-item">
                            <span class="tm-sender">{{ tmsg.sender_name or '?' }}</span>
                            <span class="tm-text">{{ (tmsg.message_text[:80] + '…') if tmsg.message_text and tmsg.message_text|length > 80 else (tmsg.message_text or '[media]') }}</span>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}

                    <div class="token-links">
                        <a href="/token/{{ tk.id }}">View all →</a>
                        {% if tk.network == 'solana' %}
                        <a href="https://dexscreener.com/{{ tk.network }}/{{ tk.address }}" target="_blank">DexScreener ↗</a>
                        <a href="https://photon-sol.tinyastro.io/en/r/@RickBurpBot/{{ tk.address }}" target="_blank">Photon ↗</a>
                        {% endif %}
                    </div>
                    <div class="token-date">discovered {{ tk.created_at[:16] if tk.created_at else '' }}</div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty">No tokens discovered yet</div>
            {% endif %}
            </div>
        </div>
    </div>

</div><!-- /grid-2 -->

</div><!-- /container -->
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────
# Token Detail Template
# ─────────────────────────────────────────────────────────────
TOKEN_DETAIL_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ token.token_name or token.address[:12] }} – TG Watcher</title>
    <style>
        :root {
            --bg: #0a0a0f;
            --surface: #12121a;
            --surface2: #1a1a26;
            --border: #2a2a3a;
            --text: #e4e4ef;
            --muted: #6b6b80;
            --accent: #7c6bf5;
            --accent-dim: rgba(124, 107, 245, 0.12);
            --green: #4ade80;
            --yellow: #facc15;
            --blue: #60a5fa;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            min-height: 100vh;
        }
        .container { max-width: 900px; margin: 0 auto; padding: 24px; }

        .back { color: var(--muted); text-decoration: none; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; margin-bottom: 16px; }
        .back:hover { color: var(--text); }

        .token-hero {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
        }
        .token-hero h1 { font-size: 22px; font-weight: 700; }
        .token-hero .ticker { color: var(--accent); font-weight: 600; }
        .token-hero .addr {
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 12px;
            color: var(--muted);
            margin: 8px 0;
            word-break: break-all;
        }
        .token-hero .links { display: flex; gap: 12px; margin-top: 10px; }
        .token-hero .links a { color: var(--blue); font-size: 13px; text-decoration: none; }
        .token-hero .links a:hover { text-decoration: underline; }

        .msg-table {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
        }
        .msg-table-header {
            padding: 14px 18px;
            border-bottom: 1px solid var(--border);
            background: var(--surface2);
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th {
            text-align: left;
            padding: 8px 14px;
            color: var(--muted);
            font-weight: 500;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        tr:hover { background: rgba(124, 107, 245, 0.03); }
        .sender { font-weight: 600; white-space: nowrap; }
        .reply-badge { color: var(--yellow); font-size: 11px; font-weight: 400; }
        .preview {
            color: var(--muted);
            max-width: 500px;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            word-break: break-word;
            line-height: 1.4;
        }
        .ts { color: var(--muted); font-size: 12px; white-space: nowrap; }
    </style>
</head>
<body>
<div class="container">

<a href="/" class="back">← Back to dashboard</a>

<div class="token-hero">
    <h1>{{ token.token_name or 'Unknown Token' }} <span class="ticker">${{ token.token_ticker or '?' }}</span></h1>
    <div class="addr">{{ token.address }}</div>
    <div style="font-size: 12px; color: var(--muted);">Network: {{ token.network }} · Discovered: {{ token.created_at[:16] if token.created_at else '?' }}</div>
    <div class="links">
        {% if token.network == 'solana' %}
        <a href="https://dexscreener.com/{{ token.network }}/{{ token.address }}" target="_blank">📊 DexScreener</a>
        <a href="https://photon-sol.tinyastro.io/en/r/@RickBurpBot/{{ token.address }}" target="_blank">🔬 Photon</a>
        <a href="https://solscan.io/token/{{ token.address }}" target="_blank">🔍 Solscan</a>
        {% endif %}
    </div>
</div>

<div class="msg-table">
    <div class="msg-table-header">💬 Tagged Messages ({{ messages|length }})</div>
    {% if messages %}
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Sender</th>
                <th>Message</th>
                <th>Channel</th>
                <th>Time</th>
            </tr>
        </thead>
        <tbody>
        {% for m in messages %}
            <tr>
                <td style="color: var(--muted); font-family: monospace; font-size: 11px;">{{ m.id }}</td>
                <td>
                    <span class="sender">{{ m.sender_name or '?' }}</span>
                    {% if m.is_reply and m.reply_to_sender %}
                    <br><span class="reply-badge">↩ {{ m.reply_to_sender }}</span>
                    {% endif %}
                </td>
                <td><div class="preview">{{ m.message_text or '[media]' }}</div></td>
                <td style="font-size: 11px; color: var(--muted);">{{ m.chat_title }}</td>
                <td class="ts">{{ m.timestamp }}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div style="text-align: center; color: var(--muted); padding: 32px; font-size: 13px;">
        No messages tagged with this token yet
    </div>
    {% endif %}
</div>

</div>
</body>
</html>
"""

if __name__ == "__main__":
    print("🌐 Dashboard starting at http://localhost:5050")
    print("   Press Ctrl+C to stop")
    app.run(host="0.0.0.0", port=5050, debug=False)
