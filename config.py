import os
import logging
from pathlib import Path
from typing import Optional, Final, FrozenSet
from dotenv import load_dotenv
from shutil import which 

load_dotenv()

BOT_VERSION = "1.3"
LAST_NOTIFIED_VERSION_FILE = "last_notified_version.txt"

LATEST_CHANGES = """
\- Increased power efficiency x2 on Termux devices;
\- Added /stories command to watch Instagram stories anonymously; (use /help or /stories for more info)
\- Fixed minor bugs and made other small improvements.
\- Added markdown compatibility for Telegram MarkdownV2 in messages;
"""

BOT_DEVELOPER_NOTES = """
That's probably the last update for this bot, as I don't have time nor ideas to develop it further. 
Нахуй идите ;)
"""

STATS_JSON_PATH = "user_stats.json"

BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "")
TARGET_GROUP_ID_STR: Final[Optional[str]] = os.getenv("TARGET_GROUP_ID")
BOT_OWNER_ID_STR: Final[Optional[str]] = os.getenv("BOT_OWNER_ID")
INSTAGRAM_COOKIE_FILE_STR: Final[Optional[str]] = os.getenv("INSTAGRAM_COOKIE_FILE")
TIKTOK_COOKIE_PATH: Final[Optional[str]] = os.getenv("TIKTOK_COOKIE_FILE")
LOGGING_MODE_STR: Final[str] = os.getenv("LOGGING_MODE", "0") # 0=console, 1=file, 2=both

ADMIN_USER_IDS_STR = f",{BOT_OWNER_ID_STR}"
ADMIN_USER_IDS = {int(admin_id.strip()) for admin_id in ADMIN_USER_IDS_STR.split(',') if admin_id.strip().isdigit()}

LOG_LEVEL: Final = logging.INFO
LOG_FORMAT: Final = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILENAME: Final = Path("bot.log") 

SUPPORTED_IMAGE_EXTENSIONS: Final[FrozenSet[str]] = frozenset(('.jpg', '.jpeg', '.png', '.webp'))
SUPPORTED_VIDEO_EXTENSIONS: Final[FrozenSet[str]] = frozenset(('.mp4', '.mov', '.avi', '.mkv', '.webm'))
SUPPORTED_AUDIO_EXTENSIONS: Final[FrozenSet[str]] = frozenset(('.mp3', '.m4a', '.ogg', '.aac', '.opus', '.wav'))
ALL_SUPPORTED_MEDIA_EXTENSIONS: Final[FrozenSet[str]] = (
    SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS | SUPPORTED_AUDIO_EXTENSIONS
)
TEMP_DOWNLOAD_PREFIX: Final[str] = "media_dl_"
GENERATED_VIDEO_PREFIX: Final[str] = "video_gen_"
PROCESSED_VIDEO_SUFFIX = "_processed"

FFMPEG_PATH_STR: Final[Optional[str]] = os.getenv("FFMPEG_PATH")
FFPROBE_PATH_STR: Final[Optional[str]] = os.getenv("FFPROBE_PATH")
FFMPEG_CONVERT_SLIDESHOW: bool = True # enable/disable slideshow conversion globally

FFMPEG_PATH: Final[str] = FFMPEG_PATH_STR or "ffmpeg"
FFPROBE_PATH: Final[str] = FFPROBE_PATH_STR or "ffprobe"
GALLERY_DL_EXECUTABLE: Final[str] = "gallery-dl" 
YT_DLP_EXECUTABLE: Final[str] = "yt-dlp" 

FFMPEG_AVAILABLE: bool = False


SUPPORTED_HOSTNAMES: Final[FrozenSet[str]] = frozenset((
    'instagram.com', 'www.instagram.com',
    'tiktok.com', 'www.tiktok.com', 'vm.tiktok.com',
    'youtube.com', 'www.youtube.com', 'youtu.be', 'vt.tiktok.com',
))

REDGIFS_API_BASE: Final[str] = "https://api.redgifs.com/v2/gifs/"
ALLOWED_REDDIT_TIME_RANGES: Final[FrozenSet[str]] = frozenset(
    ("hour", "day", "week", "month", "year", "all")
)
REDDIT_USER_AGENT: Final[str] = 'python:telegram-reels-bot:v1.3 (by /u/YourRedditUsername)' # update version/user

PROGRESS_BAR_LENGTH: Final[int] = 10
PROGRESS_FILLED_CHAR: Final[str] = '█'
PROGRESS_EMPTY_CHAR: Final[str] = '░'

_config_logger = logging.getLogger(__name__)
_config_logger.setLevel(logging.INFO)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
if not _config_logger.hasHandlers():
    _config_logger.addHandler(_console_handler)

if not BOT_TOKEN:
    _config_logger.critical("TELEGRAM_BOT_TOKEN not set in environment variables!")
    raise ValueError("TELEGRAM_BOT_TOKEN is required.")

BOT_OWNER_ID: Optional[int] = None
if BOT_OWNER_ID_STR:
    try:
        BOT_OWNER_ID = int(BOT_OWNER_ID_STR)
    except ValueError:
        _config_logger.error("Invalid BOT_OWNER_ID format. It should be an integer.")
else:
    _config_logger.warning("BOT_OWNER_ID not set. /suggestion command disabled.")

TARGET_GROUP_ID: Optional[int] = None
if TARGET_GROUP_ID_STR:
    try:
        TARGET_GROUP_ID = int(TARGET_GROUP_ID_STR)
        _config_logger.info(f"Bot usage restricted to Target Group ID: {TARGET_GROUP_ID}")
    except ValueError:
        _config_logger.error("Invalid TARGET_GROUP_ID format. Bot will respond in any allowed group.")
        TARGET_GROUP_ID = None
else:
    _config_logger.info("No TARGET_GROUP_ID specified. Bot will respond in allowed groups.")

INSTAGRAM_COOKIE_PATH: Optional[Path] = None
if INSTAGRAM_COOKIE_FILE_STR:
    cookie_path = Path(INSTAGRAM_COOKIE_FILE_STR)
    if cookie_path.is_file():
        INSTAGRAM_COOKIE_PATH = cookie_path.resolve()
        _config_logger.info(f"Using Instagram cookie file: {INSTAGRAM_COOKIE_PATH}")
    else:
        _config_logger.warning(f"Instagram cookie file specified but not found: {INSTAGRAM_COOKIE_FILE_STR}")

_ffmpeg_found = which(FFMPEG_PATH)
_ffprobe_found = which(FFPROBE_PATH)
if FFMPEG_CONVERT_SLIDESHOW:
    if not _ffmpeg_found:
        _config_logger.warning(f"ffmpeg command ('{FFMPEG_PATH}') not found or not executable.")
    else:
         _config_logger.info(f"Found ffmpeg executable: {_ffmpeg_found}")
    if not _ffprobe_found:
        _config_logger.warning(f"ffprobe command ('{FFPROBE_PATH}') not found or not executable.")
    else:
        _config_logger.info(f"Found ffprobe executable: {_ffprobe_found}")

# clean up temporary logger handler after validation
_config_logger.removeHandler(_console_handler)