import configparser
import threading
import os
import time
from app_logging import app_logger

CONFIG_FILE = "/config/config.ini"
config = configparser.ConfigParser()
config.optionxform = str  # Preserve case sensitivity

def write_config():
    """Writes the current in-memory config object to config.ini."""
    config.optionxform = str  # Preserve case sensitivity
    with open(CONFIG_FILE, "w") as configfile:
        config.write(configfile)

def load_config():
    """
    Loads or (if missing) creates the config file, ensuring
    that the [SETTINGS] section exists.
    """
    if not os.path.exists(CONFIG_FILE):
        # Create a default config.ini if none exists
        config["SETTINGS"] = {
            "WATCH": "/temp",
            "TARGET": "/processed",
            "IGNORED_TERMS": "Annual",
            "IGNORED_FILES": "cover.jpg,cvinfo,.DS_Store",
            "IGNORED_EXTENSIONS": ".crdownload,.torrent,.tmp,.mega,.rar,.bak,.zip",
            "AUTOCONVERT": "False",
            "READ_SUBDIRECTORIES": "False",
            "CONVERT_SUBDIRECTORIES": "False",
            "XML_YEAR": "False",
            "XML_MARKDOWN": "False",
            "XML_LIST": "True",
            "MOVE_DIRECTORY": "False",
            "AUTO_UNPACK": "False"
        }
        write_config()
    else:
        config.read(CONFIG_FILE)

    # Ensure the SETTINGS section is a dictionary
    if "SETTINGS" not in config:
        config["SETTINGS"] = {}


def load_flask_config(app, logger=None):
    """
    Helper function to populate a Flask app's config with
    the latest [SETTINGS] from config.ini.
    """
    # Ensure we have the most up-to-date config in memory
    load_config()

    if logger:
        logger.info("Loading config file values...")

    # **Ensure SETTINGS is a dictionary before accessing**
    settings = config["SETTINGS"] if "SETTINGS" in config else {}

    # Populate Flask app.config safely
    app.config["WATCH"] = settings.get("WATCH", "/temp")
    app.config["TARGET"] = settings.get("TARGET", "/processed")
    app.config["IGNORED_TERMS"] = settings.get("IGNORED_TERMS", "")
    app.config["IGNORED_FILES"] = settings.get("IGNORED_FILES", "")
    app.config["IGNORED_EXTENSIONS"] = settings.get("IGNORED_EXTENSIONS", "")
    app.config["AUTOCONVERT"] = config.getboolean("SETTINGS", "AUTOCONVERT", fallback=False)
    app.config["READ_SUBDIRECTORIES"] = config.getboolean("SETTINGS", "READ_SUBDIRECTORIES", fallback=False)
    app.config["CONVERT_SUBDIRECTORIES"] = config.getboolean("SETTINGS", "CONVERT_SUBDIRECTORIES", fallback=False)
    app.config["XML_YEAR"] = config.getboolean("SETTINGS", "XML_YEAR", fallback=False)
    app.config["XML_MARKDOWN"] = config.getboolean("SETTINGS", "XML_MARKDOWN", fallback=False)
    app.config["XML_LIST"] = config.getboolean("SETTINGS", "XML_LIST", fallback=False)
    app.config["MOVE_DIRECTORY"] = config.getboolean("SETTINGS", "MOVE_DIRECTORY", fallback=False)
    app.config["AUTO_UNPACK"] = config.getboolean("SETTINGS", "AUTO_UNPACK", fallback=False)

    if logger:
        logger.info(f"Watching: {app.config['WATCH']}")

def monitor_config(interval=5):
    """
    Background thread to watch config.ini for changes.
    If modified, automatically reloads the in-memory 'config' object.
    """
    last_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else None

    while True:
        time.sleep(interval)
        try:
            current_mtime = os.path.getmtime(CONFIG_FILE)
            if last_mtime is None or current_mtime != last_mtime:  # File is new or changed
                load_config()
                last_mtime = current_mtime
                app_logger.info("Config file reloaded at: ".format(time.ctime(last_mtime)))
        except FileNotFoundError:
            app_logger.info(f"Warning: {CONFIG_FILE} not found.")
            last_mtime = None  # Reset because file may appear later

# Start monitoring config.ini in the background
thread = threading.Thread(target=monitor_config, args=(5,), daemon=True)
thread.start()

# Initial config load
load_config()
