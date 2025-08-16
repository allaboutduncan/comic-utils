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
# Pattern for Volume + Subtitle (no issue number), e.g.:
#   "Infinity 8 v03 - The Gospel According to Emma (2019).cbr"
#   Group(1) => "Infinity 8"
#   Group(2) => "v03"
#   Group(3) => " - The Gospel According to Emma (2019)"
#   Group(4) => ".cbr"
# -------------------------------------------------------------------
VOLUME_SUBTITLE_PATTERN = re.compile(
    r'^(.*?)\s+(v\d{1,3})\s+(-\s*[^-]+.*?)(\.\w+)$',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Pattern for just "Title YEAR anything.ext"
# e.g. "Hulk vs. The Marvel Universe 2008 Digital4K.cbz" → "Hulk vs. The Marvel Universe (2008).cbz"
# -------------------------------------------------------------------
TITLE_YEAR_PATTERN = re.compile(
    r'^(.*?)\b((?:19|20)\d{2})\b(.*)(\.\w+)$',
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

# -------------------------------------------------------------------
# Pattern for issue number + year in parentheses, e.g.:
#   "Leonard Nimoy's Primortals (00 1996).cbz"
#   Group(1) => "Leonard Nimoy's Primortals"
#   Group(2) => "00"
#   Group(3) => "1996"
#   Group(4) => ".cbz"
# -------------------------------------------------------------------
ISSUE_YEAR_PARENTHESES_PATTERN = re.compile(
    r'^(.*?)\s*\((\d{1,3})\s+(\d{4})\)(.*)(\.\w+)$',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Pattern for Title, YYYY-MM-DD (NN) format, e.g.:
#   "Justice League Europe, 1990-02-00 ( 13) (digital) (OkC.O.M.P.U.T.O.-Novus-HD).cbz"
#   "Blue Devil, 1984-04-00 (_01) (digital) (Glorith-Novus-HD).cbz"
#   Group(1) => "Justice League Europe" or "Blue Devil"
#   Group(2) => "1990" or "1984"
#   Group(3) => "13" or "_01"
#   Group(4) => " (digital) (OkC.O.M.P.U.T.O.-Novus-HD)" or " (digital) (Glorith-Novus-HD)"
#   Group(5) => ".cbz"
# -------------------------------------------------------------------
TITLE_COMMA_YEAR_ISSUE_PATTERN = re.compile(
    r'^(.*?),\s*(\d{4})-\d{2}-\d{2}\s*\(\s*([_\d]\d{1,3})\s*\)(.*)(\.\w+)$',
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
    year_match = re.search(r'\b\d{4}\b', inner_text)
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
      1) Check for special case: issue number + year in parentheses (e.g. "Title (00 1996).ext")
      2) Pre-clean the filename by removing bracketed text,
         processing parentheses (keeping only 4-digit years),
         and removing dash-separated numbers.
      3) Try VOLUME_ISSUE_PATTERN first (e.g. "Title v3 051 (2018).ext").
      4) If it fails, try the single ISSUE_PATTERN.
      5) Next, try ISSUE_AFTER_YEAR_PATTERN for cases where the issue number follows the year.
      6) If that fails, try FALLBACK_PATTERN for just (YYYY).
      7) If none match, return None.
    """
    app_logger.info(f"Attempting to rename filename: {filename}")
    
    # ==========================================================
    # 0) Special case: Issue number + year in parentheses (BEFORE pre-cleaning)
    #    e.g. "Leonard Nimoy's Primortals (00 1996).cbz"
    # ==========================================================
    issue_year_paren_match = ISSUE_YEAR_PARENTHESES_PATTERN.match(filename)
    if issue_year_paren_match:
        app_logger.info(f"Matched ISSUE_YEAR_PARENTHESES_PATTERN for: {filename}")
        raw_title, issue_num, year, extra, extension = issue_year_paren_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        final_issue = f"{int(issue_num):03d}"
        new_filename = f"{clean_title} {final_issue} ({year}){extension}"
        return new_filename

    # ==========================================================
    # 0.5) Special case: Title, YYYY-MM-DD (NN) format (BEFORE pre-cleaning)
    #    e.g. "Justice League Europe, 1990-02-00 ( 13) (digital) (OkC.O.M.P.U.T.O.-Novus-HD).cbz"
    #    e.g. "Blue Devil, 1984-04-00 (_01) (digital) (Glorith-Novus-HD).cbz"
    # ==========================================================
    title_comma_year_issue_match = TITLE_COMMA_YEAR_ISSUE_PATTERN.match(filename)
    if title_comma_year_issue_match:
        app_logger.info(f"Matched TITLE_COMMA_YEAR_ISSUE_PATTERN for: {filename}")
        raw_title, year, issue_num, extra, extension = title_comma_year_issue_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        
        # Handle issue numbers that may have underscore prefixes
        if issue_num.startswith('_'):
            # Remove underscore and zero-pad the numeric part
            numeric_part = issue_num[1:]  # Remove the underscore
            final_issue = f"{int(numeric_part):03d}"
        else:
            # Regular numeric issue number
            final_issue = f"{int(issue_num):03d}"
            
        new_filename = f"{clean_title} {final_issue} ({year}){extension}"
        return new_filename

    # Pre-processing step
    cleaned_filename = clean_filename_pre(filename)

    # ==========================================================
    # 1) VOLUME + ISSUE pattern (e.g. "Comic Name v3 051 (2018).ext")
    # ==========================================================
    vol_issue_match = VOLUME_ISSUE_PATTERN.match(cleaned_filename)
    if vol_issue_match:
        app_logger.info(f"Matched VOLUME_ISSUE_PATTERN for: {cleaned_filename}")
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
        app_logger.info(f"Matched ISSUE_HASH_PATTERN for: {cleaned_filename}")
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
    # 3) VOLUME + SUBTITLE pattern (e.g. "Infinity 8 v03 - The Gospel According to Emma (2019).cbr")
    # ==========================================================
    vol_subtitle_match = VOLUME_SUBTITLE_PATTERN.match(cleaned_filename)
    if vol_subtitle_match:
        app_logger.info(f"Matched VOLUME_SUBTITLE_PATTERN for: {cleaned_filename}")
        raw_title, volume_part, subtitle_part, extension = vol_subtitle_match.groups()

        # Clean the title: underscores -> spaces, then strip
        clean_title = raw_title.replace('_', ' ').strip()

        # volume_part (e.g. "v03") - keep as-is
        final_volume = volume_part.strip()

        # Extract year from subtitle and clean it up
        found_year = None
        clean_subtitle = subtitle_part.strip()
        
        # Look for a 4-digit year in parentheses
        year_match = re.search(r'\((\d{4})\)', subtitle_part)
        if year_match:
            found_year = year_match.group(1)
            # Remove everything after the year parentheses, but keep the subtitle clean
            clean_subtitle = subtitle_part[:year_match.start()].strip()
            # Also remove any trailing parentheses that might be left
            clean_subtitle = re.sub(r'\s*\([^)]*\)\s*$', '', clean_subtitle).strip()
        
        if found_year:
            new_filename = f"{clean_title} {final_volume} {clean_subtitle} ({found_year}){extension}"
        else:
            new_filename = f"{clean_title} {final_volume} {clean_subtitle}{extension}"

        return new_filename

    # ==========================================================
    # 4) Series-number + issue-number (no “v”, no “#”)
    #    e.g. "Injustice 2 001 (2018).cbz"
    # ==========================================================
    series_match = SERIES_ISSUE_PATTERN.match(cleaned_filename)
    if series_match:
        app_logger.info(f"Matched SERIES_ISSUE_PATTERN for: {cleaned_filename}")
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
    # 5) Single ISSUE pattern (no separate "volume" token)
    #    e.g. "Comic Name 051 (2018).cbz" or "Comic Name v3 (2018).cbz"
    # ==========================================================
    issue_match = ISSUE_PATTERN.match(cleaned_filename)
    if issue_match:
        app_logger.info(f"Matched ISSUE_PATTERN for: {cleaned_filename}")
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
    # 6) ISSUE number AFTER YEAR pattern
    #    e.g. "Spider-Man 2099 (1992) #44 (digital) (Colecionadores.GO).cbz"
    # ==========================================================
    issue_after_year_match = ISSUE_AFTER_YEAR_PATTERN.match(cleaned_filename)
    if issue_after_year_match:
        app_logger.info(f"Matched ISSUE_AFTER_YEAR_PATTERN for: {cleaned_filename}")
        raw_title, year, issue, extra, extension = issue_after_year_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        new_filename = f"{clean_title} {issue} ({year}){extension}"
        return new_filename

    # ==========================================================
    # 7) Title with just YEAR (no volume or issue)
    #     e.g. "Hulk vs. The Marvel Universe 2008 Digital.cbz"
    # ==========================================================
    title_year_match = TITLE_YEAR_PATTERN.match(cleaned_filename)
    if title_year_match:
        app_logger.info(f"Matched TITLE_YEAR_PATTERN for: {cleaned_filename}")
        raw_title, found_year, _, extension = title_year_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        # Remove any trailing opening parenthesis that might have been captured
        clean_title = clean_title.rstrip(' (')
        return f"{clean_title} ({found_year}){extension}"

    # ==========================================================
    # 8) Fallback: Title (YYYY) anything .ext
    #    e.g. "Comic Name (2018) some extra.cbz" -> "Comic Name (2018).cbz"
    # ==========================================================
    fallback_match = FALLBACK_PATTERN.match(cleaned_filename)
    if fallback_match:
        app_logger.info(f"Matched FALLBACK_PATTERN for: {cleaned_filename}")
        raw_title, found_year, _, extension = fallback_match.groups()
        clean_title = raw_title.replace('_', ' ').strip()
        new_filename = f"{clean_title} ({found_year}){extension}"
        return new_filename

    # ==========================================================
    # 9) No match => return None
    # ==========================================================
    app_logger.info(f"No pattern matched for: {filename}")
    return None


def rename_files(directory):
    """
    Walk through the given directory (including subdirectories) and rename
    all files that match the patterns above, skipping hidden files.
    """

    app_logger.info("********************// Rename Directory Files //********************")
    app_logger.info(f"Starting rename process for directory: {directory}")
    app_logger.info(f"Current working directory: {os.getcwd()}")
    #app_logger.info(f"Directory exists: {os.path.exists(directory)}")
    #app_logger.info(f"Directory is directory: {os.path.isdir(directory)}")
    
    files_processed = 0
    files_renamed = 0

    for subdir, dirs, files in os.walk(directory):
        # Skip hidden directories.
        dirs[:] = [d for d in dirs if not is_hidden(os.path.join(subdir, d))]
        #app_logger.info(f"Processing subdirectory: {subdir} with {len(files)} files")
        
        # List all files in this subdirectory
        #for filename in files:
            #app_logger.info(f"Found file: {filename} in {subdir}")
        
        for filename in files:
            files_processed += 1
            old_path = os.path.join(subdir, filename)
            
            app_logger.info(f"Processing file: {filename}")
            #app_logger.info(f"Full old path: {old_path}")
            #app_logger.info(f"File exists: {os.path.exists(old_path)}")
            #app_logger.info(f"File size: {os.path.getsize(old_path) if os.path.exists(old_path) else 'N/A'}")
            
            # Skip hidden files.
            if is_hidden(old_path):
                app_logger.info(f"Skipping hidden file: {old_path}")
                continue

            app_logger.info(f"Processing file: {filename}")
            new_name = get_renamed_filename(filename)
            
            if new_name and new_name != filename:
                new_path = os.path.join(subdir, new_name)
                app_logger.info(f"Renaming:\n  {old_path}\n  --> {new_path}\n")
                try:
                    os.rename(old_path, new_path)
                    files_renamed += 1
                    #app_logger.info(f"Successfully renamed: {filename} -> {new_name}")
                    
                    # Verify the rename actually happened
                    if os.path.exists(new_path) and not os.path.exists(old_path):
                        app_logger.info(f"Rename verification successful: new file exists, old file removed")
                    else:
                        app_logger.warning(f"Rename verification failed: new file exists: {os.path.exists(new_path)}, old file exists: {os.path.exists(old_path)}")
                        
                except Exception as e:
                    app_logger.error(f"Failed to rename {filename}: {e}")
            else:
                if new_name is None:
                    app_logger.info(f"No rename pattern matched for: {filename}")
                else:
                    app_logger.info(f"No change needed for: {filename}")
    
    app_logger.info(f"Rename process complete. Processed {files_processed} files, renamed {files_renamed} files.")
    return files_renamed


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
