if __name__ == "__main__":
    exit("Run bot.py instead!")

import logging
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import db
from helpers import (
    check_subscription,
    get_movie_info,
    encode_payload,
    decode_payload,
    normalize_name
)
from utils.monetize import create_ad_link, is_monetization_enabled

logger = logging.getLogger(__name__)


def register_user_handlers(app: Client):
    
    # ============ /start COMMAND ============
    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(bot: Client, message: Message):
        user_id = message.from_user.id
        username = message.from_user.username
        text = message.text.strip()
        
        logger.info(f"START from {user_id}: {text}")
        
        await db.add_user(user_id, username)
        
        parts = text.split(maxsplit=1)
        
        # No payload - show welcome
        if len(parts) == 1:
            await send_welcome(message)
            return
        
        payload = parts[1].strip()
        
        if not payload:
            await send_welcome(message)
            return
        
        # Block controller payloads
        blocked = ["connect", "controller", "setup", "config", "admin", "panel", "settings"]
        if any(b in payload.lower() for b in blocked):
            if user_id != Config.ADMIN_ID:
                await send_welcome(message)
                return
        
        # ============ TOKEN FROM AD PAGE (token_xxxxx) ============
        if payload.startswith("token_"):
            token = payload.replace("token_", "", 1)
            await handle_token_verification(bot, message, token, user_id)
            return
        
        # ============ LEGACY PAYLOAD (base64 encoded) ============
        movie_code, part, quality, token = decode_payload(payload)
        
        logger.info(f"Decoded: code={movie_code}, part={part}, quality={quality}, token={token[:10] if token else 'None'}...")
        
        if not movie_code:
            await send_welcome(message)
            return
        
        # Check subscription
        if not await check_subscription(bot, user_id):
            await send_subscription_message(bot, message, payload)
            return
        
        # Has token in legacy format
        if token:
            await handle_token_verification(bot, message, token, user_id)
            return
        
        # No token - show movie details
        movie = await db.get_movie(movie_code)
        
        if not movie:
            await send_welcome(message)
            return
        
        # Multi-part movie
        if movie.get("parts", 1) > 1:
            buttons = []
            for i in range(1, movie["parts"] + 1):
                buttons.append(InlineKeyboardButton(f"📦 Part {i}", callback_data=f"part:{movie_code}:{i}"))
            
            keyboard = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
            
            await message.reply_text(
                f"🎬 **{movie['title']}**\n\n"
                f"This movie has {movie['parts']} parts.\n"
                f"Select one:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Single part - show quality selection
        qualities = movie.get("qualities", {})
        
        if not qualities:
            await message.reply_text("❌ No files available for this movie.")
            return
        
        if len(qualities) == 1:
            quality = list(qualities.keys())[0]
            await generate_download_link(bot, message, movie, 1, quality)
        else:
            await show_quality_selection(message, movie, 1)
    
    
    # ============ /help COMMAND ============
    @app.on_message(filters.command("help") & filters.private)
    async def help_cmd(bot: Client, message: Message):
        user_id = message.from_user.id
        
        text = (
            "🎬 **Movie Bot Help**\n\n"
            "**How to Use:**\n"
            "Just send me a movie name!\n\n"
            "**Examples:**\n"
            "• Kill Bill\n"
            "• Dune\n"
            "• Avengers Endgame"
        )
        
        if user_id == Config.ADMIN_ID:
            text += (
                "\n\n━━━━━━━━━━━━━━━\n"
                "👑 **Admin Commands:**\n\n"
                "`/add Movie Name | quality`\n"
                "`/addpart Movie | part | quality`\n"
                "`/delete Movie Name`\n"
                "`/delete Movie Name | quality`\n"
                "`/list` - List all movies\n"
                "`/stats` - Statistics\n"
                "`/broadcast` - Send to all"
            )
        
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    
    # ============ SEARCH (any text) ============
    @app.on_message(filters.text & filters.private)
    async def search_cmd(bot: Client, message: Message):
        text = message.text.strip()
        
        if text.startswith("/"):
            return
        
        user_id = message.from_user.id
        await db.add_user(user_id, message.from_user.username)
        
        query = normalize_name(text)
        
        if len(query) < 2:
            await message.reply_text("❌ Enter at least 2 characters!")
            return
        
        movies = await db.search_movies(query)
        
        if not movies:
            info = await get_movie_info(text)
            if info:
                await message.reply_text(
                    f"❌ **Not in database**\n\n"
                    f"Found on TMDB:\n"
                    f"🎬 {info['title']} ({info.get('year', '')})\n"
                    f"⭐ {info.get('rating', 'N/A')}/10\n\n"
                    f"Contact admin to add!",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await message.reply_text("❌ Movie not found! Check spelling.")
            return
        
        if len(movies) == 1:
            await send_movie_card(bot, message, movies[0])
            return
        
        buttons = []
        for m in movies[:10]:
            parts_text = f" ({m.get('parts', 1)} parts)" if m.get('parts', 1) > 1 else ""
            buttons.append([
                InlineKeyboardButton(
                    f"🎬 {m['title']}{parts_text}",
                    callback_data=f"movie:{m['code']}"
                )
            ])
        
        await message.reply_text(
            f"🔍 Found {len(movies)} results:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN
        )


# ============ TOKEN VERIFICATION ============

async def handle_token_verification(bot: Client, message: Message, token: str, user_id: int):
    """Handle token verification from ad page and send file"""
    
    # Check subscription first
    if not await check_subscription(bot, user_id):
        await send_subscription_message(bot, message, f"token_{token}")
        return
    
    # Show processing
    status = await message.reply_text("⏳ **Verifying your request...**")
    
    # Verify token in database
    token_data = await db.verify_token(token, user_id)
    
    if not token_data:
        await status.edit_text(
            "❌ **Link expired or already used!**\n\n"
            "Please search for the movie again and get a new link.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Get movie details from token
    movie_code = token_data.get("movie_code")
    part = token_data.get("part", 1)
    quality = token_data.get("quality", "")
    
    # Fetch movie from database
    movie = await db.get_movie(movie_code)
    
    if not movie:
        await status.edit_text("❌ **Movie not found!** It may have been removed.")
        return
    
    # Get correct file_id based on part and quality
    file_id = None
    file_size = ""
    
    if part > 1 and "parts_data" in movie:
        part_key = f"part_{part}"
        if part_key in movie["parts_data"]:
            qualities = movie["parts_data"][part_key].get("qualities", {})
            if quality in qualities:
                file_id = qualities[quality].get("file_id")
                file_size = qualities[quality].get("size", "")
    else:
        qualities = movie.get("qualities", {})
        if quality in qualities:
            file_id = qualities[quality].get("file_id")
            file_size = qualities[quality].get("size", "")
    
    if not file_id:
        await status.edit_text("❌ **File not available!** Please try searching again.")
        return
    
    # Update status and send file
    await status.edit_text("📤 **Sending your file...**")
    
    try:
        caption = (
            f"🎬 **{movie['title']}**\n\n"
            f"📦 Part: {part}\n"
            f"🎞️ Quality: {quality}\n"
            f"📁 Size: {file_size}\n\n"
            f"✅ Enjoy your movie!"
        )
        
        # Send cached media (video/document)
        await bot.send_cached_media(
            chat_id=user_id,
            file_id=file_id,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN
        )
        
        await status.delete()
        logger.info(f"✅ File sent to {user_id}: {movie['title']} - {quality}")
        
    except Exception as e:
        logger.error(f"❌ send_cached_media error: {e}")
        
        # Fallback: try sending as document
        try:
            await bot.send_document(
                chat_id=user_id,
                document=file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )
            await status.delete()
            logger.info(f"✅ File sent (document) to {user_id}")
            
        except Exception as e2:
            logger.error(f"❌ Fallback error: {e2}")
            await status.edit_text(
                f"❌ **Error sending file!**\n\n"
                f"Please try again or contact admin.",
                parse_mode=ParseMode.MARKDOWN
            )


# ============ HELPER FUNCTIONS ============

async def send_subscription_message(bot: Client, message: Message, payload: str):
    """Send force subscribe message"""
    await message.reply_text(
        "🔒 **Please Join Our Channel**\n\n"
        "You must join our channel to use this bot.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=Config.BACKUP_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ Done - Try Again", url=f"https://t.me/{bot.me.username}?start={payload}")]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


async def send_welcome(message: Message):
    """Send welcome message"""
    await message.reply_text(
        "🎬 **Welcome to Movie Bot!**\n\n"
        "Send me any movie name to search.\n\n"
        "**Examples:**\n"
        "• Kill Bill\n"
        "• Dune 2021\n"
        "• Avengers Endgame",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=Config.BACKUP_CHANNEL_LINK)]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


async def send_movie_card(bot: Client, message: Message, movie: dict):
    """Send movie info card with download button"""
    info = await get_movie_info(movie["title"])
    
    parts_text = f"\n📦 Parts: {movie['parts']}" if movie.get('parts', 1) > 1 else ""
    
    qualities = movie.get("qualities", {})
    quality_text = ""
    if qualities:
        q_list = []
        for q, data in qualities.items():
            size = data.get("size", "")
            q_list.append(f"{q} ({size})" if size else q)
        quality_text = f"\n🎞️ Available: {', '.join(q_list)}"
    
    if info:
        caption = (
            f"🎬 **{info['title']}** ({info.get('year', '')})\n"
            f"⭐ {info.get('rating', 'N/A')}/10{parts_text}{quality_text}\n\n"
            f"{info.get('overview', '')[:200]}..."
        )
    else:
        caption = f"🎬 **{movie['title']}**{parts_text}{quality_text}"
    
    payload = encode_payload(movie["code"])
    link = f"https://t.me/{bot.me.username}?start={payload}"
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=link)]])
    
    if info and info.get("poster"):
        try:
            await message.reply_photo(info["poster"], caption=caption, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            return
        except:
            pass
    
    await message.reply_text(caption, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def show_quality_selection(message: Message, movie: dict, part: int = 1):
    """Show quality selection buttons"""
    
    if part > 1 and "parts_data" in movie:
        part_key = f"part_{part}"
        qualities = movie["parts_data"].get(part_key, {}).get("qualities", {})
    else:
        qualities = movie.get("qualities", {})
    
    if not qualities:
        await message.reply_text("❌ No qualities available!")
        return
    
    buttons = []
    for quality, data in qualities.items():
        size = data.get("size", "")
        btn_text = f"🎞️ {quality}" + (f" ({size})" if size else "")
        buttons.append([
            InlineKeyboardButton(btn_text, callback_data=f"quality:{movie['code']}:{part}:{quality}")
        ])
    
    await message.reply_text(
        f"🎬 **{movie['title']}**\n\n"
        f"📦 Part: {part}\n\n"
        f"Select quality:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN
    )


async def generate_download_link(bot: Client, message: Message, movie: dict, part: int, quality: str):
    """Generate download link (through ad page if enabled)"""
    user_id = message.from_user.id
    
    # Create token
    token = await db.create_token(user_id, movie["code"], part, quality)
    
    # Get file size
    if part > 1 and "parts_data" in movie:
        part_key = f"part_{part}"
        size = movie["parts_data"].get(part_key, {}).get("qualities", {}).get(quality, {}).get("size", "")
    else:
        size = movie.get("qualities", {}).get(quality, {}).get("size", "")
    
    size_text = f"\n📁 Size: {size}" if size else ""
    
    # Create appropriate link
    if is_monetization_enabled():
        # Through ad page
        final_link = create_ad_link(
            token=token,
            movie_name=movie['title'],
            part=part,
            quality=quality,
            file_size=size,
            bot_username=bot.me.username
        )
        btn_text = "✅ Verify & Get File"
    else:
        # Direct to bot
        final_link = f"https://t.me/{bot.me.username}?start=token_{token}"
        btn_text = "📥 Get File"
    
    await message.reply_text(
        f"✅ **{movie['title']}**\n\n"
        f"📦 Part: {part}\n"
        f"🎞️ Quality: {quality}{size_text}\n\n"
        f"👇 Click to get your file:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(btn_text, url=final_link)]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )