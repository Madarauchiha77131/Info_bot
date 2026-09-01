# Telegram Professional User Info Bot

A polished aiogram 3.x Telegram bot that displays user information that is
**actually available through the official Telegram Bot API** — nothing more.
It does not and cannot access private, hidden, or restricted account data;
the Bot API simply doesn't expose that, and this bot doesn't pretend otherwise.

## Features

- `/start` — Welcome message with inline buttons (My Info, Check User, Help, Owner)
- `/info` — Shows the requesting user's own available Telegram info
- `/check @username` / `/check USER_ID` / `/check` (as a reply) — Looks up a
  user the bot has previously seen (via `/start`, any message, or a reply)
- Works in private chats, groups, and supergroups
- Premium-styled cards using Telegram custom emoji entities, with automatic
  fallback to standard Unicode emoji if custom emoji can't render
- SQLite storage (auto-created on first run), structured for an easy future
  migration to PostgreSQL
- Graceful handling of FloodWait, network errors, blocked bot, missing
  users/usernames/photos, and other Telegram API errors — no raw tracebacks
  ever reach users
- Owner section driven by `OWNER_ID` / `OWNER_USERNAME` env vars — no
  hard-coded credentials

## How `/check` actually works (important)

The Telegram Bot API **cannot resolve an arbitrary `@username` or numeric ID
into user info** unless the bot has some prior context for that user (e.g.
that user has messaged the bot, pressed a button, or is the sender of a
message you're replying to). This bot keeps a small local cache of users
who've interacted with it so `/check` can look them up later. If a user
hasn't interacted with the bot, `/check` will correctly report that the user
can't be found — this is a hard platform limitation, not a bug.

## Project Structure

```
bot/
├── main.py
├── requirements.txt
├── .gitignore
└── README.md
```

## Local Setup

1. Install Python 3.11+.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set environment variables:
   ```bash
   export BOT_TOKEN="your_bot_token_from_botfather"
   export OWNER_ID="your_numeric_telegram_id"
   export OWNER_USERNAME="X_NAGI7"   # optional, defaults shown
   ```
4. Run:
   ```bash
   python main.py
   ```

## Environment Variables

| Variable          | Required | Description                                              |
|-------------------|----------|------------------------------------------------------------|
| `BOT_TOKEN`       | Yes      | Token from [@BotFather](https://t.me/BotFather)             |
| `OWNER_ID`        | Yes      | Your numeric Telegram user ID (for owner-only features)     |
| `OWNER_USERNAME`  | No       | Displayed in the Owner section (default: `X_NAGI7`)         |
| `DB_PATH`         | No       | SQLite file path (default: `bot_data.db`)                    |
| `USE_CUSTOM_EMOJI`| No       | `true`/`false` — disable custom emoji entities if needed     |

Never commit real values for these — use your platform's secret/env manager.

## Deploying to Render

1. Push this project to a GitHub repository.
2. In Render, create a **new Background Worker** (recommended, since this bot
   uses long polling and doesn't need to accept HTTP requests). If your Render
   plan only offers Web Services, that also works — the bot doesn't bind a
   port but Render will still run the process; just be aware Web Services on
   free tiers may sleep after inactivity, which pauses polling until the next
   request/restart.
3. Configure the service:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
4. Add environment variables in Render's dashboard:
   - `BOT_TOKEN=your_bot_token`
   - `OWNER_ID=your_numeric_id`
   - `OWNER_USERNAME=your_username` (optional)
5. Deploy. The bot uses long polling with automatic reconnect/backoff, so it
   will recover on its own from transient network or Telegram API errors.

## Commands

- `/start` — Welcome + inline menu
- `/info` — Your own Telegram info
- `/check @username` — Check a previously-seen user by username
- `/check USER_ID` — Check a previously-seen user by numeric ID
- `/check` (reply to a message) — Check the replied user's info
- `/help` — Command reference and API limitation notice

## Notes on Custom Emoji

Custom emoji entities (`<tg-emoji emoji-id="...">`) require Telegram Premium
context in some client scenarios to render as the custom sticker; otherwise
clients show the fallback Unicode character embedded in the tag. This bot
always supplies a sensible fallback character, so the bot works identically
whether or not custom emoji render, and you can flip `USE_CUSTOM_EMOJI=false`
to disable the tags entirely if you ever hit rendering issues in a particular
chat type.
