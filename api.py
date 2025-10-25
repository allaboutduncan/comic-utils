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
import base64

# Mega download support
from mega import Mega
from mega.errors import RequestError
from mega.crypto import base64_to_a32, base64_url_decode, decrypt_attr, a32_to_str, str_to_a32, get_chunks
from Crypto.Cipher import AES
from Crypto.Util import Counter

import pixeldrain
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

    # Create a session with connection pooling and optimization for large files
    session = requests.Session()

    # Configure retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=10,
        pool_block=False
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Set TCP keepalive and socket options for better performance
    session.headers.update(headers)

    for attempt in range(retries):
        try:
            app_logger.info(f"Attempt {attempt + 1} to download {url}")
            # Increase timeout for large files: 60s connection, 300s read (5 minutes)
            response = session.get(url, stream=True, timeout=(60, 300))
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
            
            app_logger.info(f"Temp file path: {temp_file_path}")
            app_logger.info(f"Final file path: {file_path}")

            total_length = int(response.headers.get('content-length', 0))
            download_progress[download_id]['bytes_total'] = total_length
            downloaded = 0

            # Optimize chunk size based on file size
            if total_length > 1024 * 1024 * 1024:  # > 1GB: use 4MB chunks
                chunk_size = 4 * 1024 * 1024
            elif total_length > 100 * 1024 * 1024:  # > 100MB: use 1MB chunks
                chunk_size = 1024 * 1024
            else:  # smaller files: use 256KB chunks
                chunk_size = 256 * 1024

            app_logger.info(f"Downloading {total_length / (1024*1024):.1f}MB using {chunk_size / 1024}KB chunks")

            # Use larger buffer for writing to disk (improves I/O performance)
            buffer_size = 8 * 1024 * 1024  # 8MB write buffer

            with open(temp_file_path, 'wb', buffering=buffer_size) as f:
                # Track time for speed calculation
                start_time = time.time()
                last_log_time = start_time
                last_downloaded = 0

                for chunk in response.iter_content(chunk_size=chunk_size):
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

                        # Update progress
                        if total_length > 0:
                            percent = int((downloaded / total_length) * 100)
                            download_progress[download_id]['progress'] = percent

                            # Log speed every 10 seconds for large files
                            current_time = time.time()
                            if total_length > 100 * 1024 * 1024 and (current_time - last_log_time) >= 10:
                                speed_mbps = ((downloaded - last_downloaded) / (1024 * 1024)) / (current_time - last_log_time)
                                app_logger.info(f"Download progress: {percent}% ({downloaded / (1024*1024):.1f}MB / {total_length / (1024*1024):.1f}MB) @ {speed_mbps:.2f} MB/s")
                                last_log_time = current_time
                                last_downloaded = downloaded

            # Log final download stats
            total_time = time.time() - start_time
            avg_speed = (downloaded / (1024 * 1024)) / total_time if total_time > 0 else 0
            app_logger.info(f"Download completed in {total_time:.1f}s @ average {avg_speed:.2f} MB/s")

            # Verify download completed successfully
            if total_length > 0 and downloaded != total_length:
                raise Exception(f"Download incomplete: got {downloaded} bytes, expected {total_length} bytes")

            # Verify temp file exists and has expected size
            if not os.path.exists(temp_file_path):
                raise Exception(f"Temp file not found: {temp_file_path}")

            temp_file_size = os.path.getsize(temp_file_path)
            if total_length > 0 and temp_file_size != total_length:
                raise Exception(f"Temp file size mismatch: {temp_file_size} bytes, expected {total_length} bytes")

            # Rename temp file to final destination
            try:
                os.rename(temp_file_path, file_path)
                app_logger.info(f"Successfully renamed temp file to: {file_path}")
            except Exception as rename_err:
                app_logger.error(f"Failed to rename temp file: {rename_err}")
                raise

            # Verify final file exists
            if not os.path.exists(file_path):
                raise Exception(f"Final file not found after rename: {file_path}")

            download_progress[download_id]['progress'] = 100
            app_logger.info(f"Download completed: {file_path} ({downloaded} bytes)")

            # Clean up session
            session.close()

            return file_path

        except (ChunkedEncodingError, ConnectionError, IncompleteRead, RequestException, Exception) as e:
            app_logger.warning(f"Attempt {attempt + 1} failed with error: {e}")
            last_exception = e

            # Clean up the attempt-specific temp file
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    app_logger.info(f"Cleaned up temp file between retries: {temp_file_path}")
                except Exception as cleanup_err:
                    app_logger.warning(f"Failed to remove temp file: {cleanup_err}")
            else:
                app_logger.debug("No temp file to clean up between retries")

            # Wait before next retry (exponential backoff)
            if attempt < retries - 1:  # Don't sleep after the last attempt
                time.sleep(delay * (2 ** attempt))

    # All retries failed - cleanup
    session.close()

    app_logger.error(f"Download failed after {retries} attempts: {last_exception}")
    download_progress[download_id]['status'] = 'error'
    download_progress[download_id]['progress'] = -1

    # Remove leftover crdownload files from all attempts
    if 'file_path' in locals():
        for i in range(retries):
            leftover = file_path + f".{i}.crdownload"
            if os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except Exception as e:
                    app_logger.warning(f"Failed to remove stale temp file: {leftover} — {e}")
            else:
                app_logger.debug(f"No leftover temp file to remove: {leftover}")

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
    return urlparse(url).path.rstrip("/").split("/")[-1]

def _requests_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries, pool_connections=32, pool_maxsize=32))
    s.mount("http://",  HTTPAdapter(max_retries=retries, pool_connections=32, pool_maxsize=32))
    return s

def _parse_total_from_headers(hdrs, default_size=None):
    # Prefer Content-Range when resuming, else Content-Length
    cr = hdrs.get("Content-Range")
    if cr and "bytes" in cr:
        # e.g., "bytes 1048576-2097151/987654321"
        try:
            total = int(cr.split("/")[-1])
            return total
        except Exception:
            pass
    cl = hdrs.get("Content-Length")
    if cl:
        try:
            return int(cl)
        except Exception:
            pass
    return default_size

def download_pixeldrain(url: str, download_id: str, dest_name: Optional[str] = None) -> str:
    """
    Download a single PixelDrain file or folder (as ZIP).
    Keeps anonymous + API-key modes, but uses the fast '?download' endpoint,
    enables resume, larger chunks, and resilient retries.
    """
    file_id = _pd_id(url)

    # --- config / auth ---
    api_key = config.get("SETTINGS", "PIXELDRAIN_API_KEY", fallback="").strip()
    auth = ("", api_key) if api_key else None

    # 1) Resolve metadata (mostly for naming). We’ll try a lightweight HEAD to the download URL
    #    which is faster + works for both modes; if it fails, fall back to library/info.
    is_folder = False
    original_name = dest_name
    session = _requests_session()

    # Build the *download* endpoints up-front
    file_dl_url   = f"https://pixeldrain.com/api/file/{file_id}?download"
    folder_dl_url = f"https://pixeldrain.com/api/file/{file_id}/zip?download"

    try:
        # Quick HEAD on file endpoint (if it's actually a folder we'll detect after)
        h = session.head(file_dl_url, headers={**headers, "Accept": "application/octet-stream"},
                         auth=auth, allow_redirects=True, timeout=(10, 60))
        # PixelDrain sends filename via Content-Disposition
        cd = h.headers.get("Content-Disposition", "")
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        if m:
            original_name = unquote(m.group(1))
    except Exception:
        # Fallback to library/json info (works anonymously too)
        try:
            info = pixeldrain.info(file_id)
            is_folder = info.get("content_type") == "folder"
            if not original_name:
                original_name = info.get("name") or f"{file_id}.bin"
        except Exception:
            # still make sure we have a name
            if not original_name:
                original_name = f"{file_id}.bin"

    # If we didn’t know folder/file yet, do a tiny GET to folder URL to check
    if not is_folder:
        try:
            # Ping the folder url; folder responses are not octet-stream for direct file
            test = session.head(folder_dl_url, headers=headers, auth=auth,
                                allow_redirects=False, timeout=(5, 30))
            # folder zip exists if not 404
            is_folder = test.status_code != 404
        except Exception:
            pass

    # Final URL + name
    dl_url = folder_dl_url if is_folder else file_dl_url
    if not original_name:
        original_name = f"{file_id}.zip" if is_folder else f"{file_id}.bin"
    filename_fs = secure_filename(original_name)

    # 2) progress bootstrap
    download_progress.setdefault(download_id, {})
    download_progress[download_id] |= {"filename": filename_fs, "progress": 0}

    # 3) choose output path
    out_path = os.path.join(DOWNLOAD_DIR, filename_fs)
    base, ext = os.path.splitext(out_path)
    n = 1
    while os.path.exists(out_path):
        out_path = f"{base}_{n}{ext}"
        n += 1
    tmp_path = out_path + ".part"

    # 4) resume support
    existing = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
    range_header = {"Range": f"bytes={existing}-"} if existing > 0 else {}

    # 5) start download (bigger chunks; robust retries)
    req_headers = {
        **headers,
        "Accept": "application/octet-stream",
        "Connection": "keep-alive",
        "Accept-Encoding": "identity",  # avoid gzip on large binaries
        **range_header,
    }

    app_logger.info(
        f"PixelDrain download → {dl_url} "
        f"({'auth' if auth else 'anon'}; resume={existing>0}; tmp={os.path.basename(tmp_path)})"
    )

    # Open mode: append if resuming, else write-new
    mode = "ab" if existing > 0 else "wb"
    chunk = 8 * 1024 * 1024  # 8 MiB

    try:
        with session.get(dl_url, stream=True, headers=req_headers, auth=auth,
                         allow_redirects=True, timeout=(10, 180)) as r, open(tmp_path, mode) as f:

            r.raise_for_status()

            # If we asked for a range but didn’t get 206, start over
            if existing > 0 and r.status_code != 206:
                app_logger.info("Server did not honor Range; restarting from 0")
                f.close()
                os.remove(tmp_path)
                existing = 0
                req_headers.pop("Range", None)
                with session.get(dl_url, stream=True, headers=req_headers, auth=auth,
                                 allow_redirects=True, timeout=(10, 180)) as r2, open(tmp_path, "wb") as f2:
                    r2.raise_for_status()
                    total = _parse_total_from_headers(r2.headers, None)
                    if total:
                        download_progress[download_id]["bytes_total"] = total
                    done = 0
                    for chunk_bytes in r2.iter_content(chunk_size=chunk):
                        if chunk_bytes:
                            f2.write(chunk_bytes)
                            done += len(chunk_bytes)
                            if total:
                                download_progress[download_id]["bytes_downloaded"] = done
                                download_progress[download_id]["progress"] = int(done / total * 100)
            else:
                total = _parse_total_from_headers(r.headers, None)
                if total:
                    # If resuming, total is the full size; update counters accordingly
                    download_progress[download_id]["bytes_total"] = total
                done = existing
                if existing and total:
                    download_progress[download_id]["bytes_downloaded"] = existing
                    download_progress[download_id]["progress"] = int(existing / total * 100)

                for chunk_bytes in r.iter_content(chunk_size=chunk):
                    if not chunk_bytes:
                        continue
                    f.write(chunk_bytes)
                    done += len(chunk_bytes)
                    if total:
                        download_progress[download_id]["bytes_downloaded"] = done
                        download_progress[download_id]["progress"] = int(done / total * 100)

        os.replace(tmp_path, out_path)
        download_progress[download_id]["progress"] = 100
        app_logger.info(f"PixelDrain download complete → {out_path}")
        return out_path

    except requests.Timeout as e:
        app_logger.error(f"Timeout during PixelDrain download: {e}")
        raise Exception(f"Timeout during download: {e}")
    except requests.RequestException as e:
        app_logger.error(f"Request error during PixelDrain download: {e}")
        raise Exception(f"Request error during download: {e}")
    except Exception as e:
        app_logger.error(f"Unexpected error during PixelDrain download: {e}")
        raise

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

@app.route('/download_summary')
def download_summary():
    active = sum(1 for d in download_progress.values() if d.get("status") in ["queued", "in_progress"])
    return jsonify({"active": active})

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
