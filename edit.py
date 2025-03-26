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
<div class="row row-cols-3">
  {% for card in file_cards %}
    <!-- The updated card markup goes here -->
    <div class="col">
      <div class="card mb-3" style="max-width: 540px;">
        <div class="row g-0">
          <div class="col-md-4">
            {% if card.img_data %}
            <img src="{{ card.img_data }}" class="img-fluid rounded-start object-fit-scale border rounded" alt="{{ card.filename }}">
            {% else %}
            <img src="https://via.placeholder.com/100" class="img-fluid rounded-start object-fit-scale border rounded" alt="No image">
            {% endif %}
          </div>
          <div class="col-md-8">
            <div class="card-body">
              <p class="card-text">
                <small class="text-body-secondary">
                    <span class="editable-filename" data-rel-path="{{ card.rel_path }}" onclick="enableFilenameEdit(this)">
                        {{ card.filename }}
                    </span>
                    <input type="text" class="form-control d-none filename-input form-control-sm" value="{{ card.filename }}"  data-rel-path="{{ card.rel_path }}">
                </small>
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  {% endfor %}
</div>
'''

def process_cbz_file(file_path):
    """
    Process the CBZ file:
      1. Rename the .cbz file to .zip.
      2. Create a folder based on the file name.
      3. Extract the ZIP contents into the folder.
      4. If the ZIP contains a single folder, update folder_name to that inner folder.
      5. Delete all .nfo and .sfv files.
    Returns a dictionary with 'folder_name' and 'zip_file_path'.
    """
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
    
    # Step 3: Extract zip contents into folder
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(folder_name)
    
    # Step 4: Check if the extracted content is a single folder; if so, update folder_name
    contents = os.listdir(folder_name)
    if len(contents) == 1:
        inner_path = os.path.join(folder_name, contents[0])
        if os.path.isdir(inner_path):
            folder_name = inner_path
            app_logger.info(f"ZIP contained a single folder, updating folder_name to: {folder_name}")
    
    # Step 5: Delete all .nfo and .sfv files
    for root, _, files in os.walk(folder_name):
        for file in files:
            if file.lower().endswith(('.nfo', '.sfv')):
                os.remove(os.path.join(root, file))
    
    app_logger.info(f"Extraction complete: {folder_name}")
    return {"folder_name": folder_name, "zip_file_path": zip_path}


def get_edit_modal(file_path):
    """
    Processes the provided CBZ file and returns a dictionary with keys:
      - modal_body: rendered HTML for the modal body (Bootstrap cards)
      - folder_name, zip_file_path, original_file_path: for the hidden form fields.
    This function walks through subdirectories.
    """
    result = process_cbz_file(file_path)
    folder_name = result["folder_name"]
    zip_file_path = result["zip_file_path"]
    
    file_cards = []
    # Walk the extraction folder recursively
    for root, _, files in os.walk(folder_name):
        for f in files:
            # Create a relative path that includes subdirectories
            rel_path = os.path.relpath(os.path.join(root, f), folder_name)
            filename_only = os.path.basename(rel_path)  # Extract only the file name
            img_data = None
            # Attempt thumbnail generation for common image types
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
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
        
        # Clean up the temporary extraction folder.
        if os.path.exists(folder_name):
            shutil.rmtree(folder_name)
            
        return jsonify({
            "success": True,
            "message": f"File processed successfully: {original_file_path}"
        })
    except Exception as e:
        app_logger.error(f"Failed to complete processing: {e}")
        return jsonify({"success": False, "error": str(e)}), 500