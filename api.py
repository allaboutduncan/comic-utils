from flask import Flask, request, jsonify
import os
import requests
from urllib.parse import urlparse, unquote
import uuid
import re
from flask_cors import CORS
from app_logging import app_logger

app = Flask(__name__)

# Enable CORS for all routes.
CORS(app, resources={r"/*": {"origins": "*"}})

DOWNLOAD_DIR = '/downloads/temp'

# Ensure the download directory exists.
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36"
}

@app.after_request
def add_cors_headers(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
    return response

@app.route('/download', methods=['GET', 'POST'])
def download():
    if request.method == 'GET':
        return jsonify({
            'message': 'This endpoint accepts POST requests with a JSON payload containing the "link" key.'
        })
    
    # POST method handling.
    data = request.get_json()
    app_logger.info("Received Download Request")
    if not data or 'link' not in data:
        return jsonify({'error': 'Missing "link" in request data'}), 400
    
    url = data['link']
    app_logger.info(f"Link to download: {url}")
    
    try:
        # Stream the content to avoid memory issues for large files.
        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()
        app_logger.info(f"Download response: {response}")
        
        # Use the final URL (after redirection) for filename extraction.
        final_url = response.url
        parsed_url = urlparse(final_url)

        # Extract filename from the final URL's path and decode URL-encoded characters.
        filename = os.path.basename(parsed_url.path)
        filename = unquote(filename)

        if not filename:
            filename = str(uuid.uuid4())
            app_logger.info(f"Filename generated from final URL: {filename}")
        
        # Check for a filename from the Content-Disposition header.
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            fname_match = re.search('filename="?([^";]+)"?', content_disposition)
            if fname_match:
                cd_filename = fname_match.group(1)
                cd_filename = unquote(cd_filename)
                filename = cd_filename
                app_logger.info(f"Filename from Content-Disposition: {filename}")
        
        # Build the final file path.
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        app_logger.info(f"Final File Path: {file_path}")

        # Ensure a unique filename if the file already exists.
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(file_path):
            filename = f"{base}_{counter}{ext}"
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            counter += 1
        
        # Use a temporary file with .crdownload extension.
        temp_file_path = file_path + ".crdownload"
        app_logger.info(f"Temporary File Path: {temp_file_path}")
        
        # Write the downloaded content to the temporary file.
        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        # Rename the temporary file to the final file once the download is complete.
        os.rename(temp_file_path, file_path)
        app_logger.info("Download Success")
        return jsonify({'message': 'Download successful', 'file_path': file_path}), 200
    
    except requests.RequestException as e:
        app_logger.info("Download Failed")
        return jsonify({'error': f'Error downloading file: {str(e)}'}), 500
