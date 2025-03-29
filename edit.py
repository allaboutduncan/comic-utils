import os
import zipfile
import shutil
import io
import base64
from flask import render_template_string, request, jsonify
from PIL import Image
from app_logging import app_logger

# Partial template for the modal body (the grid of Bootstrap Cards)
modal_body_template = '''
    {% for card in file_cards %}
      <div class="col">
        <div class="card h-100 shadow-sm">
          <div class="row g-0">
            <div class="col-3">
              {% if card.img_data %}
                <img src="{{ card.img_data }}" class="img-fluid rounded-start object-fit-scale border rounded" alt="{{ card.filename }}">
              {% else %}
                <img src="https://via.placeholder.com/100" class="img-fluid rounded-start object-fit-scale border rounded" alt="No image">
              {% endif %}
            </div>
            <div class="col-9">
              <div class="card-body">
                <p class="card-text small">
                    <span class="editable-filename" data-rel-path="{{ card.rel_path }}" onclick="enableFilenameEdit(this)">
                      {{ card.filename }}
                    </span>
                    <input type="text" class="form-control d-none filename-input form-control-sm" value="{{ card.filename }}" data-rel-path="{{ card.rel_path }}">
                </p>
                <div class="d-flex justify-content-end">
                <div class="btn-group" role="group" aria-label="Basic example">
                  <button type="button" class="btn btn btn-outline-secondary btn-sm" onclick="cropImageLeft(this)" title="Crop Image Left">
                    <i class="bi bi-arrow-bar-left"></i> Left
                  </button>
                  <button type="button" class="btn btn btn-outline-secondary" onclick="cropImageCenter(this)" title="Crop Image Center">Middle</button>
                  <button type="button" class="btn btn-outline-secondary btn-sm" onclick="cropImageRight(this)" title="Crom Image Right">
                    Right <i class="bi bi-arrow-bar-right"></i>
                  </button>
                  <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteCardImage(this)">
                    <i class="bi bi-trash"></i>
                  </button>
                </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    {% endfor %}
'''

def process_cbz_file(file_path):
    """
    Process the CBZ file:
      1. Rename the .cbz file to .zip.
      2. Create a folder based on the file name.
      3. Extract the ZIP contents into the folder.
      4. If the ZIP contains a single directory (ignoring files), update folder_name to that inner directory.
         (This is done recursively in case there are multiple nested directories.)
      5. Delete all .nfo, .sfv, .db and .DS_Store files.
    Returns a dictionary with 'folder_name' and 'zip_file_path'.
    """
    app_logger.info("********************// Editing CBZ File //********************")
    if not file_path.lower().endswith('.cbz'):
        app_logger.info("Provided file is not a CBZ file.")
        raise ValueError("Provided file is not a CBZ file.")
    
    base_name = os.path.splitext(file_path)[0]
    zip_path = base_name + '.zip'
    folder_name = base_name + '_folder'
    
    app_logger.info(f"Processing CBZ: {file_path} --> {zip_path}")
    
    # Step 1: Rename .cbz to .zip
    os.rename(file_path, zip_path)
    
    # Step 2: Create folder for extraction
    os.makedirs(folder_name, exist_ok=True)
    
    # Step 3: Extract ZIP contents into folder
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(folder_name)
    
    # Optional: Remove unwanted system files (.DS_Store) before checking for single folder
    for root, _, files in os.walk(folder_name):
        for file in files:
            if file.lower() == '.ds_store':
                os.remove(os.path.join(root, file))
    
    # Step 4: Check if the extracted content contains a single directory.
    # Do this recursively in case the ZIP nests multiple single-directory levels.
    while True:
        # List only directories, ignoring any loose files.
        inner_dirs = [d for d in os.listdir(folder_name) if os.path.isdir(os.path.join(folder_name, d))]
        if len(inner_dirs) == 1:
            folder_name = os.path.join(folder_name, inner_dirs[0])
            app_logger.info(f"Found a single nested folder, updating folder_name to: {folder_name}")
        else:
            break
    
    # Step 5: Delete all .db, .nfo, .sfv and .DS_Store files in the (possibly nested) folder.
    for root, _, files in os.walk(folder_name):
        for file in files:
            if file.lower().endswith(('.nfo', '.sfv', '.db', '.ds_store')):
                os.remove(os.path.join(root, file))
    
    app_logger.info(f"Extraction complete: {folder_name}")
    return {"folder_name": folder_name, "zip_file_path": zip_path}


def get_edit_modal(file_path):
    """
    Processes the provided CBZ file and returns a dictionary with keys:
      - modal_body: rendered HTML for the modal body (Bootstrap cards)
      - folder_name, zip_file_path, original_file_path: for the hidden form fields.
    This function walks through subdirectories and ignores .xml files.
    """
    result = process_cbz_file(file_path)
    folder_name = result["folder_name"]
    zip_file_path = result["zip_file_path"]
    
    file_cards = []
    # Walk the extraction folder recursively
    for root, _, files in os.walk(folder_name):
        for f in files:
            # Ignore .xml files
            if f.lower().endswith('.xml'):
                continue

            # Create a relative path that includes subdirectories
            rel_path = os.path.relpath(os.path.join(root, f), folder_name)
            filename_only = os.path.basename(rel_path)  # Extract only the file name
            img_data = None
            # Attempt thumbnail generation for common image types
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')):
                try:
                    full_path = os.path.join(root, f)
                    with Image.open(full_path) as img:
                        if img.height > 0:
                            ratio = 100 / float(img.height)
                            new_width = int(img.width * ratio)
                            img = img.resize((new_width, 100), Image.LANCZOS)
                            buffered = io.BytesIO()
                            img.save(buffered, format="PNG")
                            encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
                            img_data = f"data:image/png;base64,{encoded}"
                except Exception as e:
                    app_logger.info(f"Thumbnail generation failed for '{rel_path}': {e}")
            file_cards.append({"filename": filename_only, "rel_path": rel_path, "img_data": img_data})
    
    modal_body_html = render_template_string(modal_body_template, file_cards=file_cards)
    
    return {
        "modal_body": modal_body_html,
        "folder_name": folder_name,
        "zip_file_path": zip_file_path,
        "original_file_path": file_path
    }


def save_cbz():
    """
    Processes the CBZ file by:
      - Renaming the .zip file (created during extraction) to .bak (Step 5)
      - Re-compressing the extracted folder (sorted) into a .cbz file (Step 6)
      - Deleting the .bak file and cleaning up (Step 7)
    This function is meant to be used as a route handler and is imported in app.py.
    """
    app_logger.info(f"Clean up and re-compressing the CBZ file.")
    folder_name = request.form.get('folder_name')
    zip_file_path = request.form.get('zip_file_path')
    original_file_path = request.form.get('original_file_path')
    
    if not folder_name or not zip_file_path or not original_file_path:
        return "Missing required data", 400

    try:
        # Step 6: Rename the original .zip file to .bak.
        bak_file_path = zip_file_path + '.bak'
        os.rename(zip_file_path, bak_file_path)
        
        # Step 7: Re-compress the folder contents into a .cbz file (sorted).
        file_list = []
        for root, _, files in os.walk(folder_name):
            for file in files:
                file_path_in_folder = os.path.join(root, file)
                arcname = os.path.relpath(file_path_in_folder, folder_name)
                file_list.append((arcname, file_path_in_folder))
        file_list.sort(key=lambda x: x[0])
        with zipfile.ZipFile(original_file_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for arcname, file_path_in_folder in file_list:
                zf.write(file_path_in_folder, arcname)
        
        app_logger.info(f"Successfully re-compressed: {original_file_path}")
        
        # Step 8: Delete the .bak file.
        os.remove(bak_file_path)
        app_logger.info(f"Deleted the .bak file: {bak_file_path}")

        # Step 9: Clean up the temporary extraction folder.
        # Instead of deleting folder_name (which might be the inner folder),
        # compute the outer extraction folder using the original file path.
        outer_folder = os.path.splitext(original_file_path)[0] + '_folder'
        if os.path.exists(outer_folder):
            shutil.rmtree(outer_folder)
            app_logger.info(f"Deleted the extraction folder: {outer_folder}")

        return jsonify({
            "success": True,
            "message": f"File processed successfully: {original_file_path}"
        })
    except Exception as e:
        app_logger.error(f"Failed to complete processing: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    

def cropRight(image_path):
    file_name, file_extension = os.path.splitext(image_path)

    try:
        # Open the image
        with Image.open(image_path) as img:
            width, height = img.size

            # Split the image in half (right half)
            right_half = (width // 2, 0, width, height)

            # Save the original image by appending "b" to the file name
            backup_path = f"{file_name}b{file_extension}"
            img.save(backup_path)

            # Save the right half by appending "a" to the file name
            right_half_img = img.crop(right_half)
            new_image_path = f"{file_name}a{file_extension}"
            right_half_img.save(new_image_path)

        # Delete the original image
        os.remove(image_path)

        app_logger.info(f"Processed: {os.path.basename(image_path)} original saved as {backup_path}, right half saved as {new_image_path}.")

        return new_image_path, backup_path
    
    except Exception as e:
        app_logger.error(f"Error processing the image: {e}")


def cropLeft(image_path):
    file_name, file_extension = os.path.splitext(image_path)

    try:
        # Open the image
        with Image.open(image_path) as img:
            width, height = img.size

            # Split the image in half (left half)
            left_half = (0, 0, width // 2, height)

            # Save the original image by appending "b" to the file name
            backup_path = f"{file_name}b{file_extension}"
            img.save(backup_path)

            # Save the left half by appending "a" to the file name
            left_half_img = img.crop(left_half)
            new_image_path = f"{file_name}a{file_extension}"
            left_half_img.save(new_image_path)

        # Delete the original image
        os.remove(image_path)

        app_logger.info(f"Processed: {os.path.basename(image_path)} original saved as {backup_path}, left half saved as {new_image_path}.")

        return new_image_path, backup_path
    
    except Exception as e:
        app_logger.error(f"Error processing the image: {e}")


def cropCenter(image_path):
    file_name, file_extension = os.path.splitext(image_path)

    try:
        # Open the image
        with Image.open(image_path) as img:
            width, height = img.size

            # Calculate the coordinates for the left, center, and right thirds
            third_width = width // 3
            left_half = (0, 0, third_width, height)  # Left third
            center_half = (third_width, 0, 2 * third_width, height)  # Center third
            right_half = (2 * third_width, 0, width, height)  # Right third

            # Save the original image by appending "b" to the file name
            backup_path = f"{file_name}b{file_extension}"
            img.save(backup_path)

            # Save the left third part as 'filename_left'
            left_img = img.crop(left_half)
            left_image_path = f"{file_name}_left{file_extension}"
            left_img.save(left_image_path)

            # Save the right third part as 'filename_right'
            right_img = img.crop(right_half)
            right_image_path = f"{file_name}_right{file_extension}"
            right_img.save(right_image_path)

            # Save the center third as 'filename_center'
            center_img = img.crop(center_half)
            new_image_path = f"{file_name}_center{file_extension}"
            center_img.save(new_image_path)

        # Delete the original image
        os.remove(image_path)

        app_logger.info(f"Processed: {os.path.basename(image_path)} original saved as {backup_path}, center saved as {new_image_path}, left part saved as {left_image_path}, right part saved as {right_image_path}.")

        return new_image_path
    
    except Exception as e:
        app_logger.error(f"Error processing the image: {e}")


def get_image_data_url(image_path):
    """Open an image, resize it to a height of 100 (keeping aspect ratio),
    encode it as a PNG in memory, and return a data URL."""
    try:
        with Image.open(image_path) as img:
            if img.height > 0:
                ratio = 100 / float(img.height)
                new_width = int(img.width * ratio)
                img = img.resize((new_width, 100), Image.LANCZOS)
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
            return f"data:image/png;base64,{encoded}"
    except Exception as e:
        app_logger.error(f"Error encoding image {image_path}: {e}")
        raise