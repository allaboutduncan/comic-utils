import logging
import os
import sys

# Logging setup
LOG_DIR = "logs"
APP_LOG = os.path.join(LOG_DIR, "app.log")

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Create logger
app_logger = logging.getLogger("app_logger")
app_logger.setLevel(logging.INFO)

# Create file handler
file_handler = logging.FileHandler(APP_LOG)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Create stream handler to log to stdout
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Add handlers to logger
if not app_logger.handlers:  # Prevent adding multiple handlers in case of multiple imports
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)