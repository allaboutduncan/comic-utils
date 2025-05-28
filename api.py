import threading
from queue import Queue
from flask import Flask, request, jsonify, render_template, redirect, url_for
import os
import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError, RequestException
from urllib.parse import urlparse, unquote, urljoin
import uuid
import re
import json
import shutil
import tempfile
from pathlib import Path
from flask_cors import CORS
from werkzeug.utils import secure_filename
from typing import Optional
from http.client import IncompleteRead
import time
import signal

# Mega download support
from mega import Mega
from mega.errors import RequestError
from mega.crypto import base64_to_a32, base64_url_decode, decrypt_attr, a32_to_str, str_to_a32, get_chunks
from Crypto.Cipher import AES
from Crypto.Util import Counter

import pixeldrain

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
# URL Resolver
# -------------------------------
def resolve_final_url(url: str, *, hdrs=headers, max_hops: int = 6) -> str:
    """
    Follow every ordinary 3xx redirect **and** the HTML *meta-refresh* pages
    that GetComics sometimes serves, stopping once we reach the real file
    host (PixelDrain, Mega, etc.).  We never download the payload – only the
    headers or a tiny bit of the HTML.
    """
    current = url
    for _ in range(max_hops):
        try:
            r = requests.head(current, headers=hdrs,
                              allow_redirects=False, timeout=15)
        except requests.RequestException:
            # some hosts block HEAD → fall back to a very small GET
            r = requests.get(current, headers=hdrs, stream=True,
                             allow_redirects=False, timeout=15)
        # Ordinary HTTP 3xx
        if 300 <= r.status_code < 400 and 'location' in r.headers:
            current = urljoin(current, r.headers['location'])
            continue
        # Meta-refresh (GetComics’ /dlds pages)
        if ('text/html' in r.headers.get('content-type', '') and
                b'<meta' in r.content[:2048]):
            m = re.search(br'url=([^">]+)', r.content[:2048], flags=re.I)
            if m:
                current = urljoin(current, m.group(1).decode().strip())
                continue
        return current
    return current        # give up after max_hops

# -------------------------------
# QUEUE AND WORKER THREAD SETUP (for non-scrape downloads)
# -------------------------------
download_queue = Queue()

def process_download(task):
    download_id = task['download_id']
    original_url  = task['url']
    dest_filename = task.get('dest_filename')

    download_progress[download_id]['status'] = 'in_progress'

    try:
        final_url = resolve_final_url(original_url)
        app_logger.info(f"Resolved → {final_url}")

        if "mega.nz" in final_url:
            file_path = download_mega(final_url, download_id, dest_filename)
        elif "pixeldrain.com" in final_url:
            file_path = download_pixeldrain(final_url, download_id, dest_filename)
        elif "comicfiles.ru" in final_url:              # GetComics’ direct host
            file_path = download_getcomics(final_url, download_id)
        else:                                           # fall-back
            file_path = download_getcomics(final_url, download_id)

        download_progress[download_id]['filename'] = file_path
        download_progress[download_id]['status']   = 'complete'
    except Exception as e:
        app_logger.error(f"Error during background download: {e}")
        download_progress[download_id]['status']   = 'error'
        download_progress[download_id]['error'] = str(e)

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
# Other Download Functions
# -------------------------------
def download_getcomics(url, download_id):
    retries = 3
    delay = 2  # base delay in seconds
    last_exception = None

    for attempt in range(retries):
        try:
            app_logger.info(f"Attempt {attempt + 1} to download {url}")
            response = requests.get(url, stream=True, headers=headers, timeout=30)
            response.raise_for_status()
            if response.status_code in (403, 404):
                app_logger.warning(f"Fatal HTTP error {response.status_code}; aborting retries.")
                break


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
                    filename = unquote(fname_match.group(1))
                    app_logger.info(f"Filename from Content-Disposition: {filename}")

            file_path = os.path.join(DOWNLOAD_DIR, filename)
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(file_path):
                filename = f"{base}_{counter}{ext}"
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                counter += 1

            download_progress[download_id]['filename'] = file_path
            # Create a unique temp file per attempt
            attempt_suffix = f".{attempt}.crdownload"
            temp_file_path = file_path + attempt_suffix


            total_length = int(response.headers.get('content-length', 0))
            download_progress[download_id]['bytes_total'] = total_length
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
                        download_progress[download_id]['bytes_downloaded'] = downloaded
                        if total_length > 0:
                            percent = int((downloaded / total_length) * 100)
                            download_progress[download_id]['progress'] = percent

            os.rename(temp_file_path, file_path)
            download_progress[download_id]['progress'] = 100
            app_logger.info(f"Download completed: {file_path}")
            return file_path

        except (ChunkedEncodingError, ConnectionError, IncompleteRead, RequestException) as e:
            app_logger.warning(f"Attempt {attempt + 1} failed with error: {e}")
            last_exception = e
            time.sleep(delay * (2 ** attempt))  # Exponential backoff

            # Clean up temp file between retries
            temp_file_path = file_path + ".crdownload" if 'file_path' in locals() else None
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as cleanup_err:
                    app_logger.warning(f"Failed to remove temp file: {cleanup_err}")

    # All retries failed
    app_logger.error(f"Download failed after {retries} attempts: {last_exception}")
    download_progress[download_id]['status'] = 'error'
    download_progress[download_id]['progress'] = -1

    # Remove leftover crdownload files from all attempts
    for i in range(retries):
        leftover = file_path + f".{i}.crdownload"
        if os.path.exists(leftover):
            try:
                os.remove(leftover)
            except Exception as e:
                app_logger.warning(f"Failed to remove stale temp file: {leftover} — {e}")

    raise Exception(f"Download failed after {retries} attempts for {url}: {last_exception}")

def get_mega_file_info(url):
    parsed = urlparse(url)
    file_id = parsed.path.split("/")[-1]
    file_key_b64 = url.split("#")[-1]

    k = base64_to_a32(file_key_b64)
    file_data = requests.get("https://g.api.mega.co.nz/cs?id=0", json=[{"a": "g", "g": 1, "p": file_id}]).json()[0]

    if 'g' not in file_data:
        raise Exception("File not accessible anymore")

    iv = k[4:6] + (0, 0)
    meta_mac = k[6:8]
    key = [(k[0] ^ k[4]), (k[1] ^ k[5]), (k[2] ^ k[6]), (k[3] ^ k[7])]
    k_str = a32_to_str(key)

    attribs = decrypt_attr(base64_url_decode(file_data["at"]), key)

    return {
        "g": file_data["g"],
        "size": file_data["s"],
        "name": attribs["n"],
        "k": key,
        "iv": iv,
        "meta_mac": meta_mac
    }

def download_mega(url, download_id, dest_filename=None):
    try:
        download_progress[download_id]['progress'] = 0
        download_progress[download_id]['bytes_downloaded'] = 0
        download_progress[download_id]['bytes_total'] = 0
        download_progress[download_id]['status'] = 'in_progress'

        file_info = get_mega_file_info(url)

        file_size = file_info['size']
        file_name = secure_filename(dest_filename or file_info['name'])

        base, ext = os.path.splitext(file_name)
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        counter = 1
        while os.path.exists(file_path):
            file_path = os.path.join(DOWNLOAD_DIR, f"{base}_{counter}{ext}")
            counter += 1
        tmp_path = file_path + ".part"

        download_progress[download_id]['filename'] = file_path
        download_progress[download_id]['bytes_total'] = file_size

        # Setup AES decryption
        k_str = a32_to_str(file_info['k'])
        counter_iv = Counter.new(128, initial_value=((file_info['iv'][0] << 32) + file_info['iv'][1]) << 64)
        aes = AES.new(k_str, AES.MODE_CTR, counter=counter_iv)

        with requests.get(file_info["g"], stream=True) as r, open(tmp_path, "wb") as f:
            r.raise_for_status()
            downloaded = 0
            for chunk in r.iter_content(chunk_size=1 << 20):
                if download_progress.get(download_id, {}).get('cancelled'):
                    app_logger.info(f"Download {download_id} cancelled mid-transfer.")
                    r.close()
                    f.close()
                    os.remove(tmp_path)
                    download_progress[download_id]['status'] = 'cancelled'
                    return None
                if chunk:
                    decrypted = aes.decrypt(chunk)
                    f.write(decrypted)
                    downloaded += len(chunk)
                    download_progress[download_id]['bytes_downloaded'] = downloaded
                    percent = int((downloaded / file_size) * 100)
                    download_progress[download_id]['progress'] = percent
                    print(f"{downloaded / 1e6:.2f}/{file_size / 1e6:.2f} MB - {percent}%", end="\r")

        os.rename(tmp_path, file_path)
        download_progress[download_id]['progress'] = 100
        download_progress[download_id]['status'] = 'complete'
        app_logger.info(f"Download from Mega complete → {file_path}")
        return file_path

    except Exception as e:
        app_logger.error(f"Error downloading from Mega: {e}")
        download_progress[download_id]['status'] = 'error'
        download_progress[download_id]['progress'] = -1
        raise Exception(f"Error downloading from Mega: {e}")

# -------------------------------
# Pixeldrain support
# -------------------------------
def _pd_id(url: str) -> str:
    """Return the last path component regardless of URL shape."""
    return urlparse(url).path.rstrip("/").split("/")[-1]


def download_pixeldrain(url: str, download_id: str, dest_name: Optional[str] = None) -> str:
    """
    Download a single PixelDrain *file* **or** a *folder/album* (as ZIP).

    • Progress is reported via `download_progress[download_id]`
    • Returns the absolute path to the completed file on success
    """
    file_id = _pd_id(url)

    # ---------- 1. Look up metadata via the library -----------------
    info = pixeldrain.info(file_id)                  # dict from API
    is_folder = info.get("content_type") == "folder"
    original_name = info.get("name") or f"{file_id}.bin"
    wanted_name = dest_name or original_name
    filename_fs  = secure_filename(wanted_name)

    # ---------- 2. Set filename in progress dict (early!) ----------
    download_progress[download_id] |= {
        "filename": filename_fs,
        "progress": 0,
    }

    # ---------- 3. Decide download URL ----------
    if is_folder:
        dl_url = f"https://pixeldrain.com/api/file/{file_id}/zip"
        total_bytes = None
    else:
        dl_url = f"{pixeldrain.file(file_id)}?download"
        total_bytes = info.get("size")
        download_progress[download_id]["bytes_total"] = total_bytes or 0

    # ---------- 4. Reserve output path ----------
    out_path = os.path.join(DOWNLOAD_DIR, filename_fs)
    base, ext = os.path.splitext(out_path)
    i = 1
    while os.path.exists(out_path):
        out_path = f"{base}_{i}{ext}"
        i += 1

    tmp_path = out_path + ".part"
    download_progress[download_id] |= {"filename": out_path, "progress": 0}

    # ---------- 5. Download ----------
    with requests.get(dl_url, stream=True, timeout=60) as r, open(tmp_path, "wb") as f:
        r.raise_for_status()
        done = 0
        for chunk in r.iter_content(chunk_size=1 << 20):   # 1 MiB
            if chunk:
                f.write(chunk)
                done += len(chunk)
                if total_bytes:
                    pct = int(done / total_bytes * 100)
                    download_progress[download_id]["progress"] = pct
                    download_progress[download_id]["bytes_downloaded"] = done

    os.rename(tmp_path, out_path)
    download_progress[download_id]["progress"] = 100
    app_logger.info(f"PixelDrain download complete → {out_path}")
    return out_path

# -------------------------------
# API Endpoints
# -------------------------------+
@app.route('/download', methods=['GET'])
def download_get_friendly():
    return """
    <html>
        <head><title>CLU Download Endpoint</title></head>
        <body style="font-family: sans-serif;">
            <h1>CLU API: /download</h1>
            <p>This endpoint is used to queue remote comic downloads via POST request.</p>
            <p>Install and configure the <a href="https://chromewebstore.google.com/detail/send-link-to-clu/cpickljbofjhmhkphgdmiagkdfijlkkg">Chrome Extension</a> to send downloads to your URL.</p>
        </body>
    </html>
    """, 200

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
         'bytes_total': 0,
         'bytes_downloaded': 0,
         'status': 'queued',
         'filename': None,
         'error': None,
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
# Graceful Shutdown
# -------------------------------

import signal
def shutdown_handler(signum, frame):
    app_logger.info("Shutting down download workers...")
    for _ in worker_threads:
        download_queue.put(None)
    for t in worker_threads:
        t.join()
    app_logger.info("All workers stopped.")
    os._exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# -------------------------------
# Run the App
# -------------------------------
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
