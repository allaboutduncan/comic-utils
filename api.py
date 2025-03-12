from flask import Flask, request, jsonify
import os
import requests
from urllib.parse import urlparse, unquote
import uuid
import re
from flask_cors import CORS
from app_logging import app_logger
from config import config, load_config

app = Flask(__name__)

load_config()

watch = config.get("SETTINGS", "WATCH", fallback="/temp")

# Enable CORS for all routes.
CORS(app, resources={r"/*": {"origins": "*"}})

DOWNLOAD_DIR = watch

# Ensure the download directory exists.
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# 1. This dictionary holds the custom headers you send to the target URL:
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
    "CF-Access-Client-Id": "ACCESS",
    "CF-Access-Client-Secret": "SECRET"
}

@app.after_request
def add_cors_headers(response):
    # 2. Ensure the browser allows these custom headers (needed for preflight):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add(
        "Access-Control-Allow-Headers",
        "Content-Type,Authorization,CF-Access-Client-Id,CF-Access-Client-Secret"
    )
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
        # Outgoing request to the target URL with our custom headers.
        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()
        app_logger.info(f"Download response: {response}")
        
        # Use the final URL (after any redirection) for filename extraction.
        final_url = response.url
        parsed_url = urlparse(final_url)

        filename = os.path.basename(parsed_url.path)
        filename = unquote(filename)  # Decode %20, etc.

        if not filename:
            filename = str(uuid.uuid4())
            app_logger.info(f"Filename generated from final URL: {filename}")
        
        # Check Content-Disposition for a more accurate filename.
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            fname_match = re.search('filename="?([^";]+)"?', content_disposition)
            if fname_match:
                cd_filename = fname_match.group(1)
                cd_filename = unquote(cd_filename)
                filename = cd_filename
                app_logger.info(f"Filename from Content-Disposition: {filename}")
        
        # Ensure a unique final file path.
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        app_logger.info(f"Final File Path: {file_path}")

        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(file_path):
            filename = f"{base}_{counter}{ext}"
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            counter += 1
        
        # Write to a .crdownload first, then rename upon completion.
        temp_file_path = file_path + ".crdownload"
        app_logger.info(f"Temporary File Path: {temp_file_path}")
        
        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        os.rename(temp_file_path, file_path)
        app_logger.info("Download Success")
        return jsonify({'message': 'Download successful', 'file_path': file_path}), 200
    
    except requests.RequestException as e:
        app_logger.info("Download Failed")
        return jsonify({'error': f'Error downloading file: {str(e)}'}), 500
