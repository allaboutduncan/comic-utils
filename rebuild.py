import os
import sys
import subprocess
import zipfile
import shutil
from app_logging import app_logger
from helpers import is_hidden, extract_rar_with_unar


def count_rebuildable_files(directory):
    """
    Count the total number of files that will be rebuilt (RAR/CBR for conversion + CBZ for rebuild).
    
    :param directory: Path to the directory containing files.
    :return: Total count of files to process
    """
    total_files = 0
    
    for file_name in os.listdir(directory):
        file_path = os.path.join(directory, file_name)
        # Skip hidden files in the source directory.
        if is_hidden(file_path):
            continue
        # Count RAR/CBR files (for conversion) and CBZ files (for rebuild)
        if file_name.lower().endswith(('.rar', '.cbr', '.cbz')):
            total_files += 1
    
    return total_files


def convert_rar_to_zip_in_directory(directory, total_files=None, processed_files=None):
    """
    Convert all RAR/CBR files in a directory to CBZ files using unar for extraction,
    skipping hidden system files and directories.
    
    :param directory: Path to the directory containing RAR/CBR files.
    :param total_files: Total number of files to process (for progress tracking)
    :param processed_files: Current processed count (for progress tracking)
    :return: List of successfully converted files (without extensions).
    """
    app_logger.info("********************// Rebuild ALL Files in Directory //********************")
    os.makedirs(directory, exist_ok=True)
    converted_files = []

    for file_name in os.listdir(directory):
        file_path = os.path.join(directory, file_name)
        # Skip hidden files in the source directory.
        if is_hidden(file_path):
            continue

        if file_name.lower().endswith(('.rar', '.cbr')):
            if total_files and processed_files is not None:
                processed_files[0] += 1
            
            rar_path = file_path
            temp_extraction_dir = os.path.join(directory, f"temp_{file_name[:-4]}")
            zip_path = os.path.join(directory, file_name[:-4] + '.cbz')

            app_logger.info(f"Converting: {rar_path} -> {zip_path}")
            try:
                os.makedirs(temp_extraction_dir, exist_ok=True)
                extract_rar_with_unar(rar_path, temp_extraction_dir)

                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(temp_extraction_dir):
                        # Filter out hidden directories.
                        dirs[:] = [d for d in dirs if not is_hidden(os.path.join(root, d))]
                        for file in files:
                            file_path_inner = os.path.join(root, file)
                            # Skip hidden files during zipping.
                            if is_hidden(file_path_inner):
                                continue
                            arcname = os.path.relpath(file_path_inner, temp_extraction_dir)
                            zf.write(file_path_inner, arcname)

                app_logger.info(f"Successfully converted: {file_name}")
                converted_files.append(file_name[:-4])  # Store the filename without extension.

                # Delete the original RAR/CBR file.
                os.remove(rar_path)
            except Exception as e:
                app_logger.error(f"Failed to convert {file_name}: {e}")
            finally:
                if os.path.exists(temp_extraction_dir):
                    shutil.rmtree(temp_extraction_dir)

    return converted_files


def rebuild_task(directory):
    if not os.path.isdir(directory):
        app_logger.error(f"Directory {directory} not found.")
        return

    # Count total files for progress tracking first
    total_rebuildable = count_rebuildable_files(directory)
    processed_files = 0
    app_logger.info(f"Checking for rar/cbr files in directory: {directory}...")

    converted_files = convert_rar_to_zip_in_directory(directory, total_rebuildable, [processed_files])

    app_logger.info(f"Rebuilding project in directory: {directory}...")

    cbz_files = [f for f in os.listdir(directory) if f.lower().endswith(".cbz")]
    total_files = len(cbz_files)
    app_logger.info(f"Total .cbz files to process: {total_files}")

    for filename in cbz_files:
        base_name, original_ext = os.path.splitext(filename)

        # Skip files that were just converted
        if base_name in converted_files:
            app_logger.info(f"Skipping rebuild for recently converted file: {filename}")
            continue

        file_path = os.path.join(directory, filename)
        # Double-check if the file is hidden.
        if is_hidden(file_path):
            app_logger.info(f"Skipping hidden file: {file_path}")
            continue

        processed_files += 1

        new_zip_file = os.path.join(directory, base_name + ".zip")

        app_logger.info(f"Processing file: {filename} ({total_files} remaining)")
        os.rename(file_path, new_zip_file)

        folder_path = os.path.join(directory, base_name)
        app_logger.info(f"Creating folder: {folder_path}")
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

    app_logger.info(f"Rebuild completed in {directory}!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        app_logger.info("No directory provided!")
    else:
        directory = sys.argv[1]
        rebuild_task(directory)
