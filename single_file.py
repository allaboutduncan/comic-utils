import os
import sys
import subprocess
import zipfile
import shutil
import time
from app_logging import app_logger
from config import config, load_config
from helpers import extract_rar_with_unar

load_config()

# Large file threshold (configurable)
LARGE_FILE_THRESHOLD = config.getint("SETTINGS", "LARGE_FILE_THRESHOLD", fallback=500) * 1024 * 1024  # Convert MB to bytes


def get_file_size_mb(file_path):
    """Get file size in MB."""
    try:
        size_bytes = os.path.getsize(file_path)
        return size_bytes / (1024 * 1024)
    except OSError:
        return 0


def convert_single_rar_file(rar_path, cbz_path, temp_extraction_dir):
    """
    Convert a single RAR file to CBZ with progress reporting.
    
    :param rar_path: Path to the RAR file
    :param cbz_path: Path for the output CBZ file
    :param temp_extraction_dir: Temporary directory for extraction
    :return: bool: True if conversion was successful
    """
    file_size_mb = get_file_size_mb(rar_path)
    is_large_file = file_size_mb > (LARGE_FILE_THRESHOLD / (1024 * 1024))
    
    if is_large_file:
        app_logger.info(f"Processing large file ({file_size_mb:.1f}MB): {os.path.basename(rar_path)}")
        app_logger.info("This may take several minutes. Progress updates will be provided.")
    
    try:
        # Create temp directory
        os.makedirs(temp_extraction_dir, exist_ok=True)
        
        # Step 1: Extract RAR file
        app_logger.info(f"Step 1/3: Extracting {os.path.basename(rar_path)}...")
        extraction_success = extract_rar_with_unar(rar_path, temp_extraction_dir)
        
        if not extraction_success:
            app_logger.error(f"Failed to extract any files from {os.path.basename(rar_path)}")
            return False
        
        # Step 2: Count extracted files for progress tracking
        extracted_files = []
        for root, dirs, files in os.walk(temp_extraction_dir):
            for file in files:
                file_path = os.path.join(root, file)
                extracted_files.append(file_path)
        
        total_files = len(extracted_files)
        app_logger.info(f"Step 2/3: Found {total_files} files to compress...")
        
        # Step 3: Create CBZ file with progress reporting
        app_logger.info(f"Step 3/3: Creating CBZ file...")
        processed_files = 0
        
        with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for extract_root, extract_dirs, extract_files in os.walk(temp_extraction_dir):
                for extract_file in extract_files:
                    file_path_inner = os.path.join(extract_root, extract_file)
                    arcname = os.path.relpath(file_path_inner, temp_extraction_dir)
                    zf.write(file_path_inner, arcname)
                    
                    processed_files += 1
                    
                    # Progress reporting for large files
                    if is_large_file and processed_files % max(1, total_files // 10) == 0:
                        progress_percent = (processed_files / total_files) * 100
                        app_logger.info(f"Compression progress: {progress_percent:.1f}% ({processed_files}/{total_files} files)")
        
        app_logger.info(f"Successfully converted: {os.path.basename(rar_path)}")
        return True
        
    except Exception as e:
        app_logger.error(f"Failed to convert {os.path.basename(rar_path)}: {e}")
        return False


def rebuild_single_cbz_file(cbz_path):
    """
    Rebuild a single CBZ file with progress reporting.
    
    :param cbz_path: Path to the CBZ file
    :return: bool: True if rebuild was successful
    """
    file_size_mb = get_file_size_mb(cbz_path)
    is_large_file = file_size_mb > (LARGE_FILE_THRESHOLD / (1024 * 1024))
    filename = os.path.basename(cbz_path)
    base_name = os.path.splitext(filename)[0]
    
    if is_large_file:
        app_logger.info(f"Processing large file ({file_size_mb:.1f}MB): {filename}")
        app_logger.info("This may take several minutes. Progress updates will be provided.")
    
    try:
        # Step 1: Rename CBZ to ZIP
        app_logger.info(f"Step 1/4: Preparing {filename} for rebuild...")
        directory = os.path.dirname(cbz_path)
        zip_path = os.path.join(directory, base_name + '.zip')
        shutil.move(cbz_path, zip_path)
        
        # Step 2: Create extraction folder
        app_logger.info(f"Step 2/4: Creating extraction folder...")
        folder_name = os.path.join(directory, base_name + '_folder')
        os.makedirs(folder_name, exist_ok=True)
        
        # Step 3: Extract ZIP file
        app_logger.info(f"Step 3/4: Extracting {filename}...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()
            total_files = len(file_list)
            extracted_files = 0
            
            for file_info in zip_ref.infolist():
                zip_ref.extract(file_info, folder_name)
                extracted_files += 1
                
                # Progress reporting for large files
                if is_large_file and extracted_files % max(1, total_files // 10) == 0:
                    progress_percent = (extracted_files / total_files) * 100
                    app_logger.info(f"Extraction progress: {progress_percent:.1f}% ({extracted_files}/{total_files} files)")
        
        # Step 4: Recompress to CBZ
        app_logger.info(f"Step 4/4: Recompressing {filename}...")
        bak_file_path = zip_path + '.bak'
        shutil.move(zip_path, bak_file_path)
        
        with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            file_count = 0
            total_files = 0
            
            # Count total files first
            for root, _, files in os.walk(folder_name):
                total_files += len(files)
            
            # Compress files with progress reporting
            for root, _, files in os.walk(folder_name):
                for file in files:
                    file_path_in_folder = os.path.join(root, file)
                    arcname = os.path.relpath(file_path_in_folder, folder_name)
                    zf.write(file_path_in_folder, arcname)
                    file_count += 1
                    
                    # Progress reporting for large files
                    if is_large_file and file_count % max(1, total_files // 10) == 0:
                        progress_percent = (file_count / total_files) * 100
                        app_logger.info(f"Compression progress: {progress_percent:.1f}% ({file_count}/{total_files} files)")
        
        # Clean up
        os.remove(bak_file_path)
        if os.path.exists(folder_name):
            shutil.rmtree(folder_name)
        
        app_logger.info(f"Successfully rebuilt: {filename}")
        return True
        
    except Exception as e:
        app_logger.error(f"Failed to rebuild {filename}: {e}")
        return False


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

    success = rebuild_single_cbz_file(file_path)
    if not success:
        app_logger.error(f"Failed to rebuild CBZ file: {file_path}")


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
        cbz_file_path = base_name + '.cbz'

        success = convert_single_rar_file(file_path, cbz_file_path, temp_extraction_dir)
        
        if success:
            # Delete the original file (RAR or CBR)
            os.remove(file_path)
        else:
            app_logger.error(f"Failed to convert {file_path}")
        
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
