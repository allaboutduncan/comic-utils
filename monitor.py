import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rename import rename_file  # Ensure this function works as expected

class DownloadCompleteHandler(FileSystemEventHandler):
    def __init__(self, directory, target_directory):
        self.directory = directory
        self.target_directory = target_directory

    def on_created(self, event):
        self._process_event(event)

    def on_modified(self, event):
        self._process_event(event)

    def _process_event(self, event):
        if not event.is_directory:
            filepath = event.src_path
            print(f"File event detected: {filepath}")
            while not self._is_download_complete(filepath):
                time.sleep(1)  # Wait for the file to finish downloading
            self._process_file(filepath)

    def _process_file(self, filepath):
        try:
            print(f"Processing file: {filepath}")
            # Rename the file in place
            renamed_filepath = self._rename_file(filepath)
            if renamed_filepath:
                print(f"Renamed file: {renamed_filepath}")
                self._move_file(renamed_filepath)
            else:
                print(f"Failed to rename file: {filepath}")
        except Exception as e:
            print(f"Error processing {filepath}: {e}")

    def _rename_file(self, filepath):
        """Rename the file using the rename_file function and return the new path."""
        try:
            # Call rename_file with the specific file path
            rename_file(filepath)
            # Check if the file was renamed
            dirname = os.path.dirname(filepath)
            original_filename = os.path.basename(filepath)
            for filename in os.listdir(dirname):
                if filename != original_filename and os.path.isfile(os.path.join(dirname, filename)):
                    return os.path.join(dirname, filename)
            return None  # No renamed file found
        except Exception as e:
            print(f"Error renaming file {filepath}: {e}")
            return None

    def _move_file(self, filepath):
        """Move a file to the target directory."""
        filename = os.path.basename(filepath)
        target_path = os.path.join(self.target_directory, filename)
        os.makedirs(self.target_directory, exist_ok=True)

        if not os.path.exists(filepath):  # Check if the file exists before moving
            print(f"File not found for moving: {filepath}")
            return

        if os.path.exists(target_path):
            print(f"File already exists in target directory: {target_path}")
        else:
            try:
                os.rename(filepath, target_path)
                print(f"Moved file to: {target_path}")
            except Exception as e:
                print(f"Error moving file {filepath} to {target_path}: {e}")

    def _is_download_complete(self, filepath):
        """Check if a file is still being written to by monitoring its size."""
        try:
            initial_size = os.path.getsize(filepath)
            time.sleep(1)  # Wait a second and check again
            final_size = os.path.getsize(filepath)
            return initial_size == final_size
        except (PermissionError, FileNotFoundError):
            return False

if __name__ == "__main__":
    directory_to_watch = "F:/downloads/demo"  # Change this to your target directory
    target_directory = "F:/downloads/processed"  # Directory to move renamed files

    if not os.path.exists(directory_to_watch):
        os.makedirs(directory_to_watch)

    event_handler = DownloadCompleteHandler(directory_to_watch, target_directory)
    observer = Observer()
    observer.schedule(event_handler, directory_to_watch, recursive=True)

    print(f"Watching directory: {directory_to_watch}")
    observer.start()

    try:
        while True:
            time.sleep(1)  # Keep the script running
    except KeyboardInterrupt:
        observer.stop()
    observer.join()