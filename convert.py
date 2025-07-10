import os
import sys
import subprocess
import zipfile
import shutil
from app_logging import app_logger
from config import config, load_config
from helpers import is_hidden, extract_rar_with_unar

load_config()

convertSubdirectories = config.getboolean("SETTINGS", "CONVERT_SUBDIRECTORIES", fallback=False)


def convert_rar_directory(directory):
    """
    Convert all RAR and CBR files in a directory (and optionally its subdirectories)
    to CBZ files using unar for extraction, skipping hidden system files and directories.

    :param directory: Path to the directory containing RAR and CBR files.
    :return: List of successfully converted files (without extensions)
    """
    app_logger.info("********************// Convert Directory to CBZ //********************")
    os.makedirs(directory, exist_ok=True)
    converted_files = []

    if convertSubdirectories:
        # Recursively traverse the directory tree.
        for root, dirs, files in os.walk(directory):
            # Skip hidden directories.
            dirs[:] = [d for d in dirs if not is_hidden(os.path.join(root, d))]
            for file_name in files:
                file_path = os.path.join(root, file_name)
                if is_hidden(file_path):
                    continue

                # Process only .rar and .cbr files.
                if file_name.lower().endswith(('.rar', '.cbr')):
                    rar_path = file_path
                    temp_extraction_dir = os.path.join(root, f"temp_{file_name[:-4]}")
                    zip_path = os.path.join(root, f"{file_name[:-4]}.cbz")

                    app_logger.info(f"Converting: {rar_path} -> {zip_path}")
                    try:
                        os.makedirs(temp_extraction_dir, exist_ok=True)
                        extraction_success = extract_rar_with_unar(rar_path, temp_extraction_dir)
                        
                        if not extraction_success:
                            app_logger.error(f"Failed to extract any files from {file_name}")
                            continue

                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for extract_root, extract_dirs, extract_files in os.walk(temp_extraction_dir):
                                # Skip hidden directories within the extraction folder.
                                extract_dirs[:] = [d for d in extract_dirs if not is_hidden(os.path.join(extract_root, d))]
                                for extract_file in extract_files:
                                    file_path_inner = os.path.join(extract_root, extract_file)
                                    if is_hidden(file_path_inner):
                                        continue
                                    arcname = os.path.relpath(file_path_inner, temp_extraction_dir)
                                    zf.write(file_path_inner, arcname)

                        app_logger.info(f"Successfully converted: {file_name}")
                        converted_files.append(file_name[:-4])
                        # Delete the original RAR/CBR file.
                        os.remove(rar_path)
                    except Exception as e:
                        app_logger.error(f"Failed to convert {file_name}: {e}")
                    finally:
                        if os.path.exists(temp_extraction_dir):
                            shutil.rmtree(temp_extraction_dir)
    else:
        # Non-recursive conversion: only process files in the given directory.
        for file_name in os.listdir(directory):
            file_path = os.path.join(directory, file_name)
            if is_hidden(file_path):
                continue

            if file_name.lower().endswith(('.rar', '.cbr')):
                rar_path = file_path
                temp_extraction_dir = os.path.join(directory, f"temp_{file_name[:-4]}")
                zip_path = os.path.join(directory, f"{file_name[:-4]}.cbz")

                app_logger.info(f"Converting: {rar_path} -> {zip_path}")
                try:
                    os.makedirs(temp_extraction_dir, exist_ok=True)
                    extraction_success = extract_rar_with_unar(rar_path, temp_extraction_dir)
                    
                    if not extraction_success:
                        app_logger.error(f"Failed to extract any files from {file_name}")
                        continue

                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for root2, dirs2, files2 in os.walk(temp_extraction_dir):
                            dirs2[:] = [d for d in dirs2 if not is_hidden(os.path.join(root2, d))]
                            for file2 in files2:
                                file_path_inner = os.path.join(root2, file2)
                                if is_hidden(file_path_inner):
                                    continue
                                arcname = os.path.relpath(file_path_inner, temp_extraction_dir)
                                zf.write(file_path_inner, arcname)

                    app_logger.info(f"Successfully converted: {file_name}")
                    converted_files.append(file_name[:-4])
                    os.remove(rar_path)
                except Exception as e:
                    app_logger.error(f"Failed to convert {file_name}: {e}")
                finally:
                    if os.path.exists(temp_extraction_dir):
                        shutil.rmtree(temp_extraction_dir)

    return converted_files


def main(directory):
    if not os.path.isdir(directory):
        app_logger.error(f"Directory '{directory}' does not exist.")
        return

    app_logger.info(f"Starting conversion in directory: {directory}")
    converted_files = convert_rar_directory(directory)
    app_logger.info(f"Conversion completed. Total files converted: {len(converted_files)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        app_logger.error("No directory provided! Usage: python script.py <directory_path>")
    else:
        directory = sys.argv[1]
        main(directory)
