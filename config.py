import configparser
import threading
import os
import time

CONFIG_FILE = "config.ini"
config = configparser.ConfigParser()

def write_config():
    """Writes the current in-memory config object to config.ini."""
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
            "IGNORED_FILES": "",
            "IGNORED_EXTENSIONS": "",
            "AUTOCONVERT": "False",
            "SUBDIRECTORIES": "False",
            "XML_YEAR": "False",
            "XML_MARKDOWN": "False",
            "XML_LIST": "True"
        }
        write_config()
    else:
        config.read(CONFIG_FILE)

    # **🚀 Ensure the SETTINGS section is a dictionary**
    if "SETTINGS" not in config:
        config["SETTINGS"] = {}

    print("Config reloaded.")

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
    app.config["AUTOCONVERT"] = settings.get("AUTOCONVERT", "False") == "True"
    app.config["SUBDIRECTORIES"] = settings.get("SUBDIRECTORIES", "False") == "True"
    app.config["XML_YEAR"] = settings.get("XML_YEAR", "False") == "False"
    app.config["XML_MARKDOWN"] = settings.get("XML_MARKDOWN", "False") == "True"
    app.config["XML_LIST"] = settings.get("XML_LIST", "False") == "True"

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
        except FileNotFoundError:
            print(f"Warning: {CONFIG_FILE} not found.")
            last_mtime = None  # Reset because file may appear later

# Start monitoring config.ini in the background
thread = threading.Thread(target=monitor_config, args=(5,), daemon=True)
thread.start()

# Initial config load
load_config()
