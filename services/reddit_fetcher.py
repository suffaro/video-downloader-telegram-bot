import json
import random
from dataclasses import dataclass
from typing import Optional, Literal
from urllib.parse import urlparse, urlunparse 

import httpx

import config 
from logging_config import logger 
from .downloader import get_redgifs_video_url

@dataclass
class RedditPost:
    """Represents fetched data for a suitable Reddit post."""
    type: Literal['image', 'video'] # type of media found
    url: str                      # direct URL to the media file (or resolved RedGifs URL)
    title: str                    # title of the Reddit post
    permalink: str                # full URL to the Reddit post comments page
    error: Optional[str] = None   # used for returning errors instead of post data

async def fetch_random_reddit_media(
    subreddit: str,
    time_range: Optional[str] = None,
    media_type_filter: Literal['image', 'video', 'both'] = 'both'
) -> RedditPost:
    """
    Fetches a random suitable media post (image or video) from a subreddit's listing.

    Prioritizes non-stickied posts. Handles standard image hosts, Reddit videos (v.redd.it),
    and RedGifs links (by resolving them to direct MP4s). Stops searching and returns
    the first suitable post found after shuffling the listing.

    Args:
        subreddit: The name of the subreddit (without 'r/').
        time_range: Optional sorting time range (e.g., 'day', 'week'). See config.ALLOWED_REDDIT_TIME_RANGES.
                   Defaults to 'hot' listing if None or invalid.
        media_type_filter: Type of media to fetch ('image', 'video', or 'both').

    Returns:
        A RedditPost object containing media info, or a RedditPost object with an error message set.
    """
    headers = {'User-Agent': config.REDDIT_USER_AGENT}
    reddit_api_url: str
    sort_mode_description: str

    valid_time_range = time_range and time_range in config.ALLOWED_REDDIT_TIME_RANGES
    if valid_time_range:
        reddit_api_url = f"https://www.reddit.com/r/{subreddit}/top.json?limit=100&t={time_range}"
        sort_mode_description = f"top ({time_range})"
    else:
        if time_range: 
             logger.warning(f"Invalid time_range '{time_range}' provided for r/{subreddit}. Defaulting to 'hot'.")
        reddit_api_url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=75"
        sort_mode_description = "hot"

    logger.info(f"Fetching Reddit posts from r/{subreddit} ({sort_mode_description}, Filter: {media_type_filter})")
    logger.debug(f"Reddit API URL: {reddit_api_url}")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            response = await client.get(reddit_api_url, headers=headers)

            if response.status_code == 404:
                 logger.warning(f"Subreddit 'r/{subreddit}' not found (404).")
                 return RedditPost(type='image', url='', title='', permalink='', error=f"Subreddit 'r/{subreddit}' not found.")
            if response.status_code == 403:
                 logger.warning(f"Access denied (403) for subreddit 'r/{subreddit}'. It might be private or quarantined.")
                 return RedditPost(type='image', url='', title='', permalink='', error=f"Cannot access 'r/{subreddit}' (private/quarantined?).")
            response.raise_for_status()

            data = response.json()

            if 'data' not in data or 'children' not in data['data'] or not isinstance(data['data']['children'], list):
                logger.warning(f"Unexpected JSON structure received from Reddit API for r/{subreddit}. Keys: {list(data.keys())}")
                return RedditPost(type='image', url='', title='', permalink='', error="Could not parse Reddit response.")

            posts = data['data']['children']
            if not posts:
                logger.warning(f"No posts found in '{sort_mode_description}' listing for r/{subreddit}.")
                return RedditPost(type='image', url='', title='', permalink='', error=f"No posts found in r/{subreddit} ({sort_mode_description}).")
            
            random.shuffle(posts)
            logger.debug(f"Shuffled {len(posts)} posts from r/{subreddit} for processing.")

            for post_container in posts:
                if not isinstance(post_container, dict) or post_container.get('kind') != 't3': continue # ensure it's a post link
                post_data = post_container.get('data', {})
                if not isinstance(post_data, dict): continue

                if post_data.get('stickied', False):
                    logger.debug("Skipping stickied post.")
                    continue

                # Skip posts marked as spoiler or NSFW if needed (currently allowing both)
                # if post_data.get('over_18', False):
                #     logger.debug("Skipping NSFW post (if configured).")
                #     continue
                # if post_data.get('spoiler', False):
                #     logger.debug("Skipping spoiler post (if configured).")
                #     continue

                title = post_data.get('title', 'Untitled Reddit Post')
                permalink_path = post_data.get('permalink', '')
                permalink = f"https://www.reddit.com{permalink_path}" if permalink_path else 'https://www.reddit.com/'
                post_url = post_data.get('url_overridden_by_dest') or post_data.get('url', '')
                domain = post_data.get('domain', '').lower()
                post_hint = post_data.get('post_hint', '').lower() # e.g., 'image', 'hosted:video', 'link'
                is_gallery = post_data.get('is_gallery', False) # skip galleries for now

                if is_gallery:
                     logger.debug(f"Skipping gallery post: '{title}'")
                     continue

                found_type: Optional[Literal['image', 'video']] = None
                media_url_to_use: Optional[str] = None

                # 1. check RedGifs (if video or both allowed)
                if media_type_filter in ['video', 'both'] and "redgifs.com" in domain:
                    logger.debug(f"Processing potential RedGifs link: {post_url} (Post: '{title}')")
                    # use the helper function to resolve the direct video URL
                    direct_video_url = await get_redgifs_video_url(post_url)
                    if direct_video_url:
                        found_type = 'video'
                        media_url_to_use = direct_video_url
                        logger.debug(f"Successfully resolved RedGifs URL: {direct_video_url}")
                    else:
                        logger.warning(f"Failed to resolve RedGifs URL: {post_url} - Skipping post.")
                        continue 

                # 2. check Native Reddit Video (v.redd.it) (if video or both allowed)
                elif not found_type and media_type_filter in ['video', 'both'] and post_data.get('is_video', False):
                    media_info = post_data.get('secure_media') or post_data.get('media')
                    if media_info and isinstance(media_info, dict) and 'reddit_video' in media_info:
                        reddit_video_data = media_info['reddit_video']
                        if isinstance(reddit_video_data, dict):
                             # prefer 'fallback_url' (usually direct MP4), then 'hls_url', then 'dash_url'
                             video_url = reddit_video_data.get('fallback_url') or \
                                         reddit_video_data.get('hls_url') or \
                                         reddit_video_data.get('dash_url')

                             if video_url and isinstance(video_url, str):
                                  media_url_to_use = urlunparse(urlparse(video_url)._replace(query=''))
                                  found_type = 'video'
                                  logger.debug(f"Found Reddit video URL: {media_url_to_use}")
                             else:
                                  logger.warning(f"Post '{title}' marked as video, but no suitable URL found in reddit_video data.")
                        else:
                              logger.warning(f"Post '{title}' marked as video, but 'reddit_video' data is not a dict.")
                    else:
                         logger.debug(f"Post '{title}' marked as video, but no 'secure_media' or 'media' containing 'reddit_video' found.")


                # 3. check Image (i.redd.it, i.imgur.com, or direct link) (if image or both allowed)
                elif not found_type and media_type_filter in ['image', 'both']:
                    is_known_image_domain = domain in ['i.redd.it', 'i.imgur.com']
                    is_direct_image_link = urlparse(post_url).path.lower().endswith(tuple(config.SUPPORTED_IMAGE_EXTENSIONS))
                    is_simple_imgur = domain == 'imgur.com' and '/a/' not in urlparse(post_url).path

                    if (is_known_image_domain or is_direct_image_link or is_simple_imgur or post_hint == 'image'):
                        found_type = 'image'
                        media_url_to_use = post_url
                        logger.debug(f"Found potential image URL: {media_url_to_use}")



                if found_type and media_url_to_use:
                    logger.info(f"Selected first suitable {found_type} post from r/{subreddit}: '{title}'")
                    return RedditPost(type=found_type, url=media_url_to_use, title=title, permalink=permalink)


            filter_msg = f" matching type '{media_type_filter}'" if media_type_filter != 'both' else ""
            logger.warning(f"No suitable posts{filter_msg} found after checking {len(posts)} posts from r/{subreddit} ({sort_mode_description}).")
            return RedditPost(type='image', url='', title='', permalink='', error=f"Couldn't find a suitable post{filter_msg} in r/{subreddit} ({sort_mode_description}). Try a different time range or filter?")

    # catch specific exceptions first, then broader ones
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code} fetching from Reddit r/{subreddit}: {e}")
        return RedditPost(type='image', url='', title='', permalink='', error=f"Error connecting to Reddit (Status {e.response.status_code}).")
    except httpx.RequestError as e:
        # network errors (DNS, connection refused, timeout, etc.)
        logger.error(f"Network error fetching from Reddit r/{subreddit}: {e}")
        return RedditPost(type='image', url='', title='', permalink='', error="Network error connecting to Reddit.")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        # errors parsing the JSON response or accessing expected keys
        # typeError is added here as it was the original error type
        logger.error(f"Error processing Reddit response for r/{subreddit}: {e}", exc_info=True) # log traceback for these
        return RedditPost(type='image', url='', title='', permalink='', error="Error reading response from Reddit.")
    except Exception as e:
        logger.exception(f"Unexpected error fetching from Reddit r/{subreddit}: {e}")
        return RedditPost(type='image', url='', title='', permalink='', error="An unexpected error occurred.")