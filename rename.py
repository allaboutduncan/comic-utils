import sys
import os
import re

def rename_files(directory):
    pattern = re.compile(r'^(.*?)\s*(\d{2,3})(.*?)(\.\w+)$', re.IGNORECASE)

    for subdir, _, files in os.walk(directory):
        for filename in files:
            old_path = os.path.join(subdir, filename)
            
            match = pattern.match(filename)
            if match:
                raw_title = match.group(1)       # e.g., "Title Name"
                issue_str = match.group(2)       # e.g., "002" or "23"
                middle    = match.group(3)       # e.g., " c2c (July 1981) (Scanner c2c)"
                extension = match.group(4)       # e.g., ".cbr"

                clean_title = raw_title.replace('_', ' ').strip()
                
                issue_num_padded = f"{int(issue_str):03d}"
                
                paren_groups = re.findall(r'\(([^)]*)\)', middle)
                
                found_year = None
                for group_text in paren_groups:
                    # If there's a 4-digit year in this group, we capture it.
                    year_match = re.search(r'\b(\d{4})\b', group_text)
                    if year_match:
                        found_year = year_match.group(1)  # e.g., "1981"
                        break  # stop at the first found year

                if found_year:
                    # If year, final format: "Title ISSUE (YEAR).ext"
                    new_filename = f"{clean_title} {issue_num_padded} ({found_year}){extension}"
                else:
                    # If no year found, just "Title ISSUE.ext"
                    new_filename = f"{clean_title} {issue_num_padded}{extension}"

                new_path = os.path.join(subdir, new_filename)

                # Only rename if there's an actual change
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
