import os
import time
import logging
import shutil
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from rename import rename_file  # Make sure it returns the new path or None

# Ensure the log directory exists
LOG_FILE_PATH = "/logs/log.txt"
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)

# Configure logging
logging.basicConfig(
    filename=LOG_FILE_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

class DownloadCompleteHandler(FileSystemEventHandler):
    def __init__(self, directory, target_directory, ignored_extensions=None):
        """
        :param directory: The directory to watch.
        :param target_directory: The directory where completed files should go.
        :param ignored_extensions: An iterable of file extensions (e.g. [".crdownload", ".tmp"]).
                                  Files with these extensions will be ignored.
        """
        self.directory = directory
        self.target_directory = target_directory
        
        # Provide a default list if none is supplied
        if ignored_extensions is None:
            ignored_extensions = [".crdownload", ".torrent", ".tmp"]
        
        # Convert to a set for fast membership checks
        self.ignored_extensions = set(ext.lower() for ext in ignored_extensions)

    def on_created(self, event):
        if not event.is_directory:
            self._handle_file_if_complete(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle_file_if_complete(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            # event.dest_path is the new file path after the rename
            self._handle_file_if_complete(event.dest_path)


    def _handle_file_if_complete(self, filepath):
        """Check if the file is done downloading, then process it (unless it's ignored)."""
        # Determine the extension in lowercase
        _, extension = os.path.splitext(filepath)
        extension = extension.lower()
        
        # If the file extension is in the ignored list, skip processing
        if extension in self.ignored_extensions:
            logging.info(f"Ignoring file with extension '{extension}': {filepath}")
            return

        # Proceed if download is complete
        if self._is_download_complete(filepath):
            self._process_file(filepath)
        else:
            logging.info(f"File not yet complete: {filepath}")

    def _process_file(self, filepath):
        """Try to rename the file. If no rename needed, just move it."""
        try:
            logging.info(f"Processing file: {filepath}")
            renamed_filepath = self._rename_file(filepath)
            
            # If rename_file returns None or the same path, assume "no rename needed."
            if not renamed_filepath or renamed_filepath == filepath:
                logging.info(f"No rename needed (or rename failed) for: {filepath}")
                # Move the original file
                self._move_file(filepath)
            else:
                # Move the renamed file
                logging.info(f"Renamed file: {renamed_filepath}")
                self._move_file(renamed_filepath)
        except Exception as e:
            logging.info(f"Error processing {filepath}: {e}")

    def _rename_file(self, filepath):
        """Call rename_file and return the new path (or None if no rename needed)."""
        try:
            new_filepath = rename_file(filepath)
            return new_filepath  # Could be None if no rename done
        except Exception as e:
            logging.info(f"Error renaming file {filepath}: {e}")
            return None

    def _move_file(self, filepath):
        """Move a file to the target directory."""
        filename = os.path.basename(filepath)
        target_path = os.path.join(self.target_directory, filename)
        os.makedirs(self.target_directory, exist_ok=True)

        if not os.path.exists(filepath):
            logging.info(f"File not found for moving: {filepath}")
            return

        if os.path.exists(target_path):
            logging.info(f"File already exists in target directory: {target_path}")
        else:
            try:
                shutil.move(filepath, target_path)
                logging.info(f"Moved file to: {target_path}")
            except Exception as e:
                logging.info(f"Error moving file {filepath} to {target_path}: {e}")

    def _is_download_complete(self, filepath):
        """Check if a file is still being written to by monitoring its size."""
        try:
            initial_size = os.path.getsize(filepath)
            time.sleep(5)
            final_size = os.path.getsize(filepath)
            return initial_size == final_size
        except (PermissionError, FileNotFoundError):
            return False

if __name__ == "__main__":
    directory_to_watch = os.environ.get("WATCH", "/temp")
    logging.info(f"Monitoring: {directory_to_watch}")
    target_directory = os.environ.get("TARGET", "/processed")
    logging.info(f"Target: {target_directory}")

    #   IGNORED_EXTENSIONS=".crdownload,.torrent,.tmp"
    ignored_exts_env = os.environ.get("IGNORED_EXTENSIONS", ".crdownload,.torrent,.tmp,.zip,.mega")
    # Convert the comma-separated string into a list of extensions
    ignored_extensions = [ext.strip() for ext in ignored_exts_env.split(",") if ext.strip()]

    if not os.path.exists(directory_to_watch):
        os.makedirs(directory_to_watch)

    event_handler = DownloadCompleteHandler(
        directory=directory_to_watch,
        target_directory=target_directory,
        ignored_extensions=ignored_extensions
    )
    observer = PollingObserver(timeout=30)
    observer.schedule(event_handler, directory_to_watch, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
