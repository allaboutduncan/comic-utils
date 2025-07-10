import os
import sys
import subprocess
import zipfile
import shutil
from app_logging import app_logger
from helpers import extract_rar_with_unar


def handle_cbz_file(file_path):
    """
    Handle the conversion of a .cbz file: unzip, rename, compress, and clean up.

    :param file_path: Path to the .cbz file.
    :return: None
    """
    app_logger.info(f"Handling CBZ file: {file_path}")
    
    if not file_path.lower().endswith('.cbz'):
        app_logger.info("Provided file is not a CBZ file.")
        return

    base_name = os.path.splitext(file_path)[0]  # Removes the .cbz extension
    zip_path = base_name + '.zip'
    folder_name = base_name + '_folder'
    
    app_logger.info(f"Processing CBZ: {file_path} -> {zip_path}")

    try:
        # Step 1: Rename .cbz to .zip
        os.rename(file_path, zip_path)

        # Step 2: Create a folder with the file name
        os.makedirs(folder_name, exist_ok=True)

        # Step 3: Unzip the .zip file contents into the folder
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(folder_name)

        # Step 4: Rename the original .zip file to .bak
        bak_file_path = zip_path + '.bak'
        os.rename(zip_path, bak_file_path)

        # Step 5: Compress the folder contents back into a .cbz file
        with zipfile.ZipFile(file_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder_name):
                for file in files:
                    file_path_in_folder = os.path.join(root, file)
                    arcname = os.path.relpath(file_path_in_folder, folder_name)
                    zf.write(file_path_in_folder, arcname)

        app_logger.info(f"Successfully re-compressed: {file_path}")

        # Step 6: Delete the .bak file
        os.remove(bak_file_path)

    except Exception as e:
        app_logger.error(f"Failed to process {file_path}: {e}")
    finally:
        # Clean up the temporary folder
        if os.path.exists(folder_name):
            shutil.rmtree(folder_name)

def convert_to_cbz(file_path):
    """
    Convert a single RAR or CBR file to a ZIP file using unar for extraction.

    :param file_path: Path to the RAR or CBR file.
    :return: None
    """
    app_logger.info(f"********************// Single File Conversion //********************")
    app_logger.info(f"-- Path to file: {file_path}")
    
    # Check if the file exists
    if not os.path.exists(file_path):
        app_logger.error(f"File does not exist: {file_path}")
        return
    
    # Check if it's a .rar or .cbr file
    if file_path.lower().endswith(('.rar', '.cbr')):
        app_logger.info("Converting RAR/CBR to CBZ format")
        
        base_name = os.path.splitext(file_path)[0]  # Removes the extension
        temp_extraction_dir = f"temp_{base_name}"

        # Create a temporary directory for extraction
        os.makedirs(temp_extraction_dir, exist_ok=True)
        
        try:
            # Extract the RAR or CBR file into the temp directory
            extraction_success = extract_rar_with_unar(file_path, temp_extraction_dir)
            
            if not extraction_success:
                app_logger.error(f"Failed to extract any files from {file_path}")
                return

            # Create the final CBZ file from extracted content
            cbz_file_path = base_name + '.cbz'
            extracted_count = 0
            
            with zipfile.ZipFile(cbz_file_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(temp_extraction_dir):
                    for file in files:
                        file_path_in_dir = os.path.join(root, file)
                        arcname = os.path.relpath(file_path_in_dir, temp_extraction_dir)
                        zf.write(file_path_in_dir, arcname)
                        extracted_count += 1

            app_logger.info(f"Successfully converted: {file_path} to {cbz_file_path} ({extracted_count} files)")

            # Delete the original file (RAR or CBR)
            os.remove(file_path)
        except Exception as e:
            app_logger.error(f"Failed to convert {file_path}: {e}")
            # Don't re-raise the exception here to allow cleanup to proceed
        finally:
            # Clean up temporary extraction directory
            if os.path.exists(temp_extraction_dir):
                try:
                    shutil.rmtree(temp_extraction_dir)
                    app_logger.info(f"Cleaned up temporary directory: {temp_extraction_dir}")
                except Exception as cleanup_error:
                    app_logger.error(f"Failed to clean up temporary directory {temp_extraction_dir}: {cleanup_error}")

    # Check if it's a .cbz file
    elif file_path.lower().endswith('.cbz'):
        handle_cbz_file(file_path)

    else:
        app_logger.info("File is not a recognized .rar, .cbr, or .cbz file.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        app_logger.error("No file provided!")
    else:
        file_path = sys.argv[1]
        convert_to_cbz(file_path)
