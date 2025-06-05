import asyncio
import html
import re
import tempfile
import shutil
from pathlib import Path
from contextlib import suppress
import httpx
import yt_dlp
from typing import Literal, Optional 

from telegram import Update, Chat
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest

import config 
from logging_config import logger 

from services.reddit_fetcher import fetch_random_reddit_media

from services.media_processing import process_video_for_streaming
from services.downloader import download_gallery_dl, DownloadResult 

from utils.telegram_helpers import get_user_mention, get_user_identifier
from utils.file_cleanup import _cleanup_media_files 

import config 
from utils.user_stats import increment_user_count, get_all_user_data, get_totals, UsageContext 

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message 

    if not user or not chat or not message:
        logger.warning("Start command received without user, chat, or message.")
        return

    if chat.type == Chat.PRIVATE:
        logger.info(f"User {user.id} ({user.username or 'N/A'}) started the bot in private chat.")
        start_text = (
            "Hello! Send me an Instagram post/reel, TikTok video/slideshow, or YouTube Shorts link to download.\n\n"
            "Use the `/reddit` command to get a random post from a subreddit (see `/help` for details).\n\n"
            "<b>In groups:</b> Add me as an admin with the 'Delete Messages' permission. "
            "I will automatically delete supported links, show a loading indicator, "
            "post the downloaded media (along with any text following the link), and clean up.\n\n"
            "Use `/help` for more command details."
        )
        await message.reply_text(start_text, parse_mode=ParseMode.HTML)
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        logger.info(f"/start command used in group {chat.id} ('{chat.title}') by user {user.id} ({user.username or 'N/A'}).")
        start_text = (
            "Hi there! I'm ready to download media from supported links (Instagram, TikTok, YT Shorts) and Reddit.\n"
            "Use `/help` to see available commands.\n"
            "Please ensure I have administrator permissions, especially 'Delete Messages', for the best experience."
        )
        await message.reply_text(start_text, quote=True) 


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    if user.id not in config.ADMIN_USER_IDS:
        await update.message.reply_text("Sorry, this command is only available to bot administrators.")
        return

    await increment_user_count(user.id, UsageContext.STATS_COMMAND)

    logger.info(f"Admin user {user.id} requested /stats in chat {chat.id}.")
    try:
        all_user_stats = await get_all_user_data()
        totals = await get_totals()

        if not all_user_stats:
            await update.message.reply_text("No usage statistics found yet.")
            return

        sorted_users = sorted(all_user_stats.items(), key=lambda item: item[1].get('call_count', 0), reverse=True)

        total_users = len(sorted_users)
        total_calls = totals.get("all_calls", 0)
        total_private_link = totals.get(UsageContext.PRIVATE_LINK.value, 0)
        total_group_link = totals.get(UsageContext.GROUP_LINK.value, 0)
        total_reddit_private = totals.get(UsageContext.REDDIT_COMMAND_PRIVATE.value, 0)
        total_reddit_group = totals.get(UsageContext.REDDIT_COMMAND_GROUP.value, 0)
        total_stats = totals.get(UsageContext.STATS_COMMAND.value, 0) 

        stats_message = f"📊 **Bot Usage Statistics**\n\n"
        stats_message += f"👤 Users: `{total_users}` | ▶️ Calls: `{total_calls}`\n"
        stats_message += f"   ├ Links: `{total_private_link}` \\(P\\) / `{total_group_link}` \\(G\\)\n"
        stats_message += f"   └ /reddit: `{total_reddit_private}` \\(P\\) / `{total_reddit_group}` \\(G\\)\n"
        stats_message += f"   └ /stats: `{total_stats}`\n\n" 
        stats_message += "📈 **Top Users \\(by total calls\\):**\n"
        stats_message += "`ID        Total   Link P/G   /rd P/G  Last Seen`\n" 
        stats_message += "`-------------------------------------------------`\n"

        max_users_to_show = 15
        for i, (user_id_str, data) in enumerate(sorted_users[:max_users_to_show], 1):
            call_count = data.get('call_count', 0)
            last_seen = data.get('last_seen_iso', 'N/A').split('T')[0]
            contexts = data.get('contexts', {})
            pl = contexts.get(UsageContext.PRIVATE_LINK.value, 0)
            gl = contexts.get(UsageContext.GROUP_LINK.value, 0)
            rp = contexts.get(UsageContext.REDDIT_COMMAND_PRIVATE.value, 0)
            rg = contexts.get(UsageContext.REDDIT_COMMAND_GROUP.value, 0)

            # basic escaping for user ID
            escaped_id = user_id_str.replace('_', r'\_').replace('*', r'\*').replace('[', r'\[').replace(']', r'\]').replace('(', r'\(').replace(')', r'\)').replace('~', r'\~').replace('`', r'\`').replace('>', r'\>').replace('#', r'\#').replace('+', r'\+').replace('-', r'\-').replace('=', r'\=').replace('|', r'\|').replace('{', r'\{').replace('}', r'\}').replace('.', r'\.').replace('!', r'\!')

            stats_message += (
                f"`{escaped_id:<9}` `{call_count:<5}` `{pl:>4}/{gl:<4}` `{rp:>4}/{rg:<4}` `{last_seen}`\n"
            )


        if total_users > max_users_to_show:
             stats_message += "`...`\n"
        stats_message += "`-------------------------------------------------`\n"

        await update.message.reply_text(
            stats_message,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception(f"Error generating or sending stats: {e}")
        await update.message.reply_text("❌ An error occurred while retrieving statistics.")


async def stories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /stories command to download and send Instagram stories individually."""
    message = update.message
    if not message or not message.text:
        return

    command_parts = message.text.split()
    if len(command_parts) < 2:
        await message.reply_text(
            "Please provide an Instagram username.\n"
            "Example: `/stories username`\n"
            "To get the latest N stories: `/stories username 5`"
        )
        return

    nickname = command_parts[1].lstrip('@')
    url = f"https://www.instagram.com/stories/{nickname}/"
    
    num_latest_stories: Optional[int] = None
    if len(command_parts) > 2:
        try:
            num_latest_stories = int(command_parts[2])
            if num_latest_stories <= 0:
                await message.reply_text("Please provide a positive number for the count of stories.")
                return
        except ValueError:
            await message.reply_text("Invalid number for story count. Please use a positive integer.")
            return

    if not message.from_user:
        logger.warning("Received stories command without from_user info.")
        return
    user_id = message.from_user.id
    
    log_message_text = f"User {user_id} requested stories from Instagram user '{nickname}' ({url})."
    if num_latest_stories:
        log_message_text += f" Requesting latest {num_latest_stories}."
    logger.info(log_message_text)

    processing_message_text = f"Attempting to download stories for '{nickname}'..."
    if num_latest_stories:
        processing_message_text = f"Attempting to download the latest {num_latest_stories} stories for '{nickname}'..."
    processing_message = await message.reply_text(processing_message_text + " Please wait.")

    # list to hold all downloaded file paths for cleanup
    all_downloaded_files_for_cleanup: list[Path] = []

    await increment_user_count(user_id, UsageContext.STORY_COMMAND)

    try:
        if not Path(config.INSTAGRAM_COOKIE_PATH).is_file():
            logger.error(f"Instagram cookie file not found: {config.INSTAGRAM_COOKIE_PATH}")
            await processing_message.edit_text(
                "Error: Instagram cookie file is not configured or not found. "
                "Stories download requires authentication."
            )
            return

        download_result: DownloadResult = await download_gallery_dl(url)
        
        if download_result.media_files:
            all_downloaded_files_for_cleanup.extend(download_result.media_files)

        if not download_result.success:
            error_to_show = download_result.error_message or "Failed to download stories. Unknown error."
            logger.error(f"Failed to download stories for {nickname}: {error_to_show}")
            await processing_message.edit_text(error_to_show)
            return

        if not download_result.media_files:
            logger.info(f"No stories found or downloaded for {nickname}.")
            await processing_message.edit_text(f"No stories found for '{nickname}' at the moment, or they have expired.")
            return

        files_to_send = download_result.media_files
        total_downloaded_count = len(files_to_send)

        if num_latest_stories is not None and num_latest_stories < total_downloaded_count:
            logger.info(f"Slicing downloaded stories: keeping latest {num_latest_stories} of {total_downloaded_count}.")
            files_to_send = files_to_send[-num_latest_stories:]
        elif num_latest_stories is not None and num_latest_stories >= total_downloaded_count:
            logger.info(f"Requested {num_latest_stories} latest stories, but only {total_downloaded_count} were available. Sending all.")
        
        if not files_to_send: 
            logger.warning(f"No stories to send for {nickname} after potential slicing (or initial download was empty).")
            await processing_message.edit_text(f"No stories found to send for '{nickname}'.")
            return
        
        await processing_message.edit_text(
            f"Downloaded {total_downloaded_count} story item(s) for '{nickname}'. "
            f"Sending {len(files_to_send)} item(s) individually..."
        )

        sent_count = 0
        for idx, file_path in enumerate(files_to_send):
            caption = None 

            try:
                file_suffix = file_path.suffix.lower()
                logger.debug(f"Attempting to send file: {file_path.name} (Type: {file_suffix})")

                if not isinstance(file_path, Path): 
                    logger.warning(f"File path is not a Path object: {file_path}, type: {type(file_path)}. Attempting to convert.")
                    file_path = Path(str(file_path)) 

                if not file_path.is_file():
                    logger.error(f"File to send does not exist or is not a file: {file_path}")
                    await message.reply_text(f"Sorry, an error occurred: could not find story file {file_path.name}.")
                    continue 

                with open(file_path, 'rb') as f_media:
                    if file_suffix in config.SUPPORTED_IMAGE_EXTENSIONS:
                        await message.reply_photo(photo=f_media, caption=caption)
                    elif file_suffix in config.SUPPORTED_VIDEO_EXTENSIONS:
                        await message.reply_video(video=f_media, caption=caption)
                    else:
                        logger.info(f"Sending file {file_path.name} as document (unrecognized media extension).")
                        await message.reply_document(document=f_media, caption=caption or f"Story item: {file_path.name}")
                
                sent_count += 1
                await asyncio.sleep(0.5) 

            except FileNotFoundError:
                logger.error(f"File not found error for: {file_path}")
                await message.reply_text(f"Sorry, an error occurred: a story file ({file_path.name}) could not be found.")
            except Exception as send_err:
                logger.error(f"Error sending story file {file_path.name} for {nickname}: {send_err}")
                await message.reply_text(f"Sorry, I encountered an error sending one of the story files ({file_path.name}).")
        
        if sent_count > 0:
            final_msg = f"Finished sending {sent_count} story/stories from '{nickname}'."
            if sent_count < len(files_to_send):
                final_msg += f" (Some files may have failed to send)."
            await message.reply_text(final_msg)
        elif files_to_send: 
             await processing_message.edit_text(f"Downloaded stories for '{nickname}' but couldn't send them. Please check bot logs.")

    except FileNotFoundError as fnf_err: 
        logger.critical(f"gallery-dl executable not found: {fnf_err}")
        await processing_message.edit_text(
            "Error: The stories downloader (gallery-dl) is not installed or configured correctly on the server."
        )
    except Exception as e:
        logger.exception(f"An unexpected error occurred in /stories command for {nickname}: {e}")
        await processing_message.edit_text(f"An unexpected error occurred while trying to get stories for '{nickname}'. Please try again later.")
    finally:
        # --- cleanup ---
        if all_downloaded_files_for_cleanup:
            logger.info(f"Cleaning up {len(all_downloaded_files_for_cleanup)} temporary story files for {nickname}.")
            for file_path_to_clean in all_downloaded_files_for_cleanup:
                with suppress(OSError): 
                    if file_path_to_clean.exists(): 
                         logger.debug(f"Deleting temp file: {file_path_to_clean}")
                         file_path_to_clean.unlink()
                    else:
                         logger.debug(f"Temp file already gone or never existed: {file_path_to_clean}")
        else:
            logger.info(f"No files in cleanup list for {nickname}.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides help information about the bot's commands."""
    message = update.message
    if not message: return

    help_text = (
        "ℹ️ <b>Bot Help</b> ℹ️\n\n"
        "I can download media from various sources.\n\n"
        "<b>Supported Links:</b>\n"
        "Simply send a link from Instagram (Posts, Reels), TikTok (Videos, Slideshows), or YouTube Shorts.\n"
        "- In <b>Private Chats</b>, I'll reply with the media.\n"
        "- In <b>Groups</b> (where I have admin rights), I'll delete the original message and post the media.\n\n"
        "<b>Commands:</b>\n"
        "🔹 `/start` - Shows the welcome message.\n"
        "🔹 `/help` - Shows this help message.\n"
        "🔹 `/reddit subreddit [time] [image|video]` - Fetches a random post.\n"
        "   - `subreddit`: Name of the subreddit (e.g., `aww`).\n"
        "   - `[time]` (Optional): Time range (`hour`, `day`, `week`, `month`, `year`, `all`). Defaults to `hot`.\n"
        "   - `[image|video]` (Optional): Filter by media type. Defaults to `both`.\n"
        "   - <i>Example:</i> `/reddit cats week video`\n"
        "🔹 `/stories username` - Download the latest stories for the specified Instagram username.\n"
        "   - <i>Example:</i> `/stories nick` (no spaces)\n"
        "   - To get the latest <b>N</b> stories: `/stories username N`\n"
        "   - <i>Example:</i> `/stories nick 5` (no spaces)\n"
        "🔹 `/suggestion your feedback` - (Private Chat Only) Send a suggestion to the bot owner.\n\n"
        "<i>Note: For slideshows, I'll try to convert them into a video if possible.</i>"
    )
    if not config.BOT_OWNER_ID:
        help_text = help_text.replace("🔹 `/suggestion <your feedback>` - (Private Chat Only) Send a suggestion to the bot owner.\n\n", "")

    await message.reply_text(help_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def suggestion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /suggestion command (private chat only)."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not user or not chat or not message: return
    if chat.type != Chat.PRIVATE:
        logger.info(f"Ignoring /suggestion command in non-private chat {chat.id}")
        return 

    if not config.BOT_OWNER_ID:
        await message.reply_text("Sorry, the suggestion feature is not configured by the bot owner.", quote=True)
        return

    suggestion_text = " ".join(context.args).strip()
    if not suggestion_text:
        await message.reply_text(
            "Please provide your suggestion after the command.\n"
            "Usage: `/suggestion Your feedback text here`",
            parse_mode=ParseMode.MARKDOWN_V2,
            quote=True
        )
        return

    logger.info(f"Received suggestion from user {user.id} ({user.username or 'N/A'}): '{suggestion_text[:50]}...'")

    user_mention_html = get_user_mention(user)
    owner_message = (
        f"📬 <b>Suggestion Received</b>\n\n"
        f"<b>From:</b> {user_mention_html}\n"
        f"<b>User ID:</b> <code>{user.id}</code>\n"
        f"<b>Username:</b> @{user.username or 'N/A'}\n\n"
        f"<b>Suggestion:</b>\n<pre>{html.escape(suggestion_text)}</pre>"
    )

    try:
        await context.bot.send_message(chat_id=config.BOT_OWNER_ID, text=owner_message, parse_mode=ParseMode.HTML)
        logger.info(f"Suggestion successfully forwarded to owner (ID: {config.BOT_OWNER_ID}).")
        await message.reply_text("✅ Thank you! Your suggestion has been sent to the bot owner.", quote=True)
    except Forbidden:
        logger.error(f"Bot is blocked or doesn't have permission to send message to owner ID {config.BOT_OWNER_ID}.")
        await message.reply_text("❌ Error: Could not send the suggestion. The bot might be blocked by the owner.", quote=True)
    except TelegramError as e:
        logger.error(f"Failed to send suggestion to owner {config.BOT_OWNER_ID}: {e}")
        await message.reply_text("❌ An error occurred while sending your suggestion. Please try again later.", quote=True)
    except Exception as e:
        logger.exception(f"Unexpected error forwarding suggestion to owner {config.BOT_OWNER_ID}")
        await message.reply_text("❌ An unexpected error occurred. Please try again later.", quote=True)


async def reddit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /reddit command with optional time range and media type filter."""
    message, chat, user = update.message, update.effective_chat, update.effective_user
    if not all([message, chat, user]):
        return

    original_message_id = message.message_id

    if chat.type != Chat.PRIVATE:
        with suppress(Forbidden, BadRequest):
            await context.bot.delete_message(chat_id=chat.id, message_id=original_message_id)
            logger.info(f"Deleted original /reddit command message {original_message_id} in chat {chat.id}")

    args = context.args or []
    subreddit = time_range = None
    media_type_filter: Literal['image', 'video', 'both'] = 'both' 
    unrecognized_args = []
    media_type_arg_found = False
    remaining_args = []

    if args:
        sub_arg = args[0].lower().removeprefix('r/')
        if re.fullmatch(r'[a-z0-9_]+', sub_arg):
            subreddit, remaining_args = sub_arg, args[1:]
        else:
            if sub_arg in config.ALLOWED_REDDIT_TIME_RANGES and not time_range:
                 time_range = sub_arg
                 remaining_args = args[1:]
            elif sub_arg in ['image', 'video'] and not media_type_arg_found:
                 media_type_filter = sub_arg 
                 media_type_arg_found = True
                 remaining_args = args[1:]
            else:
                 await message.reply_text("Invalid subreddit name. Subreddits can only contain letters, numbers, and underscores.", quote=False)
                 return

    for arg in remaining_args:
        arg_lower = arg.lower()
        if arg_lower in config.ALLOWED_REDDIT_TIME_RANGES:
            if time_range: 
                unrecognized_args.append(arg)
            else:
                time_range = arg_lower
        elif arg_lower in ['image', 'video']:
            if media_type_arg_found:
                unrecognized_args.append(arg)
            else:
                media_type_filter = arg_lower 
                media_type_arg_found = True
        else:
            unrecognized_args.append(arg)

    if not subreddit:
        await message.reply_text(
            "Usage: `/reddit <subreddit> [time] [image|video]`\nExample: `/reddit aww day image`\nUse `/help` for more details.",
            quote=False,
            parse_mode=ParseMode.MARKDOWN_V2 
        )
        return

    if unrecognized_args:
        logger.warning(f"Unrecognized arguments for /reddit command: {unrecognized_args}")



    sort_mode_log = f"top ({time_range})" if time_range else "hot"
    type_filter_log = f"Type: {media_type_filter}"
    logger.info(f"Received /reddit command for r/{subreddit} ({sort_mode_log}, {type_filter_log}) from user {user.id} in chat {chat.id}")

    processing_msg = await context.bot.send_message(chat_id=chat.id, text=f"Searching r/{subreddit} ({sort_mode_log}, {type_filter_log})... Please wait.")

    original_temp_media_path: Optional[Path] = None
    processed_temp_media_path: Optional[Path] = None
    path_to_send: Optional[Path] = None
    temp_download_dir: Optional[tempfile.TemporaryDirectory] = None


    if chat.type == Chat.PRIVATE:
        usage_context = UsageContext.REDDIT_COMMAND_PRIVATE
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        usage_context = UsageContext.REDDIT_COMMAND_GROUP
    else:
        usage_context = UsageContext.OTHER_COMMAND 

    await increment_user_count(user.id, usage_context)

    try:
        post_result = await fetch_random_reddit_media(subreddit, time_range, media_type_filter)

        if post_result.error:
            await processing_msg.edit_text(f"❌ {html.escape(post_result.error)}")
            return

        media_url, media_type, post_title, post_permalink = post_result.url, post_result.type, post_result.title, post_result.permalink

        logger.info(f"Attempting to download {media_type} for r/{subreddit}: {media_url}")
        await processing_msg.edit_text(f"Found {media_type}. Downloading...") 

        download_success = False

        try:
            temp_download_dir = tempfile.TemporaryDirectory(prefix=f"reddit_dl_{subreddit}_")
            temp_dir_path = Path(temp_download_dir.name)

            ydl_opts_reddit = {
                'format': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/bestvideo+bestaudio/best' if media_type == 'video' else 'best[height<=2048]', # Limit image height too?
                'outtmpl': str(temp_dir_path / '%(id)s.%(ext)s'), # Use ID for uniqueness
                'quiet': True,
                'no_warnings': True,
                'ignoreerrors': True,
                'merge_output_format': 'mp4',
                'http_headers': {'User-Agent': config.REDDIT_USER_AGENT}, 
                'max_filesize': 50 * 1024 * 1024, # Optional: Limit download size (Telegram bot limit is 50MB)
                'postprocessors': [] 
            }

            with yt_dlp.YoutubeDL(ydl_opts_reddit) as ydl:
                loop = asyncio.get_running_loop()
                info_dict = await loop.run_in_executor(None, lambda: ydl.extract_info(media_url, download=True))

                if info_dict:
                    potential_files = list(temp_dir_path.glob('*.*'))
                    valid_files = [
                        f for f in potential_files if f.is_file() and
                        f.suffix.lower() in (config.SUPPORTED_VIDEO_EXTENSIONS if media_type == 'video' else config.SUPPORTED_IMAGE_EXTENSIONS)
                    ]

                    if valid_files:
                        downloaded_file = valid_files[0]
                        perm_suffix = downloaded_file.suffix
                        perm_filename = f"reddit_{subreddit}_{media_type}_{Path(downloaded_file.stem).name}{perm_suffix}"
                        original_temp_media_path = Path(tempfile.gettempdir()) / perm_filename
                        count = 1
                        while original_temp_media_path.exists():
                             perm_filename = f"reddit_{subreddit}_{media_type}_{Path(downloaded_file.stem).name}_{count}{perm_suffix}"
                             original_temp_media_path = Path(tempfile.gettempdir()) / perm_filename
                             count += 1

                        shutil.move(str(downloaded_file), original_temp_media_path)
                        download_success = True
                        logger.info(f"{media_type.capitalize()} downloaded via yt-dlp: {original_temp_media_path.name} ({original_temp_media_path.stat().st_size / (1024 * 1024):.2f} MB)")
                    else:
                         logger.warning(f"yt-dlp ran for {media_url} but no valid files found in {temp_dir_path}")
                else:
                     logger.warning(f"yt-dlp extract_info returned None or empty for {media_url}")

        except yt_dlp.utils.DownloadError as ydl_err:
             if 'max_filesize' in str(ydl_err).lower():
                  logger.error(f"Download failed for {media_url}: File exceeded max size limit.")
                  await processing_msg.edit_text(f"❌ Download failed: The {media_type} is too large (>{ydl_opts_reddit['max_filesize']/(1024*1024)}MB).")
                  return
             else:
                  logger.warning(f"yt-dlp DownloadError for Reddit URL {media_url}: {ydl_err}")
        except Exception as ydl_err:
            logger.error(f"Unexpected error during yt-dlp download for {media_url}: {ydl_err}", exc_info=True)
        finally:
            if temp_download_dir:
                with suppress(Exception): 
                    temp_download_dir.cleanup()


        # --- Method 2: Fallback to httpx (simpler, good for direct image links) ---
        if not download_success and media_type == 'image':
            logger.info(f"yt-dlp failed or not used for image, falling back to httpx download: {media_url}")
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                    async with client.stream("GET", media_url, headers={'User-Agent': config.REDDIT_USER_AGENT}, timeout=60.0) as response:
                        response.raise_for_status()

                        content_length = int(response.headers.get('content-length', 0))
                        if content_length > 50 * 1024 * 1024: # Telegram 50MB limit for photos sent by bots
                             logger.error(f"Download failed for {media_url}: Image too large ({content_length / (1024*1024):.1f}MB).")
                             await processing_msg.edit_text("❌ Download failed: The image is too large for Telegram.")
                             return

                        file_ext = Path(httpx.URL(media_url).path).suffix.lower()
                        if not file_ext or file_ext not in config.SUPPORTED_IMAGE_EXTENSIONS:
                             # Guess from content type if possible
                             content_type = response.headers.get('content-type', '').lower()
                             if 'jpeg' in content_type or 'jpg' in content_type: file_ext = '.jpg'
                             elif 'png' in content_type: file_ext = '.png'
                             elif 'webp' in content_type: file_ext = '.webp'
                             else: file_ext = '.jpg' 

                        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext, prefix=f"reddit_{subreddit}_img_") as temp_file:
                            original_temp_media_path = Path(temp_file.name)
                            downloaded_size = 0
                            async for chunk in response.aiter_bytes():
                                temp_file.write(chunk)
                                downloaded_size += len(chunk)
                                if downloaded_size > 50 * 1024 * 1024 + (1024 * 1024): # Allow slight overshoot before aborting
                                     logger.error(f"Download aborted for {media_url}: Image exceeded size limit during download.")
                                     await processing_msg.edit_text("❌ Download failed: The image is too large.")
                                     with suppress(OSError): original_temp_media_path.unlink()
                                     original_temp_media_path = None
                                     return

                            if original_temp_media_path: 
                                 download_success = True
                                 logger.info(f"Image downloaded via httpx: {original_temp_media_path.name} ({downloaded_size / (1024 * 1024):.2f} MB)")

            except httpx.HTTPStatusError as e:
                 logger.error(f"httpx download failed for {media_url}: Status {e.response.status_code}")
                 error_text = f"❌ Download failed (HTTP {e.response.status_code})."
                 if e.response.status_code == 404: error_text += " Media not found?"
                 await processing_msg.edit_text(error_text)
                 return
            except httpx.RequestError as e:
                 logger.error(f"httpx network error downloading {media_url}: {e}")
                 await processing_msg.edit_text("❌ Download failed (Network Error).")
                 return
            except Exception as e:
                 logger.error(f"Unexpected error during httpx download for {media_url}: {e}", exc_info=True)
                 await processing_msg.edit_text("❌ Download failed (Unexpected Error).")
                 return

        if not download_success or not original_temp_media_path or not original_temp_media_path.exists():
            logger.error(f"Failed to download media from {media_url} using all available methods.")
            if processing_msg.text.startswith("Searching") or processing_msg.text.startswith("Found"):
                 await processing_msg.edit_text("❌ Failed to download media content after search.")

        path_to_send = original_temp_media_path 

        if media_type == 'video' and config.FFMPEG_AVAILABLE:
            await processing_msg.edit_text("Processing video...")
            processed_filename = f"reddit_{subreddit}_proc_{original_temp_media_path.stem}.mp4"
            processed_temp_media_path = Path(tempfile.gettempdir()) / processed_filename
            count = 1
            while processed_temp_media_path.exists():
                  processed_filename = f"reddit_{subreddit}_proc_{original_temp_media_path.stem}_{count}.mp4"
                  processed_temp_media_path = Path(tempfile.gettempdir()) / processed_filename
                  count += 1

            if await process_video_for_streaming(original_temp_media_path, processed_temp_media_path):
                logger.info(f"Video processed successfully: {processed_temp_media_path.name}")
                path_to_send = processed_temp_media_path 
            else:
                logger.warning("Video processing (faststart) failed, will send original file.")
                if processed_temp_media_path and processed_temp_media_path.exists():
                    with suppress(OSError): processed_temp_media_path.unlink()
                processed_temp_media_path = None 

        if not path_to_send or not path_to_send.exists():
            logger.error(f"Media path is invalid before sending: {path_to_send}")
            await processing_msg.edit_text("❌ Error preparing file for sending.")
            if original_temp_media_path and original_temp_media_path.exists() and original_temp_media_path != path_to_send:
                 with suppress(OSError): original_temp_media_path.unlink()
            if processed_temp_media_path and processed_temp_media_path.exists() and processed_temp_media_path != path_to_send:
                 with suppress(OSError): processed_temp_media_path.unlink()
            return


        await processing_msg.edit_text(f"Uploading {media_type}...")
        user_id_str = get_user_identifier(user)
        full_command_text = message.text or f"/reddit {subreddit}"

        safe_title = html.escape(post_title)
        safe_command = html.escape(full_command_text)

        send_caption = (
            f"Command: <code>{safe_command}</code> by {user_id_str}\n\n" # use <code> for better display
            f"<b>{safe_title}</b>\n\n"
            f"<a href=\"{post_permalink}\">View on Reddit</a>"
        )

        send_method = context.bot.send_photo if media_type == 'image' else context.bot.send_video
        send_kwargs = {
            'chat_id': chat.id,
            'caption': send_caption,
            'parse_mode': ParseMode.HTML,
            'has_spoiler': True
        }
        send_kwargs.update({'read_timeout': 300, 'write_timeout': 300, 'connect_timeout': 60})

        if media_type == 'image':
            send_kwargs['photo'] = path_to_send 
        else:
            send_kwargs['video'] = path_to_send 
            if path_to_send == processed_temp_media_path:
                send_kwargs['supports_streaming'] = True

        try:
            await send_method(**send_kwargs)
            logger.info(f"Sent Reddit {media_type} from r/{subreddit} (Post: {post_permalink}) to chat {chat.id}")
            with suppress(Exception):
                 await processing_msg.delete()
        except FileNotFoundError:
            logger.error(f"File not found error during sending: {path_to_send}")
            await processing_msg.edit_text("❌ Error: Media file disappeared before sending.")
        except BadRequest as e:
             logger.error(f"Telegram BadRequest during sending {media_type}: {e} (File: {path_to_send.name if path_to_send else 'N/A'})") # Log filename
             error_text = f"❌ Failed to send {media_type} (Telegram Error)."
             if "can't parse entities" in str(e).lower():
                  error_text = "❌ Failed to send: Error parsing caption format."
             elif "file is too big" in str(e).lower():
                 file_size_mb = path_to_send.stat().st_size / (1024 * 1024) if path_to_send.stat() else 'N/A'
                 error_text = f"❌ {media_type.capitalize()} is too large ({file_size_mb:.1f} MB)."
             elif "WEBPAGE_CURL_FAILED" in str(e): 
                  error_text = "❌ Failed to send: Telegram couldn't fetch the link preview (if any)."
             await processing_msg.edit_text(error_text)
        except Forbidden as e:
             logger.error(f"Telegram Forbidden error during sending: {e}")
             await processing_msg.edit_text(f"❌ Failed to send: Bot lacks permission in this chat.")
        except TelegramError as e:
             logger.error(f"Telegram sending error: {e} (File: {path_to_send.name if path_to_send else 'N/A'})")
             await processing_msg.edit_text(f"❌ Failed to send {media_type} (Telegram API Error).")

    except Exception as e:
        logger.exception(f"Unexpected error processing /reddit command for r/{subreddit}")
        if processing_msg:
            try:
                await processing_msg.edit_text("⚠️ An unexpected error occurred.")
            except Exception as final_edit_err:
                 logger.error(f"Failed to edit processing message with final error: {final_edit_err}")
    finally:
        files_to_clean = [original_temp_media_path, processed_temp_media_path]
        logger.debug(f"Running cleanup for /reddit command. Files to check: {[str(f) for f in files_to_clean if f]}")
        await _cleanup_media_files(files_to_clean, f"reddit command r/{subreddit}")