import asyncio
import glob
import json
import random
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse
import re 

import config
from logging_config import logger 

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError, ExtractorError
except ImportError:
    logger.error("yt-dlp library is not installed. Please install it: pip install yt-dlp")
    yt_dlp = None
    DownloadError = Exception # placeholder
    ExtractorError = Exception # placeholder


class NonVideoContentError(Exception):
    """
    Custom exception for when a downloader (like yt-dlp) fails in a way
    suggesting non-standard video content (e.g., photo slideshows on TikTok/Instagram)
    that might require a different tool like gallery-dl.
    Renamed from TikTokPhotoError for broader use.
    """
    pass

@dataclass
class DownloadResult:
    """Holds the results of a download attempt."""
    success: bool = False
    media_files: List[Path] = field(default_factory=list)
    error_message: Optional[str] = None
    is_slideshow: bool = False # flag to indicate if this is a slideshow/gallery download

def extract_filename_index(path: Path) -> int:
    """
    Extracts a leading or trailing number from a filename stem for sorting.
    Handles gallery-dl's default naming like '1.jpg', 'image_01.png', 'prefix_123_abc.ext'.
    Returns a large number if no index is found, placing non-indexed files last.
    """
    stem = path.stem
    match = re.search(r"^(?:(\d+)[_\-\s]?|.*?[_\-\s](\d+))$", stem)
    if match:
        num_str = next((g for g in match.groups() if g is not None), None)
        if num_str:
            try:
                return int(num_str)
            except ValueError:
                pass 
    if stem.isdigit():
         try:
             return int(stem)
         except ValueError:
             pass
    logger.debug(f"Could not extract sort index from filename: {path.name}")
    return 999999



async def download_gallery_dl(url: str) -> DownloadResult:
    """
    Downloads image galleries, slideshows (images + optional audio), or single images
    using gallery-dl. Primarily used as a fallback for TikTok/Instagram slideshows.
    Moves downloaded files out of the temporary directory before returning.
    """
    result = DownloadResult(is_slideshow=True)
    unique_prefix = f"{config.TEMP_DOWNLOAD_PREFIX}gdl_{random.randint(10000, 99999)}"
    moved_files_for_result: List[Path] = []

    parsed_url = urlparse(url)
    hostname = parsed_url.netloc.lower().replace('www.', '')
    is_instagram_url = 'instagram.com' in hostname
    is_tiktok_url = 'tiktok.com' in hostname or 'vt.tiktok.com' in hostname

    try:
        with tempfile.TemporaryDirectory(prefix="gdl_tmp_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            logger.info(f"Attempting gallery-dl download for {url} into temp dir {temp_dir.name}")

            # Command arguments for gallery-dl
            command = [
                config.GALLERY_DL_EXECUTABLE,
                '--directory', str(temp_dir),
                '--write-metadata', 
                '--no-mtime',       
                '--option', 'extractor.tiktok.redirect=true',
                '--verbose', 
            ]

            cookie_file_to_use: Optional[str] = None
            if is_instagram_url and config.INSTAGRAM_COOKIE_PATH:
                 if Path(config.INSTAGRAM_COOKIE_PATH).is_file():
                     cookie_file_to_use = str(config.INSTAGRAM_COOKIE_PATH)
                     logger.debug("Using Instagram cookies for gallery-dl.")
                 else:
                     logger.warning(f"Instagram cookie file not found for gallery-dl: {config.INSTAGRAM_COOKIE_PATH}")
            elif is_tiktok_url and config.TIKTOK_COOKIE_PATH:
                 if Path(config.TIKTOK_COOKIE_PATH).is_file():
                     cookie_file_to_use = str(config.TIKTOK_COOKIE_PATH)
                     logger.debug("Using TikTok cookies for gallery-dl (if needed).")
                 else:
                     logger.warning(f"TikTok cookie file not found for gallery-dl: {config.TIKTOK_COOKIE_PATH}")

            if cookie_file_to_use:
                command.extend(['--cookies', cookie_file_to_use])

            command.append(url) 

            logger.debug(f"Executing gallery-dl command: {' '.join(command)}")

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            stdout_str = stdout.decode(errors='ignore').strip()
            stderr_str = stderr.decode(errors='ignore').strip()

            if stdout_str: logger.debug(f"gallery-dl stdout:\n{stdout_str}")
            if stderr_str: logger.debug(f"gallery-dl stderr:\n{stderr_str}") 

            if process.returncode == 0:
                logger.info(f"gallery-dl potentially completed successfully for {url} (check files).")

                temp_files = list(temp_dir.glob('*.*'))
                if not temp_files:
                     if "warning" in stderr_str.lower() or "nothing to download" in stderr_str.lower():
                         logger.warning(f"gallery-dl ran successfully but reported no new files to download for {url}.")
                         result.error_message = "Gallery/Slideshow: No new media found or already downloaded."
                     else:
                         logger.error(f"gallery-dl exited 0 but found no files in temp dir for {url}. Stderr: {stderr_str}")
                         result.error_message = "Slideshow download succeeded technically but found no media files."
                     return result 

                logger.debug(f"Found {len(temp_files)} potential files in temp dir: {[f.name for f in temp_files]}")

                found_images: List[Path] = []
                found_audio: Optional[Path] = None
                found_videos: List[Path] = []

                for temp_file in temp_files:
                    if not temp_file.is_file() or temp_file.suffix.lower() == '.json': continue 

                    original_stem = temp_file.stem
                    dest_filename = f"{unique_prefix}_{original_stem}{temp_file.suffix}"
                    dest_path = Path(tempfile.gettempdir()) / dest_filename

                    count = 1
                    while dest_path.exists():
                         dest_filename = f"{unique_prefix}_{original_stem}_{count}{temp_file.suffix}"
                         dest_path = Path(tempfile.gettempdir()) / dest_filename
                         count += 1

                    try:
                        shutil.move(str(temp_file), dest_path) 
                        logger.debug(f"Moved temp file '{temp_file.name}' to '{dest_path.resolve()}'")

                        file_suffix_lower = dest_path.suffix.lower()
                        if file_suffix_lower in config.SUPPORTED_IMAGE_EXTENSIONS:
                            found_images.append(dest_path)
                        elif file_suffix_lower in config.SUPPORTED_AUDIO_EXTENSIONS:
                            if found_audio: 
                                logger.warning(f"Multiple audio files found by gallery-dl? Keeping first, discarding extra: {dest_path.name}")
                                with suppress(OSError): dest_path.unlink()
                            else:
                                found_audio = dest_path
                        elif file_suffix_lower in config.ALL_SUPPORTED_MEDIA_EXTENSIONS - config.SUPPORTED_IMAGE_EXTENSIONS - config.SUPPORTED_AUDIO_EXTENSIONS:
                            found_videos.append(dest_path)
                        else:
                            logger.debug(f"Ignoring non-media file moved from gallery-dl: {dest_path.name}")
                            with suppress(OSError): dest_path.unlink()

                    except Exception as move_err:
                        logger.error(f"Failed to move or classify file {temp_file.name} -> {dest_path.name}: {move_err}")

                moved_files_for_result = found_images + found_videos + ([found_audio] if found_audio else [])

                if len(found_images) > 1:
                    logger.debug(f"Attempting to sort {len(found_images)} images based on filename index...")
                    try:
                         found_images.sort(key=extract_filename_index)
                         logger.debug(f"Sorted image order: {[p.name for p in found_images]}")
                         moved_files_for_result = found_images + found_videos + ([found_audio] if found_audio else [])
                    except Exception as sort_err:
                         logger.error(f"Error sorting images: {sort_err}. Proceeding with original discovered order.")
                         moved_files_for_result.sort(key=lambda p: p.name)

                if not moved_files_for_result:
                    logger.warning(f"gallery-dl ran but no valid media files were successfully moved/classified for {url}.")
                    result.error_message = "Slideshow download resulted in no usable media files."
                else:
                    result.success = True
                    result.media_files = moved_files_for_result
                    log_msg = (f"Processed gallery-dl for {url}: "
                               f"{len(found_images)} images, {len(found_videos)} videos, "
                               f"Audio: {'Yes' if found_audio else 'No'}. Files stored.")
                    logger.info(log_msg)

            else:
                logger.error(f"gallery-dl failed for {url} (Code: {process.returncode}).")
                logger.error(f"gallery-dl stderr:\n{stderr_str}")
                err_lower = stderr_str.lower()
                if "404 not found" in err_lower or "unavailable" in err_lower:
                     result.error_message = "Slideshow/Gallery not found or unavailable (404)."
                elif "login required" in err_lower or "authentication required" in err_lower:
                     result.error_message = "Slideshow/Gallery requires login."
                elif "private" in err_lower:
                     result.error_message = "Content is private."
                elif "age restricted" in err_lower:
                     result.error_message = "Content is age-restricted (and login failed/not provided)."
                elif "no supported extractor found" in err_lower:
                     result.error_message = "URL is not supported by gallery-dl."
                else:
                     error_snippet = stderr_str.splitlines()[-1] if stderr_str else "Unknown gallery-dl error"
                     result.error_message = f"Failed to download slideshow/gallery ({error_snippet[:100]}...)"


    except FileNotFoundError:
        logger.critical(f"'{config.GALLERY_DL_EXECUTABLE}' command not found. Please install gallery-dl and check config.")
        result.error_message = "Slideshow/Gallery downloader (gallery-dl) not installed or configured correctly."
    except Exception as e:
        logger.exception(f"Unexpected error during gallery-dl process for {url}: {e}")
        result.error_message = "An unexpected error occurred during slideshow/gallery download."
        logger.warning("Attempting cleanup of potentially moved files due to exception during gallery-dl processing.")
        for f_path in moved_files_for_result: 
            logger.debug(f"Cleaning up (due to exception): {f_path.name}")
            with suppress(OSError): f_path.unlink(missing_ok=True)

    result.media_files = [p.resolve() for p in result.media_files]
    return result


async def download_media_yt_dlp(url: str) -> DownloadResult:
    """
    Downloads media (video/audio) using yt-dlp.
    Raises NonVideoContentError for suspected photo/slideshow content needing gallery-dl.
    Returns absolute paths to downloaded files.
    """
    if not yt_dlp:
         return DownloadResult(error_message="yt-dlp library is not available.")

    result = DownloadResult() 
    unique_prefix = f"{config.TEMP_DOWNLOAD_PREFIX}ytdl_{random.randint(10000, 99999)}"
    output_template = f"{unique_prefix}_%(id)s.%(ext)s"
    output_path_pattern = Path(tempfile.gettempdir()) / output_template

    info_dict = None
    search_pattern_str = ""

    # --- yt-dlp Options ---
    ydl_opts = {
        'format': (
            'bestvideo[ext=mp4][vcodec^=avc][height<=1440]+bestaudio[ext=m4a][acodec^=mp4a]/bestvideo[ext=mp4][vcodec^=avc][height<=1440]+bestaudio[ext=m4a]/'
            'best[ext=mp4][vcodec^=avc][height<=1440]/best[ext=mp4][height<=1440]/'
            'bestvideo[vcodec^=avc]+bestaudio/'
            'bestvideo[height<=1440]+bestaudio/best[height<=1440]/'
            'bestvideo+bestaudio/best'
        ),
        'outtmpl': str(output_path_pattern),
        'quiet': True,
        'no_warnings': False,
        'merge_output_format': 'mp4',
        'ignoreerrors': 'only_download',
        'verbose': False,
        'max_filesize': 250 * 1024 * 1024,
        'postprocessors': [],
    }

    parsed_url = urlparse(url)
    hostname = parsed_url.netloc.lower().replace('www.', '')
    is_tiktok_url = 'tiktok.com' in hostname or 'vt.tiktok.com' in hostname
    is_instagram_url = 'instagram.com' in hostname
        
    cookie_file_to_use: Optional[str] = None
    if is_instagram_url and config.INSTAGRAM_COOKIE_PATH:
        if Path(config.INSTAGRAM_COOKIE_PATH).is_file():
            cookie_file_to_use = str(config.INSTAGRAM_COOKIE_PATH)
            logger.debug(f"Using Instagram cookie file for yt-dlp: {cookie_file_to_use}")
        else:
            logger.warning(f"Instagram cookie file specified but not found: {config.INSTAGRAM_COOKIE_PATH}")
    elif is_tiktok_url and config.TIKTOK_COOKIE_PATH:
        if Path(config.TIKTOK_COOKIE_PATH).is_file():
            cookie_file_to_use = str(config.TIKTOK_COOKIE_PATH)
            logger.debug(f"Using TikTok cookie file for yt-dlp: {cookie_file_to_use}")
        else:
            logger.warning(f"TikTok cookie file specified but not found: {config.TIKTOK_COOKIE_PATH}")

    if cookie_file_to_use:
        ydl_opts['cookiefile'] = cookie_file_to_use

    try:
        logger.info(f"Attempting yt-dlp download for: {url}")
        loop = asyncio.get_running_loop()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )

        if not info_dict:
            logger.warning(f"yt-dlp extract_info returned None for URL: {url}. Raising NonVideoContentError.")
            result.is_slideshow = True
            raise NonVideoContentError(f"yt-dlp returned no info, potentially unsupported content (photo/slideshow?): {url}")

        download_id = info_dict.get('id')
        if not download_id:
            logger.warning(f"Could not retrieve 'id' from info_dict for {url}. File searching may be less reliable.")
            search_pattern_str = str(Path(tempfile.gettempdir()) / f"{unique_prefix}*.*")
        else:
            safe_id = glob.escape(str(download_id))
            search_pattern_str = str(Path(tempfile.gettempdir()) / f"{unique_prefix}_{safe_id}*.*")

        logger.debug(f"Searching for downloaded files using pattern: {search_pattern_str}")
        possible_files = glob.glob(search_pattern_str)

        min_file_size_bytes = 1024
        downloaded_media: List[Path] = []
        merged_file: Optional[Path] = None

        for f_str in possible_files:
            p = Path(f_str)
            try:
                if p.is_file() and p.stat().st_size > min_file_size_bytes:
                    if p.suffix.lower() in config.ALL_SUPPORTED_MEDIA_EXTENSIONS:
                        resolved_path = p.resolve()
                        downloaded_media.append(resolved_path)
                        if p.suffix.lower() == f".{ydl_opts['merge_output_format']}":
                             merged_file = resolved_path
            except FileNotFoundError:
                logger.warning(f"File disappeared during glob processing: {f_str}")
                continue

        if merged_file:
            result.success = True
            result.media_files = [merged_file]
            logger.info(f"yt-dlp successful for {url}. Found final merged file: {merged_file.name}")
            for f in downloaded_media:
                if f != merged_file:
                    logger.debug(f"Cleaning up intermediate file: {f.name}")
                    with suppress(OSError): f.unlink(missing_ok=True)
        elif downloaded_media:
            result.success = True
            result.media_files = sorted(downloaded_media, key=lambda p: p.name)
            logger.info(f"yt-dlp successful for {url}. Found files (no specific merge target found): {[f.name for f in result.media_files]}")
        else:
            logger.error(f"yt-dlp finished for {url} (ID: {download_id}), but no compatible media files found matching pattern '{search_pattern_str}' or files were too small/invalid.")
            log_info_dict_details(info_dict, url)

            if is_tiktok_url or is_instagram_url:
                logger.warning(f"Raising NonVideoContentError for {url} because no valid media files were found after yt-dlp processing.")
                result.is_slideshow = True
                raise NonVideoContentError(f"yt-dlp found no valid media files (likely photo/slideshow or other issue): {url}")
            else:
                result.error_message = "Download failed (no compatible media files found after processing)."

    except NonVideoContentError as e:
        logger.info(f"Propagating NonVideoContentError for {url}.")
        result.is_slideshow = True
        raise e # re-raise the caught exception

    except (DownloadError, ExtractorError) as e:
        err_msg = str(e)
        err_msg_clean = err_msg.split('; please report this issue on')[0].strip()
        logger.error(f"yt-dlp Error occurred for {url}: {err_msg}", exc_info=False)

        err_lower = err_msg.lower()
        unsupported_markers = [
            'unsupported url', 'no supported formats found', 'not a video',
            'are you sure this is a video url?', 'story', 'photo', 'image',
            'graphql error', 'login required', 'this post contains no media',
            'age restricted', 'private content'
        ]
        is_likely_non_video = any(marker in err_lower for marker in unsupported_markers)

        if (is_tiktok_url or is_instagram_url) and is_likely_non_video:
            logger.warning(f"yt-dlp error suggests unsupported/non-video content for {url}. Raising NonVideoContentError.")
            result.is_slideshow = True
            result.error_message = f"Content likely requires login or is not a standard video ({err_msg_clean[:100]}...)."
            raise NonVideoContentError(f"yt-dlp failed with error suggesting unsupported content: {url}") from e

        elif 'private video' in err_lower or 'private account' in err_lower:
             result.error_message = "Download failed (Video/Account is private)."
        elif 'geo-restricted' in err_lower or 'unavailable in your country' in err_lower:
             result.error_message = "Download failed (Video is geo-restricted)."
        elif 'copyright' in err_lower:
              result.error_message = "Download failed (Copyright claim)."
        elif 'max_filesize' in err_lower:
              limit_mb = ydl_opts.get('max_filesize', 0)/(1024*1024)
              result.error_message = f"Download failed (File size exceeds limit: {limit_mb:.0f} MB)."
        elif '404 not found' in err_lower or 'unable to download webpage' in err_lower:
              result.error_message = "Download failed (Content not found - 404 Error)."
        elif 'live event' in err_lower or 'is a live event' in err_lower or 'premiere' in err_lower:
              result.error_message = "Download failed (Livestreams/Premieres not supported)."
        else:
             result.error_message = f"Download failed (yt-dlp: {err_msg_clean[:150]}...)"

    except Exception as e:
        logger.exception(f"Unexpected error during yt-dlp download for {url}: {e}")
        if is_tiktok_url or is_instagram_url:
            logger.warning(f"Unexpected error for {url}, raising NonVideoContentError as fallback attempt.")
            result.is_slideshow = True
            result.error_message = "An unexpected error occurred during download."
            raise NonVideoContentError(f"Unexpected yt-dlp failure for {url}: {e}") from e
        else:
             result.error_message = "An unexpected error occurred during download."

    finally:
        if not result.success and search_pattern_str:
             potential_partials = glob.glob(search_pattern_str)
             if potential_partials:
                  logger.debug(f"Cleaning up potential partial files for failed download of {url} using pattern: {search_pattern_str}")
                  for partial_file in potential_partials:
                     if Path(partial_file).resolve() not in result.media_files:
                         logger.debug(f"Deleting potential partial/temporary file: {partial_file}")
                         with suppress(OSError, FileNotFoundError):
                             Path(partial_file).unlink(missing_ok=True)

    result.media_files = [p.resolve() for p in result.media_files]
    return result


async def get_redgifs_video_url(redgifs_page_url: str) -> Optional[str]:
    """
    Attempts to extract the direct video URL (.mp4) from a RedGifs page URL
    using yt-dlp's JSON dump feature via subprocess.

    Args:
        redgifs_page_url: The URL like https://www.redgifs.com/watch/slug

    Returns:
        The direct .mp4 URL or None if an error occurs or no suitable video found.
    """
    logger.info(f"Attempting to extract RedGifs video URL using yt-dlp JSON dump: {redgifs_page_url}")

    try:
        command = [
            config.YT_DLP_EXECUTABLE,
            '--dump-json',
            '--no-warnings',
            '--quiet',
            '--skip-download',
            '--ignore-config', # avoid interference from global config
            '--no-check-certificate', # sometimes needed for sites like RedGifs
            redgifs_page_url
        ]
        logger.debug(f"Executing yt-dlp command for JSON dump: {' '.join(command)}")

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        stderr_str = stderr.decode(errors='ignore').strip()

        if process.returncode != 0:
             logger.error(f"yt-dlp --dump-json process failed for {redgifs_page_url} (Code: {process.returncode}). Stderr: {stderr_str}")
             return None

        result_json_str = stdout.decode(errors='ignore').strip()
        if not result_json_str:
            logger.warning(f"yt-dlp --dump-json returned empty output for {redgifs_page_url}. Stderr: {stderr_str}")
            return None

        result_json = json.loads(result_json_str)
        if not result_json:
            logger.warning(f"Parsed JSON from yt-dlp is empty for RedGifs URL: {redgifs_page_url}")
            return None

        video_url: Optional[str] = None

        top_level_url = result_json.get('url')
        if isinstance(top_level_url, str) and urlparse(top_level_url).path.lower().endswith('.mp4'):
            video_url = top_level_url
            logger.debug(f"Found MP4 URL in top-level 'url': {video_url}")

        if not video_url:
            formats = result_json.get('formats', [])
            best_mp4_format = None
            max_quality = -1 

            if isinstance(formats, list):
                for f in formats:
                    if not isinstance(f, dict): continue
                    format_url = f.get('url')
                    ext = f.get('ext')
                    vcodec = f.get('vcodec', 'none')
                    if isinstance(format_url, str) and vcodec != 'none' and (ext == 'mp4' or urlparse(format_url).path.lower().endswith('.mp4')):
                        quality = f.get('quality', f.get('height', 0))
                        if isinstance(quality, (int, float)) and quality > max_quality:
                             max_quality = quality
                             best_mp4_format = f
                             logger.debug(f"Found candidate MP4 format: Quality={quality}, URL={format_url}")

            if best_mp4_format:
                video_url = best_mp4_format.get('url')

        if video_url:
            logger.info(f"Successfully extracted RedGifs video URL: {video_url}")
            return video_url
        else:
            logger.warning(f"yt-dlp did not find a suitable .mp4 URL for {redgifs_page_url}.")
            log_info_dict_details(result_json, redgifs_page_url)
            return None

    except FileNotFoundError:
         logger.error(f"'{config.YT_DLP_EXECUTABLE}' command not found for RedGifs JSON dump.")
         return None
    except json.JSONDecodeError as e:
        output_snippet = result_json_str[:500].replace('\n', ' ') if 'result_json_str' in locals() else "N/A"
        logger.error(f"Failed to parse yt-dlp JSON output for {redgifs_page_url}: {e}. Output snippet: '{output_snippet}...'")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error using yt-dlp JSON dump for RedGifs URL {redgifs_page_url}: {e}")
        return None


def log_info_dict_details(info_dict: Optional[dict], url: str):
    """Helper to log relevant details from info_dict for debugging."""
    if not info_dict:
        logger.debug(f"No info_dict provided for URL: {url}")
        return

    keys_to_log = ['id', 'title', 'extractor', 'extractor_key', 'webpage_url', 'original_url',
                   'duration', 'width', 'height', 'fps', 'vcodec', 'acodec', 'ext',
                   'filesize', 'filesize_approx', '_type', 'formats', 'requested_formats',
                   'requested_downloads', '__last_download_stderr', '__last_download_stdout']

    details = {k: info_dict.get(k) for k in keys_to_log if k in info_dict}

    formats_info = "N/A"
    if 'formats' in details and isinstance(details['formats'], list):
        formats_info = f"{len(details['formats'])} formats found. Example: {details['formats'][0].get('format_id', 'N/A')} ({details['formats'][0].get('ext', 'N/A')})" if details['formats'] else "Empty list"
    details['formats_summary'] = formats_info
    if 'formats' in details: del details['formats']

    req_dl_info = "N/A"
    if 'requested_downloads' in details and isinstance(details['requested_downloads'], list):
         req_dl_info = f"{len(details['requested_downloads'])} requested. Filepath: {details['requested_downloads'][0].get('filepath', 'N/A')}" if details['requested_downloads'] else "Empty list"
    details['requested_downloads_summary'] = req_dl_info
    if 'requested_downloads' in details: del details['requested_downloads']

    logger.debug(f"yt-dlp info_dict details for {url}: {details}")