import logging
import sys
import config

def get_logger(name=None):
    """Return a configured logger instance."""
    logger = logging.getLogger(name)

    if not logger.hasHandlers(): 
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '[%(levelname)s] %(asctime)s - %(name)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Optional file logging
        if config.LOG_FILE:
            file_handler = logging.FileHandler(config.LOG_FILE)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    logger.setLevel(logging.DEBUG if config.DEBUG else logging.INFO)
    return logger