import os
import sys
import subprocess
import zipfile
import shutil
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

def extract_rar_with_unar(rar_path, output_dir):
    """
    Extract a RAR file using the unar command-line tool.

    :param rar_path: Path to the RAR file.
    :param output_dir: Directory to extract the contents into.
    :return: None
    """
    try:
        subprocess.run(
            ["unar", "-o", output_dir, "-f", rar_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to extract {rar_path}: {e.stderr.decode().strip()}")

def convert_rar_to_zip_in_directory(directory):
    """
    Convert all RAR and CBR files in a directory to CBZ files using unar for extraction.

    :param directory: Path to the directory containing RAR and CBR files.
    :return: List of successfully converted files (without extensions)
    """
    os.makedirs(directory, exist_ok=True)
    converted_files = []

    for file_name in os.listdir(directory):
        # Process only .rar and .cbr files; skip .cbz and others
        if file_name.lower().endswith(('.rar', '.cbr')):
            rar_path = os.path.join(directory, file_name)
            temp_extraction_dir = os.path.join(directory, f"temp_{file_name[:-4]}")
            zip_path = os.path.join(directory, f"{file_name[:-4]}.cbz")

            logger.info(f"Converting: {rar_path} -> {zip_path}")
            try:
                os.makedirs(temp_extraction_dir, exist_ok=True)
                extract_rar_with_unar(rar_path, temp_extraction_dir)

                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(temp_extraction_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, temp_extraction_dir)
                            zf.write(file_path, arcname)

                logger.info(f"Successfully converted: {file_name}")
                converted_files.append(file_name[:-4])  # Track the file without extension

                # Delete the original RAR/CBR file
                os.remove(rar_path)
            except Exception as e:
                logger.error(f"Failed to convert {file_name}: {e}")
            finally:
                if os.path.exists(temp_extraction_dir):
                    shutil.rmtree(temp_extraction_dir)

    return converted_files

def main(directory):
    if not os.path.isdir(directory):
        logger.error(f"Directory '{directory}' does not exist.")
        return

    logger.info(f"Starting conversion in directory: {directory}")
    converted_files = convert_rar_to_zip_in_directory(directory)
    logger.info(f"Conversion completed. Total files converted: {len(converted_files)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("No directory provided! Usage: python script.py <directory_path>")
    else:
        directory = sys.argv[1]
        main(directory)
