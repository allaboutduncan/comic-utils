import sys
import os
import re
import logging

# Ensure the log directory exists
LOG_FILE_PATH = "/logs/log.txt"
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)

# Configure logging
logging.basicConfig(
    filename=LOG_FILE_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 1) Main pattern for: Title + [space] + (v## or up to 3 digits) + optional year
#    Explanation:
#      - ^(.*?)\s+      : Capture everything (title) until we hit at least one space
#      - ((?:v\d{1,3})|(?:\d{1,3})) : The "issue" is either v## or up to 3 digits
#      - \b             : Word boundary ensures we don't chop partial digits (e.g. 2024 -> 202)
#      - (.*)           : The "middle" (could contain parentheses, etc.) up until extension
#      - (\.\w+)$       : The file extension
ISSUE_PATTERN = re.compile(
    r'^(.*?)\s+((?:v\d{1,3})|(?:\d{1,3}))\b(.*)(\.\w+)$',
    re.IGNORECASE
)

# 2) Fallback pattern for: Title (YYYY) anything .ext
#    - We assume no issue # is intended, only a 4-digit year, and we keep only that 4-digit year.
FALLBACK_PATTERN = re.compile(
    r'^(.*?)\((\d{4})\)(.*)(\.\w+)$',
    re.IGNORECASE
)

def get_renamed_filename(filename):
    """
    Given a single filename (no directory path):
      1) Try the main ISSUE_PATTERN first.
      2) If it fails, try the FALLBACK_PATTERN for a 4-digit year.
      3) If neither pattern matches, return None.
    """

    # ---------------------
    # 1) Main "issue/volume" pattern
    # ---------------------
    issue_match = ISSUE_PATTERN.match(filename)
    if issue_match:
        raw_title, issue_part, middle, extension = issue_match.groups()
        
        # Clean the title: underscores -> spaces, then strip
        clean_title = raw_title.replace('_', ' ').strip()

        # If issue_part starts with 'v', keep "vXX" as-is, else zero-pad the numeric part
        if issue_part.lower().startswith('v'):
            final_issue = issue_part  # e.g. 'v01'
        else:
            # purely numeric (1–3 digits)
            final_issue = f"{int(issue_part):03d}"  # e.g. 1 -> 001

        # Attempt to find a 4-digit year in `middle` (e.g. "(2024)", "(1986)" etc.)
        # We'll keep only the first 4-digit year we see.
        found_year = None
        paren_groups = re.findall(r'\(([^)]*)\)', middle)
        for group_text in paren_groups:
            year_match = re.search(r'\b(\d{4})\b', group_text)
            if year_match:
                found_year = year_match.group(1)
                break

        if found_year:
            # Title 001 (YEAR).ext
            new_filename = f"{clean_title} {final_issue} ({found_year}){extension}"
        else:
            # Title 001.ext
            new_filename = f"{clean_title} {final_issue}{extension}"

        return new_filename

    # ---------------------
    # 2) Fallback: no issue/volume, but we do have (YYYY)
    # ---------------------
    fallback_match = FALLBACK_PATTERN.match(filename)
    if fallback_match:
        raw_title, found_year, _, extension = fallback_match.groups()

        # Clean up the raw_title
        clean_title = raw_title.replace('_', ' ').strip()

        # Rebuild as "Title (YYYY).ext"
        new_filename = f"{clean_title} ({found_year}){extension}"
        return new_filename

    # ---------------------
    # 3) No match -> return None
    # ---------------------
    return None

def rename_files(directory):
    """
    Walk through the given directory (including subdirectories) and rename
    all files that match the patterns above.
    """
    for subdir, _, files in os.walk(directory):
        for filename in files:
            old_path = os.path.join(subdir, filename)
            new_name = get_renamed_filename(filename)

            if new_name and new_name != filename:
                new_path = os.path.join(subdir, new_name)
                logging.info(f"Renaming:\n  {old_path}\n  --> {new_path}\n")
                os.rename(old_path, new_path)

def rename_file(file_path):
    """
    Renames a single file if it matches either pattern using the logic
    in get_renamed_filename().
    """
    directory, filename = os.path.split(file_path)
    new_name = get_renamed_filename(filename)

    if new_name and new_name != filename:
        new_path = os.path.join(directory, new_name)
        logging.info(f"Renaming:\n  {file_path}\n  --> {new_path}\n")
        os.rename(file_path, new_path)
        return new_path
    else:
        logging.info("No renaming pattern matched or no change needed.")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        logging.info("No directory provided!")
    else:
        directory = sys.argv[1]
        rename_files(directory)
