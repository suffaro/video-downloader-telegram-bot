import asyncio
import html
from pathlib import Path
from typing import List, Optional, Tuple
from contextlib import suppress

# --- Telegram Imports ---
from telegram import (
    Update, InputFile, Chat, InputMediaPhoto, User, MessageEntity,
    InputMediaAudio, Message, InputMediaVideo # Added InputMediaVideo
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest

# --- Project Imports ---
import config # For progress bar chars etc.
from logging_config import logger # Use configured logger
# Import specific service/util needed here
from services.media_processing import extract_filename_index # For sorting media group images

# --- Telegram Helper Functions ---

def get_user_mention(user: Optional[User]) -> str:
    """
    Generates a safe HTML mention string for a Telegram user.

    Uses the user's first name if available, otherwise tries username,
    and falls back to User ID. Escapes HTML in names.

    Args:
        user: The Telegram User object.

    Returns:
        An HTML-formatted string (e.g., "<b>John Doe</b>" or "<b>@username</b>").
    """
    if not user:
        logger.warning("get_user_mention called with None user.")
        return "<b>Unknown User</b>"

    # Prefer first_name if available
    user_name = user.first_name
    # Fallback to username if first_name is empty
    if not user_name and user.username:
        # Usernames are generally safe, but we wrap in <b> anyway
        # No need to escape @ symbol for mentions typically
        user_name = f"@{user.username}"
        logger.debug(f"User {user.id} has no first name, using username: {user_name}")
    # Fallback to User ID if neither first_name nor username is available
    elif not user_name:
        user_name = f"User (ID: {user.id})"
        logger.debug(f"User {user.id} has no first name or username, using ID.")

    # Escape the chosen display name to prevent HTML injection if it came from first_name/ID fallback
    safe_display_name = html.escape(str(user_name))

    logger.debug(f"Generated mention for user {user.id}: <b>{safe_display_name}</b>")
    return f"<b>{safe_display_name}</b>"


def get_user_identifier(user: Optional[User]) -> str:
    """
    Gets a user identifier string primarily for logging or simple text captions.

    Prefers @username if available (no HTML escaping needed).
    Falls back to first name (HTML escaped) or User ID string.

    Args:
        user: The Telegram User object.

    Returns:
        A string identifier (e.g., "@username", "John Doe", "User (ID: 12345)").
    """
    if not user:
        return "Unknown User"

    if user.username:
        # Usernames are safe for direct use in text/logs
        return f"@{user.username}"
    elif user.first_name:
        # Escape first name just in case it contains HTML characters
        return html.escape(user.first_name)
    else:
        # Fallback if no username or first name
        return f"User (ID: {user.id})"


async def send_downloaded_media(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    media_files: List[Path],
    base_caption: Optional[str]
) -> bool:
    """
    Sends downloaded media files (images as group, video, audio) to Telegram.

    Handles sending different media types appropriately. Applies caption
    correctly to single items or the first item of a media group.

    Args:
        context: The bot's context object.
        chat_id: The target chat ID.
        media_files: A list of Path objects for the downloaded media.
        base_caption: The base caption text (HTML formatted) to use.

    Returns:
        True if at least one media item was sent successfully, False otherwise.
    """
    if not media_files:
        logger.warning(f"send_downloaded_media called with no media files for chat {chat_id}.")
        return False

    # Separate media types
    # Sort images by filename index for consistent media group order
    images = sorted(
        [f for f in media_files if f.suffix.lower() in config.SUPPORTED_IMAGE_EXTENSIONS],
        key=extract_filename_index
    )
    videos = [f for f in media_files if f.suffix.lower() in config.SUPPORTED_VIDEO_EXTENSIONS]
    audios = [f for f in media_files if f.suffix.lower() in config.SUPPORTED_AUDIO_EXTENSIONS]

    sent_something = False
    media_group_caption_used = False # Track if caption was used in media group

    # --- Timeouts (consider making these configurable) ---
    connect_timeout = 60
    # Allow longer read/write for potentially large files
    read_timeout_media = 300 # For photos, videos, media groups
    write_timeout_media = 300
    read_timeout_audio = 120 # Usually smaller
    write_timeout_audio = 120

    try:
        # --- 1. Send Image(s) ---
        if len(images) > 1:
            # Send as Media Group (max 10 items)
            logger.info(f"Sending media group ({len(images)} images) to chat {chat_id}")
            media_group_items: List[InputMediaPhoto] = []
            # Limit to 10 items per Telegram rules
            images_to_send = images[:10]
            if len(images) > 10:
                 logger.warning(f"Too many images ({len(images)}), sending only the first 10.")

            opened_files = [] # Keep track of files to close explicitly later

            try:
                for i, img_path in enumerate(images_to_send):
                     if not img_path.exists():
                          logger.error(f"Image file not found for media group item: {img_path}. Skipping.")
                          continue
                     # Open file in binary read mode
                     file_obj = open(img_path, 'rb')
                     opened_files.append(file_obj)

                     # Add caption only to the first item in the group
                     caption_to_add = base_caption if i == 0 else None
                     parse_mode_to_use = ParseMode.HTML if caption_to_add else None # Use HTML only if caption exists

                     media_group_items.append(InputMediaPhoto(
                          media=file_obj,
                          caption=caption_to_add,
                          parse_mode=parse_mode_to_use
                     ))
                     if caption_to_add:
                         media_group_caption_used = True # Mark caption as used

                if not media_group_items:
                     logger.error("No valid image files found to create media group.")
                else:
                     await context.bot.send_media_group(
                          chat_id=chat_id,
                          media=media_group_items,
                          read_timeout=read_timeout_media,
                          write_timeout=write_timeout_media,
                          connect_timeout=connect_timeout
                     )
                     logger.info(f"Media group ({len(media_group_items)} items) sent successfully to {chat_id}.")
                     sent_something = True

            finally:
                # Ensure all opened files are closed
                for f in opened_files:
                    if not f.closed:
                        f.close()

        elif len(images) == 1:
            # Send as single photo
            img_path = images[0]
            if not img_path.exists():
                logger.error(f"Single image file not found: {img_path}. Cannot send.")
            else:
                 logger.info(f"Sending single photo '{img_path.name}' to chat {chat_id}")
                 await context.bot.send_photo(
                      chat_id=chat_id,
                      photo=img_path, # Pass Path object directly
                      caption=base_caption,
                      parse_mode=ParseMode.HTML if base_caption else None,
                      read_timeout=read_timeout_media,
                      write_timeout=write_timeout_media,
                      connect_timeout=connect_timeout
                 )
                 logger.info(f"Single photo sent successfully to {chat_id}.")
                 sent_something = True

        # --- 2. Send Video (if exists) ---
        # Currently sends only the first video found if multiple exist
        if videos:
            video_path = videos[0]
            if not video_path.exists():
                 logger.error(f"Video file not found: {video_path}. Cannot send.")
            else:
                 # Add caption only if it wasn't already used by images
                 video_caption = base_caption if not (sent_something or media_group_caption_used) else None
                 logger.info(f"Sending video '{video_path.name}' to chat {chat_id}")

                 # Check if video was likely processed for streaming
                 supports_streaming = config.FFMPEG_AVAILABLE and '+faststart' in str(video_path.name) # Heuristic based on common naming

                 await context.bot.send_video(
                      chat_id=chat_id,
                      video=video_path, # Pass Path object directly
                      caption=video_caption,
                      parse_mode=ParseMode.HTML if video_caption else None,
                      read_timeout=read_timeout_media,
                      write_timeout=write_timeout_media,
                      connect_timeout=connect_timeout,
                      supports_streaming=supports_streaming # Set based on check
                 )
                 logger.info(f"Video sent successfully to {chat_id}.")
                 sent_something = True

        # --- 3. Send Audio (if exists) ---
        # Currently sends only the first audio found
        if audios:
            audio_path = audios[0]
            if not audio_path.exists():
                 logger.error(f"Audio file not found: {audio_path}. Cannot send.")
            else:
                 # Add caption only if nothing else was sent with a caption yet
                 audio_caption = base_caption if not (sent_something or media_group_caption_used) else None
                 logger.info(f"Sending audio '{audio_path.name}' to chat {chat_id}")
                 await context.bot.send_audio(
                      chat_id=chat_id,
                      audio=audio_path, # Pass Path object directly
                      caption=audio_caption,
                      parse_mode=ParseMode.HTML if audio_caption else None,
                      read_timeout=read_timeout_audio,
                      write_timeout=write_timeout_audio,
                      connect_timeout=connect_timeout
                 )
                 logger.info(f"Audio sent successfully to {chat_id}.")
                 sent_something = True

    except FileNotFoundError as e:
        # This might occur if a file is deleted between listing and sending
        logger.error(f"Media file not found during sending attempt to chat {chat_id}: {e}")
        return False # Indicate failure
    except BadRequest as e:
        logger.error(f"Telegram BadRequest sending media to chat {chat_id}: {e}")
        # Provide more specific feedback if possible
        if "can't parse entities" in str(e).lower() and base_caption:
            logger.warning("Potential caption parsing error. Check HTML validity.")
        elif "file is too big" in str(e).lower():
             logger.warning(f"Media file too large for Telegram API limit.") # Size limit depends on method
        elif "media_group_must_contain_2_to_10_items" in str(e):
             logger.error("Media group error: Incorrect number of items prepared.")
        # Add more specific BadRequest checks if needed
        return False # Indicate failure
    except Forbidden as e:
         logger.error(f"Telegram Forbidden error sending media to chat {chat_id}: {e}. Bot might be blocked or lack permissions.")
         return False
    except TelegramError as e:
        # Catch other Telegram API errors (timeouts, network issues, server errors etc.)
        logger.error(f"Telegram API error sending media to chat {chat_id}: {e}", exc_info=True)
        return False # Indicate failure
    except Exception as e:
        # Catch any other unexpected errors during the sending process
        logger.exception(f"Unexpected error sending media to chat {chat_id}: {e}")
        return False # Indicate failure

    if not sent_something:
        logger.warning(f"Completed send_downloaded_media for chat {chat_id}, but nothing was actually sent (files might have been missing or invalid).")

    return sent_something


async def _update_loading_message(
    msg_to_edit: Message,
    initial_text: str,
    interval: float,
    stop_event: asyncio.Event
):
    """
    Internal helper: Periodically updates a message with a simple progress bar animation.

    Edits the message in place until the stop_event is set. Handles common
    Telegram errors during editing gracefully.

    Args:
        msg_to_edit: The Message object to edit.
        initial_text: The base text to display before the progress bar.
        interval: Time in seconds between updates.
        stop_event: An asyncio.Event; when set, the animation loop stops.
    """
    progress_step = 0
    # Ensure initial text doesn't have the code block yet, remove if accidentally included
    base_text = initial_text.split('<code>[')[0].strip()
    logger.debug(f"Starting loading animation task for message {msg_to_edit.message_id} in chat {msg_to_edit.chat_id}. Base text: '{base_text}'")

    while not stop_event.is_set():
        try:
            # Calculate progress bar state
            filled_blocks = progress_step % (config.PROGRESS_BAR_LENGTH + 1)
            empty_blocks = config.PROGRESS_BAR_LENGTH - filled_blocks
            bar = f"[{config.PROGRESS_FILLED_CHAR * filled_blocks}{config.PROGRESS_EMPTY_CHAR * empty_blocks}]"
            text_to_send = f"{base_text} <code>{bar}</code>" # Append code block

            # Avoid editing if the text is identical to prevent "message is not modified" errors
            # Fetch current message text if possible to be sure (might fail if deleted)
            try:
                 current_message_text = msg_to_edit.text # Access cached text
            except AttributeError:
                 # If message object doesn't have text (e.g., only photo), trying to edit text will fail anyway
                 logger.warning(f"Cannot get text from message {msg_to_edit.message_id} to compare for loading update.")
                 current_message_text = None # Assume different

            if current_message_text != text_to_send:
                await msg_to_edit.edit_text(text_to_send, parse_mode=ParseMode.HTML)
                # logger.debug(f"Updated loading message {msg_to_edit.message_id}") # Can be noisy
            # else: logger.debug("Skipping edit: message text identical.")

            progress_step += 1

        except Forbidden:
            logger.warning(f"Permission error: Cannot edit loading message {msg_to_edit.message_id} in chat {msg_to_edit.chat_id}. Stopping animation.")
            break # Stop the loop on permission errors
        except BadRequest as e:
            # Ignore "message is not modified" error specifically
            if "message is not modified" in str(e).lower():
                logger.debug(f"Message {msg_to_edit.message_id} not modified, continuing animation.")
                # Need to increment step even if not modified to keep animation moving
                progress_step += 1
            elif "message to edit not found" in str(e).lower():
                logger.warning(f"Loading message {msg_to_edit.message_id} not found (likely deleted). Stopping animation.")
                break # Stop if message is gone
            else:
                # Log other BadRequests and stop
                logger.error(f"BadRequest error editing loading message {msg_to_edit.message_id}: {e}")
                break # Stop on other bad requests
        except TelegramError as e: # Catch other potential telegram errors (timeouts, etc.)
            logger.error(f"TelegramError editing loading message {msg_to_edit.message_id}: {e}")
            break # Stop on other Telegram errors
        except Exception as e:
            logger.exception(f"Unexpected error updating loading message {msg_to_edit.message_id}: {e}")
            break # Stop animation on unexpected errors

        # Wait for the interval OR until stop_event is set
        try:
            # wait_for will raise TimeoutError if the wait completes without event being set
            # or CancelledError if the task is cancelled externally
            await asyncio.wait_for(stop_event.wait(), timeout=interval*10)
            # If wait() finishes without timeout, it means stop_event was set.
            logger.debug(f"Stop event received for loading message {msg_to_edit.message_id}, exiting loop.")
            break # Exit loop cleanly if event is set
        except asyncio.TimeoutError:
            # This is the normal case: interval passed, continue loop
            pass
        except asyncio.CancelledError:
            logger.debug(f"Loading animation task cancelled externally for message {msg_to_edit.message_id}.")
            break # Exit loop immediately on cancellation

    logger.debug(f"Loading animation task finished for message {msg_to_edit.message_id}.")