import re
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

# --- Project Imports ---
import config # For SUPPORTED_HOSTNAMES
from logging_config import logger # Use configured logger

# --- Validation and Extraction Functions ---

def extract_supported_link_and_text(message_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts the first supported media link and any subsequent text from a message.

    Args:
        message_text: The raw text from the Telegram message.

    Returns:
        A tuple containing:
            - The cleaned, validated URL (str) if found, otherwise None.
            - Any text found after the URL (str) if found, otherwise None.
    """
    if not message_text:
        return None, None

    words = message_text.split()
    found_url: Optional[str] = None
    url_part_index: int = -1

    for i, word in enumerate(words):
        # Basic check for scheme before more expensive parsing
        # Allow flexibility, e.g., if user pastes link without scheme but domain is known
        # For simplicity here, require http/https scheme initially.
        # Consider adding logic later to prepend scheme if missing but domain matches.
        if word.startswith("http://") or word.startswith("https://"):
            # Validate the structure and hostname of the potential link
            cleaned_url = validate_link_structure(word)
            if cleaned_url:
                found_url = cleaned_url
                url_part_index = i
                logger.debug(f"Found supported link structure: {found_url}")
                break # Stop after finding the first valid link

    if not found_url:
        # logger.debug("No supported link found in message.")
        return None, None

    # Get text after the URL part
    extra_text: Optional[str] = None
    if url_part_index < len(words) - 1:
        extra_text = " ".join(words[url_part_index + 1:]).strip()
        # Limit length of extra text?
        # max_extra_text_len = 500
        # if len(extra_text) > max_extra_text_len:
        #     extra_text = extra_text[:max_extra_text_len] + "..."
        #     logger.debug(f"Truncated extra text to {max_extra_text_len} chars.")

    logger.debug(f"Extracted URL: {found_url}, Extra Text: '{extra_text if extra_text else ''}'")
    return found_url, extra_text


def validate_link_structure(url: str) -> Optional[str]:
    """
    Checks if a URL has a supported hostname and potentially supported path structure.
    Cleans the URL by removing query parameters and fragments.

    Args:
        url: The input URL string.

    Returns:
        The cleaned URL string if valid and supported, otherwise None.
    """
    try:
        parsed_url = urlparse(url)

        # Basic validation: scheme and network location (domain) must exist
        if not (parsed_url.scheme in ['http', 'https'] and parsed_url.netloc):
            logger.debug(f"URL '{url}' rejected: Missing scheme or network location.")
            return None

        # Check hostname against supported list (case-insensitive)
        normalized_netloc = parsed_url.netloc.lower()
        # Allow www. prefix implicitly
        base_netloc = normalized_netloc.replace('www.', '')

        # Use the configured list of hostnames
        if not any(hostname == base_netloc or hostname == normalized_netloc for hostname in config.SUPPORTED_HOSTNAMES):
            # logger.debug(f"URL '{url}' rejected: Hostname '{normalized_netloc}' not in supported list.")
            return None

        # Optional: Add path structure checks (can be refined)
        # This helps filter out links to profiles, settings pages etc. that are not media.
        path = parsed_url.path.lower()
        is_instagram = 'instagram.com' in normalized_netloc
        is_tiktok = 'tiktok.com' in normalized_netloc or 'vm.tiktok.com' in normalized_netloc
        is_youtube = 'youtube.com' in normalized_netloc or 'youtu.be' in normalized_netloc

        is_supported_path_structure = False
        if is_instagram and any(p in path for p in ['/p/', '/reel/', '/reels/']):
            is_supported_path_structure = True
        elif is_tiktok:
            # Most TikTok paths containing a likely ID are potentially valid video/slideshows.
            # /@username/video/12345... or /@username/photo/12345... or short links vm.tiktok.com/xyz
            # Let downloader handle specific errors like photo mode.
            if '/video/' in path or '/photo/' in path or 'vm.tiktok.com' in normalized_netloc:
                 is_supported_path_structure = True
            # Avoid things like /@username or /tag/something?
            elif not path.startswith('/@') or '/' not in path.strip('/'): # Very basic filter
                 logger.debug(f"Potentially unsupported TikTok path structure: {path}")
                 pass # Still allow for now, downloader will fail if unsupported type
                 is_supported_path_structure = True # Let downloader try
        elif is_youtube:
             # Allow Shorts URLs, youtu.be short links, and potentially standard /watch links (downloader will handle)
             if '/shorts/' in path or 'youtu.be' in normalized_netloc or path.startswith('/watch'):
                  is_supported_path_structure = True

        # If specific path checks are enabled and fail, reject the URL
        # For now, we primarily rely on the hostname check and let downloaders handle specific content types/errors
        # if not is_supported_path_structure:
        #     logger.debug(f"URL '{url}' rejected: Hostname supported, but path structure '{path}' not recognized as media.")
        #     return None

        # Clean URL: remove query parameters and fragment, keep scheme, netloc, path
        cleaned_url = urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, '', '', ''))
        # Remove trailing slash if present for consistency
        if cleaned_url.endswith('/') and len(cleaned_url) > 10: # Avoid removing slash from base domain
             cleaned_url = cleaned_url.rstrip('/')

        return cleaned_url

    except Exception as e:
        # Catch potential errors during URL parsing (e.g., invalid characters)
        logger.warning(f"Error validating URL structure for '{url}': {e}")
        return None