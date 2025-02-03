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
    logger.info(f"<strong>Handling CBZ file:</strong> {file_path}")
    
    if not file_path.lower().endswith('.cbz'):
        logger.info("Provided file is not a CBZ file.")
        return

    base_name = os.path.splitext(file_path)[0]  # Removes the .cbz extension
    zip_path = base_name + '.zip'
    folder_name = base_name + '_folder'
    
    logger.info(f"<strong>Processing CBZ:</strong> {file_path} --> {zip_path}")

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
        process_image(folder_name)

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

        logger.info(f"<strong>Successfully re-compressed:</strong> {file_path}")

        # Step 7: Delete the .bak file
        os.remove(bak_file_path)

    except Exception as e:
        logger.error(f"<strong>Failed to process {file_path}:</strong> {e}")
    finally:
        # Clean up the temporary folder
        if os.path.exists(folder_name):
            shutil.rmtree(folder_name)


def process_image(directory: str) -> None:
    # Ensure the directory exists
    if not os.path.exists(directory):
        print(f"Directory {directory} does not exist.")
        return

    # Recursively search for files in the directory and subdirectories
    def find_images(dir_path):
        for root, _, files in os.walk(dir_path):
            for file in files:
                if file != "ComicInfo.xml":
                    yield os.path.join(root, file)

    # Get the first image file found
    image_files = list(find_images(directory))
    if not image_files:
        print("No files found in the directory or its subdirectories.")
        return

    first_image_path = image_files[0]
    file_name, file_extension = os.path.splitext(first_image_path)

    try:
        # Open the image
        with Image.open(first_image_path) as img:
            width, height = img.size

            # Split the image in half
            right_half = (width // 2, 0, width, height)

            # Save the original image by appending "b" to the file name
            backup_path = f"{file_name}b{file_extension}"
            img.save(backup_path)

            # Save the right half by appending "a" to the file name
            right_half_img = img.crop(right_half)
            new_image_path = f"{file_name}a{file_extension}"
            right_half_img.save(new_image_path)

        # Delete the original image
        os.remove(first_image_path)

        print(f"<strong>Processed:</strong> {os.path.basename(first_image_path)}<br /> original saved as {backup_path}, <br />right half saved as {new_image_path}.")
    except Exception as e:
        print(f"Error processing the image: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("No file provided!")
    else:
        file_path = sys.argv[1]
        handle_cbz_file(file_path)
