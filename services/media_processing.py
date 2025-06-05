import asyncio
import json
import os
import tempfile
import re

from contextlib import suppress
from pathlib import Path
from typing import List, Optional
from shutil import which

import config
from logging_config import logger

def check_ffmpeg() -> bool:
    """Checks if ffmpeg and ffprobe executables are accessible in the environment PATH or configured path."""
    ffmpeg_exec = which(config.FFMPEG_PATH)
    ffprobe_exec = which(config.FFPROBE_PATH)

    found_ffmpeg = bool(ffmpeg_exec)
    found_ffprobe = bool(ffprobe_exec)

    if not found_ffmpeg:
        logger.critical(f"ffmpeg command ('{config.FFMPEG_PATH}') not found or not executable.")
    else:
        logger.info(f"Found ffmpeg executable: {ffmpeg_exec}")

    if not found_ffprobe:
        logger.critical(f"ffprobe command ('{config.FFPROBE_PATH}') not found or not executable.")
    else:
         logger.info(f"Found ffprobe executable: {ffprobe_exec}")

    return found_ffmpeg and found_ffprobe


def extract_filename_index(file_path: Path) -> int:
    """
    Extracts a numerical index from a filename stem for sorting.

    Prioritizes numbers after common separators ('_', '-', ' ') at the end of the stem.
    Falls back to any number at the end, then any number within the stem.
    Used primarily for sorting gallery-dl output based on original filenames.

    Returns a large number (99999) if no index is found, placing such files last.
    """
    stem = file_path.stem
    # pattern: separator + digits + end_of_string
    match = re.search(r'[_\s-](\d+)$', stem)
    if match:
        return int(match.group(1))

    # pattern: digits + end_of_string (if no separator pattern matched)
    match = re.search(r'(\d+)$', stem)
    if match:
        return int(match.group(1))

    # pattern: any digits anywhere in the stem (less reliable, used as last resort)
    match = re.search(r'(\d+)', stem)
    if match:
         logger.debug(f"Extracting index from general digits in stem '{stem}': {match.group(1)}")
         return int(match.group(1))

    logger.warning(f"Could not extract numerical index from filename stem: '{stem}'. Using default sort value.")
    return 99999 


async def get_audio_duration(audio_path: Path) -> Optional[float]:
    """
    Gets the duration of an audio file in seconds using ffprobe.

    Returns:
        Duration in seconds (float) or None if ffprobe fails or duration not found.
    """
    if not config.FFMPEG_AVAILABLE:
        logger.warning("Cannot get audio duration: ffprobe is not available.")
        return None
    if not audio_path.exists():
        logger.error(f"Cannot get duration: Audio file not found at {audio_path}")
        return None

    command = [
        config.FFPROBE_PATH,
        '-v', 'quiet',             
        '-print_format', 'json',   
        '-show_format',            
        '-show_streams',           
        str(audio_path.resolve()) 
    ]
    logger.debug(f"Running ffprobe to get duration: {' '.join(command)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        stderr_str = stderr.decode(errors='ignore').strip()

        if process.returncode != 0:
            logger.error(f"ffprobe failed for {audio_path.name} (Code: {process.returncode}). Stderr: {stderr_str}")
            return None

        stdout_str = stdout.decode(errors='ignore').strip()
        if not stdout_str:
             logger.error(f"ffprobe returned empty stdout for {audio_path.name}.")
             return None

        data = json.loads(stdout_str)
        duration = None

        if 'format' in data and isinstance(data['format'], dict) and 'duration' in data['format']:
            try:
                duration = float(data['format']['duration'])
                logger.debug(f"Found duration in format section: {duration}s for {audio_path.name}")
            except (ValueError, TypeError):
                 logger.warning(f"Could not parse duration from format section for {audio_path.name}: {data['format'].get('duration')}")

        if duration is None and 'streams' in data and isinstance(data['streams'], list):
             for stream in data['streams']:
                  if isinstance(stream, dict) and stream.get('codec_type') == 'audio' and 'duration' in stream:
                       try:
                            duration = float(stream['duration'])
                            logger.debug(f"Found duration in audio stream section: {duration}s for {audio_path.name}")
                            break
                       except (ValueError, TypeError):
                            logger.warning(f"Could not parse duration from stream section for {audio_path.name}: {stream.get('duration')}")

        if duration is not None and duration > 0:
            logger.info(f"Successfully obtained audio duration for {audio_path.name}: {duration:.3f} seconds")
            return duration
        else:
             logger.error(f"Could not find valid duration information in ffprobe output for {audio_path.name}.")
             logger.debug(f"ffprobe JSON output (abbreviated): {str(data)[:500]}")
             return None

    except FileNotFoundError:
        logger.critical(f"ffprobe command ('{config.FFPROBE_PATH}') not found during execution. Disabling FFmpeg features.")
        config.FFMPEG_AVAILABLE = False
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing ffprobe JSON output for {audio_path.name}: {e}. Output was: '{stdout_str[:500]}...'")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error getting audio duration for {audio_path.name}: {e}")
        return None


async def create_slideshow_video(
    image_paths: List[Path],
    audio_path: Path,
    output_path: Path,
    duration_secs: float
) -> bool:
    """
    Creates a video from a list of images and an audio file using ffmpeg.

    Uses the concat demuxer with explicit durations per image for better timing control.
    Aims for the final video duration to match the provided audio duration.

    Args:
        image_paths: Sorted list of absolute paths to input images.
        audio_path: Absolute path to the input audio file.
        output_path: Absolute path for the output video file.
        duration_secs: Target duration for the video (should match audio).

    Returns:
        True if video creation was successful, False otherwise.
    """
    if not config.FFMPEG_AVAILABLE:
        logger.warning("Cannot create slideshow video: ffmpeg/ffprobe not available.")
        return False
    if not image_paths:
        logger.error("Cannot create slideshow video: No image paths provided.")
        return False
    if not audio_path.exists():
         logger.error(f"Cannot create slideshow video: Audio file not found at {audio_path}")
         return False
    if duration_secs <= 0:
        logger.error(f"Cannot create slideshow video: Invalid target duration ({duration_secs}s).")
        return False

    num_images = len(image_paths)
    duration_per_image = max(0.001, duration_secs / num_images)
    logger.debug(f"Target duration: {duration_secs:.3f}s. Images: {num_images}. Calculated duration per image: {duration_per_image:.5f}s")

    temp_list_file = None
    try:
        fd, list_filename_str = tempfile.mkstemp(suffix=".txt", prefix="ffmpeg_imagelist_")
        os.close(fd) 
        temp_list_file = Path(list_filename_str)
        logger.debug(f"Creating temporary image list file: {temp_list_file}")

        with open(temp_list_file, "w", encoding='utf-8') as f:
            f.write("ffconcat version 1.0\n") # required header for ffconcat
            for img_path in image_paths:
                safe_path_str = str(img_path.resolve()).replace("\\", "/")
                f.write(f"file '{safe_path_str}'\n")
                f.write(f"duration {duration_per_image:.5f}\n")
                last_img_path = image_paths[-1]
                last_safe_path_str = str(last_img_path.resolve()).replace("\\", "/")
                f.write(f"file '{last_safe_path_str}'\n")

        command = [
            config.FFMPEG_PATH,
            '-y',                   # Overwrite output file if it exists
            '-f', 'concat',         # Use the concat demuxer
            '-safe', '0',           # Allow complex paths in list file (use cautiously)
            '-i', str(temp_list_file.resolve()), # Input image list with durations
            '-i', str(audio_path.resolve()),     # Input audio file
            '-c:v', 'libx264',      # Widely compatible H.264 codec
            '-preset', 'medium',    # Balance speed and quality (faster, fast, medium, slow, slower)
            '-tune', 'stillimage',  # Optimize encoding for sequences of static images
            '-pix_fmt', 'yuv420p',  # Common pixel format for compatibility
            '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', # Ensure dimensions are divisible by 2
            '-c:a', 'aac',          # Common AAC audio codec
            '-b:a', '192k',         # Decent audio bitrate
            '-vsync', 'vfr',        # Variable Frame Rate often works well with concat
            '-shortest',            # Finish encoding when the shortest input (audio) ends
            str(output_path.resolve()) # Output video file path
        ]

        logger.info(f"Starting ffmpeg slideshow creation: Output '{output_path.name}'")
        logger.debug(f"Executing ffmpeg command: {' '.join(command)}")

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        stderr_str = stderr.decode(errors='ignore').strip()

        if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 100: # Check size > 100 bytes
            output_duration = await get_audio_duration(output_path)
            if output_duration:
                duration_diff = abs(output_duration - duration_secs)
                if duration_diff > 0.5: # allow 0.5s tolerance
                    logger.warning(f"Output video duration ({output_duration:.2f}s) differs significantly from target audio duration ({duration_secs:.2f}s). Diff: {duration_diff:.2f}s")
                else:
                    logger.debug(f"Output video duration check OK ({output_duration:.2f}s vs target {duration_secs:.2f}s).")
            else:
                 logger.warning(f"Could not verify output video duration for {output_path.name}")

            logger.info(f"Successfully created slideshow video: {output_path.name}")
            return True
        else:
            logger.error(f"ffmpeg failed to create slideshow video '{output_path.name}' (Code: {process.returncode}).")
            logger.error(f"ffmpeg stderr:\n{stderr_str}")
            with suppress(FileNotFoundError, OSError):
                if output_path.exists(): output_path.unlink()
            return False

    except FileNotFoundError:
        logger.critical(f"ffmpeg command ('{config.FFMPEG_PATH}') not found during execution. Disabling FFmpeg features.")
        config.FFMPEG_AVAILABLE = False
        return False
    except Exception as e:
        logger.exception(f"Unexpected error creating slideshow video '{output_path.name}': {e}")
        with suppress(FileNotFoundError, OSError):
            if output_path.exists(): output_path.unlink()
        return False
    finally:
        if temp_list_file and temp_list_file.exists():
            logger.debug(f"Deleting temporary image list file: {temp_list_file}")
            with suppress(FileNotFoundError, OSError):
                temp_list_file.unlink()


async def process_video_for_streaming(input_path: Path, output_path: Path) -> bool:
    """
    Re-muxes a video file using ffmpeg to move the 'moov' atom to the beginning.

    This optimizes the video for streaming/web playback ('faststart').
    Uses '-c copy' for speed (no re-encoding).

    Args:
        input_path: Path to the original video file.
        output_path: Path where the processed video should be saved.

    Returns:
        True if processing was successful and output file exists, False otherwise.
    """
    if not config.FFMPEG_AVAILABLE:
        logger.warning("Cannot process video for streaming: ffmpeg not available.")
        return False
    if not input_path.exists():
         logger.error(f"Cannot process video for streaming: Input file not found at {input_path}")
         return False

    command = [
        config.FFMPEG_PATH,
        '-y',                   # overwrite output file if it exists
        '-i', str(input_path.resolve()), # input file path
        '-c', 'copy',           # copy video and audio streams (no re-encoding)
        '-movflags', '+faststart', # move moov atom to the beginning
        '-loglevel', 'warning', 
        str(output_path.resolve()) 
    ]

    logger.info(f"Processing video for streaming ('faststart'): {input_path.name} -> {output_path.name}")
    logger.debug(f"Executing ffmpeg command: {' '.join(command)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        stderr_str = stderr.decode(errors='ignore').strip()

        if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 100:
            logger.info(f"Video successfully processed for streaming: {output_path.name}")
            return True
        else:
            logger.error(f"ffmpeg failed to process video '{input_path.name}' for streaming (Code: {process.returncode}). Output exists: {output_path.exists()}, Size: {output_path.stat().st_size if output_path.exists() else 'N/A'}")
            logger.error(f"ffmpeg stderr:\n{stderr_str}")
            with suppress(FileNotFoundError, OSError):
                if output_path.exists(): output_path.unlink()
            return False

    except FileNotFoundError:
        logger.critical(f"ffmpeg command ('{config.FFMPEG_PATH}') not found during execution. Disabling FFmpeg features.")
        config.FFMPEG_AVAILABLE = False
        return False
    except Exception as e:
        logger.exception(f"Unexpected error processing video {input_path.name} for streaming: {e}")
        with suppress(FileNotFoundError, OSError):
            if output_path.exists(): output_path.unlink()
        return False
    

async def process_video_for_telegram(input_path: Path, output_path: Path) -> bool:
    """
    Re-encodes a video using H.264 Baseline/AAC for Telegram compatibility and streaming.
    
    Uses "7_H264_Baseline_Ultrafast_FS" profile with settings:
    - libx264 baseline profile, level 3.0
    - ultrafast preset, CRF 28
    - AAC 128k audio
    - faststart for streaming

    Args:
        input_path: Path to the original video file.
        output_path: Path where the processed video should be saved.

    Returns:
        True if processing was successful and output file exists, False otherwise.
    """

    if not config.FFMPEG_AVAILABLE:
        logger.warning("Cannot process video for Telegram: ffmpeg not available.")
        return False
    if not input_path.exists():
         logger.error(f"Cannot process video for Telegram: Input file not found at {input_path}")
         return False

    command = [
        config.FFMPEG_PATH,              
        '-i', str(input_path.resolve()), # Input file path
        '-max_muxing_queue_size', '9999', # Option and its value
        '-c:v', 'libx264',            # Video codec option and its value
        '-crf', '28',                 # CRF option and its value
        '-maxrate', '4.5M',           # Maxrate option and its value
        '-preset', 'faster',          # Preset option and its value
        '-flags', '+global_header',   # Flags option and its value
        '-pix_fmt', 'yuv420p',        # Pixel format option and its value
        '-profile:v', 'baseline',     # Video profile option and its value
        '-movflags', '+faststart',    # MOV flags option and its value
        '-c:a', 'aac',                # Audio codec option and its value
        '-ac', '2',                   # A # Move moov atom to the beginning for streaming
        '-loglevel', 'warning',   # Show warnings and errors
    ]
    
    command.append(str(output_path.resolve()))

    logger.info(f"Processing video for Telegram compatibility: {input_path.name} -> {output_path.name}")
    logger.debug(f"Executing ffmpeg command: {' '.join(command)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        stderr_str = stderr.decode(errors='ignore').strip()

        if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 100: #check size > 100 bytes
            logger.info(f"Video successfully processed for Telegram: {output_path.name}")
            return True
        else:
            logger.error(f"ffmpeg failed to process video '{input_path.name}' for Telegram (Code: {process.returncode}). Output exists: {output_path.exists()}, Size: {output_path.stat().st_size if output_path.exists() else 'N/A'}")
            logger.error(f"ffmpeg stderr:\n{stderr_str}")
            with suppress(FileNotFoundError, OSError):
                if output_path.exists(): output_path.unlink()
            return False

    except FileNotFoundError:
        logger.critical(f"ffmpeg command ('{config.FFMPEG_PATH}') not found during execution. Disabling FFmpeg features.")
        config.FFMPEG_AVAILABLE = False
        return False
    except Exception as e:
        logger.exception(f"Unexpected error processing video {input_path.name} for Telegram: {e}")
        with suppress(FileNotFoundError, OSError):
            if output_path.exists(): output_path.unlink()
        return False