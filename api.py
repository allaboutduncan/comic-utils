from flask import Flask, request, jsonify
import os
import requests
from urllib.parse import urlparse, unquote
import uuid
import re
import json
from flask_cors import CORS
from mega import Mega
from app_logging import app_logger
from config import config, load_config
import shutil  # Import shutil for cross-device file moves

app = Flask(__name__)

load_config()

watch = config.get("SETTINGS", "WATCH", fallback="/temp")
custom_headers_str = config.get("SETTINGS", "HEADERS", fallback="")

CORS(app, resources={r"/*": {"origins": "*"}})

DOWNLOAD_DIR = watch
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

default_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36"
}

if custom_headers_str:
    try:
        custom_headers = json.loads(custom_headers_str)
        if isinstance(custom_headers, dict):
            default_headers.update(custom_headers)
            app_logger.info("Custom headers from settings applied.")
        else:
            app_logger.warning("Custom headers from settings are not a valid dictionary. Ignoring.")
    except Exception as e:
        app_logger.warning(f"Failed to parse custom headers: {e}. Ignoring.")

headers = default_headers

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET,PUT,POST,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,CF-Access-Client-Id,CF-Access-Client-Secret"
    return response

def download_getcomics(url):
    """
    Downloads files using the existing getcomics.org logic.
    """
    try:
        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()
        final_url = response.url
        parsed_url = urlparse(final_url)
        filename = os.path.basename(parsed_url.path)
        filename = unquote(filename)
        if not filename:
            filename = str(uuid.uuid4())
            app_logger.info(f"Filename generated from final URL: {filename}")
        # Check for Content-Disposition header for a better filename.
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            fname_match = re.search('filename="?([^";]+)"?', content_disposition)
            if fname_match:
                cd_filename = unquote(fname_match.group(1))
                filename = cd_filename
                app_logger.info(f"Filename from Content-Disposition: {filename}")
        # Ensure a unique file path.
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(file_path):
            filename = f"{base}_{counter}{ext}"
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            counter += 1
        temp_file_path = file_path + ".crdownload"
        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        os.rename(temp_file_path, file_path)
        app_logger.info("Download Success")
        return file_path
    except requests.RequestException as e:
        app_logger.error(f"Download Failed: {e}")
        raise Exception(f"Error downloading file: {str(e)}")

def download_file_from_mega(url, dest_filename=None):
    """
    Downloads files from mega.nz using the mega.py library and saves them in DOWNLOAD_DIR.
    """
    try:
        mega = Mega()
        m = mega.login()  # anonymous login
        if dest_filename:
            dest_path = os.path.join(DOWNLOAD_DIR, dest_filename)
            file_path = m.download_url(url, dest_filename=dest_path)
        else:
            file_path = m.download_url(url)
            filename = os.path.basename(file_path)
            target_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.abspath(file_path) != os.path.abspath(target_path):
                # Use shutil.move to support cross-device moves.
                shutil.move(file_path, target_path)
                file_path = target_path
        app_logger.info(f"Downloaded file saved as: {file_path}")
        return file_path
    except Exception as e:
        app_logger.error(f"Error downloading from Mega: {e}")
        raise Exception(f"Error downloading from Mega: {e}")

@app.route('/download', methods=['GET', 'POST', 'OPTIONS'])
def download():
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    if request.method == 'GET':
        return jsonify({
            'message': 'This endpoint accepts POST requests with a JSON payload containing the "link" key.'
        })
    
    data = request.get_json()
    app_logger.info("Received Download Request")
    if not data or 'link' not in data:
        return jsonify({'error': 'Missing "link" in request data'}), 400

    url = data['link']
    app_logger.info(f"Original link to download: {url}")

    try:
        # Use GET with stream to capture the final URL after redirection.
        try:
            r = requests.get(url, stream=True, headers=headers, allow_redirects=True)
            final_url = r.url
            r.close()  # Close the connection immediately.
            app_logger.info(f"Final URL after redirection: {final_url}")
        except Exception as e:
            app_logger.error(f"GET request for redirection failed: {e}")
            final_url = url  # Fallback

        # If the final URL is from Mega.nz, use the Mega download function.
        if "mega.nz" in final_url:
            file_path = download_file_from_mega(final_url, data.get("dest_filename"))
        else:
            file_path = download_getcomics(url)
        
        return jsonify({'message': 'Download successful', 'file_path': file_path}), 200
    
    except Exception as e:
        return jsonify({'error': f'Error downloading file: {str(e)}'}), 500

if __name__ == '__main__':
    # Disable the reloader to avoid duplicate execution.
    app.run(debug=True, use_reloader=False)
