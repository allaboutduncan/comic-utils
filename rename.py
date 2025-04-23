import sys
import os
import re
from app_logging import app_logger
from helpers import is_hidden

# -------------------------------------------------------------------
#  Pattern for Volume + Issue, e.g.:
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
# Pattern for explicit hash‐issue notation, e.g.:
#   "Title 2 #10 (2018).cbz"
#   Group(1) ⇒ "Title 2"
#   Group(2) ⇒ "10"
#   Group(3) ⇒ " (2018)"
#   Group(4) ⇒ ".cbz"
# -------------------------------------------------------------------
ISSUE_HASH_PATTERN = re.compile(
    r'^(.*?)\s*#\s*(\d{1,3})\b(.*)(\.\w+)$',
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
# New pattern for cases where the issue number comes after the year.
# e.g. "Spider-Man 2099 (1992) #44 (digital) (Colecionadores.GO).cbz"
#   Group(1) => Title (e.g. "Spider-Man 2099")
#   Group(2) => Year (e.g. "1992")
#   Group(3) => Issue number (e.g. "#44")
#   Group(4) => Extra text (ignored)
#   Group(5) => Extension (e.g. ".cbz")
# -------------------------------------------------------------------
ISSUE_AFTER_YEAR_PATTERN = re.compile(
    r'^(.*?)\s*\((\d{4})\)\s*(#\d{1,3})(.*)(\.\w+)$',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Pattern for series-number + issue-number with no “v” or “#”
# e.g. "Injustice 2 001 (2018).cbz"
#   Group(1) ⇒ "Injustice"
#   Group(2) ⇒ "2"
#   Group(3) ⇒ "001"
#   Group(4) ⇒ " (2018)"
#   Group(5) ⇒ ".cbz"
# -------------------------------------------------------------------
SERIES_ISSUE_PATTERN = re.compile(
    r'^(.*?)\s+(\d{1,3})\s+(\d{1,3})\b(.*)(\.\w+)$',
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


def parentheses_replacer(match):
    """
    Process a parentheses group:
      - If it contains a 4-digit year, return just that year in parentheses.
      - Otherwise, remove the entire parentheses group.
    """
    # Strip the outer parentheses
    inner_text = match.group(0)[1:-1]
    # Look for a 4-digit year
    year_match = re.search(r'\d{4}', inner_text)
    if year_match:
        year = year_match.group(0)
        return f"({year})"
    return ''


def clean_filename_pre(filename):
    """
    Pre-process the filename to:
      1) Remove anything in [brackets].
      2) Process parentheses:
         - If a 4-digit year is present, keep only that year.
         - Otherwise, remove the parentheses entirely.
      3) Handle dash-separated numbers:
         - Replace patterns like 'YYYY-XX' or 'YYYY-YYYY' with 'YYYY'.
         - Remove any other dash-separated numbers (e.g. '01-05').
      4) Remove " - Issue" from the filename.
    """
    filename = filename.replace('_', ' ')

    # 1) Remove bracketed text [ ... ]
    filename = re.sub(r'\[.*?\]', '', filename)

    # 2) Process parentheses using the helper
    filename = re.sub(r'\([^)]*\)', parentheses_replacer, filename)

    # 3a) Replace 4-digit–dash–2-digit (e.g. "2018-04") with the 4-digit year.
    filename = re.sub(r'\b(\d{4})-\d{2}\b', r'\1', filename)
    # 3b) Replace 4-digit–dash–4-digit (e.g. "1989-1990") with the first 4-digit year.
    filename = re.sub(r'\b(\d{4})-\d{4}\b', r'\1', filename)
    # 3c) Remove any other dash-separated numbers (e.g. "01-05")
    filename = re.sub(r'\b\d+(?:-\d+)+\b', '', filename)

    # 4) Remove " - Issue" from the filename
    filename = re.sub(r'\s*-\s*Issue\b', '', filename, flags=re.IGNORECASE)

    # Trim extra spaces that might result
    filename = re.sub(r'\s+', ' ', filename).strip()

    return filename


def clean_directory_name(directory_name):
    """
    Pre-process the directory name using the same rules as the filename:
      1) Remove anything in [brackets].
      2) Remove parentheses that don't contain a 4-digit year.
      3) If a parentheses contains a 4-digit year followed by -XX (month),
         remove that -XX piece (e.g. "2023-04" -> "2023").
      4) Remove " - Issue" from the directory name.
    """
    return clean_filename_pre(directory_name)


def get_renamed_filename(filename):
    """
    Given a single filename (no directory path):
      1) Pre-clean the filename by removing bracketed text,
         processing parentheses (keeping only 4-digit years),
         and removing dash-separated numbers.
      2) Try VOLUME_ISSUE_PATTERN first (e.g. "Title v3 051 (2018).ext").
      3) If it fails, try the single ISSUE_PATTERN.
      4) Next, try ISSUE_AFTER_YEAR_PATTERN for cases where the issue number follows the year.
      5) If that fails, try FALLBACK_PATTERN for just (YYYY).
      6) If none match, return None.
    """
    # Pre-processing step
    cleaned_filename = clean_filename_pre(filename)

    # ==========================================================
    # 1) VOLUME + ISSUE pattern (e.g. "Comic Name v3 051 (2018).ext")
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
    # 2) Hash‐issue pattern (explicit "#NNN"): catch before bare digits
    #    e.g. "Injustice 2 #1 (2018).cbz"
    # ==========================================================
    hash_match = ISSUE_HASH_PATTERN.match(cleaned_filename)
    if hash_match:
        raw_title, issue_num, middle, extension = hash_match.groups()

        clean_title = raw_title.replace('_', ' ').strip()
        final_issue = f"{int(issue_num):03d}"

        # Try to pull a year out of any parentheses in `middle`
        found_year = None
        for group_text in re.findall(r'\(([^)]*)\)', middle):
            if year := re.search(r'\b(\d{4})\b', group_text):
                found_year = year.group(1)
                break

        if found_year:
            new_filename = f"{clean_title} {final_issue} ({found_year}){extension}"
        else:
            new_filename = f"{clean_title} {final_issue}{extension}"

        return new_filename

    # ==========================================================
    # 2) Series-number + issue-number (no “v”, no “#”)
    #    e.g. "Injustice 2 001 (2018).cbz"
    # ==========================================================
    series_match = SERIES_ISSUE_PATTERN.match(cleaned_filename)
    if series_match:
        raw_title, series_num, issue_num, middle, extension = series_match.groups()

        # Keep the series number in the title
        clean_title = f"{raw_title.replace('_', ' ').strip()} {series_num}"
        final_issue = f"{int(issue_num):03d}"

        # Pull out a 4-digit year if present
        found_year = None
        for grp in re.findall(r'\(([^)]*)\)', middle):
            if ym := re.search(r'\b(\d{4})\b', grp):
                found_year = ym.group(1)
                break

        if found_year:
            return f"{clean_title} {final_issue} ({found_year}){extension}"
        return f"{clean_title} {final_issue}{extension}"

    # ==========================================================
    # 3) Single ISSUE pattern (no separate "volume" token)
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
    # 4) ISSUE number AFTER YEAR pattern
    #    e.g. "Spider-Man 2099 (1992) #44 (digital) (Colecionadores.GO).cbz"
    # ==========================================================
    issue_after_year_match = ISSUE_AFTER_YEAR_PATTERN.match(cleaned_filename)
    if issue_after_year_match:
        raw_title, year, issue, extra, extension = issue_after_year_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        new_filename = f"{clean_title} {issue} ({year}){extension}"
        return new_filename

    # ==========================================================
    # 5) Fallback: Title (YYYY) anything .ext
    #    e.g. "Comic Name (2018) some extra.cbz" -> "Comic Name (2018).cbz"
    # ==========================================================
    fallback_match = FALLBACK_PATTERN.match(cleaned_filename)
    if fallback_match:
        raw_title, found_year, _, extension = fallback_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        new_filename = f"{clean_title} ({found_year}){extension}"
        return new_filename

    # ==========================================================
    # 6) No match => return None
    # ==========================================================
    return None


def rename_files(directory):
    """
    Walk through the given directory (including subdirectories) and rename
    all files that match the patterns above, skipping hidden files.
    """

    app_logger.info("********************// Rename Directory Files //********************")

    for subdir, dirs, files in os.walk(directory):
        # Skip hidden directories.
        dirs[:] = [d for d in dirs if not is_hidden(os.path.join(subdir, d))]
        for filename in files:
            old_path = os.path.join(subdir, filename)
            # Skip hidden files.
            if is_hidden(old_path):
                app_logger.info(f"Skipping hidden file: {old_path}")
                continue

            new_name = get_renamed_filename(filename)
            if new_name and new_name != filename:
                new_path = os.path.join(subdir, new_name)
                app_logger.info(f"Renaming:\n  {old_path}\n  --> {new_path}\n")
                os.rename(old_path, new_path)


def rename_file(file_path):
    """
    Renames a single file if it matches either pattern using the logic
    in get_renamed_filename(), skipping hidden files.
    """
    app_logger.info("********************// Rename Single File //********************")

    # Skip hidden files using the is_hidden helper.
    if is_hidden(file_path):
        app_logger.info(f"Skipping hidden file: {file_path}")
        return None

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
