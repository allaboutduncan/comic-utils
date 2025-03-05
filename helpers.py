import os
import stat
import zipfile
from PIL import Image, ImageEnhance, ImageFilter
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
        app_logger.error(f"Failed to extract {rar_path}: {e.stderr.decode().strip()}")
        raise RuntimeError(f"Failed to extract {rar_path}: {e.stderr.decode().strip()}")

#########################
#   Image Enhancement   #
#########################

def modified_s_curve_lut():
    lut = []
    for i in range(256):
        # Basic S-curve value using cosine.
        s = 0.5 - 0.5 * math.cos(math.pi * (i / 255))
        s_val = 255 * s
        # For values below 128, use the full S-curve.
        # For values 128 and above, blend the S-curve with the original value.
        if i < 128:
            blend = 0.0
        else:
            blend = (i - 128) / (255 - 128)  # goes from 0 at i=128 to 1 at i=255
        new_val = (1 - blend) * s_val + blend * i
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


def enhance_image(image_path):
    # Open an image file
    image = Image.open(image_path)

    enhanced = apply_modified_s_curve(image)
    
    return enhanced