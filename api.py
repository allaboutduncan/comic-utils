from flask import Flask, request, jsonify
import os
import requests
from urllib.parse import urlparse
import uuid

app = Flask(__name__)

DOWNLOAD_DIR = '/downloads/temp'

# Ensure the download directory exists.
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

@app.route('/download', methods=['POST'])
def download_file():
    data = request.get_json()
    if not data or 'link' not in data:
        return jsonify({'error': 'Missing "link" in request data'}), 400
    
    url = data['link']
    
    try:
        # Stream the content to avoid memory issues for large files.
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Try to determine a filename from the URL.
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        if not filename:
            # Generate a unique filename if the URL does not provide one.
            filename = str(uuid.uuid4())
        
        # Build full file path.
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        
        # Ensure a unique filename if the file already exists.
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(file_path):
            filename = f"{base}_{counter}{ext}"
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            counter += 1
        
        # Write the downloaded content to file.
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    
        return jsonify({'message': 'Download successful', 'file_path': file_path}), 200
    
    except requests.RequestException as e:
        return jsonify({'error': f'Error downloading file: {str(e)}'}), 500
