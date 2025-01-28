import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rename import rename_files  # Import the rename_files function from rename.py

# Define the directory to watch
WATCH_DIRECTORY = "/path/to/your/directory"

# Time to wait (in seconds) to ensure the file is fully downloaded
STABLE_TIME = 5  # Adjust this value based on your download speed

class NewFileHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.file_sizes = {}  # Dictionary to track file sizes and timestamps

    def on_created(self, event):
        # This method is called when a file or directory is created
        if not event.is_directory:
            file_path = event.src_path
            print(f"New file detected: {file_path}")
            self.file_sizes[file_path] = (os.path.getsize(file_path), time.time())

    def on_modified(self, event):
        # This method is called when a file is modified
        if not event.is_directory:
            file_path = event.src_path
            if file_path in self.file_sizes:
                current_size = os.path.getsize(file_path)
                last_size, last_time = self.file_sizes[file_path]

                if current_size != last_size:
                    # File size has changed, update the size and timestamp
                    self.file_sizes[file_path] = (current_size, time.time())
                else:
                    # File size has not changed, check if it's stable for STABLE_TIME seconds
                    if time.time() - last_time >= STABLE_TIME:
                        print(f"File download complete: {file_path}")
                        rename_files(WATCH_DIRECTORY)  # Call the rename function
                        del self.file_sizes[file_path]  # Remove the file from tracking

if __name__ == "__main__":
    # Create an observer to monitor the directory
    event_handler = NewFileHandler()
    observer = Observer()
    observer.schedule(event_handler, path=WATCH_DIRECTORY, recursive=True)

    # Start the observer
    observer.start()
    print(f"Monitoring directory: {WATCH_DIRECTORY}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()