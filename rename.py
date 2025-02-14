import sys
import os
import re
from app_logging import app_logger

# -------------------------------------------------------------------
# New pattern for Volume + Issue, e.g.:
#   "Comic Name v3 051 (2018) (DCP-Scan Final).cbz"
#   Group(1) => "Comic Name"
#   Group(2) => "v3"
#   Group(3) => "051"
#   Group(4) => " (2018) (DCP-Scan Final)"
#   Group(5) => ".cbz"
# -------------------------------------------------------------------
VOLUME_ISSUE_PATTERN = re.compile(
    r'^(.*?)\s+(v\d{1,3})\s+(\d{1,3})(.*)(\.\w+)$',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Original ISSUE_PATTERN:
#   Title + space + (v## or up to 3 digits) + (middle) + extension
#   e.g. "Comic Name 051 (2018).cbz"  or  "Comic Name v3 (2022).cbr"
# -------------------------------------------------------------------
ISSUE_PATTERN = re.compile(
    r'^(.*?)\s+((?:v\d{1,3})|(?:\d{1,3}))\b(.*)(\.\w+)$',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Fallback for Title (YYYY) anything .ext
# e.g. "Comic Name (2018) some extra.cbz" -> "Comic Name (2018).cbz"
# -------------------------------------------------------------------
FALLBACK_PATTERN = re.compile(
    r'^(.*?)\((\d{4})\)(.*)(\.\w+)$',
    re.IGNORECASE
)

def clean_filename_pre(filename):
    """
    Pre-process the filename to:
      1) Remove anything in [brackets].
      2) Remove parentheses that don't contain a 4-digit year.
      3) If a parentheses contains a 4-digit year followed by -XX (month),
         remove that -XX piece (e.g. "2023-04" -> "2023").
    """

    # 1) Remove bracketed text [ ... ]
    filename = re.sub(r'\[.*?\]', '', filename)

    # 2 & 3) Process parentheses:
    #    - Keep only parentheses that contain a 4-digit year.
    #    - Within those parentheses, remove "-XX" if it follows the year.
    def parentheses_replacer(match):
        """
        If the parentheses group contains a 4-digit year, keep it
        (while removing any '-NN' month after the year).
        Otherwise, remove the entire parentheses group.
        """
        text_with_parens = match.group(0)  # e.g. "(2018)" or "(Scan Final)"
        inner_text = text_with_parens[1:-1]  # strip outer parentheses

        # Check for a 4-digit year
        # We'll do a quick search for something like "2023" and optional "-05".
        # If no 4-digit year is found, remove the entire parentheses block.
        # If found, remove the dash + two digits if they exist.
        year_match = re.search(r'\d{4}', inner_text)
        if not year_match:
            return ''  # remove the parentheses altogether

        # We found a 4-digit year. Now also remove the dash-month if present.
        # e.g. "2023-07" -> "2023"
        new_inner = re.sub(r'(\d{4})-\d{2}\b', r'\1', inner_text)
        return f"({new_inner})"

    # Replace all (...) groups according to the logic above
    filename = re.sub(r'\([^)]*\)', parentheses_replacer, filename)

    # 4) Remove " - Issue" from the filename
    filename = re.sub(r'\s*-\s*Issue\b', '', filename, flags=re.IGNORECASE)

    # Trim extra spaces that might result
    filename = re.sub(r'\s+', ' ', filename).strip()

    return filename

def get_renamed_filename(filename):
    """
    Given a single filename (no directory path):
      1) Pre-clean the filename by removing bracketed text,
         removing parentheses without a 4-digit year,
         and removing -XX from parentheses containing a 4-digit year.
      2) Try VOLUME_ISSUE_PATTERN first (e.g. "Title v3 051 (2018).ext").
      3) If it fails, try the single ISSUE_PATTERN.
      4) If that fails, try FALLBACK_PATTERN for just (YYYY).
      5) If none match, return None.
    """
    # Pre-processing step
    cleaned_filename = clean_filename_pre(filename)

    # ==========================================================
    # 1) VOLUME + ISSUE pattern (e.g. "v3 051")
    # ==========================================================
    vol_issue_match = VOLUME_ISSUE_PATTERN.match(cleaned_filename)
    if vol_issue_match:
        raw_title, volume_part, issue_part, middle, extension = vol_issue_match.groups()

        # Clean the title: underscores -> spaces, then strip
        clean_title = raw_title.replace('_', ' ').strip()

        # volume_part (e.g. "v3") - keep as-is
        final_volume = volume_part.strip()

        # If issue_part starts with 'v', keep as-is, else zero-pad numeric
        if issue_part.lower().startswith('v'):
            final_issue = issue_part
        else:
            final_issue = f"{int(issue_part):03d}"  # zero-pad if numeric

        # Look for the first 4-digit year in `middle`
        found_year = None
        paren_groups = re.findall(r'\(([^)]*)\)', middle)
        for group_text in paren_groups:
            year_match = re.search(r'\b(\d{4})\b', group_text)
            if year_match:
                found_year = year_match.group(1)
                break

        if found_year:
            new_filename = f"{clean_title} {final_volume} {final_issue} ({found_year}){extension}"
        else:
            new_filename = f"{clean_title} {final_volume} {final_issue}{extension}"

        return new_filename

    # ==========================================================
    # 2) Single ISSUE pattern (no separate "volume" token)
    #    e.g. "Comic Name 051 (2018).cbz" or "Comic Name v3 (2018).cbz"
    # ==========================================================
    issue_match = ISSUE_PATTERN.match(cleaned_filename)
    if issue_match:
        raw_title, issue_part, middle, extension = issue_match.groups()

        # Clean the title: underscores -> spaces, then strip
        clean_title = raw_title.replace('_', ' ').strip()

        # If issue_part starts with 'v', keep "vXX" as-is, else zero-pad
        if issue_part.lower().startswith('v'):
            final_issue = issue_part  # e.g. 'v01'
        else:
            final_issue = f"{int(issue_part):03d}"  # e.g. 1 -> 001

        # Attempt to find a 4-digit year in `middle`
        found_year = None
        paren_groups = re.findall(r'\(([^)]*)\)', middle)
        for group_text in paren_groups:
            year_match = re.search(r'\b(\d{4})\b', group_text)
            if year_match:
                found_year = year_match.group(1)
                break

        if found_year:
            new_filename = f"{clean_title} {final_issue} ({found_year}){extension}"
        else:
            new_filename = f"{clean_title} {final_issue}{extension}"

        return new_filename

    # ==========================================================
    # 3) Fallback: Title (YYYY) anything .ext
    # ==========================================================
    fallback_match = FALLBACK_PATTERN.match(cleaned_filename)
    if fallback_match:
        raw_title, found_year, _, extension = fallback_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        new_filename = f"{clean_title} ({found_year}){extension}"
        return new_filename

    # ==========================================================
    # 4) No match => return None
    # ==========================================================
    return None

def rename_files(directory):
    """
    Walk through the given directory (including subdirectories) and rename
    all files that match the patterns above.
    """

    app_logger.info(f"********************// Rename Directory Files //********************")

    for subdir, _, files in os.walk(directory):
        for filename in files:
            old_path = os.path.join(subdir, filename)
            new_name = get_renamed_filename(filename)

            if new_name and new_name != filename:
                new_path = os.path.join(subdir, new_name)
                app_logger.info(f"Renaming:\n  {old_path}\n  --> {new_path}\n")
                os.rename(old_path, new_path)

def rename_file(file_path):
    """
    Renames a single file if it matches either pattern using the logic
    in get_renamed_filename().
    """
    app_logger.info(f"********************// Rename Single File //********************")

    directory, filename = os.path.split(file_path)
    new_name = get_renamed_filename(filename)

    if new_name and new_name != filename:
        new_path = os.path.join(directory, new_name)
        app_logger.info(f"Renaming:\n  {file_path}\n  --> {new_path}\n")
        os.rename(file_path, new_path)
        return new_path
    else:
        app_logger.info("No renaming pattern matched or no change needed.")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        app_logger.info("No directory provided!")
    else:
        directory = sys.argv[1]
        rename_files(directory)
