from flask import Flask, render_template, request, Response, send_from_directory, redirect, jsonify, url_for, stream_with_context, render_template_string
import subprocess
import os
import shutil
import uuid
import sys
import threading
import click
import time
import logging
import signal
import psutil
import select
import pwd
from functools import lru_cache
from collections import defaultdict
import hashlib
from api import app 
from config import config, load_flask_config, write_config, load_config
from edit import get_edit_modal, save_cbz, cropCenter, cropLeft, cropRight, get_image_data_url, modal_body_template
from memory_utils import initialize_memory_management, cleanup_on_exit, memory_context, get_global_monitor

load_config()

# app = Flask(__name__)

DATA_DIR = "/data"  # Directory to browse
TARGET_DIR = config.get("SETTINGS", "TARGET", fallback="/processed")

#########################
#     Cache System      #
#########################

# Global cache for directory listings
directory_cache = {}
cache_timestamps = {}
CACHE_DURATION = 5  # Cache for 5 seconds
MAX_CACHE_SIZE = 100  # Maximum number of cached directories

def get_directory_hash(path):
    """Generate a hash for directory contents to detect changes."""
    try:
        stat = os.stat(path)
        # Use modification time and size as a simple change detector
        return f"{stat.st_mtime}_{stat.st_size}"
    except:
        return "error"

def is_cache_valid(path):
    """Check if cached data is still valid."""
    if path not in cache_timestamps:
        return False
    
    # Check if cache has expired
    if time.time() - cache_timestamps[path] > CACHE_DURATION:
        return False
    
    # Check if directory has changed
    current_hash = get_directory_hash(path)
    cached_hash = directory_cache.get(path, {}).get('hash')
    return current_hash == cached_hash

def cleanup_cache():
    """Remove expired entries from cache."""
    current_time = time.time()
    expired_paths = [
        path for path, timestamp in cache_timestamps.items()
        if current_time - timestamp > CACHE_DURATION
    ]
    for path in expired_paths:
        directory_cache.pop(path, None)
        cache_timestamps.pop(path, None)

def get_directory_listing(path):
    """Get directory listing with optimized file system operations."""
    try:
        with memory_context("list_directories"):
            entries = os.listdir(path)
            
            # Single pass to categorize entries
            directories = []
            files = []
            excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db"}
            
            for entry in entries:
                if entry.startswith(('.', '_')):
                    continue
                    
                full_path = os.path.join(path, entry)
                try:
                    stat = os.stat(full_path)
                    if stat.st_mode & 0o40000:  # Directory
                        directories.append(entry)
                    else:  # File
                        # Check if file should be excluded
                        if not any(entry.lower().endswith(ext) for ext in excluded_extensions):
                            files.append({
                                "name": entry,
                                "size": stat.st_size
                            })
                except (OSError, IOError):
                    # Skip files we can't access
                    continue
            
            # Sort both lists
            directories.sort(key=lambda s: s.lower())
            files.sort(key=lambda f: f["name"].lower())
            
            return {
                "directories": directories,
                "files": files,
                "hash": get_directory_hash(path)
            }
    except Exception as e:
        app_logger.error(f"Error getting directory listing for {path}: {e}")
        raise

def invalidate_cache_for_path(path):
    """Invalidate cache for a specific path and its parent."""
    if path in directory_cache:
        del directory_cache[path]
        del cache_timestamps[path]
    
    # Also invalidate parent directory cache
    parent = os.path.dirname(path)
    if parent in directory_cache:
        del directory_cache[parent]
        del cache_timestamps[parent]

@app.route('/clear-cache', methods=['POST'])
def clear_cache():
    """Manually clear the directory cache."""
    global directory_cache, cache_timestamps
    directory_cache.clear()
    cache_timestamps.clear()
    app_logger.info("Directory cache cleared manually")
    return jsonify({"success": True, "message": "Cache cleared"})

#########################
#     Global Values     #
#########################

@app.context_processor
def inject_monitor():
    return {'monitor': os.getenv("MONITOR", "no")}  # Default to "no" if not set

#########################
#     Logging Setup     #
#########################

# Define log file paths
MONITOR_LOG = "logs/monitor.log"
os.makedirs(os.path.dirname(MONITOR_LOG), exist_ok=True)
if not os.path.exists(MONITOR_LOG):
    with open(MONITOR_LOG, "w") as f:
        f.write("")  # Create an empty file

APP_LOG = "logs/app.log"
os.makedirs(os.path.dirname(APP_LOG), exist_ok=True)
if not os.path.exists(APP_LOG):
    with open(APP_LOG, "w") as f:
        f.write("")  # Create an empty file

# Create a logger for app.py
app_logger = logging.getLogger("app_logger")
app_logger.setLevel(logging.INFO)

# Prevent duplicate handlers
if not app_logger.handlers:
    app_handler = logging.FileHandler(APP_LOG)
    app_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    app_logger.addHandler(app_handler)

# Optionally disable propagation to avoid duplicate logs from the root logger
app_logger.propagate = False

# Example usage
app_logger.info("App started successfully!")

# Initialize memory management
initialize_memory_management()

#########################
#   List Directories    #
#########################
@app.route('/list-directories', methods=['GET'])
def list_directories():
    """List directories and files in the given path, excluding images,
    and excluding any directories or files that start with '.' or '_'."""
    current_path = request.args.get('path', DATA_DIR)  # Default to /data

    if not os.path.exists(current_path):
        return jsonify({"error": "Directory not found"}), 404

    try:
        # Clean up expired cache entries
        cleanup_cache()
        
        # Check if we have valid cached data
        if is_cache_valid(current_path):
            cached_data = directory_cache[current_path]
            parent_dir = os.path.dirname(current_path) if current_path != DATA_DIR else None
            
            return jsonify({
                "current_path": current_path,
                "directories": cached_data["directories"],
                "files": cached_data["files"],
                "parent": parent_dir,
                "cached": True
            })
        
        # Get fresh directory listing
        listing_data = get_directory_listing(current_path)
        
        # Cache the result
        directory_cache[current_path] = listing_data
        cache_timestamps[current_path] = time.time()
        
        # Limit cache size
        if len(directory_cache) > MAX_CACHE_SIZE:
            # Remove oldest entries
            oldest_path = min(cache_timestamps.keys(), key=lambda k: cache_timestamps[k])
            directory_cache.pop(oldest_path, None)
            cache_timestamps.pop(oldest_path, None)
        
        parent_dir = os.path.dirname(current_path) if current_path != DATA_DIR else None

        return jsonify({
            "current_path": current_path,
            "directories": listing_data["directories"],
            "files": listing_data["files"],
            "parent": parent_dir,
            "cached": False
        })
    except Exception as e:
        app_logger.error(f"Error in list_directories for {current_path}: {e}")
        return jsonify({"error": str(e)}), 500


#########################
#    List Downloads     #
#########################
@app.route('/list-downloads', methods=['GET'])
def list_downloads():
    """List directories and files in the given path, excluding images,
    and excluding any directories or files that start with '.' or '_'."""
    current_path = request.args.get('path', TARGET_DIR)

    if not os.path.exists(current_path):
        return jsonify({"error": "Directory not found"}), 404

    try:
        # Clean up expired cache entries
        cleanup_cache()
        
        # Check if we have valid cached data
        if is_cache_valid(current_path):
            cached_data = directory_cache[current_path]
            parent_dir = os.path.dirname(current_path) if current_path != TARGET_DIR else None
            
            return jsonify({
                "current_path": current_path,
                "directories": cached_data["directories"],
                "files": cached_data["files"],
                "parent": parent_dir,
                "cached": True
            })
        
        # Get fresh directory listing
        listing_data = get_directory_listing(current_path)
        
        # Cache the result
        directory_cache[current_path] = listing_data
        cache_timestamps[current_path] = time.time()
        
        # Limit cache size
        if len(directory_cache) > MAX_CACHE_SIZE:
            # Remove oldest entries
            oldest_path = min(cache_timestamps.keys(), key=lambda k: cache_timestamps[k])
            directory_cache.pop(oldest_path, None)
            cache_timestamps.pop(oldest_path, None)
        
        parent_dir = os.path.dirname(current_path) if current_path != TARGET_DIR else None

        return jsonify({
            "current_path": current_path,
            "directories": listing_data["directories"],
            "files": listing_data["files"],
            "parent": parent_dir,
            "cached": False
        })
    except Exception as e:
        app_logger.error(f"Error in list_downloads for {current_path}: {e}")
        return jsonify({"error": str(e)}), 500

#####################################
#  Move Files/Folders (Drag & Drop) #
#####################################
@app.route('/move', methods=['POST'])
def move():
    """
    Move a file or folder from the source path to the destination.
    If the "X-Stream" header is true and the source is a file,
    streams progress updates as SSE.
    """
    data = request.get_json()
    source = data.get('source')
    destination = data.get('destination')
    app_logger.info("********************// Move File //********************")
    app_logger.info(f"Requested move from: {source} to: {destination}")
    
    if not source or not destination:
        app_logger.error("Missing source or destination in request")
        return jsonify({"success": False, "error": "Missing source or destination"}), 400

    if not os.path.exists(source):
        app_logger.warning(f"Source path does not exist: {source}")
        return jsonify({"success": False, "error": "Source path does not exist"}), 404

    stream = request.headers.get('X-Stream', 'false').lower() == 'true'

    if os.path.isfile(source) and stream:
        file_size = os.path.getsize(source)
        
        # Use memory context for large file operations
        cleanup_threshold = 1000 if file_size > 100 * 1024 * 1024 else 500  # 100MB threshold

        def generate():
            with memory_context("file_move", cleanup_threshold):
                bytes_copied = 0
                chunk_size = 1024 * 1024  # 1 MB
                try:
                    app_logger.info(f"Streaming file move with progress: {source}")
                    with open(source, 'rb') as fsrc, open(destination, 'wb') as fdst:
                        while True:
                            chunk = fsrc.read(chunk_size)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            bytes_copied += len(chunk)
                            progress = int((bytes_copied / file_size) * 100)
                            yield f"data: {progress}\n\n"
                    os.remove(source)
                    app_logger.info(f"Move complete (streamed): Removed {source}")
                    yield "data: 100\n\n"
                except Exception as e:
                    app_logger.exception(f"Error during streaming move from {source} to {destination}")
                    yield f"data: error: {str(e)}\n\n"
                yield "data: done\n\n"

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "close"
        }
        return Response(stream_with_context(generate()), headers=headers)

    else:
        # Non-streaming move for folders or when streaming is disabled
        with memory_context("file_move"):
            try:
                if os.path.isfile(source):
                    shutil.move(source, destination)
                else:
                    shutil.move(source, destination)
                app_logger.info(f"Move complete: {source} -> {destination}")
                
                # Invalidate cache for affected directories
                invalidate_cache_for_path(os.path.dirname(source))
                invalidate_cache_for_path(os.path.dirname(destination))
                
                return jsonify({"success": True})
            except Exception as e:
                app_logger.error(f"Error moving {source} to {destination}: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
    
#####################################
#       Calculate Folder Size       #
#####################################
@app.route('/folder-size', methods=['GET'])
def folder_size():
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return jsonify({"error": "Invalid path"}), 400

    def get_directory_stats(path):
        total_size = 0
        comic_count = 0
        magazine_count = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    if os.path.exists(fp):
                        total_size += os.path.getsize(fp)
                        ext = f.lower()
                        if ext.endswith(('.cbz', '.cbr')):
                            comic_count += 1
                        elif ext.endswith('.pdf'):
                            magazine_count += 1
                except Exception:
                    pass
        return total_size, comic_count, magazine_count

    size, comic_count, magazine_count = get_directory_stats(path)
    return jsonify({
        "size": size,
        "comic_count": comic_count,
        "magazine_count": magazine_count
    })

#####################################
#     Move Files/Folders UI Page    #
#####################################
@app.route('/files')
def files_page():
    watch = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_dir = config.get("SETTINGS", "TARGET", fallback="/processed")
    return render_template('files.html', watch=watch, target_dir=target_dir)


#####################################
#       Rename Files/Folders        #
#####################################
@app.route('/rename', methods=['POST'])
def rename():
    data = request.get_json()
    old_path = data.get('old')
    new_path = data.get('new')
    
    app_logger.info("Renaming:", old_path, "to", new_path)  

    # Validate input
    if not old_path or not new_path:
        return jsonify({"error": "Missing old or new path"}), 400
    
    # Check if the old path exists
    if not os.path.exists(old_path):
        return jsonify({"error": "Source file or directory does not exist"}), 404

    # Optionally, check if the new path already exists to avoid overwriting
    if os.path.exists(new_path):
        return jsonify({"error": "Destination already exists"}), 400

    try:
        os.rename(old_path, new_path)
        
        # Invalidate cache for affected directories
        invalidate_cache_for_path(os.path.dirname(old_path))
        invalidate_cache_for_path(os.path.dirname(new_path))
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#####################################
#           Crop Images             #
#####################################
@app.route('/crop', methods=['POST'])
def crop_image():
    try:
        data = request.json
        file_path = data.get('target')
        crop_type = data.get('cropType')
        app_logger.info("********************// Crop Image //********************")
        app_logger.info(f"File Path: {file_path}")
        app_logger.info(f"Crop Type: {crop_type}")

        # Validate input
        if not file_path or not crop_type:
            return jsonify({'success': False, 'error': 'Missing file path or crop type'}), 400

        file_cards = []

        if crop_type == 'left':
            new_image_path, backup_path = cropLeft(file_path)
            for path in [new_image_path, backup_path]:
                file_cards.append({
                    "filename": os.path.basename(path),
                    "rel_path": path,
                    "img_data": get_image_data_url(path)
                })

        elif crop_type == 'right':
            new_image_path, backup_path = cropRight(file_path)
            for path in [new_image_path, backup_path]:
                file_cards.append({
                    "filename": os.path.basename(path),
                    "rel_path": path,
                    "img_data": get_image_data_url(path)
                })

        elif crop_type == 'center':
            result = cropCenter(file_path)
            for key, path in result.items():
                file_cards.append({
                    "filename": os.path.basename(path),
                    "rel_path": path,
                    "img_data": get_image_data_url(path)
                })
        else:
            return jsonify({'success': False, 'error': 'Invalid crop type'}), 400

        # Render the cards as HTML
        
        modal_card_html = render_template_string(modal_body_template, file_cards=file_cards)

        return jsonify({
            'success': True,
            'html': modal_card_html,
            'message': f'{crop_type.capitalize()} crop completed.',
        })

    except Exception as e:
        app_logger.error(f"Crop error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


#####################################
#       Delete Files/Folders        #
#####################################
@app.route('/delete', methods=['POST'])
def delete():
    data = request.get_json()
    target = data.get('target')
    if not target:
        return jsonify({"error": "Missing target path"}), 400
    if not os.path.exists(target):
        return jsonify({"error": "Target does not exist"}), 404

    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        
        # Invalidate cache for the directory containing the deleted item
        invalidate_cache_for_path(os.path.dirname(target))
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
#########################
#  Serve Static Files   #
#########################
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)

#########################
# Restart Flask App     #
#########################
def restart_app():
    """Gracefully restart the Flask application."""
    time.sleep(2)  # Delay to ensure the response is sent before restart
    os.execv(sys.executable, ['python'] + sys.argv)

@app.route('/restart', methods=['POST'])
def restart():
    threading.Thread(target=restart_app).start()  # Restart in a separate thread
    app_logger.info(f"Restarting Flask app...")
    return jsonify({"message": "Restarting Flask app..."}), 200

#########################
#   Config Page Route   #
#########################
@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        # Ensure SETTINGS section exists
        if "SETTINGS" not in config:
            config["SETTINGS"] = {}

        # Safely update config values
        config["SETTINGS"]["WATCH"] = request.form.get("watch", "/temp")
        config["SETTINGS"]["TARGET"] = request.form.get("target", "/processed")
        config["SETTINGS"]["IGNORED_TERMS"] = request.form.get("ignored_terms", "")
        config["SETTINGS"]["IGNORED_FILES"] = request.form.get("ignored_files", "")
        config["SETTINGS"]["IGNORED_EXTENSIONS"] = request.form.get("ignored_extensions", "")
        config["SETTINGS"]["AUTOCONVERT"] = str(request.form.get("autoConvert") == "on")
        config["SETTINGS"]["READ_SUBDIRECTORIES"] = str(request.form.get("readSubdirectories") == "on")
        config["SETTINGS"]["CONVERT_SUBDIRECTORIES"] = str(request.form.get("convertSubdirectories") == "on")        
        config["SETTINGS"]["XML_YEAR"] = str(request.form.get("xmlYear") == "on")
        config["SETTINGS"]["XML_MARKDOWN"] = str(request.form.get("xmlMarkdown") == "on")
        config["SETTINGS"]["XML_LIST"] = str(request.form.get("xmlList") == "on")
        config["SETTINGS"]["MOVE_DIRECTORY"] = str(request.form.get("moveDirectory") == "on")
        config["SETTINGS"]["AUTO_UNPACK"] = str(request.form.get("autoUnpack") == "on")
        config["SETTINGS"]["HEADERS"] = request.form.get("customHeaders", "")
        config["SETTINGS"]["SKIPPED_FILES"] = request.form.get("skippedFiles", "")
        config["SETTINGS"]["DELETED_FILES"] = request.form.get("deletedFiles", "")
        config["SETTINGS"]["OPERATION_TIMEOUT"] = request.form.get("operationTimeout", "3600")
        config["SETTINGS"]["LARGE_FILE_THRESHOLD"] = request.form.get("largeFileThreshold", "500")

        write_config()  # Save changes to config.ini
        load_flask_config(app)  # Reload into Flask config

        return redirect(url_for("config_page"))

    # Ensure SETTINGS section is a dictionary before accessing
    settings = config["SETTINGS"] if "SETTINGS" in config else {}

    return render_template(
        "config.html",
        watch=settings.get("WATCH", "/temp"),
        target=settings.get("TARGET", "/processed"),
        ignored_terms=settings.get("IGNORED_TERMS", ""),
        ignored_files=settings.get("IGNORED_FILES", ""),
        ignored_extensions=settings.get("IGNORED_EXTENSIONS", ""),
        autoConvert=settings.get("AUTOCONVERT", "False") == "True",
        readSubdirectories=settings.get("READ_SUBDIRECTORIES", "False") == "True",
        convertSubdirectories=settings.get("CONVERT_SUBDIRECTORIES", "False") == "True",        
        xmlYear=settings.get("XML_YEAR", "False") == "True",
        xmlMarkdown=settings.get("XML_MARKDOWN", "False") == "True",
        xmlList=settings.get("XML_LIST", "False") == "True",
        moveDirectory=settings.get("MOVE_DIRECTORY", "False") == "True",
        autoUnpack=settings.get("AUTO_UNPACK", "False") == "True",
        skippedFiles=settings.get("SKIPPED_FILES", ""),
        deletedFiles=settings.get("DELETED_FILES", ""),
        customHeaders=settings.get("HEADERS", ""),
        operationTimeout=settings.get("OPERATION_TIMEOUT", "3600"),
        largeFileThreshold=settings.get("LARGE_FILE_THRESHOLD", "500"),
        config=settings,  # Pass full settings dictionary
    )

#########################
#   Streaming Routes    #
#########################
@app.route('/stream/<script_type>')
def stream_logs(script_type):
    file_path = request.args.get('file_path')  # Get file_path for single_file script
    directory = request.args.get('directory')  # Get directory for rebuild/rename script

    # Define supported script types for single file actions
    single_file_scripts = ['single_file', 'crop', 'remove', 'delete','enhance_single', 'add']

    # Check if the correct parameter is passed for single_file scripts
    if script_type in single_file_scripts:
        if not file_path:
            return Response("Missing file_path for single file action.", status=400)
        elif not os.path.isfile(file_path):
            return Response("Invalid file_path.", status=400)

        script_file = f"{script_type}.py"

        def generate_logs():
            process = subprocess.Popen(
                ['python', '-u', script_file, file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
            # Capture both stdout and stderr
            for line in process.stdout:
                yield f"data: {line}\n\n"  # Format required by SSE
            for line in process.stderr:
                yield f"data: ERROR: {line}\n\n"
            process.wait()
            if process.returncode != 0:
                yield f"data: An error occurred while streaming logs. Return code: {process.returncode}.\n\n"
            else:
                yield "event: completed\ndata: Process completed successfully.\n\n"

        return Response(generate_logs(), content_type='text/event-stream')

    # Handle scripts that operate on directories
    elif script_type in ['rebuild', 'rename', 'convert', 'pdf', 'missing', 'enhance_dir','comicinfo']:
        if not directory or not os.path.isdir(directory):
            return Response("Invalid or missing directory path.", status=400)

        script_file = f"{script_type}.py"

        def generate_logs():
            # Set longer timeout for large file operations
            timeout_seconds = int(config.get("SETTINGS", "OPERATION_TIMEOUT", fallback="3600"))
            
            process = subprocess.Popen(
                ['python', '-u', script_file, directory],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
            
            # Use select to handle timeouts and prevent blocking
            import select
            
            while True:
                # Check if process is still running
                if process.poll() is not None:
                    break
                
                # Use select with timeout to check for output
                ready, _, _ = select.select([process.stdout, process.stderr], [], [], 1.0)
                
                if ready:
                    for stream in ready:
                        line = stream.readline()
                        if line:
                            if stream == process.stderr:
                                yield f"data: ERROR: {line}\n\n"
                            else:
                                yield f"data: {line}\n\n"
                        else:
                            # No more output from this stream
                            continue
                else:
                    # No output available, send keepalive for long operations
                    if script_type in ['convert', 'rebuild']:
                        yield f"data: \n\n"  # Keepalive to prevent timeout

            # Wait for process to complete
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                yield f"data: ERROR: Process timed out after {timeout_seconds} seconds\n\n"
                return

            if script_type == 'missing' and process.returncode == 0:
                # Define the path to the generated missing.txt
                missing_file_path = os.path.join(directory, "missing.txt")
                
                if os.path.exists(missing_file_path):
                    # Generate a unique filename to prevent overwriting
                    unique_id = uuid.uuid4().hex
                    static_missing_filename = f"missing_{unique_id}.txt"
                    static_missing_path = os.path.join(STATIC_DIR, static_missing_filename)
                    
                    try:
                        shutil.move(missing_file_path, static_missing_path)
                        missing_url = f"/static/{static_missing_filename}"
                        yield f"data: Download missing list: <a href='{missing_url}' target='_blank'>missing.txt</a>\n\n"
                    except Exception as e:
                        yield f"data: ERROR: Failed to move missing.txt: {str(e)}\n\n"

            if process.returncode != 0:
                yield f"data: An error occurred while streaming logs. Return code: {process.returncode}.\n\n"
            else:
                yield "event: completed\ndata: Process completed successfully.\n\n"

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
        return Response(generate_logs(), headers=headers, content_type='text/event-stream')

    return Response("Invalid script type.", status=400)

#########################
#    Create Diretory    #
#########################
@app.route('/create-folder', methods=['POST'])
def create_folder():
    data = request.json
    path = data.get('path')
    if not path:
        return jsonify({"success": False, "error": "No path specified"}), 400
    
    try:
        os.mkdir(path)
        
        # Invalidate cache for the parent directory
        invalidate_cache_for_path(os.path.dirname(path))
        
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

#########################
#       Home Page       #
#########################
@app.route('/')
def index():
    # These environment variables are set/updated by load_config_into_env()
    watch = config.get("SETTINGS", "WATCH", fallback="/temp")
    convert_subdirectories = config.getboolean('SETTINGS', 'CONVERT_SUBDIRECTORIES', fallback=False)
    return render_template('index.html', watch=watch, config=app.config, convertSubdirectories=convert_subdirectories)
    
#########################
#        App Logs       #
#########################
# Route for app logs page
@app.route('/app-logs')
def app_logs_page():
    return render_template('app-logs.html', config=app.config)

# Route for monitor logs page
@app.route('/mon-logs')
def mon_logs_page():
    return render_template('mon-logs.html', config=app.config)

# Function to stream logs in real-time
def stream_logs_file(log_file):
    with open(log_file, "r") as file:
        file.seek(0)  # Start from the beginning of the file
        while True:
            line = file.readline()
            if line:
                yield f"data: {line}\n\n"
            else:
                time.sleep(1)  # Wait for new log entries

# Streaming endpoint for application logs
@app.route('/stream/app')
def stream_app_logs():
    return Response(stream_logs_file(APP_LOG), content_type='text/event-stream')

# Streaming endpoint for monitor logs
MONITOR_LOG = "logs/monitor.log"
@app.route('/stream/mon')
def stream_mon_logs():
    return Response(stream_logs_file(MONITOR_LOG), content_type='text/event-stream')

#########################
#    Edit CBZ Route     #
#########################
@app.route('/edit', methods=['GET'])
def edit_cbz():
    """
    Processes the provided CBZ file (via 'file_path' query parameter) and returns a JSON
    object containing:
      - modal_body: HTML snippet for inline editing,
      - folder_name, zip_file_path, original_file_path for the hidden form fields.
    """
    file_path = request.args.get('file_path')
    if not file_path:
        return jsonify({"error": "Missing file path parameter"}), 400
    try:
        result = get_edit_modal(file_path)  # Reuse existing logic for generating modal content
        return jsonify(result)
    except Exception as e:
        app_logger.error(f"Error in /edit route: {e}")
        return jsonify({"error": str(e)}), 500

# Register the save route using the imported save_cbz function.
app.add_url_rule('/save', view_func=save_cbz, methods=['POST'])

#########################
#    Monitor Process    #
#########################
monitor_process = None  # Track subprocess

def run_monitor():
    global monitor_process
    app_logger.info("Attempting to start monitor.py...")
    
    monitor_process = subprocess.Popen(
        [sys.executable, 'monitor.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = monitor_process.communicate()
    if stdout:
        app_logger.info(f"monitor.py stdout:\n{stdout}")
    if stderr:
        app_logger.error(f"monitor.py stderr:\n{stderr}")

def cleanup():
    """Terminate monitor.py before shutdown."""
    if monitor_process and monitor_process.poll() is None:
        app_logger.info("Terminating monitor.py process...")
        monitor_process.terminate()
        try:
            monitor_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            app_logger.warning("Monitor did not terminate in time. Force killing...")
            monitor_process.kill()

def shutdown_server():
    app_logger.info("Shutting down Flask...")
    cleanup()
    os._exit(0)

# Handle termination signals
signal.signal(signal.SIGTERM, lambda signum, frame: shutdown_server())
signal.signal(signal.SIGINT, lambda signum, frame: shutdown_server())

@app.route('/watch-count')
def watch_count():
    watch_dir = config.get("SETTINGS", "WATCH", fallback="/temp")
    ignored_exts = config.get("SETTINGS", "IGNORED_EXTENSIONS", fallback=".crdownload")
    ignored = set(ext.strip().lower() for ext in ignored_exts.split(",") if ext.strip())

    total = 0
    for root, _, files in os.walk(watch_dir):
        for f in files:
            if f.startswith('.') or f.startswith('_'):
                continue
            if any(f.lower().endswith(ext) for ext in ignored):
                continue
            total += 1
    return jsonify({"total_files": total})

#########################
#   Application Start   #
#########################

if __name__ == '__main__':
    app_logger.info("Flask app is starting up...")
    
    if os.environ.get("MONITOR", "").strip().lower() == "yes":
        app_logger.info("MONITOR=yes detected. Starting monitor.py...")
        threading.Thread(target=run_monitor, daemon=True).start()

    user_name = pwd.getpwuid(os.geteuid()).pw_name
    app_logger.info(f"Running as user: {user_name}")
        
    app.run(debug=True, use_reloader=False, threaded=True, host='0.0.0.0', port=5577)