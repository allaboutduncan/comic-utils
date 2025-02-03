import sys
import os
import re
import logging

IGNORE = [os.environ.get("IGNORE")]

# Ensure the log directory exists
LOG_FILE_PATH = "/logs/log.txt"
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)

# Configure logging
logging.basicConfig(
    filename=LOG_FILE_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def check_missing_issues(root_directory):
    """
    Recursively scans 'root_directory' (including sub-directories) for 
    filenames in one of these forms:
        <Series Name> <Issue Number> (<Year>).cbz
        <Series Name> <Issue Number> (<Year>).cbr
        <Series Name> <Issue Number>.cbz
        <Series Name> <Issue Number>.cbr

    When multiple files in the same directory have the same series name, 
    but some include a year and others do not, they are treated as the 
    same series, adopting the year if at least one file has it.

    Any filename containing any of the substrings in IGNORE (case-insensitive)
    is skipped. We also normalize curly quotes ('’‘“”) to straight quotes 
    ('"') before checking.

    All missing issues (e.g., #003, #004) are written to a single 
    'missing.txt' in 'root_directory'. If a consecutive run of missing issues
    is 50 or more, a single condensed line is used to report that run.

    Now updated to print the directory path once (for each series) only if
    there are missing issues for that series.
    """

    # Regex to capture series name, issue number, optional year, extension
    pattern = re.compile(r'^(.+?)\s+#?(\d+)(?:\s*\((\d+)\))?\.(?:cbz|cbr)$', re.IGNORECASE)

    data_dict = {}
    not_matched = []

    # Walk through all files in the root directory (recursively)
    for dirpath, dirnames, filenames in os.walk(root_directory):
        for fname in filenames:
            fname_stripped = fname.strip()

            # Normalize curly quotes to straight quotes
            normalized_fname = (fname_stripped
                                .replace("‘", "'")
                                .replace("’", "'")
                                .replace("“", '"')
                                .replace("”", '"'))

            # Check if this file should be ignored (case-insensitive substring check)
            if any(ignore_word and ignore_word.lower() in normalized_fname.lower() for ignore_word in IGNORE):
                # If needed, uncomment to see which files are being skipped:
                # print(f"SKIPPING (ignored): {os.path.join(dirpath, fname)}")
                continue

            # Attempt to match with the comic filename pattern
            match = pattern.match(fname_stripped)
            if match:
                series_name = match.group(1).strip()
                issue_str   = match.group(2)
                year_str    = match.group(3)  # may be None if no (Year)

                try:
                    issue_num = int(issue_str)
                except ValueError:
                    not_matched.append(os.path.join(dirpath, fname))
                    continue

                key = (dirpath, series_name.lower())
                if key not in data_dict:
                    data_dict[key] = {
                        "series_name": series_name,
                        "years": set(),
                        "issues": set()
                    }

                if year_str:
                    data_dict[key]["years"].add(year_str)
                data_dict[key]["issues"].add(issue_num)
            else:
                not_matched.append(os.path.join(dirpath, fname))

    if not data_dict:
        print("No matching comic files found in the entire directory tree (or all were ignored).")
        if not_matched:
            print("Files not matched (or skipped):")
            for nm in not_matched:
                print("  ", nm)
        return

    missing_file_path = os.path.join(root_directory, "missing.txt")
    num_missing_total = 0

    with open(missing_file_path, 'w') as f_out:
        # Sort keys for consistent output
        for (dirpath, series_name_lower) in sorted(data_dict.keys()):
            info = data_dict[(dirpath, series_name_lower)]

            series_name_original = info["series_name"]
            years_found          = info["years"]
            issues_found         = sorted(info["issues"])

            if not years_found:
                adopted_year = None
            elif len(years_found) == 1:
                adopted_year = next(iter(years_found))  # or list(years_found)[0]
            else:
                # if multiple years, adopt earliest (or first) year
                adopted_year = min(years_found)

            if not issues_found:
                # No valid issues found (unlikely if matched, but just in case)
                continue

            max_issue = issues_found[-1]

            # Identify all consecutive ranges of missing issues
            missing_runs = []
            start_run = None

            for i in range(1, max_issue + 1):
                if i not in issues_found:
                    if start_run is None:
                        start_run = i
                else:
                    if start_run is not None:
                        # We were in a missing run; close it
                        missing_runs.append((start_run, i - 1))
                        start_run = None

            # If we end the loop in a run, close it out
            if start_run is not None:
                missing_runs.append((start_run, max_issue))

            if missing_runs:
                # Only now do we print/write the directory path 
                # (i.e., for this series) if there are missing issues
                f_out.write(f"Directory: {dirpath}\n")

            # Write out the missing info for each run
            for (start_miss, end_miss) in missing_runs:
                run_length = end_miss - start_miss + 1
                num_missing_total += run_length

                # If the run is large (>= 50), condense it
                if run_length >= 50:
                    if adopted_year:
                        missing_str = (
                            f"{series_name_original} "
                            f"{start_miss:03d}-{end_miss:03d} ({adopted_year}) "
                            f"[Total missing: {run_length}]"
                        )
                    else:
                        missing_str = (
                            f"{series_name_original} "
                            f"{start_miss:03d}-{end_miss:03d} "
                            f"[Total missing: {run_length}]"
                        )
                    f_out.write(missing_str + "\n")
                else:
                    # List each missing issue individually
                    for m in range(start_miss, end_miss + 1):
                        if adopted_year:
                            missing_str = f"{series_name_original} {m:03d} ({adopted_year}).cbz"
                        else:
                            missing_str = f"{series_name_original} {m:03d}.cbz"
                        f_out.write(missing_str + "\n")

            # Add a blank line if any missing issues were found, 
            # to space out sections for each series
            if missing_runs:
                f_out.write("\n")

    if num_missing_total == 0:
        os.remove(missing_file_path)
        print("No missing issues found.")
    else:
        print(f"Found {num_missing_total} missing issues.")


    if not_matched:
        print("The following files did NOT match our pattern (or were skipped):")
        for nm in not_matched:
            print("  ", nm)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logging.info("No directory provided!")
    else:
        directory = sys.argv[1]
        check_missing_issues(directory)
