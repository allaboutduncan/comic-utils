from PIL import Image, ImageEnhance, ImageFilter
from helpers import is_hidden, unzip_file, enhance_image
import os
import zipfile
import shutil
from app_logging import app_logger
import sys


def enhance_comic(file_path):
    # If the file is hidden, skip it
    if is_hidden(file_path):
        print(f"Skipping hidden file: {file_path}")
        return

    # Process only if the file is a ZIP archive with a .cbz extension.
    if file_path.lower().endswith('.cbz'):
        # Determine the backup file path (with .bak extension).
        bak_file_path = os.path.splitext(file_path)[0] + '.bak'
        
        # Check if the original .cbz file exists.
        if os.path.exists(file_path):
            # Rename the original .cbz file to .bak before extraction.
            os.rename(file_path, bak_file_path)
            print(f"Renamed '{file_path}' to '{bak_file_path}'")
        elif os.path.exists(bak_file_path):
            # The file may have already been renamed.
            print(f"File '{file_path}' not found; using backup '{bak_file_path}'")
        else:
            # Neither file exists – raise an error.
            raise FileNotFoundError(f"Neither {file_path} nor {bak_file_path} exists.")

        # Extract the ZIP archive from the backup file.
        extracted_dir = unzip_file(bak_file_path)
        print(f"Extracted to: {extracted_dir}")

        # Find all image files in the extracted directory.
        image_files = []
        for root, _, files in os.walk(extracted_dir):
            for file in files:
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    image_files.append(os.path.join(root, file))
        
        # Enhance each image file.
        for image_file in image_files:
            enhanced_image = enhance_image(image_file)
            # Build a temporary filename that keeps the original extension.
            base, ext = os.path.splitext(image_file)
            tmp_path = base + "_tmp" + ext  # e.g., image_tmp.jpg
            enhanced_image.save(tmp_path)
            # Atomically replace the original image with the enhanced one.
            os.replace(tmp_path, image_file)
        
        # Compress the enhanced files back into a ZIP archive with a .cbz extension.
        enhanced_cbz_path = os.path.splitext(file_path)[0] + '.cbz'
        with zipfile.ZipFile(enhanced_cbz_path, 'w') as cbz_file:
            for root, _, files in os.walk(extracted_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, extracted_dir)
                    cbz_file.write(full_path, relative_path)
        
        # Clean up the extracted directory.
        shutil.rmtree(extracted_dir)
        
        # Once processing is complete, delete the backup (.bak) file.
        os.remove(bak_file_path)
        print(f"Deleted backup file '{bak_file_path}'")
    else:
        # Enhance a single image file.
        enhance_image(file_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        app_logger.error("No file provided!")
    else:
        file_path = sys.argv[1]
        enhance_comic(file_path)
        app_logger.info(f"Starting Image Enhancement for: {file_path}")