import logging
import os
from datetime import datetime

LOGGERS = {}

class MicroSecondFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created)  # Convert seconds since epoch to datetime
        if datefmt:
            formatted_time = ct.strftime(datefmt)
        else:
            formatted_time = ct.isoformat(timespec='microseconds')
        return formatted_time


def get_logger(prefix, level=None) -> logging.Logger:
    # Get if has logger with this prefix
    if prefix in LOGGERS:
        return LOGGERS[prefix]

    # Create a logger with the specified prefix as its name
    logger = logging.getLogger(prefix)

    # Determine level
    if level is None:
        env_level = os.getenv("LOG_LEVEL")
        
        if env_level is not None:
            level = getattr(logging, env_level)  # raises if not found
        else:
            level = logging.DEBUG  # default

    # Set level
    logger.setLevel(level)
    
    # Ensure logger does not propagate logs to root logger
    logger.propagate = False

    # Create a log handler
    ch = logging.StreamHandler()
    
    # Create a formatter with microseconds, including the logger's name (prefix) in the format string
    formatter = MicroSecondFormatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S.%f')
    ch.setFormatter(formatter)

    # Add the handler to the logger
    logger.addHandler(ch)

    # Save logger
    LOGGERS[prefix] = logger

    return logger
