"""
Telegram Professional User Info Bot
------------------------------------
A polished aiogram 3.x bot that displays user information legitimately
available through the Telegram Bot API. Does not attempt to access
private, hidden, or restricted account data.

Run with: python main.py
Requires: BOT_TOKEN and OWNER_ID environment variables.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from html import escape
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("info_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID_RAW = os.getenv("OWNER_ID", "0")
DB_PATH = os.getenv("DB_PATH", "bot_data.db")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "X_NAGI7")

if not BOT_TOKEN:
    logger.critical("BOT_TOKEN environment variable is not set. Exiting.")
    sys.exit(1)

try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError:
    logger.critical("OWNER_ID environment variable must be an integer. Exiting.")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Custom emoji mapping
#
# Centralized here so IDs can be swapped later without touching handler code.
# Each entry maps a logical name -> (custom_emoji_id, fallback_unicode_emoji).
# Custom emoji entities require the placeholder character to be a real emoji
# (Telegram renders the custom sticker over it), so we always pass the
# fallback character as the visible text and attach a "custom_emoji" entity
# pointing at the given document ID.
# --------------------------------------------------------------------------- #

CUSTOM_EMOJI: dict[str, tuple[str, str]] = {
    "star": ("6041689667324089197", "⭐"),
    "grave": ("6041779685543646246", "🪦"),
    "pick": ("6039504503928001723", "⛏"),
    "ogre": ("5994775713780078407", "👹"),
    "skull": ("6039730311833587915", "☠"),
    "kiss1": ("6041760624478788043", "💋"),
    "heart1": ("6041671637051382747", "❤"),
    "heart2": ("6043893518122882120", "❤"),
    "kiss2": ("6041702273053102622", "💋"),
    "kiss3": ("6044277926285808167", "💋"),
    "rocket1": ("6041813121864045289", "🚀"),
    "rocket2": ("6041875995890291421", "🚀"),
    "check1": ("6041597085009056322", "✅"),
    "check2": ("6041961929595949681", "✅"),
    "web": ("6041881502038365939", "🕸"),
    "wolf1": ("5994586189758206767", "🐺"),
    "sparkle_star": ("6041883679586784182", "🌟"),
    "wolf2": ("6041845042060988512", "🐺"),
    "moon": ("6039605637522919796", "🌙"),
    "sparkles": ("6042065549976934388", "✨"),
    "gem1": ("6039520867753398589", "💎"),
    "gem2": ("6041797264844788741", "💎"),
    "gem3": ("6041781081408016829", "💎"),
    "sparkle_star2": ("6041629829839720302", "🌟"),
    "cross1": ("6042061156225388967", "✝"),
    "crown": ("6043961485980342530", "👑"),
    "black_heart": ("6042126916469659147", "🖤"),
    "heart_arrow": ("6044369675377185243", "💘"),
    "cross2": ("6041972800158175584", "✝"),
    "cross3": ("6039389175466169613", "✝"),
}

# Whether to attempt custom emoji entities at all. Custom emoji require the
# bot (or the sending account) to have appropriate permissions/tier in some
# contexts; if Telegram rejects them we gracefully fall back to plain emoji.
USE_CUSTOM_EMOJI = os.getenv("USE_CUSTOM_EMOJI", "true").lower() == "true"


def emoji(name: str) -> str:
    """
    Return an HTML tg-emoji tag for the given logical emoji name when custom
    emoji are enabled, otherwise return the plain unicode fallback.

    Usage in HTML-parsed messages:
        f"{emoji('crown')} Owner"
    """
    entry = CUSTOM_EMOJI.get(name)
    if not entry:
        return ""
    custom_id, fallback = entry
    if not USE_CUSTOM_EMOJI:
        return fallback
    # Telegram custom emoji HTML syntax:
    # <tg-emoji emoji-id="ID">fallback</tg-emoji>
    return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'


# --------------------------------------------------------------------------- #
# Database layer (SQLite, easy to migrate to Postgres later)
# --------------------------------------------------------------------------- #

class Database:
    """Thin async wrapper around SQLite for storing seen-user metadata.

    Only stores data that users themselves exposed to the bot by
    interacting with it (id, name, username, language code). This lets
    /check resolve usernames the bot has previously seen, which is the
    only legitimate way a bot can resolve a bare @username via the Bot API.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    is_premium INTEGER,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_username ON seen_users(username)"
            )
            await db.commit()
        logger.info("Database initialized at %s", self.path)

    async def upsert_user(self, user: User) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO seen_users (user_id, username, first_name, last_name,
                                         language_code, is_premium, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    language_code=excluded.language_code,
                    is_premium=excluded.is_premium,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    user.id,
                    user.username,
                    user.first_name,
                    user.last_name,
                    user.language_code,
                    1 if getattr(user, "is_premium", False) else 0,
                ),
            )
            await db.commit()

    async def find_by_username(self, username: str) -> Optional[dict]:
        username = username.lstrip("@").lower()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM seen_users WHERE lower(username) = ?",
                (username,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def find_by_id(self, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM seen_users WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None


db = Database(DB_PATH)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def h(text: Optional[str]) -> str:
    """HTML-escape user-provided text safely, with a fallback for None."""
    if not text:
        return "N/A"
    return escape(str(text))


def yes_no_unknown(value: Optional[bool]) -> str:
    if value is None:
        return "Unknown"
    return "Yes" if value else "No"


def full_name(first: Optional[str], last: Optional[str]) -> str:
    parts = [p for p in (first, last) if p]
    return " ".join(parts) if parts else "N/A"


@dataclass
class UserInfoData:
    user_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    username: Optional[str]
    is_premium: Optional[bool]
    language_code: Optional[str]
    has_photo: Optional[bool]
    is_scam: Optional[bool] = None
    is_fake: Optional[bool] = None
    chat_type: Optional[str] = None


def render_user_card(data: UserInfoData, title: str = "USER INFO") -> str:
    """Render the premium-style info card as safe HTML."""
    username_line = f"@{h(data.username)}" if data.username else "Not set"
    photo_line = "Set" if data.has_photo else ("Not set" if data.has_photo is not None else "Unknown")

    lines = [
        "╭━━━━━━━━━━━━━━━━━━╮",
        f"{emoji('crown')} <b>{h(title)}</b>",
        "╰━━━━━━━━━━━━━━━━━━╯",
        "",
        f"{emoji('sparkle_star')} <b>ID</b>        : <code>{data.user_id}</code>",
        f"{emoji('sparkles')} <b>Name</b>      : {h(full_name(data.first_name, data.last_name))}",
        f"{emoji('gem1')} <b>Username</b>  : {username_line}",
        f"{emoji('star')} <b>Premium</b>   : {yes_no_unknown(data.is_premium)}",
        f"{emoji('moon')} <b>Language</b>  : {h(data.language_code) if data.language_code else 'Unknown'}",
        f"{emoji('web')} <b>Photo</b>     : {photo_line}",
    ]

    if data.is_scam is not None or data.is_fake is not None:
        lines += [
            "",
            f"{emoji('skull')} <b>Scam</b>      : {yes_no_unknown(data.is_scam)}",
            f"{emoji('ogre')} <b>Fake</b>      : {yes_no_unknown(data.is_fake)}",
        ]

    if data.chat_type:
        lines.append(f"{emoji('check1')} <b>Chat Type</b> : {h(data.chat_type)}")

    lines += [
        "",
        "╭━━━━━━━━━━━━━━━━━━╮",
        f"{emoji('crown')} <b>OWNER</b>",
        "╰━━━━━━━━━━━━━━━━━━╯",
        f"{emoji('crown')} Owner : @{h(OWNER_USERNAME)}",
    ]
    return "\n".join(lines)


def info_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Profile", callback_data=f"profile:{user_id}")
    builder.button(text="🆔 Copy ID", callback_data=f"copyid:{user_id}")
    builder.button(text="🔄 Refresh", callback_data=f"refresh:{user_id}")
    builder.button(text="❌ Close", callback_data="close")
    builder.adjust(2, 2)
    return builder.as_markup()


def start_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 My Info", callback_data="start:myinfo")
    builder.button(text="🔎 Check User", callback_data="start:checkhelp")
    builder.button(text="ℹ️ Help", callback_data="start:help")
    builder.button(text="👑 Owner", callback_data="start:owner")
    builder.adjust(2, 2)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Chat type display helper
# --------------------------------------------------------------------------- #

def chat_type_label(chat_type: str) -> str:
    return {
        ChatType.PRIVATE: "Private",
        ChatType.GROUP: "Group",
        ChatType.SUPERGROUP: "Supergroup",
        ChatType.CHANNEL: "Channel",
    }.get(chat_type, chat_type.title() if chat_type else "Unknown")


# --------------------------------------------------------------------------- #
# Router & handlers
# --------------------------------------------------------------------------- #

router = Router(name="main")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await db.upsert_user(message.from_user)
    text = (
        f"{emoji('sparkle_star')} <b>Welcome to Professional User Info Bot</b>\n\n"
        "I can show information about you or other users, limited strictly to "
        "what the Telegram Bot API actually provides.\n\n"
        f"{emoji('gem1')} Use the buttons below or type /help to see all commands."
    )
    await message.answer(text, reply_markup=start_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        f"{emoji('sparkles')} <b>Help &amp; Commands</b>\n\n"
        "<b>/info</b> — Show your Telegram information\n"
        "<b>/check @username</b> — Check available information for a username\n"
        "<b>/check USER_ID</b> — Check available information for a numeric ID\n"
        "<b>/check</b> (as a reply) — Check the replied user's available information\n\n"
        f"{emoji('web')} <i>Note: the Telegram Bot API does not provide arbitrary "
        "access to private or hidden account information. This bot only shows "
        "data made available through the official API, and only for users who "
        "have interacted with the bot or are visible in the current chat "
        "context.</i>"
    )
    await message.answer(text)


@router.message(Command("info"))
async def cmd_info(message: Message) -> None:
    user = message.from_user
    await db.upsert_user(user)

    has_photo: Optional[bool] = None
    try:
        photos = await message.bot.get_user_profile_photos(user.id, limit=1)
        has_photo = photos.total_count > 0
    except TelegramAPIError as e:
        logger.warning("get_user_profile_photos failed for %s: %s", user.id, e)

    data = UserInfoData(
        user_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        is_premium=getattr(user, "is_premium", None),
        language_code=user.language_code,
        has_photo=has_photo,
        chat_type=chat_type_label(message.chat.type),
    )

    await message.answer(
        render_user_card(data, title="USER INFO"),
        reply_markup=info_keyboard(user.id),
    )


async def _resolve_target(message: Message, command: CommandObject) -> Optional[User | dict]:
    """
    Resolve the target of /check from (in priority order):
      1. A replied-to message's sender
      2. A command argument (@username or numeric ID) matched against
         users previously seen by the bot
    Returns a User object, a dict from the DB, or None if unresolved.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    if not command.args:
        return None

    arg = command.args.strip()

    if arg.startswith("@"):
        return await db.find_by_username(arg)

    if arg.lstrip("-").isdigit():
        return await db.find_by_id(int(arg))

    # Bare username without @
    return await db.find_by_username(arg)


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject) -> None:
    await db.upsert_user(message.from_user)

    processing = await message.answer(f"{emoji('sparkles')} Looking that up…")

    target = await _resolve_target(message, command)

    if target is None:
        await processing.edit_text(
            f"{emoji('skull')} <b>User Not Found</b>\n\n"
            "The bot cannot access this user's information through the "
            "Telegram Bot API. This usually means the user has not "
            "interacted with the bot yet, or the username/ID could not be "
            "resolved.",
        )
        return

    if isinstance(target, User):
        has_photo: Optional[bool] = None
        try:
            photos = await message.bot.get_user_profile_photos(target.id, limit=1)
            has_photo = photos.total_count > 0
        except TelegramAPIError as e:
            logger.warning("get_user_profile_photos failed for %s: %s", target.id, e)

        data = UserInfoData(
            user_id=target.id,
            first_name=target.first_name,
            last_name=target.last_name,
            username=target.username,
            is_premium=getattr(target, "is_premium", None),
            language_code=target.language_code,
            has_photo=has_photo,
        )
    else:
        # dict from DB (previously seen user)
        data = UserInfoData(
            user_id=target["user_id"],
            first_name=target["first_name"],
            last_name=target["last_name"],
            username=target["username"],
            is_premium=bool(target["is_premium"]) if target["is_premium"] is not None else None,
            language_code=target["language_code"],
            has_photo=None,
        )

    await processing.edit_text(
        render_user_card(data, title="CHECK RESULT"),
        reply_markup=info_keyboard(data.user_id),
    )


# --------------------------------------------------------------------------- #
# Callback query handlers
# --------------------------------------------------------------------------- #

@router.callback_query(F.data == "close")
async def cb_close(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except TelegramAPIError:
        await callback.message.edit_text(f"{emoji('check1')} Closed.")
    await callback.answer()


@router.callback_query(F.data.startswith("copyid:"))
async def cb_copyid(callback: CallbackQuery) -> None:
    user_id = callback.data.split(":", 1)[1]
    await callback.answer(text=f"ID: {user_id}", show_alert=True)


@router.callback_query(F.data.startswith("profile:"))
async def cb_profile(callback: CallbackQuery) -> None:
    user_id = int(callback.data.split(":", 1)[1])
    try:
        photos = await callback.bot.get_user_profile_photos(user_id, limit=1)
    except TelegramAPIError:
        await callback.answer("Could not load profile photo.", show_alert=True)
        return

    if photos.total_count == 0:
        await callback.answer("This user has no profile photo set.", show_alert=True)
        return

    file_id = photos.photos[0][-1].file_id
    try:
        await callback.message.answer_photo(file_id, caption=f"{emoji('gem1')} Profile photo")
        await callback.answer()
    except TelegramAPIError as e:
        logger.warning("Failed to send profile photo: %s", e)
        await callback.answer("Could not send profile photo.", show_alert=True)


@router.callback_query(F.data.startswith("refresh:"))
async def cb_refresh(callback: CallbackQuery) -> None:
    user_id = int(callback.data.split(":", 1)[1])
    stored = await db.find_by_id(user_id)

    if not stored:
        await callback.answer("No cached data to refresh.", show_alert=True)
        return

    has_photo: Optional[bool] = None
    try:
        photos = await callback.bot.get_user_profile_photos(user_id, limit=1)
        has_photo = photos.total_count > 0
    except TelegramAPIError:
        pass

    data = UserInfoData(
        user_id=stored["user_id"],
        first_name=stored["first_name"],
        last_name=stored["last_name"],
        username=stored["username"],
        is_premium=bool(stored["is_premium"]) if stored["is_premium"] is not None else None,
        language_code=stored["language_code"],
        has_photo=has_photo,
    )

    try:
        await callback.message.edit_text(
            render_user_card(data, title="CHECK RESULT"),
            reply_markup=info_keyboard(user_id),
        )
    except TelegramBadRequest:
        pass  # message content unchanged
    await callback.answer("Refreshed.")


@router.callback_query(F.data == "start:myinfo")
async def cb_start_myinfo(callback: CallbackQuery) -> None:
    fake_msg = callback.message
    await cmd_info(fake_msg.model_copy(update={"from_user": callback.from_user}))
    await callback.answer()


@router.callback_query(F.data == "start:checkhelp")
async def cb_start_checkhelp(callback: CallbackQuery) -> None:
    await callback.message.answer(
        f"{emoji('sparkles')} Use <code>/check @username</code>, "
        "<code>/check USER_ID</code>, or reply to someone's message with "
        "<code>/check</code>."
    )
    await callback.answer()


@router.callback_query(F.data == "start:help")
async def cb_start_help(callback: CallbackQuery) -> None:
    await cmd_help(callback.message)
    await callback.answer()


@router.callback_query(F.data == "start:owner")
async def cb_start_owner(callback: CallbackQuery) -> None:
    await callback.message.answer(f"{emoji('crown')} Owner: @{h(OWNER_USERNAME)}")
    await callback.answer()


# --------------------------------------------------------------------------- #
# Global message tracking (so any interaction lets /check resolve them later)
# --------------------------------------------------------------------------- #

@router.message()
async def track_any_message(message: Message) -> None:
    if message.from_user:
        await db.upsert_user(message.from_user)


# --------------------------------------------------------------------------- #
# Startup / polling with reconnect handling
# --------------------------------------------------------------------------- #

async def main() -> None:
    await db.init()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    logger.info("Bot started as @%s (id=%s)", me.username, me.id)

    backoff = 1
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break  # start_polling returned normally (shutdown requested)
        except TelegramRetryAfter as e:
            logger.warning("FloodWait: sleeping for %s seconds", e.retry_after)
            await asyncio.sleep(e.retry_after)
        except TelegramNetworkError as e:
            logger.warning("Network error: %s — retrying in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except TelegramForbiddenError as e:
            logger.error("Bot forbidden (blocked?): %s", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:  # noqa: BLE001 - top-level resilience by design
            logger.exception("Unexpected error in polling loop: %s", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        else:
            backoff = 1


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
