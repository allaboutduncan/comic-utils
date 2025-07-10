import os
import stat
import zipfile
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import math
import shutil
from app_logging import app_logger
import subprocess

#########################
# Hidden File Handling  #
#########################

def is_hidden(filepath):
    """
    Returns True if the file or directory is considered hidden.
    This function marks files as hidden if their names start with a '.' or '_',
    and it also checks the Windows hidden attribute.
    """
    name = os.path.basename(filepath)
    # Check for names starting with '.' or '_'
    if name.startswith('.') or name.startswith('_'):
        return True
    # For Windows, check the hidden attribute
    if os.name == 'nt':
        try:
            attrs = os.stat(filepath).st_file_attributes
            return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
        except AttributeError:
            pass
    return False

#########################
#   File Extraction     #
#########################

def unzip_file(file_path):
    """
    Extracts all files from a ZIP archive into a directory with the same name as the ZIP file.

    Parameters:
        zip_file_path (str): The path to the ZIP archive.

    Returns:
        str: The full path to the directory where the files were extracted.
    """
    # Remove the .zip extension to form the directory name.
    base_dir, ext = os.path.splitext(file_path)
    if ext.lower() != '.bak':
        raise ValueError("The provided file does not have a .bak extension.")
    
    # Create the directory if it doesn't exist.
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    
    # Extract all files into the created directory.
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        zip_ref.extractall(base_dir)
    
    return base_dir


def extract_rar_with_unar(rar_path, output_dir):
    """
    Extract a RAR file using the unar command-line tool.
    Returns True if extraction was successful (even with some failed files),
    False if extraction completely failed.

    :param rar_path: Path to the RAR file.
    :param output_dir: Directory to extract the contents into.
    :return: bool: True if any files were extracted successfully
    """
    try:
        # First check if unar is available
        try:
            subprocess.run(["unar", "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            app_logger.error("unar command not found. Please install unar first.")
            raise RuntimeError("unar command not found. Please install unar first.")
        
        # Check if the input file exists
        if not os.path.exists(rar_path):
            app_logger.error(f"Input file does not exist: {rar_path}")
            raise RuntimeError(f"Input file does not exist: {rar_path}")
        
        app_logger.info(f"Extracting {rar_path} to {output_dir}")
        result = subprocess.run(
            ["unar", "-o", output_dir, "-f", rar_path],
            capture_output=True,
            text=True
        )
        
        stdout_msg = result.stdout.strip() if result.stdout else ""
        stderr_msg = result.stderr.strip() if result.stderr else ""
        
        # Check if any files were extracted successfully
        extracted_files = []
        failed_files = []
        
        if stdout_msg:
            for line in stdout_msg.split('\n'):
                if '... OK.' in line:
                    # Extract filename from line like "filename.jpg  (size B)... OK."
                    filename = line.split('...')[0].strip().split('  ')[0]
                    extracted_files.append(filename)
                elif '... Failed!' in line:
                    # Extract filename from line like "filename.jpg  (size B)... Failed!"
                    filename = line.split('...')[0].strip().split('  ')[0]
                    failed_files.append(filename)
        
        if extracted_files:
            app_logger.info(f"Successfully extracted {len(extracted_files)} files from {rar_path}")
            if failed_files:
                app_logger.warning(f"Failed to extract {len(failed_files)} files: {', '.join(failed_files)}")
            
            # Check if the output directory has any files
            if os.path.exists(output_dir) and any(os.listdir(output_dir)):
                app_logger.info(f"Extraction completed with some files. Output directory: {output_dir}")
                return True
            else:
                app_logger.error("No files were actually extracted despite successful status")
                return False
        else:
            app_logger.error(f"No files were extracted from {rar_path}")
            if stderr_msg:
                app_logger.error(f"stderr: {stderr_msg}")
            return False
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode().strip() if e.stderr else "Unknown error"
        app_logger.error(f"Failed to extract {rar_path}: {error_msg}")
        raise RuntimeError(f"Failed to extract {rar_path}: {error_msg}")
    except Exception as e:
        app_logger.error(f"Unexpected error extracting {rar_path}: {str(e)}")
        raise RuntimeError(f"Unexpected error extracting {rar_path}: {str(e)}")

#########################
#   Image Enhancement   #
#########################

def apply_gamma(image, gamma=0.9):
    inv = 1.0 / gamma
    table = [int(((i/255)**inv)*255) for i in range(256)]
    if image.mode == "RGB":
        table = table*3
    return image.point(table)

def modified_s_curve_lut(shadow_lift=0.1):
    lut = []
    for i in range(256):
        s = 0.5 - 0.5*math.cos(math.pi*(i/255))
        s_val = 255*s
        # lift the darkest 20% by a fixed offset
        if i < 64:
            s_val = s_val + shadow_lift*(64 - i)
        # blend into original in highlights as before…
        blend = max(0, (i-128)/(127))
        new_val = (1-blend)*s_val + blend*i
        lut.append(int(round(new_val)))
    return lut

def apply_modified_s_curve(image):
    single_lut = modified_s_curve_lut()
    
    # If the image is grayscale, apply the LUT directly.
    if image.mode == "L":
        return image.point(single_lut)
    # For RGB images, replicate the LUT for each channel.
    elif image.mode == "RGB":
        full_lut = single_lut * 3
        return image.point(full_lut)
    # For RGBA images, apply the curve to RGB channels only.
    elif image.mode == "RGBA":
        r, g, b, a = image.split()
        r = r.point(single_lut)
        g = g.point(single_lut)
        b = b.point(single_lut)
        return Image.merge("RGBA", (r, g, b, a))
    else:
        raise ValueError(f"Unsupported image mode: {image.mode}")

def enhance_image(path):
    img = Image.open(path)
    img = apply_modified_s_curve(img)
    img = apply_gamma(img, gamma=0.9)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img
