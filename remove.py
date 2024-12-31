import os
import logging
import sys
import zipfile
import shutil
from PIL import Image, ImageFilter


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)


def handle_cbz_file(file_path):
    """
    Handle the conversion of a .cbz file: unzip, process images, compress, and clean up.

    :param file_path: Path to the .cbz file.
    :return: None
    """
    logger.info(f"Handling CBZ file: {file_path}")
    
    if not file_path.lower().endswith('.cbz'):
        logger.info("Provided file is not a CBZ file.")
        return

    base_name = os.path.splitext(file_path)[0]  # Removes the .cbz extension
    zip_path = base_name + '.zip'
    folder_name = base_name + '_folder'
    
    logger.info(f"Processing CBZ: {file_path} -> {zip_path}")

    try:
        # Step 1: Rename .cbz to .zip
        os.rename(file_path, zip_path)

        # Step 2: Create a folder with the file name
        os.makedirs(folder_name, exist_ok=True)

        # Step 3: Unzip the .zip file contents into the folder
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(folder_name)
        
        # Step 4: Process the extracted images
        #logger.info(f"Processing images in folder: {folder_name}")
        remove_first_jpg_file(folder_name)

        # Step 5: Rename the original .zip file to .bak
        bak_file_path = zip_path + '.bak'
        os.rename(zip_path, bak_file_path)

        # Step 6: Compress the folder contents back into a .cbz file
        with zipfile.ZipFile(file_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder_name):
                for file in files:
                    file_path_in_folder = os.path.join(root, file)
                    arcname = os.path.relpath(file_path_in_folder, folder_name)
                    zf.write(file_path_in_folder, arcname)

        logger.info(f"Successfully re-compressed: {file_path}")

        # Step 7: Delete the .bak file
        os.remove(bak_file_path)

    except Exception as e:
        logger.error(f"Failed to process {file_path}: {e}")
    finally:
        # Clean up the temporary folder
        if os.path.exists(folder_name):
            shutil.rmtree(folder_name)


def remove_first_jpg_file(dir_path):
    # Check if the given directory exists
    if not os.path.exists(dir_path):
        logger.info(f"The directory {dir_path} does not exist.")
        return
    
    # First, try to find a .jpg file in the main directory
    for root, dirs, files in os.walk(dir_path):
        # Skip subdirectories if we already found a .jpg in the current directory
        for file in files:
            if file.lower().endswith('.jpg'):
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    logger.info(f"Removed: {file_path}")
                    return
                except Exception as e:
                    logger.info(f"Failed to remove {file_path}. Error: {e}")
                return
    
    logger.info(f"No .jpg files found in {dir_path} or its subdirectories.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("No file provided!")
    else:
        file_path = sys.argv[1]
        handle_cbz_file(file_path)
