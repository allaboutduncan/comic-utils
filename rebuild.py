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
    Convert all RAR files in a directory to ZIP files using unar for extraction.

    :param directory: Path to the directory containing RAR files.
    :return: List of successfully converted files (without extensions).
    """
    os.makedirs(directory, exist_ok=True)
    converted_files = []

    for file_name in os.listdir(directory):
        if file_name.lower().endswith(('.rar', '.cbr')):
            rar_path = os.path.join(directory, file_name)
            temp_extraction_dir = os.path.join(directory, f"temp_{file_name[:-4]}")
            zip_path = os.path.join(directory, file_name[:-4] + '.cbz')

            print(f"Converting: {rar_path} -> {zip_path}")
            try:
                os.makedirs(temp_extraction_dir, exist_ok=True)
                extract_rar_with_unar(rar_path, temp_extraction_dir)

                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(temp_extraction_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, temp_extraction_dir)
                            zf.write(file_path, arcname)

                print(f"Successfully converted: {file_name}")
                converted_files.append(file_name[:-4])  # Track the file without extension

                # Delete the original RAR/CBR file
                os.remove(rar_path)
            except Exception as e:
                print(f"Failed to convert {file_name}: {e}")
            finally:
                if os.path.exists(temp_extraction_dir):
                    shutil.rmtree(temp_extraction_dir)

    return converted_files

def rebuild_task(directory):
    if not os.path.isdir(directory):
        print(f"Directory {directory} not found.")
        return

    logger.info(f"Checking for rar/cbr files in directory: {directory}...")

    converted_files = convert_rar_to_zip_in_directory(directory)

    logger.info(f"Rebuilding project in directory: {directory}...")

    cbz_files = [f for f in os.listdir(directory) if f.lower().endswith(".cbz")]
    total_files = len(cbz_files)
    logger.info(f"Total .cbz files to process: {total_files}")

    for filename in cbz_files:
        base_name, original_ext = os.path.splitext(filename)

        # Skip files that were just converted
        if base_name in converted_files:
            logger.info(f"Skipping rebuild for recently converted file: {filename}")
            continue

        file_path = os.path.join(directory, filename)
        new_zip_file = os.path.join(directory, base_name + ".zip")

        logger.info(f"Processing file: {filename} ({total_files} remaining)")
        os.rename(file_path, new_zip_file)

        folder_path = os.path.join(directory, base_name)
        logger.info(f"Creating folder: {folder_path}")
        os.makedirs(folder_path, exist_ok=True)

        with zipfile.ZipFile(new_zip_file, 'r') as zip_ref:
            zip_ref.extractall(folder_path)

        bak_file = os.path.join(directory, base_name + ".bak")
        os.rename(new_zip_file, bak_file)

        cbz_file = os.path.join(directory, base_name + ".cbz")
        with zipfile.ZipFile(cbz_file, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, _, files in os.walk(folder_path):
                for file in files:
                    file_full_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_full_path, folder_path)
                    zip_ref.write(file_full_path, arcname=arcname)

        for root, dirs, files in os.walk(folder_path, topdown=False):
            for file in files:
                os.remove(os.path.join(root, file))
            for dir in dirs:
                os.rmdir(os.path.join(root, dir))
        os.rmdir(folder_path)

        total_files -= 1

    for filename in os.listdir(directory):
        if filename.lower().endswith(".bak"):
            os.remove(os.path.join(directory, filename))

    logger.info(f"Rebuild completed in {directory}!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.info("No directory provided!")
    else:
        directory = sys.argv[1]
        rebuild_task(directory)
