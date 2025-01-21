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
            
            # Apply the regex to clean the filename
            new_filename = re.sub(r'\((?!\d{4}\))(.*?)\)', '', filename)
            new_filename = re.sub(r'\[.*?\]', '', new_filename)
            new_filename = re.sub(r'\s+\.', '.', new_filename)  # Remove spaces before the file extension
            new_filename = re.sub(r'\s*c2c', '', new_filename)  # Remove " c2c"
            pattern = r'^(.*?\d{3}(?:\s*\(\d{4}\))?)\b.*(\.\w+)$'
            match = re.match(pattern, new_filename)
            
            if match:
                # Reconstruct the filename with the desired part and the extension.
                new_filename = match.group(1) + match.group(2)
                # Example:
                # Original: "Captain Marvel, Jr. 019 inc JVJ rh Yoc.cbz"
                # Group 1: "Captain Marvel, Jr. 019"
                # Group 2: ".cbz"
                # Result: "Captain Marvel, Jr. 019.cbz"
            else:
                # If the pattern doesn't match, optionally handle it or leave as is.
                pass
            
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
