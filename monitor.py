import time
import logging
import shutil
import os
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from rename import rename_file
from single_file import convert_to_cbz
from config import config, load_config

load_config()

# These initial reads remain for startup.
directory_to_watch = config.get("SETTINGS", "WATCH", fallback="/temp")
target_directory = config.get("SETTINGS", "TARGET", fallback="/processed")
ignored_exts_config = config.get("SETTINGS", "IGNORED_EXTENSIONS", fallback=".crdownload")
ignored_extensions = [ext.strip() for ext in ignored_exts_config.split(",") if ext.strip()]
autoconvert = config.getboolean("SETTINGS", "AUTOCONVERT", fallback=False)
subdirectories = config.getboolean("SETTINGS", "SUBDIRECTORIES", fallback=False)

# Logging setup
MONITOR_LOG = "logs/monitor.log"
os.makedirs(os.path.dirname(MONITOR_LOG), exist_ok=True)
if not os.path.exists(MONITOR_LOG):
    with open(MONITOR_LOG, "w") as f:
        f.write("")  # Create an empty file

monitor_logger = logging.getLogger("monitor_logger")
monitor_logger.setLevel(logging.INFO)
monitor_handler = logging.FileHandler(MONITOR_LOG)
monitor_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
monitor_logger.addHandler(monitor_handler)

monitor_logger.info("Monitor script started!")
monitor_logger.info(f"1. Monitoring: {directory_to_watch}")
monitor_logger.info(f"2. Target: {target_directory}")
monitor_logger.info(f"3. Ignored Extensions: {ignored_extensions}")
monitor_logger.info(f"4. Auto-Conversion Enabled: {autoconvert}")
monitor_logger.info(f"5. Monitor Sub-Directories Enabled: {subdirectories}")


class DownloadCompleteHandler(FileSystemEventHandler):
    def __init__(self, directory, target_directory, ignored_extensions):
        """
        Store initial values. We'll refresh them on each event
        to get updated config values.
        """
        self.directory = directory
        self.target_directory = target_directory
        self.ignored_extensions = set(ext.lower() for ext in ignored_extensions)
        self.autoconvert = autoconvert  # We store this as well

    def reload_settings(self):
        """
        Re-reads config values so that if config.ini changes,
        this handler will use the latest settings.
        """
        self.directory = config.get("SETTINGS", "WATCH", fallback="/temp")
        self.target_directory = config.get("SETTINGS", "TARGET", fallback="/processed")

        ignored_exts_config = config.get("SETTINGS", "IGNORED_EXTENSIONS", fallback=".crdownload")
        self.ignored_extensions = set(ext.strip().lower() for ext in ignored_exts_config.split(",") if ext.strip())

        self.autoconvert = config.getboolean("SETTINGS", "AUTOCONVERT", fallback=False)

        monitor_logger.info(
            f"Config reloaded. Now watching: {self.directory}, target: {self.target_directory}, "
            f"ignored: {self.ignored_extensions}, autoconvert: {self.autoconvert}"
        )

    def on_created(self, event):
        # Refresh settings on every event
        self.reload_settings()
        monitor_logger.info(f"** on_created triggered ** => Path: {event.src_path}, Is directory? {event.is_directory}")

        if not event.is_directory:
            self._handle_file_if_complete(event.src_path)
            monitor_logger.info(f"File created: {event.src_path}")
        else:
            monitor_logger.info(f"Directory created: {event.src_path}")
            self._scan_directory(event.src_path)

    def on_modified(self, event):
        self.reload_settings()

        if not event.is_directory:
            self._handle_file_if_complete(event.src_path)
            monitor_logger.info(f"File Modified: {event.src_path}")

    def on_moved(self, event):
        self.reload_settings()

        if not event.is_directory:
            self._handle_file_if_complete(event.dest_path)
            monitor_logger.info(f"File Moved: {event.dest_path}")
        else:
            monitor_logger.info(f"Directory Moved: {event.dest_path}")
            self._scan_directory(event.dest_path)

    def _scan_directory(self, directory):
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                self._handle_file_if_complete(file_path)
                monitor_logger.info(f"Scanning directory - found file: {file_path}")

    def _handle_file_if_complete(self, filepath):
        _, extension = os.path.splitext(filepath)
        extension = extension.lower()
        if extension in self.ignored_extensions:
            monitor_logger.info(f"Ignoring file with extension '{extension}': {filepath}")
            return

        if self._is_download_complete(filepath):
            self._process_file(filepath)
            monitor_logger.info(f"File Download Complete: {filepath}")
        else:
            monitor_logger.info(f"File not yet complete: {filepath}")

    def _process_file(self, filepath):
        try:
            monitor_logger.info(f"Processing file: {filepath}")
            renamed_filepath = self._rename_file(filepath)
            if not renamed_filepath or renamed_filepath == filepath:
                monitor_logger.info(f"No rename needed for: {filepath}")
                self._move_file(filepath)
            else:
                monitor_logger.info(f"Renamed file: {renamed_filepath}")
                self._move_file(renamed_filepath)
        except Exception as e:
            monitor_logger.info(f"Error processing {filepath}: {e}")

    def _rename_file(self, filepath):
        try:
            new_filepath = rename_file(filepath)
            if new_filepath:
                monitor_logger.info(f"Renamed File: {new_filepath}")
            return new_filepath
        except Exception as e:
            monitor_logger.info(f"Error renaming file {filepath}: {e}")
            return None

    def _move_file(self, filepath):
        """
        Moves the file from its source location to the target directory,
        ensuring the move is completed before proceeding with conversion.
        Then checks if the source sub-directory is empty and removes it if so.
        """
        filename = os.path.basename(filepath)
        target_path = os.path.join(self.target_directory, filename)

        if not os.path.exists(filepath):
            monitor_logger.info(f"File not found for moving: {filepath}")
            return

        # **Wait for file download completion**
        monitor_logger.info(f"Waiting for '{filepath}' to finish downloading before moving...")
        if not _wait_for_download_completion(filepath):
            monitor_logger.warning(f"File not yet complete: {filepath}")
            return  # Exit early; do not move an incomplete file

        if os.path.exists(target_path):
            monitor_logger.info(f"File already exists in target directory: {target_path}")
        else:
            try:
                os.makedirs(self.target_directory, exist_ok=True)
                shutil.move(filepath, target_path)
                monitor_logger.info(f"Moved file to: {target_path}")

                # Ensure the file is fully moved before proceeding
                time.sleep(1)  # Allow filesystem update

                if os.path.exists(target_path):
                    monitor_logger.info(f"Check if '{target_path}' is CBR and convert")

                    # Re-check autoconvert setting each time (using self.autoconvert)
                    if self.autoconvert:
                        monitor_logger.info(f"Sending Convert Request for '{target_path}'")

                        # Extra safety check - ensure the file is still accessible before conversion
                        retries = 3
                        for _ in range(retries):
                            if os.path.exists(target_path):
                                break
                            time.sleep(0.5)

                        try:
                            convert_to_cbz(target_path)
                        except Exception as e:
                            monitor_logger.error(f"Conversion failed for '{target_path}': {e}")
                else:
                    monitor_logger.warning(f"File move verification failed: {target_path} not found.")

            except Exception as e:
                monitor_logger.error(f"Error moving file {filepath} to {target_path}: {e}")

        # Remove the source directory if it's now empty (and it's not the main watch folder).
        source_folder = os.path.dirname(filepath)
        if os.path.abspath(source_folder) != os.path.abspath(self.directory):
            try:
                if not os.listdir(source_folder):
                    os.rmdir(source_folder)
                    monitor_logger.info(f"Deleted empty sub-directory: {source_folder}")
            except Exception as e:
                monitor_logger.error(f"Error removing directory {source_folder}: {e}")

    def _is_download_complete(self, filepath):
        try:
            initial_size = os.path.getsize(filepath)
            time.sleep(5)  # Adjust sleep time as needed
            final_size = os.path.getsize(filepath)
            return initial_size == final_size
        except (PermissionError, FileNotFoundError):
            return False


def _wait_for_download_completion(filepath, wait_time=2.0, retries=20):
    """
    Waits until a file is fully downloaded before proceeding.
    - Checks for stable file size.
    - Ensures any ".part" or ".tmp" files are gone.
    - Retries several times with a delay before confirming the file is complete.
    """
    if not os.path.exists(filepath):
        return False

    previous_size = -1
    for _ in range(retries):
        if not os.path.exists(filepath):  # Ensure file hasn't disappeared
            return False

        current_size = os.path.getsize(filepath)
        if current_size == previous_size:
            return True  # File size is stable, assume complete

        previous_size = current_size
        time.sleep(wait_time)

    return False


if __name__ == "__main__":
    os.makedirs(directory_to_watch, exist_ok=True)

    event_handler = DownloadCompleteHandler(
        directory=directory_to_watch,
        target_directory=target_directory,
        ignored_extensions=ignored_extensions
    )

    # Initial scan
    for root, _, files in os.walk(directory_to_watch):
        for file in files:
            filepath = os.path.join(root, file)
            monitor_logger.info(f"Initial startup scan for: {filepath}")
            event_handler._handle_file_if_complete(filepath)

    observer = PollingObserver(timeout=30)
    observer.schedule(event_handler, directory_to_watch, recursive=subdirectories)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
