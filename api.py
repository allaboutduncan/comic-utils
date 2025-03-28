import threading
from queue import Queue
from flask import Flask, request, jsonify, render_template, redirect, url_for
import os
import requests
from urllib.parse import urlparse, unquote, urljoin
import uuid
import re
import json
import shutil
import tempfile
from pathlib import Path
from flask_cors import CORS

# Mega download support
from mega import Mega
from mega.errors import RequestError
from mega.crypto import base64_to_a32, base64_url_decode, decrypt_attr, a32_to_str, str_to_a32, get_chunks
from Crypto.Cipher import AES
from Crypto.Util import Counter

# Application logging and configuration (adjust these as needed)
from app_logging import app_logger
from config import config, load_config

# Load config and initialize Flask app.
app = Flask(__name__)
load_config()

# -------------------------------
# Global Variables & Configuration
# -------------------------------
# Global download progress dictionary
download_progress = {}

# Setup the download directory from config.
watch = config.get("SETTINGS", "WATCH", fallback="watch")
custom_headers_str = config.get("SETTINGS", "HEADERS", fallback="")

DOWNLOAD_DIR = watch
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Default headers for HTTP requests.
default_headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/112.0.0.0 Safari/537.36"
    )
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

# Allow cross-origin requests.
CORS(app, resources={r"/*": {"origins": "*"}})

# -------------------------------
# QUEUE AND WORKER THREAD SETUP (for non-scrape downloads)
# -------------------------------
download_queue = Queue()

def process_download(task):
    download_id = task['download_id']
    url = task['url']
    dest_filename = task.get('dest_filename')
    # Mark the download as in progress.
    download_progress[download_id]['status'] = 'in_progress'
    try:
        # Follow any redirection to get the final URL.
        r = requests.get(url, stream=True, headers=headers, allow_redirects=True)
        final_url = r.url
        r.close()
        app_logger.info(f"Final URL: {final_url}")
        if "mega.nz" in final_url:
            file_path = download_mega(final_url, download_id, dest_filename)
        elif "comicfiles.ru" in final_url:
            file_path = download_getcomics(final_url, download_id)
        else:
            file_path = download_getcomics(final_url, download_id)
        
        download_progress[download_id]['filename'] = file_path
        download_progress[download_id]['status'] = 'complete'
    except Exception as e:
        app_logger.error(f"Error during background download: {e}")
        download_progress[download_id]['status'] = 'error'

def worker():
    while True:
        task = download_queue.get()
        if task is None:  # Shutdown signal if needed.
            break
        process_download(task)
        download_queue.task_done()

# Start a few worker threads for processing downloads.
worker_threads = []
for i in range(3):
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    worker_threads.append(t)

# -------------------------------
# Monkey-Patch for Mega Downloads
# -------------------------------
def my_download_file(self,
                     file_handle,
                     file_key,
                     dest_path=None,
                     dest_filename=None,
                     is_public=False,
                     file=None):
    global altfileinfo
    if file is None:
        if is_public:
            file_key = base64_to_a32(file_key)
            file_data = self._api_request({
                'a': 'g',
                'g': 1,
                'p': file_handle
            })
        else:
            file_data = self._api_request({
                'a': 'g',
                'g': 1,
                'n': file_handle
            })
        k = (file_key[0] ^ file_key[4],
             file_key[1] ^ file_key[5],
             file_key[2] ^ file_key[6],
             file_key[3] ^ file_key[7])
        iv = file_key[4:6] + (0, 0)
        meta_mac = file_key[6:8]
    else:
        file_data = self._api_request({'a': 'g', 'g': 1, 'n': file['h']})
        k = file['k']
        iv = file['iv']
        meta_mac = file['meta_mac']

    altfileinfo = 0
    if 'g' not in file_data:
        raise RequestError('File not accessible anymore')
    file_url = file_data['g']
    file_size = file_data['s']
    attribs = base64_url_decode(file_data['at'])
    attribs = decrypt_attr(attribs, k)
    
    file_name = dest_filename if dest_filename is not None else attribs['n']
    dest_path = dest_path or ''
    if dest_path:
        dest_path += '/'
    
    # Update download progress if download_id is set.
    d_id = getattr(self, 'download_id', None)
    if d_id is not None:
        download_progress[d_id]['filename'] = dest_path + file_name

    with tempfile.NamedTemporaryFile(mode='w+b', prefix='megapy_', delete=False) as temp_output_file:
        k_str = a32_to_str(k)
        counter = Counter.new(128, initial_value=((iv[0] << 32) + iv[1]) << 64)
        aes = AES.new(k_str, AES.MODE_CTR, counter=counter)

        mac_str = '\0' * 16
        mac_encryptor = AES.new(k_str, AES.MODE_CBC, mac_str.encode("utf8"))
        iv_str = a32_to_str([iv[0], iv[1], iv[0], iv[1]])

        for chunk_start, chunk_size in get_chunks(file_size):
            chunk = requests.get(file_url, stream=True).raw.read(chunk_size)
            chunk = aes.decrypt(chunk)
            temp_output_file.write(chunk)
            encryptor = AES.new(k_str, AES.MODE_CBC, iv_str)
            for i in range(0, len(chunk) - 16, 16):
                block = chunk[i:i + 16]
                encryptor.encrypt(block)
            if file_size > 16:
                i += 16
            else:
                i = 0
            block = chunk[i:i + 16]
            if len(block) % 16:
                block += b'\0' * (16 - (len(block) % 16))
            mac_str = mac_encryptor.encrypt(encryptor.encrypt(block))
            file_info = os.stat(temp_output_file.name)
            progress_percent = float("{:.2f}".format(file_info.st_size / file_size * 100))
            print('{:.2f}/{:.2f}mb downloaded - {}%'.format(
                file_info.st_size / 1000000,
                file_size / 1000000,
                progress_percent,
            ), end="\r")
            if d_id is not None:
                download_progress[d_id]['progress'] = progress_percent
        file_mac = str_to_a32(mac_str)
        if (file_mac[0] ^ file_mac[1], file_mac[2] ^ file_mac[3]) != meta_mac:
            raise ValueError('Mismatched mac')
        output_path = Path(dest_path + file_name)
        shutil.move(temp_output_file.name, output_path)
        print("\n")
        return output_path

# Apply the monkey patch.
Mega._download_file = my_download_file

# -------------------------------
# Other Download Functions
# -------------------------------
def download_getcomics(url, download_id):
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
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            fname_match = re.search('filename="?([^";]+)"?', content_disposition)
            if fname_match:
                cd_filename = unquote(fname_match.group(1))
                filename = cd_filename
                app_logger.info(f"Filename from Content-Disposition: {filename}")
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(file_path):
            filename = f"{base}_{counter}{ext}"
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            counter += 1

        download_progress[download_id]['filename'] = file_path

        temp_file_path = file_path + ".crdownload"
        total_length = response.headers.get('content-length')
        if total_length is None:
            total_length = 0
        else:
            total_length = int(total_length)

        downloaded = 0
        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if download_progress.get(download_id, {}).get('cancelled'):
                    app_logger.info(f"Download {download_id} cancelled; deleting temp file.")
                    f.close()
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                    download_progress[download_id]['status'] = 'cancelled'
                    return None
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_length > 0:
                        percent = int((downloaded / total_length) * 100)
                    else:
                        percent = 0
                    download_progress[download_id]['progress'] = percent
        os.rename(temp_file_path, file_path)
        download_progress[download_id]['progress'] = 100
        app_logger.info("Download Success")
        return file_path
    except requests.RequestException as e:
        app_logger.error(f"Download Failed: {e}")
        download_progress[download_id]['status'] = 'error'
        download_progress[download_id]['progress'] = -1
        raise Exception(f"Error downloading file: {str(e)}")

def download_mega(url, download_id, dest_filename=None):
    try:
        download_progress[download_id]['progress'] = 0
        mega = Mega()
        m = mega.login()  # Anonymous login.
        
        # Inject the download_id so that our monkey-patched _download_file updates progress.
        m.download_id = download_id

        if download_progress.get(download_id, {}).get('cancelled'):
            app_logger.info(f"Download {download_id} cancelled before starting.")
            raise Exception("Download cancelled")

        if dest_filename:
            app_logger.info("Starting download with dest_filename")
            dest_path = os.path.join(DOWNLOAD_DIR, dest_filename)
            file_path = m.download_url(url, dest_filename=dest_path)
        else:
            file_path = m.download_url(url)
            filename = os.path.basename(file_path)
            target_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.abspath(file_path) != os.path.abspath(target_path):
                shutil.move(file_path, target_path)
                file_path = target_path

        download_progress[download_id]['filename'] = file_path
        download_progress[download_id]['status'] = 'complete'
        app_logger.info(f"Downloaded file saved as: {file_path}")
        return file_path
    except Exception as e:
        app_logger.error(f"Error downloading from Mega: {e}")
        download_progress[download_id]['status'] = 'error'
        download_progress[download_id]['progress'] = -1
        raise Exception(f"Error downloading from Mega: {e}")

# -------------------------------
# API Endpoints
# -------------------------------
@app.route('/download', methods=['POST', 'OPTIONS'])
def download():
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    data = request.get_json()
    app_logger.info("Received Download Request")
    if not data or 'link' not in data:
        return jsonify({'error': 'Missing "link" in request data'}), 400

    url = data['link']
    download_id = str(uuid.uuid4())
    download_progress[download_id] = {
         'url': url,
         'progress': 0,
         'status': 'queued',
         'filename': None,
    }
    task = {
         'download_id': download_id,
         'url': url,
         'dest_filename': data.get("dest_filename")
    }
    download_queue.put(task)
    return jsonify({'message': 'Download queued', 'download_id': download_id}), 200

@app.route('/download_status/<download_id>', methods=['GET'])
def download_status(download_id):
    progress = download_progress.get(download_id, 0)
    return jsonify({'download_id': download_id, 'progress': progress})

@app.route('/cancel_download/<download_id>', methods=['POST'])
def cancel_download(download_id):
    if download_id in download_progress:
        download_progress[download_id]['cancelled'] = True
        download_progress[download_id]['status'] = 'cancelled'
        return jsonify({'message': 'Download cancelled'}), 200
    else:
        return jsonify({'error': 'Download not found'}), 404

@app.route('/download_status_all', methods=['GET'])
def download_status_all():
    return jsonify(download_progress)

@app.route('/clear_downloads', methods=['POST'])
def clear_downloads():
    keys_to_delete = [
        download_id for download_id, details in download_progress.items() 
        if details.get('status') in ['complete', 'cancelled', 'error']
    ]
    for download_id in keys_to_delete:
        del download_progress[download_id]
    return jsonify({'message': f'Cleared {len(keys_to_delete)} downloads'}), 200

@app.route('/status', methods=['GET'])
def status():
    return render_template('status.html')

# -------------------------------
# Run the App
# -------------------------------
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
