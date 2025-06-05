import asyncio
import html
from pathlib import Path
from typing import Optional, List
from contextlib import suppress
import tempfile

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest

import config 
from logging_config import logger 

from services.downloader import (
    download_media_yt_dlp,
    download_gallery_dl, 
    DownloadResult,
    NonVideoContentError 
)
from services.media_processing import (
    create_slideshow_video,
    get_audio_duration,
    extract_filename_index, 
    process_video_for_telegram
)

from utils.telegram_helpers import (
    send_downloaded_media,
    _update_loading_message,
    get_user_mention
)
from utils.validation import extract_supported_link_and_text
from utils.file_cleanup import _cleanup_media_files
from utils.user_stats import increment_user_count, UsageContext

async def process_link(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    valid_url: str,
    user_mention: Optional[str] = None, # required for group messages caption
    extra_text: Optional[str] = None, # text after the link in group messages
    reply_to_message: Optional[Message] = None # original message in private chats
) -> None:
    """
    Core logic to download, process, and send media for a given URL.
    Handles loading indicators, error reporting, cleanup, and optional
    slideshow-to-video conversion. Includes loading indicator during sending.
    Uses gallery-dl as a fallback for non-video content detected by yt-dlp.
    """
    processing_msg: Optional[Message] = None
    stop_event = asyncio.Event()
    loading_task: Optional[asyncio.Task] = None
    download_result: Optional[DownloadResult] = None
    all_files_to_cleanup: List[Path] = []
    final_status_message: Optional[str] = None
    media_sent = False
    current_status_text = "Processing link..." # initial status

    try:
        base_processing_text = f"Processing link from {user_mention}" if user_mention else "Processing link..."
        current_status_text = base_processing_text
        logger.debug(f"Sending initial processing message for {valid_url} in chat {chat_id}")

        send_opts = {'text': base_processing_text, 'parse_mode': ParseMode.HTML}
        if reply_to_message: 
            send_opts['reply_to_message_id'] = reply_to_message.message_id

        processing_msg = await context.bot.send_message(chat_id=chat_id, **send_opts)

        if not processing_msg:
            logger.error(f"Failed to send initial processing message for {valid_url} in chat {chat_id}. Aborting.")
            return

        logger.info(f"Sent initial status message {processing_msg.message_id} for {valid_url}")

        loading_task = asyncio.create_task(
            _update_loading_message(processing_msg, current_status_text, 1.0, stop_event)
        )

        try:
            logger.info(f"Starting yt-dlp download for: {valid_url} (Chat: {chat_id}, Msg: {processing_msg.message_id})")
            download_result = await download_media_yt_dlp(valid_url)
            if download_result and download_result.media_files:
                 all_files_to_cleanup.extend(download_result.media_files)

            if not download_result.success:
                if download_result.error_message:
                     final_status_message = f"❌ {download_result.error_message}"
                     logger.warning(f"yt-dlp failed for {valid_url}: {download_result.error_message}")
                else:
                     logger.info(f"yt-dlp indicated failure but no error message for {valid_url}, likely NonVideoContentError raised.")

        except NonVideoContentError as nve:
            logger.warning(f"Caught NonVideoContentError for {valid_url}. Trying gallery-dl fallback. (Original error detail: {nve})")

            slideshow_processing_text = f"Trying alternative download..."
            current_status_text = slideshow_processing_text
            try:
                if loading_task and not loading_task.done():
                    if not stop_event.is_set(): stop_event.set()
                    with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                        await asyncio.wait_for(loading_task, timeout=0.5)
                stop_event.clear()
                await processing_msg.edit_text(current_status_text, parse_mode=ParseMode.HTML)
                loading_task = asyncio.create_task(
                    _update_loading_message(processing_msg, current_status_text, 1.0, stop_event)
                )
                logger.info(f"Updated status message {processing_msg.message_id} for gallery-dl attempt.")
            except Exception as edit_err:
                logger.error(f"Failed to edit processing message {processing_msg.message_id} for gallery-dl attempt: {edit_err}")

            logger.info(f"Starting gallery-dl download for: {valid_url} (Chat: {chat_id}, Msg: {processing_msg.message_id})")
            download_result = await download_gallery_dl(valid_url)
            if download_result and download_result.media_files:
                 all_files_to_cleanup.extend(list(set(download_result.media_files) - set(all_files_to_cleanup)))


            if not download_result.success:
                error_msg = download_result.error_message or "Failed alternative download."
                final_status_message = f"❌ {error_msg}"
                logger.error(f"gallery-dl also failed for {valid_url}: {error_msg}")

            elif config.FFMPEG_AVAILABLE and config.FFMPEG_CONVERT_SLIDESHOW and download_result.media_files:
                images = sorted(
                    [f for f in download_result.media_files if f.suffix.lower() in config.SUPPORTED_IMAGE_EXTENSIONS],
                    key=extract_filename_index 
                )
                audios = [f for f in download_result.media_files if f.suffix.lower() in config.SUPPORTED_AUDIO_EXTENSIONS]

                if images and audios:
                    audio_path = audios[0]
                    logger.info(f"gallery-dl successful. Attempting conversion: {len(images)} images, 1 audio ({audio_path.name}).")

                    conversion_processing_text = f"Converting slideshow to video..."
                    current_status_text = conversion_processing_text
                    try:
                        if loading_task and not loading_task.done():
                           if not stop_event.is_set(): stop_event.set()
                           with suppress(asyncio.CancelledError, asyncio.TimeoutError): await asyncio.wait_for(loading_task, timeout=0.5)
                        stop_event.clear()
                        await processing_msg.edit_text(current_status_text, parse_mode=ParseMode.HTML)
                        loading_task = asyncio.create_task(
                           _update_loading_message(processing_msg, current_status_text, 1.0, stop_event)
                        )
                        logger.info(f"Updated status message {processing_msg.message_id} for video conversion.")
                    except Exception as edit_conv_err:
                        logger.warning(f"Could not update processing message {processing_msg.message_id} for video conversion state: {edit_conv_err}")

                    audio_duration = await get_audio_duration(audio_path)
                    if audio_duration and audio_duration > 0.1:
                        video_filename_stem = Path(audio_path.stem).name
                        video_filename = Path(tempfile.gettempdir()) / f"{config.GENERATED_VIDEO_PREFIX}{video_filename_stem}.mp4"
                        count = 1
                        while video_filename.exists():
                              video_filename = Path(tempfile.gettempdir()) / f"{config.GENERATED_VIDEO_PREFIX}{video_filename_stem}_{count}.mp4"
                              count += 1

                        logger.info(f"Starting ffmpeg conversion. Output: {video_filename.name}")
                        conversion_success = await create_slideshow_video(
                            images, audio_path, video_filename, audio_duration
                        )

                        if conversion_success and video_filename.exists():
                            logger.info(f"Slideshow conversion successful: {video_filename.name}")
                            download_result.media_files = [video_filename]
                            all_files_to_cleanup.append(video_filename) 
                            download_result.is_slideshow = False 
                        else:
                            logger.warning(f"Slideshow video conversion failed for {valid_url}. Sending original images and audio.")
                            if video_filename.exists(): 
                                if video_filename not in all_files_to_cleanup:
                                    all_files_to_cleanup.append(video_filename)
                    else:
                        logger.warning(f"Could not get valid audio duration for {audio_path.name}. Skipping video conversion.")
                else:
                    logger.debug("Not attempting video conversion: Missing images or audio in gallery-dl result.")

        if not final_status_message and download_result and download_result.success and download_result.media_files:
            logger.info(f"Download/Conversion successful for {valid_url}. Processing {len(download_result.media_files)} files for sending.")

            if config.FFMPEG_AVAILABLE: 
                video_to_process = None
                for file_path in download_result.media_files:
                    if file_path.suffix.lower() in config.ALL_SUPPORTED_MEDIA_EXTENSIONS - config.SUPPORTED_IMAGE_EXTENSIONS - config.SUPPORTED_AUDIO_EXTENSIONS:
                        video_to_process = file_path
                        break

                if video_to_process:
                    logger.info(f"Found video file '{video_to_process.name}' for Telegram processing.")
                    processing_video_text = f"Optimizing video for Iphones and Telegram..."
                    current_status_text = processing_video_text
                    try:
                        if loading_task and not loading_task.done():
                           if not stop_event.is_set(): stop_event.set()
                           with suppress(asyncio.CancelledError, asyncio.TimeoutError): await asyncio.wait_for(loading_task, timeout=0.5)
                        stop_event.clear()
                        await processing_msg.edit_text(current_status_text, parse_mode=ParseMode.HTML)
                        loading_task = asyncio.create_task(_update_loading_message(processing_msg, current_status_text, 1.0, stop_event))
                        logger.info(f"Updated status message {processing_msg.message_id} for video optimization.")
                    except Exception as edit_proc_err: logger.warning(f"Could not update message {processing_msg.message_id} for optimization: {edit_proc_err}")

                    processed_filename_stem = f"{video_to_process.stem}{config.PROCESSED_VIDEO_SUFFIX}"
                    processed_video_path = Path(tempfile.gettempdir()) / f"{processed_filename_stem}.mp4"
                    count = 1
                    while processed_video_path.exists():
                          processed_video_path = Path(tempfile.gettempdir()) / f"{processed_filename_stem}_{count}.mp4"
                          count += 1

                    logger.info(f"Starting ffmpeg processing for Telegram. Output: {processed_video_path.name}")
                    processing_success = await process_video_for_telegram(video_to_process, processed_video_path)

                    if processing_success and processed_video_path.exists():
                        logger.info(f"Video processing successful: {processed_video_path.name}")
                        download_result.media_files = [processed_video_path]
                        all_files_to_cleanup.append(processed_video_path)
                    else:
                        logger.warning(f"Video processing failed for {video_to_process.name}. Sending the original video file.")
                        if processed_video_path.exists():
                           if processed_video_path not in all_files_to_cleanup:
                               all_files_to_cleanup.append(processed_video_path)
                else:
                    logger.debug("No video file found in results, skipping Telegram processing step.")
            else:
                 logger.info("FFmpeg not available, skipping Telegram video processing step.")

            sending_text = f"Sending media..."
            current_status_text = sending_text
            try:
                # Restart animation for sending phase
                if loading_task and not loading_task.done():
                    if not stop_event.is_set(): stop_event.set()
                    with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                        await asyncio.wait_for(loading_task, timeout=0.5)
                stop_event.clear()
                await processing_msg.edit_text(current_status_text, parse_mode=ParseMode.HTML)
                loading_task = asyncio.create_task(
                    _update_loading_message(processing_msg, current_status_text, 1.0, stop_event)
                )
                logger.info(f"Updated status message {processing_msg.message_id} to indicate sending.")
            except Exception as edit_err:
                logger.warning(f"Could not edit processing message {processing_msg.message_id} for sending state: {edit_err}")

            # Construct caption
            final_caption = ""
            if user_mention:
                final_caption = f"Sent by {user_mention}"
            if extra_text:
                escaped_extra_text = html.escape(extra_text)
                final_caption += f"\n\n{escaped_extra_text}" if final_caption else escaped_extra_text

            # Send
            logger.info(f"Calling send_downloaded_media for chat {chat_id} with {len(download_result.media_files)} files.")
            media_sent = await send_downloaded_media(
                context,
                chat_id,
                download_result.media_files,
                final_caption.strip() if final_caption else None
            )
            logger.info(f"send_downloaded_media completed for {valid_url}. Success: {media_sent}")

            # Stop animation task after sending
            if loading_task and not loading_task.done():
                logger.debug(f"Stopping loading task after send attempt for {valid_url}.")
                if not stop_event.is_set(): stop_event.set()
                with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(loading_task, timeout=0.5)

            if media_sent: 
                if processing_msg:
                    logger.info(f"Media sent successfully for {valid_url}. Deleting status message {processing_msg.message_id}.")
                    with suppress(Exception): await processing_msg.delete()
                    processing_msg = None
            else: 
                TELEGRAM_MAX_SIZE = 50 * 1000 * 1000 # 50 MB
                logger.error(f"Failed to send media after successful download/conversion for {valid_url}")
                if sum([f.stat().st_size for f in download_result.media_files]) > TELEGRAM_MAX_SIZE:
                    user_ref = user_mention or 'user'
                    final_status_message = f"❌ Failed to send media for {user_ref} (file too large). The problem with Telegram is that it doesn't support files larger than 50 MB. Please try to download the file directly from the source."
                else:      
                    user_ref = user_mention or 'user'
                    final_status_message = f"⚠️ Downloaded successfully, but failed to send media for {user_ref}."
        elif not final_status_message and download_result and not download_result.success:
             logger.error(f"Processing finished for {valid_url}, download result indicates failure but no specific error message was set.")
             user_ref = user_mention or 'user'
             final_status_message = f"❌ Download failed for {user_ref} (unknown reason)."
        elif not final_status_message and not download_result:
             logger.error(f"Processing finished for {valid_url} but download_result is None.")
             user_ref = user_mention or 'user'
             final_status_message = f"❌ Download failed for {user_ref} (internal error)."

    except Exception as e:
        logger.exception(f"Unhandled exception during link processing for {valid_url} in chat {chat_id}")
        user_ref = user_mention or 'user'
        final_status_message = f"⚠️ An unexpected error occurred while processing the link for {user_ref}."
        if loading_task and not loading_task.done() and stop_event and not stop_event.is_set():
            stop_event.set()

    finally:
        logger.debug(f"Entering finally block for {valid_url}. Final status msg: '{final_status_message}'. Media sent: {media_sent}")

        if loading_task:
            if not loading_task.done():
                logger.debug(f"Ensuring loading task is stopped in finally block for {valid_url}")
                if not stop_event.is_set(): stop_event.set()
                loading_task.cancel()
                with suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(loading_task, timeout=1.0)
            elif loading_task.cancelled(): logger.debug(f"Loading task for {valid_url} was already cancelled.")
            elif loading_task.done():
                 exc = loading_task.exception()
                 if exc: logger.warning(f"Loading task for {valid_url} finished with exception: {exc}")

        if final_status_message and not media_sent:
            status_update_text = f"{final_status_message}\nLink: <a href=\"{html.escape(valid_url)}\">{html.escape(valid_url)}</a>"
            if processing_msg: 
                logger.info(f"Updating final status on message {processing_msg.message_id}: {final_status_message}")
                try:
                    await processing_msg.edit_text(status_update_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    logger.debug(f"Edited message {processing_msg.message_id} with final error status.")
                except (BadRequest, TelegramError) as final_edit_err:
                     if "message is not modified" in str(final_edit_err).lower():
                         logger.debug("Message already contained the final error status.")
                     elif "message to edit not found" in str(final_edit_err).lower():
                          logger.warning(f"Processing message {processing_msg.message_id} not found when trying to set final error status.")
                     else:
                          logger.error(f"Failed to edit final status on message {processing_msg.message_id}: {final_edit_err}")
                     if not reply_to_message:
                         logger.warning("Falling back to sending new message for final status.")
                         try: await context.bot.send_message(chat_id, status_update_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                         except Exception as final_send_err: logger.error(f"Also failed to send final status as new msg to {chat_id}: {final_send_err}")
                except Exception as final_edit_err: 
                    logger.error(f"Unexpected error editing final status on message {processing_msg.message_id}: {final_edit_err}")
                    if not reply_to_message:
                         logger.warning("Falling back to sending new message for final status.")
                         try: await context.bot.send_message(chat_id, status_update_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                         except Exception as final_send_err: logger.error(f"Also failed to send final status as new msg to {chat_id}: {final_send_err}")
            else: 
                logger.warning(f"Processing message object lost. Sending final status as new message: {final_status_message}")
                try:
                    await context.bot.send_message(chat_id, status_update_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                except Exception as final_send_err:
                    logger.error(f"Failed to send final status message to chat {chat_id}: {final_send_err}")

        unique_files_to_cleanup = list(set(all_files_to_cleanup))
        await _cleanup_media_files(unique_files_to_cleanup, valid_url)
        logger.debug(f"Finished processing and cleanup for {valid_url}")


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages containing supported links in groups/supergroups."""
    message = update.message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user or not message.text:
        return
    if chat.type not in [Chat.GROUP, Chat.SUPERGROUP]:
        logger.warning("handle_group_message called for non-group chat type.")
        return

    if config.TARGET_GROUP_ID and chat.id != config.TARGET_GROUP_ID:
        return

    message_text = message.text.strip()
    original_message_id = message.message_id
    user_mention = get_user_mention(user)

    valid_url, extra_text = extract_supported_link_and_text(message_text)

    if not valid_url:
        return

    logger.info(f"Detected supported link in group {chat.id} from user {user.id} ({user_mention}). URL: {valid_url}")

    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=original_message_id)
        logger.info(f"Successfully deleted original message {original_message_id} in group {chat.id}.")
    except Forbidden:
        logger.warning(f"Failed to delete message {original_message_id} in group {chat.id}: Bot lacks 'Delete Messages' permission.")
    except BadRequest as e:
        if "message to delete not found" in str(e).lower():
            logger.info(f"Message {original_message_id} already deleted.")
        else:
            logger.warning(f"BadRequest deleting message {original_message_id} in group {chat.id}: {e}")
    except TelegramError as e:
        logger.warning(f"TelegramError deleting message {original_message_id} in group {chat.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error deleting message {original_message_id} in group {chat.id}: {e}", exc_info=True)

    await increment_user_count(user.id, UsageContext.GROUP_LINK)
    await process_link(
        context=context,
        chat_id=chat.id,
        valid_url=valid_url,
        user_mention=user_mention,
        extra_text=extra_text,
        reply_to_message=None
    )

async def handle_private_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages containing supported links in private chats."""
    message = update.message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user or not message.text:
        return
    if chat.type != Chat.PRIVATE:
        logger.warning("handle_private_link called for non-private chat type.")
        return

    message_text = message.text.strip()
    logger.info(f"Processing private message from user {user.id} (@{user.username or 'N/A'})")

    valid_url, _ = extract_supported_link_and_text(message_text)

    if not valid_url:
        if "http" in message_text or any(host in message_text for host in ["instagram", "tiktok", "youtu"]):
            logger.debug(f"Invalid/unsupported link attempt from user {user.id}: {message_text[:100]}")
            await message.reply_text(
                "Sorry, I couldn't recognize a supported link (Instagram, TikTok, YT Shorts) in your message. "
                "Please send the link directly.\n\nSee `/help` for supported sources.",
                quote=True,
                disable_web_page_preview=True
            )
        else:
            logger.debug(f"Ignoring non-link private message from user {user.id}")
        return

    logger.info(f"Detected supported link in private chat from user {user.id}. URL: {valid_url}")

    await increment_user_count(user.id, UsageContext.PRIVATE_LINK)
    await process_link(
        context=context,
        chat_id=chat.id,
        valid_url=valid_url,
        user_mention=None,
        extra_text=None,
        reply_to_message=message
    )