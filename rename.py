import sys
import os
import re

def rename_files(directory):
    if not os.path.isdir(directory):
        print(f"Directory {directory} not found.")
        return

    print(f"Renaming files in directory: {directory}...")
    
    # Example: Rename a file in the provided directory
    for dirpath, _, filenames in os.walk(directory):
        for filename in filenames:
            # Construct the full file path
            file_path = os.path.join(dirpath, filename)
            
            pattern = r'^(.*?\d{3})(?:\s*\((\d{4})\))?.*?(\.\w+)$'
            match = re.match(pattern, filename)

            if match:
                # Extract issue number, optional year, and file extension
                base_name = match.group(1)
                year = f" ({match.group(2)})" if match.group(2) else ""
                extension = match.group(3)
                # Combine the cleaned parts
                new_filename = f"{base_name}{year}{extension}"
                print(f"New Filename: {new_filename}")
            else:
                # If no match, default to removing everything after issue number
                fallback_pattern = r'^(.*?\d{3}).*?(\.\w+)$'
                fallback_match = re.match(fallback_pattern, filename)
                if fallback_match:
                    base_name = fallback_match.group(1)
                    extension = fallback_match.group(2)
                    new_filename = f"{base_name}{extension}"
                    print(f"New Filename: {new_filename}")
                else:
                    # Leave filename unchanged if no patterns match
                    new_filename = filename
                    print(f"New Filename: {new_filename}")
            
            # Only rename if the filename has changed
            if filename != new_filename:
                new_file_path = os.path.join(dirpath, new_filename)
                os.rename(file_path, new_file_path)
                print(f'Renamed: {file_path} -> {new_file_path}')

if __name__ == "__main__":
    # The directory path is passed as the first argument
    if len(sys.argv) < 2:
        print("No directory provided!")
    else:
        directory = sys.argv[1]
        rename_files(directory)
