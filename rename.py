import sys
import os
import re

def rename_files(directory):
    pattern = re.compile(
        r'^(.*?)\s*(\d{2,3}|v\d{2,3})(.*?)(\.\w+)$',
        re.IGNORECASE
    )

    for subdir, _, files in os.walk(directory):
        for filename in files:
            old_path = os.path.join(subdir, filename)
            
            match = pattern.match(filename)
            if match:
                raw_title  = match.group(1)   # e.g. "Series Name"
                issue_part = match.group(2)   # e.g. "002", "23", "v01", "v12"
                middle     = match.group(3)   # e.g. " c2c (July 1981) (A-Team-DCP)"
                extension  = match.group(4)   # e.g. ".cbr"

                # Clean the title: underscores -> spaces, strip
                clean_title = raw_title.replace('_', ' ').strip()
                
                # Determine how to represent the issue string
                # If it starts with 'v', keep "vXX" as is; otherwise numeric zero-pad.
                if issue_part.lower().startswith('v'):
                    # e.g. 'v01', 'v12', 'v123' -> keep as is
                    final_issue = issue_part
                else:
                    # purely numeric (2 or 3 digits)
                    final_issue = f"{int(issue_part):03d}"

                # Find parentheses groups in 'middle' to detect a 4-digit year (if any)
                paren_groups = re.findall(r'\(([^)]*)\)', middle)
                
                found_year = None
                for group_text in paren_groups:
                    # Look for a 4-digit year
                    # e.g. "July 1981" -> 1981
                    year_match = re.search(r'\b(\d{4})\b', group_text)
                    if year_match:
                        found_year = year_match.group(1)
                        break  # stop at first found year

                # Build the new filename
                if found_year:
                    # keep the (YEAR)
                    new_filename = f"{clean_title} {final_issue} ({found_year}){extension}"
                else:
                    # no year found -> no parentheses
                    new_filename = f"{clean_title} {final_issue}{extension}"

                new_path = os.path.join(subdir, new_filename)

                # Rename if there's an actual change
                if new_path != old_path:
                    print(f"Renaming:\n  {old_path}\n  --> {new_path}\n")
                    os.rename(old_path, new_path)

if __name__ == "__main__":
    # The directory path is passed as the first argument
    if len(sys.argv) < 2:
        print("No directory provided!")
    else:
        directory = sys.argv[1]
        rename_files(directory)
