import os
import sys

def delete_file(file_path):
    """
    Deletes the file at the specified file_path.

    Args:
        file_path (str): The path to the file to be deleted.

    Returns:
        None

    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the file cannot be deleted due to permission issues.
        Exception: For any other exceptions that may occur.
    """
    try:
        if not os.path.isfile(file_path):
            print(f"ERROR: The file '{file_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
        
        os.remove(file_path)
        print(f"SUCCESS: The file '{file_path}' has been deleted.")
    
    except PermissionError:
        print(f"ERROR: Permission denied while trying to delete '{file_path}'.", file=sys.stderr)
        sys.exit(1)
    
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    if len(sys.argv) != 2:
        print("ERROR: Incorrect number of arguments.", file=sys.stderr)
        print("Usage: python delete.py <file_path>", file=sys.stderr)
        sys.exit(1)
    
    file_path = sys.argv[1]
    delete_file(file_path)

if __name__ == "__main__":
    main()
