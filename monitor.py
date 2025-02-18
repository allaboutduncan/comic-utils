import time
import logging
import shutil
import os
import zipfile
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from rename import rename_file, clean_directory_name
from single_file import convert_to_cbz
from config import config, load_config

load_config()

# These initial reads remain for startup.
directory = config.get("SETTINGS", "WATCH", fallback="/temp")
target_directory = config.get("SETTINGS", "TARGET", fallback="/processed")
ignored_exts_config = config.get("SETTINGS", "IGNORED_EXTENSIONS", fallback=".crdownload")
ignored_extensions = [ext.strip() for ext in ignored_exts_config.split(",") if ext.strip()]
autoconvert = config.getboolean("SETTINGS", "AUTOCONVERT", fallback=False)
subdirectories = config.getboolean("SETTINGS", "SUBDIRECTORIES", fallback=False)
move_directories = config.getboolean("SETTINGS", "MOVE_DIRECTORY", fallback=False)
auto_unpack = config.getboolean("SETTINGS", "AUTO_UNPACK", fallback=False)

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
monitor_logger.info(f"1. Monitoring: {directory}")
monitor_logger.info(f"2. Target: {target_directory}")
monitor_logger.info(f"3. Ignored Extensions: {ignored_extensions}")
monitor_logger.info(f"4. Auto-Conversion Enabled: {autoconvert}")
monitor_logger.info(f"5. Monitor Sub-Directories Enabled: {subdirectories}")
monitor_logger.info(f"6. Move Sub-Directories Enabled: {move_directories}")
monitor_logger.info(f"7. Auto Unpack Enabled: {auto_unpack}")

class DownloadCompleteHandler(FileSystemEventHandler):
    def __init__(self, directory, target_directory, ignored_extensions):
        """
        Store initial values. We'll refresh them on each event
        to get updated config values.
        """
        self.directory = directory
        self.target_directory = target_directory
        self.ignored_extensions = set(ext.lower() for ext in ignored_extensions)
        self.autoconvert = autoconvert
        self.subdirectories = subdirectories
        self.move_directories = move_directories
        self.auto_unpack = auto_unpack

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
        self.subdirectories = config.getboolean("SETTINGS", "SUBDIRECTORIES", fallback=False)
        self.move_directories = config.getboolean("SETTINGS", "MOVE_DIRECTORY", fallback=False)
        self.auto_unpack = config.getboolean("SETTINGS", "AUTO_UNPACK", fallback=False)

        monitor_logger.info(f"********************// Config Reloaded //********************")
        monitor_logger.info(
            f"Directory: {self.directory}, Target: {self.target_directory}, "
            f"Ignored: {self.ignored_extensions}, autoconvert: {self.autoconvert}, "
            f"subdirectories: {self.subdirectories}, move_directories: {self.move_directories}, auto_unpack: {self.auto_unpack}"
        )


    def unzip_file(self, zip_filename):
        """
        Unzips the specified .zip file located in the current directory.
        Extracts all contents into the current directory.
        """
        # Check if the file exists in the current directory
        if not os.path.isfile(zip_filename):
            print(f"Error: {zip_filename} not found in the current directory.")
            return

        # Open and extract the zip file
        with zipfile.ZipFile(zip_filename, 'r') as zip_ref:
            zip_ref.extractall(directory)  # Defaults to current directory

        monitor_logger.info(f"Successfully extracted {zip_filename} into {os.getcwd()}")

        # Delete the zip file after extraction if it still exists
        if os.path.exists(zip_filename):
            try:
                os.remove(zip_filename)
                monitor_logger.info(f"Deleted zip file: {zip_filename}")
            except Exception as e:
                monitor_logger.error(f"Error deleting {zip_filename}: {e}")
        else:
            monitor_logger.info(f"Zip file {zip_filename} not found during deletion; it may have been already removed.")


    def on_created(self, event):
        # Refresh settings on every event
        self.reload_settings()

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
                if file.startswith('.'):  # Skip hidden files
                    monitor_logger.info(f"Skipping hidden file: {file}")
                    continue

                file_path = os.path.join(root, file)
                self._handle_file_if_complete(file_path)
                monitor_logger.info(f"Scanning directory - found file: {file_path}")


    def _handle_file_if_complete(self, filepath):
        filename = os.path.basename(filepath)

        # **Skip hidden files**
        if filename.startswith('.'):
            monitor_logger.info(f"Skipping hidden file: {filename}")
            return

        _, extension = os.path.splitext(filepath)
        extension = extension.lower()

        # If the extension is in the ignored list, ignore it—unless it's a .zip file and auto_unpack is enabled.
        if extension in self.ignored_extensions:
            if extension == '.zip' and getattr(self, 'auto_unpack', False):
                monitor_logger.info(f"Zip file detected with auto_unpack enabled: {filepath}")
            else:
                monitor_logger.info(f"Ignoring file with extension '{extension}': {filepath}")
                return

        if self._is_download_complete(filepath):
            self._process_file(filepath)
            monitor_logger.info(f"File Download Complete: {filepath}")
        else:
            monitor_logger.info(f"File not yet complete: {filepath}")


    def _rename_file(self, filepath):
        try:
            new_filepath = rename_file(filepath)
            if new_filepath:
                monitor_logger.info(f"Renamed File: {new_filepath}")
            return new_filepath
        except Exception as e:
            monitor_logger.info(f"Error renaming file {filepath}: {e}")
            return None


    def _process_file(self, filepath):
        try:
            monitor_logger.info(f"Processing file: {filepath}")
            
            # Check if the file is a zip file
            if filepath.lower().endswith('.zip'):
                if self.auto_unpack:
                    monitor_logger.info(f"Zip file detected and auto_unpack is enabled. Unzipping: {filepath}")
                    self.unzip_file(filepath)
                    return  # Exit after unzipping
                else:
                    monitor_logger.info(f"Zip file detected, but auto_unpack is disabled. Processing as normal file: {filepath}")
            
            # Continue with the normal processing for non-zip files (or zip files when auto_unpack is disabled)
            renamed_filepath = self._rename_file(filepath)
            if not renamed_filepath or renamed_filepath == filepath:
                monitor_logger.info(f"No rename needed for: {filepath}")
                self._move_file(filepath)
            else:
                monitor_logger.info(f"Renamed file: {renamed_filepath}")
                self._move_file(renamed_filepath)
                    
        except Exception as e:
            monitor_logger.info(f"Error processing {filepath}: {e}")


    def _move_file(self, filepath):
        """
        Moves the file from its source location to the target directory,
        ensuring the move is completed before proceeding with conversion.
        If move_directories is True, the file is renamed based on its original
        sub-directory structure (flattening the hierarchy).
        """
        filename = os.path.basename(filepath)

        # **Skip hidden files**
        if filename.startswith('.'):
            monitor_logger.info(f"Skipping hidden file: {filename}")
            return

        if not os.path.exists(filepath):
            monitor_logger.info(f"File not found for moving: {filepath}")
            return

        # Wait for file download completion
        monitor_logger.info(f"Waiting for '{filepath}' to finish downloading before moving...")
        if not _wait_for_download_completion(filepath):
            monitor_logger.warning(f"File not yet complete: {filepath}")
            return  # Exit early; do not move an incomplete file

        if move_directories:
            # Calculate the relative path from the source directory.
            rel_path = os.path.relpath(filepath, self.directory)
            # Build the target path preserving the sub-directory structure.
            target_path = os.path.join(self.target_directory, rel_path)
        else:
            # If not moving directories, keep the original filename.
            filename = os.path.basename(filepath)
            target_path = os.path.join(self.target_directory, filename)

        # Apply cleaning to the directory portion of target_path
        # This cleans the folder names (as per our directory cleaning rules)
        target_dir = os.path.dirname(target_path)
        cleaned_target_dir = clean_directory_name(target_dir)
        target_path = os.path.join(cleaned_target_dir, os.path.basename(target_path))

        try:
            # Ensure that the target sub-directory exists.
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.move(filepath, target_path)
            monitor_logger.info(f"Moved file to: {target_path}")

            # Allow filesystem update
            time.sleep(1)

            if os.path.exists(target_path):
                monitor_logger.info(f"Checking if '{target_path}' is a CBR file")
                if target_path.lower().endswith('.cbr'):
                    if self.autoconvert:
                        monitor_logger.info(f"Sending Convert Request for '{target_path}'")
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
                        monitor_logger.info("Auto-conversion is disabled.")
                else:
                    monitor_logger.info(f"File '{target_path}' is not a CBR file. No conversion needed.")
            else:
                monitor_logger.warning(f"File move verification failed: {target_path} not found.")


        except Exception as e:
            monitor_logger.error(f"Error moving file: {e}")
            # Allow filesystem update
            time.sleep(1)

        # Remove empty directories along the processed file's source path,
        # but only those in the chain up to the main watch folder.
        source_folder = os.path.dirname(filepath)
        watch_dir = os.path.abspath(self.directory)
        current_dir = os.path.abspath(source_folder)

        while current_dir != watch_dir:
            try:
                # Only remove the directory if it's empty.
                if not os.listdir(current_dir):
                    os.rmdir(current_dir)
                    monitor_logger.info(f"Deleted empty sub-directory: {current_dir}")
                else:
                    # Stop if the directory contains any files or non-empty folders.
                    break
            except Exception as e:
                monitor_logger.error(f"Error removing directory {current_dir}: {e}")
                break
            # Move one level up in the directory hierarchy.
            current_dir = os.path.dirname(current_dir)


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
    os.makedirs(directory, exist_ok=True)

    event_handler = DownloadCompleteHandler(
        directory=directory,
        target_directory=target_directory,
        ignored_extensions=ignored_extensions
    )

    # Initial scan
    for root, _, files in os.walk(directory):
        for file in files:
            filepath = os.path.join(root, file)
            monitor_logger.info(f"Initial startup scan for: {filepath}")
            event_handler._handle_file_if_complete(filepath)

    observer = PollingObserver(timeout=30)
    observer.schedule(event_handler, directory, recursive=subdirectories)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
