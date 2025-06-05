import logging
import sys
from contextlib import suppress 

import config

_LOGGING_CONFIGURED = False

def setup_logging():
    """Configures the root logger based on settings in config.py."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        # logging.getLogger(__name__).debug("Logging already configured.")
        return

    try:
        logging_mode = int(config.LOGGING_MODE_STR)
        if logging_mode not in [0, 1, 2]:
            raise ValueError("Invalid LOGGING_MODE value")
    except (ValueError, TypeError):
        logging_mode = 0 # default to console only

    root_logger = logging.getLogger()
    root_logger.setLevel(config.LOG_LEVEL)
    log_formatter = logging.Formatter(config.LOG_FORMAT)

    for handler in root_logger.handlers[:]:
        with suppress(Exception): 
             handler.close()
        root_logger.removeHandler(handler)

    handlers_added = []
    if logging_mode in [0, 2]:
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(log_formatter)
            root_logger.addHandler(console_handler)
            handlers_added.append("Console")
        except Exception as e:
            print(f"Error setting up console logging: {e}", file=sys.stderr)

    if logging_mode in [1, 2]: 
        try:
            log_path = config.LOG_FILENAME.resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setFormatter(log_formatter)
            root_logger.addHandler(file_handler)
            handlers_added.append(f"File ({log_path})")
        except Exception as e:
            print(f"Error setting up file logging to {config.LOG_FILENAME}: {e}", file=sys.stderr)
            if logging_mode == 1 and "Console" not in handlers_added:
                try:
                    console_handler = logging.StreamHandler(sys.stdout)
                    console_handler.setFormatter(log_formatter)
                    root_logger.addHandler(console_handler)
                    handlers_added.append("Console (Fallback)")
                    logging_mode = 0 # reflect the change
                except Exception as fallback_e:
                     print(f"Error setting up fallback console logging: {fallback_e}", file=sys.stderr)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _LOGGING_CONFIGURED = True

    logger = logging.getLogger(__name__) 
    logger.info(f"--- Logging Initialized ---")
    logger.info(f"Logging Mode: {logging_mode} ({' '.join(handlers_added)})")
    if logging_mode in [1, 2] and any(isinstance(h, logging.FileHandler) for h in root_logger.handlers):
         logger.info(f"Log file path: {config.LOG_FILENAME.resolve()}")

setup_logging()
logger = logging.getLogger(__name__)