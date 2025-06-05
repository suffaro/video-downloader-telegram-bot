import os
from pathlib import Path
from typing import List, Optional
from contextlib import suppress

# --- Project Imports ---
from logging_config import logger # Use configured logger

# --- File Cleanup Function ---

async def _cleanup_media_files(media_files: List[Path], url: str = "N/A"):
    """
    Safely attempts to remove a list of temporary media files.

    Logs successes and failures.

    Args:
        media_files: A list of Path objects representing files to be deleted.
        url: The source URL associated with these files (for logging context).
    """
    if not media_files:
        # logger.debug(f"No media files provided for cleanup (URL: {url}).")
        return

    logger.info(f"Starting cleanup of {len(media_files)} temporary file(s) for URL: {url}")
    deleted_count = 0
    failed_count = 0

    for file_path in media_files:
        # Ensure we have a valid Path object and it points to a file
        if not isinstance(file_path, Path):
             logger.warning(f"Cleanup skipped: Invalid path object '{file_path}' provided.")
             failed_count += 1
             continue

        if file_path.exists() and file_path.is_file():
            try:
                file_path.unlink() # Use pathlib's unlink
                logger.debug(f"Successfully deleted temp file: {file_path}")
                deleted_count += 1
            except OSError as e:
                logger.error(f"Error deleting temp file {file_path}: {e}")
                failed_count += 1
            except Exception as e:
                logger.exception(f"Unexpected error deleting temp file {file_path}: {e}")
                failed_count += 1
        elif file_path.exists() and not file_path.is_file():
             logger.warning(f"Cleanup skipped: Path exists but is not a file - {file_path}")
             failed_count += 1
        else:
             # File doesn't exist, might have been cleaned up already or failed to create
             logger.debug(f"Cleanup skipped: File not found (already deleted?) - {file_path}")
             # Don't count non-existent files as failures unless necessary

    logger.info(f"Cleanup complete for URL {url}. Deleted: {deleted_count}, Failed/Skipped: {failed_count}.")