import sys
import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ApplicationBuilder, Defaults
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from utils.user_stats import load_stats, save_stats

import config 
import logging_config 
logger = logging_config.logger 

from services.media_processing import check_ffmpeg

from handlers.command_handlers import start, suggestion, reddit_command, help_command, stats_command, stories_command 
from handlers.message_handlers import handle_group_message, handle_private_link


def escape_markdown_v2(text):
    """Escape special characters for Telegram MarkdownV2"""
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

async def check_and_notify_on_update(application: Application):
    """Checks if the bot version has changed and notifies the target group with changes."""
    logger.info("Checking bot version for update notification.")
    current_version = getattr(config, "BOT_VERSION", "N/A")
    latest_changes = getattr(config, "LATEST_CHANGES", "").strip()
    bot_developer_notes = getattr(config, "BOT_DEVELOPER_NOTES", "No notes provided.").strip()

    if current_version == "N/A":
        logger.warning("BOT_VERSION not defined in config. Skipping update notification.")
        return

    last_notified_version = ""
    version_file = Path(config.LAST_NOTIFIED_VERSION_FILE)

    try:
        version_file.parent.mkdir(parents=True, exist_ok=True)
        if version_file.is_file():
            last_notified_version = version_file.read_text(encoding='utf-8').strip()
            logger.debug(f"Read last notified version '{last_notified_version}' from {version_file}")
    except Exception as e:
        logger.error(f"Could not read last notified version file {version_file}: {e}")

    if current_version != last_notified_version:
        logger.info(f"Version change detected! Current: '{current_version}', Last Notified: '{last_notified_version}'. Sending notification.")
        if config.TARGET_GROUP_ID:
            try:
                
                bot_info = await application.bot.get_me()
                bot_username = bot_info.username
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

                # Escape dynamic content for MarkdownV2
                escaped_username = escape_markdown_v2(bot_username)
                escaped_version = escape_markdown_v2(current_version)
                escaped_timestamp = escape_markdown_v2(timestamp)
                escaped_notes = escape_markdown_v2(config.BOT_DEVELOPER_NOTES.strip())
                
                message_text = (
                    f"🚀 *Bot Updated* ✨\n\n"
                    f"@{escaped_username} is now running version `{escaped_version}`\n"
                    f"Time: `{escaped_timestamp}`\n\n"
                    f"💬 Notes from developer: _{escaped_notes}_\n"
                )

                if latest_changes:
                    message_text += f"\n*Recent Changes:*\n```\n{latest_changes}\n```"

                await application.bot.send_message(
                    chat_id=config.TARGET_GROUP_ID,
                    text=message_text,
                    parse_mode=ParseMode.MARKDOWN_V2

                )
                logger.info(f"Successfully sent update notification to group {config.TARGET_GROUP_ID}")

                try:
                    version_file.write_text(current_version, encoding='utf-8')
                    logger.info(f"Updated last notified version file to '{current_version}'")
                except Exception as e:
                    logger.error(f"Failed to write updated version to {version_file}: {e}")

            except TelegramError as e:
                logger.error(f"Failed to send update notification to group {config.TARGET_GROUP_ID}: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error in update notification hook: {e}")
        else:
            logger.warning("Skipping update notification: TARGET_GROUP_ID not configured.")
    else:
        logger.info(f"Bot version '{current_version}' has not changed. No update notification sent.")

async def initialize_bot(application: Application):
    """Initializes bot components like loading stats and checking version."""
    logger.info("Loading user statistics...")
    await load_stats()
    logger.info("User statistics loaded successfully.")
    

    await check_and_notify_on_update(application)

def main() -> None:
    """Sets up and runs the Telegram bot."""
    logger.info("--- Initializing Bot ---")
    if config.FFMPEG_CONVERT_SLIDESHOW:
        ffmpeg_status = check_ffmpeg()
        config.FFMPEG_AVAILABLE = ffmpeg_status 
        if not config.FFMPEG_AVAILABLE:
            logger.warning("Slideshow-to-video conversion is DISABLED due to missing ffmpeg/ffprobe.")
        else:
            logger.info("FFmpeg/FFprobe found. Slideshow-to-video conversion is ENABLED.")
    else:
        logger.info("Slideshow-to-video conversion is disabled by configuration (FFMPEG_CONVERT_SLIDESHOW=False).")
        config.FFMPEG_AVAILABLE = False 

    defaults = Defaults(parse_mode=ParseMode.HTML)

    async def on_shutdown(application: Application):
        logger.info("Shutdown initiated. Forcing save of user stats...")
        await save_stats(force=True)
        logger.info("User stats saved.")

    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .defaults(defaults)
        .connect_timeout(30)    # general connection timeout
        .read_timeout(30)       # default read timeout 
        .write_timeout(30)      # default write timeout 
        .pool_timeout(60)       # timeout for operations within the connection pool
        .get_updates_connect_timeout(30) 
        .get_updates_read_timeout(45)    
        .post_init(initialize_bot)
        .post_shutdown(on_shutdown) 
        .build()
    )

    # --- register handlers ---
    logger.info("Registering handlers...")

    # commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command)) 
    application.add_handler(CommandHandler("suggestion", suggestion, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("reddit", reddit_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("stories", stories_command, filters=filters.ChatType.PRIVATE))

    # message Handlers for links 
    group_link_filter = (
        filters.TEXT &
        ~filters.COMMAND &
        (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
    )
    application.add_handler(MessageHandler(group_link_filter, handle_group_message))

    private_link_filter = (
        filters.TEXT &
        ~filters.COMMAND &
        filters.ChatType.PRIVATE
    )
    application.add_handler(MessageHandler(private_link_filter, handle_private_link))


    logger.info("--- Bot Handlers Registered ---")
    logger.info("--- Starting Bot Polling ---")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        logger.info("Bot polling stopped manually (KeyboardInterrupt).")
    except TelegramError as e:
        logger.critical(f"Bot polling failed due to TelegramError: {e}")
    except Exception as e:
        logger.critical("Bot polling failed due to unexpected error:", exc_info=True)
    finally:
        logger.info("--- Bot Polling Stopped ---")


if __name__ == "__main__":
    try: #
        main()
    except ValueError as e: 
        logger.critical(f"Configuration Error: {e}")
        sys.exit(1)
    except ImportError as e:
         logger.critical(f"Import Error: {e}. Please ensure all modules are correctly placed and dependencies installed.")
         sys.exit(1)
    except Exception as e:
        logger.exception("An unexpected critical error occurred during bot startup.")
        sys.exit(1)