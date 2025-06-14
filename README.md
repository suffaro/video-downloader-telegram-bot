# 📹 Video Downloader Telegram Bot

A powerful Telegram bot that allows users to download videos from **YouTube**, **Instagram**, **TikTok**, and browse Reddit content — all directly inside Telegram. Features anonymous Instagram story viewing and handles special content like slideshows and age-restricted TikToks with ease.

## ✨ Features

- 📥 **Video Downloads** from:
  - **YouTube** (various qualities and formats)
  - **Instagram** (posts, reels, IGTV)
  - **TikTok** (including watermark removal)
- 📸 **Instagram Story Viewer** (anonymous viewing with session cookie)
- 🖼️ **Reddit Content Fetcher** via /reddit <subreddit>
- 👥 **Group Integration**: Add the bot to Telegram groups where it will:
  - Automatically detect and process video links
  - Remove original messages containing links
  - Upload processed videos directly to the group
  - **Note**: Bot requires admin rights in the group for message deletion
- 🧩 **Slideshow Handling**: Automatically processes photo slideshows by:
  - Concatenating images using **FFmpeg**
  - Adding original audio (when available)
  - Delivering as a compiled video
- 🔞 **Age-Restricted Content Support**: Handles TikTok videos marked as adult content with valid session cookies
- 🛠️ **Admin-Only Commands**:
  - /stats: View bot usage statistics and analytics
- 💬 **Flexible Input**: Supports both direct links and messages with embedded URLs

## 🧰 Tech Stack

- **Python 3.10+**
- python-telegram-bot - Telegram Bot API wrapper
- yt-dlp - Universal video downloader
- FFmpeg - Media processing and conversion
- requests, aiohttp, pydantic - HTTP clients and data validation
- Additional utility libraries for enhanced functionality

## 📱 Quick Deployment

### ✅ Compatible Platforms:
- **Linux/macOS** (recommended)
- **Windows** (with Python and FFmpeg installed)
- **UserLAnd/Termux on Android** - Deploy directly on your mobile device!

## 🧪 Setup Instructions

### 1. Clone the Repository
```bash
git clone https://github.com/suffaro/video-downloader-telegram-bot.git
cd video-downloader-telegram-bot
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Install FFmpeg
**On Termux:**
```bash
pkg install ffmpeg
```

**On Ubuntu/Debian:**
```bash
sudo apt install ffmpeg
```

**On macOS:**
```bash
brew install ffmpeg
```

### 4. Configure Environment Variables
Rename .env.example to .env and configure:

```env
# Required
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TARGET_GROUP_ID=your_telegram_group # if you have one
BOT_OWNER_ID=your_telegram_id
LOGGING_MODE=0 #check .env.example file

# Optional (for enhanced features)
COOKIE_INSTAGRAM=your_instagram_session_cookie  # For anonymous story viewing
COOKIE_TIKTOK=your_tiktok_session_cookie        # Required for age-restricted content
ADMINS=123456789,987654321                      # Telegram user IDs (comma-separated)
```

### 5. Run the Bot
```bash
python bot.py
```

## 🔧 Usage Guide

### Private Chat:
- **Download Videos**: Send any YouTube, TikTok, or Instagram link
- **Browse Reddit**: /reddit <subreddit_name>
- **View Instagram Stories**: /stories <username> (requires valid session cookie)
- **Admin Statistics**: /stats (admin users only)

### Group Usage:
1. Add the bot to your Telegram group
2. Grant the bot **admin rights** (required for message deletion)
3. The bot will automatically:
   - Detect video links in group messages
   - Process and download the videos
   - Delete the original message with the link
   - Upload the processed video to the group

**Note**: For detailed configuration of link parsing behavior in groups, check the `config.py` file.

## 📁 Project Structure

```
video-downloader-telegram-bot/
├── bot.py                 # Main entry point
├── handlers/              # Telegram command & message handlers
├── services/              # Core download & processing logic
├── utils/                 # Utility modules and helpers
├── cookies/               # Cookies for Tiktok, Instagram
├── config.py              # Environment configuration & link parsing settings
├── logging_config.py      # Logging setup
├── requirements.txt       # Python dependencies
└── .env.example          # Environment template
```

## 📋 Requirements

- Python 3.10 or higher
- FFmpeg installed and accessible via PATH
- Valid Telegram Bot Token
- Telegram API credentials (USER_ID)
- **For group usage**: Admin rights in the target group

## 🔒 Privacy & Security

- The bot processes content locally without storing personal data
- Session cookies are used only for accessing restricted content
- All downloads are temporary and cleaned up automatically
- Group messages are processed automatically but original links are removed for privacy

## ⚠️ Legal Disclaimer

This project is intended for **educational and personal use only**. Users are responsible for:
- Respecting copyright laws and content ownership
- Complying with platform terms of service
- Using downloaded content appropriately and legally

Please ensure you have permission to download and use any content before proceeding.

## 📄 License

This project is provided as-is for educational purposes. Please review and comply with all applicable laws and platform policies.