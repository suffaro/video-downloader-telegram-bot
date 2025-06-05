# user_stats.py
import json
import logging
from pathlib import Path
import asyncio
import time
from datetime import datetime
from contextlib import suppress
from enum import Enum

import config

logger = logging.getLogger(__name__)

STATS_FILE = Path(config.STATS_JSON_PATH)
_stats_lock = asyncio.Lock()
_stats_data = {}
_last_save_time = 0
SAVE_INTERVAL = 60

class UsageContext(Enum):
    PRIVATE_LINK = "private_link"
    GROUP_LINK = "group_link"
    REDDIT_COMMAND_PRIVATE = "reddit_command_private"
    REDDIT_COMMAND_GROUP = "reddit_command_group"
    STATS_COMMAND = "stats_command"
    OTHER_COMMAND = "other_command"
    STORY_COMMAND = "story_command"
    OTHER = "other"

# Helper to get all context keys for initialization/totals
def _get_all_context_keys():
    return [context.value for context in UsageContext]

async def load_stats():
    global _stats_data, _last_save_time
    async with _stats_lock:
        # Default empty totals structure
        default_totals = {key: 0 for key in _get_all_context_keys()}
        default_totals["all_calls"] = 0

        try:
            if STATS_FILE.is_file():
                with open(STATS_FILE, 'r', encoding='utf-8') as f:
                    _stats_data = json.load(f)
                    logger.info(f"Loaded user stats from {STATS_FILE}")
            else:
                _stats_data = {"users": {}, "totals": default_totals.copy()}
                logger.info(f"Stats file {STATS_FILE} not found, starting fresh.")

            # Ensure base structures exist and add new total keys if missing
            if "users" not in _stats_data:
                _stats_data["users"] = {}
            if "totals" not in _stats_data:
                _stats_data["totals"] = default_totals.copy()
            else:
                # Ensure all current enum keys exist in totals
                for key in _get_all_context_keys():
                    if key not in _stats_data["totals"]:
                        _stats_data["totals"][key] = 0
                if "all_calls" not in _stats_data["totals"]:
                     _stats_data["totals"]["all_calls"] = 0


        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON from {STATS_FILE}. Starting fresh.", exc_info=True)
            _stats_data = {"users": {}, "totals": default_totals.copy()}
        except Exception as e:
            logger.exception(f"Failed to load user stats from {STATS_FILE}: {e}")
            _stats_data = {"users": {}, "totals": default_totals.copy()}
        _last_save_time = time.time()


async def save_stats(force: bool = False):
    global _last_save_time
    current_time = time.time()

    if not force and (current_time - _last_save_time < SAVE_INTERVAL):
        return

    async with _stats_lock:
        if not force and (current_time - _last_save_time < SAVE_INTERVAL):
            return

        try:
            STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp_file = STATS_FILE.with_suffix(f".tmp_{int(current_time)}")

            # --- Recalculate Totals Before Saving ---
            users_data = _stats_data.get("users", {})
            _stats_data["totals"]["all_calls"] = sum(
                d.get('call_count', 0) for d in users_data.values()
            )
            # Sum up each specific context across all users
            for key in _get_all_context_keys():
                 _stats_data["totals"][key] = sum(
                     d.get("contexts", {}).get(key, 0) for d in users_data.values()
                 )
            # --- End Recalculate Totals ---

            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(_stats_data, f, indent=4)

            temp_file.replace(STATS_FILE)
            _last_save_time = current_time
            logger.debug(f"Saved user stats to {STATS_FILE}")

        except Exception as e:
            logger.exception(f"Failed to save user stats to {STATS_FILE}: {e}")
        finally:
            with suppress(FileNotFoundError, OSError):
                if temp_file.exists():
                    temp_file.unlink()

async def increment_user_count(user_id: int, context: UsageContext):
    """
    Increments the call count for a given user_id and specific context.
    """
    if not user_id:
        logger.warning("Attempted to increment count for invalid user_id")
        return
    # Ensure context is a valid UsageContext enum member
    if not isinstance(context, UsageContext):
        try:
            # Attempt to convert if a string matching enum value was passed
            context = UsageContext(str(context))
        except ValueError:
            logger.error(f"Invalid usage context provided: {context}. Using OTHER.")
            context = UsageContext.OTHER

    user_id_str = str(user_id)
    context_key = context.value # e.g., "reddit_command_private"

    async with _stats_lock:
        user_data = _stats_data["users"].get(user_id_str, {})

        # Update overall count and timestamps
        user_data["call_count"] = user_data.get("call_count", 0) + 1
        user_data["last_seen_iso"] = datetime.utcnow().isoformat() + "Z"
        if "first_seen_iso" not in user_data:
            user_data["first_seen_iso"] = user_data["last_seen_iso"]

        # Initialize contexts dict if needed
        if "contexts" not in user_data:
            user_data["contexts"] = {key: 0 for key in _get_all_context_keys()} # Init all context keys

        # Increment specific context counter
        user_data["contexts"][context_key] = user_data["contexts"].get(context_key, 0) + 1

        _stats_data["users"][user_id_str] = user_data

    logger.debug(f"Incremented '{context_key}' count for user_id {user_id} in memory.")
    asyncio.create_task(save_stats())


async def get_user_data(user_id: int) -> dict:
    user_id_str = str(user_id)
    async with _stats_lock:
        # Ensure all context keys exist when returning data, defaulting to 0
        user_info = _stats_data["users"].get(user_id_str, {}).copy()
        if user_info and "contexts" not in user_info:
             user_info["contexts"] = {} # Initialize if missing
        # Ensure all possible context keys are present in the returned dict
        if "contexts" in user_info:
            for key in _get_all_context_keys():
                if key not in user_info["contexts"]:
                    user_info["contexts"][key] = 0
        return user_info


async def get_all_user_data() -> dict:
    async with _stats_lock:
        all_data = _stats_data.get("users", {}).copy()
        # Ensure all users have all context keys for consistency
        for user_id, user_info in all_data.items():
             if "contexts" not in user_info:
                 user_info["contexts"] = {}
             for key in _get_all_context_keys():
                  if key not in user_info["contexts"]:
                       user_info["contexts"][key] = 0
        return all_data


async def get_totals() -> dict:
    async with _stats_lock:
        # Recalculate totals on read for accuracy
        users_data = _stats_data.get("users", {})
        current_totals = {key: 0 for key in _get_all_context_keys()}
        current_totals["all_calls"] = sum(d.get('call_count', 0) for d in users_data.values())
        for key in _get_all_context_keys():
             current_totals[key] = sum(d.get("contexts", {}).get(key, 0) for d in users_data.values())
        return current_totals