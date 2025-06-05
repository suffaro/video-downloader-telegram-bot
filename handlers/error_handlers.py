import html
import json
import traceback

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import (
    TelegramError
)

import config
from logging_config import logger


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer (if configured)."""
    logger.error(f"Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )
    if config.BOT_OWNER_ID:
        # split the message if it's too long for Telegram
        max_len = 4096
        for i in range(0, len(message), max_len):
            try:
                await context.bot.send_message(
                    chat_id=config.BOT_OWNER_ID,
                    text=message[i:i + max_len],
                    parse_mode=ParseMode.HTML
                )
            except TelegramError as e:
                logger.error(f"Failed to send error message chunk to owner {config.BOT_OWNER_ID}: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error sending error message chunk to owner {config.BOT_OWNER_ID}: {e}", exc_info=True)
    else:
        logger.warning("BOT_OWNER_ID not set, cannot send error notification.")