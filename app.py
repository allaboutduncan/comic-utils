from flask import Flask, render_template, request, Response, send_from_directory, redirect, jsonify, url_for
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
from api import app 

from config import config, load_flask_config, write_config, load_config

load_config()

# app = Flask(__name__)

DATA_DIR = "/data"  # Directory to browse

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

# Create file handler
app_handler = logging.FileHandler(APP_LOG)
app_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Add the handler
app_logger.addHandler(app_handler)

# Example usage
app_logger.info("App started successfully!")


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
        entries = os.listdir(current_path)
        # Only include directories that do not start with '.' or '_'
        directories = [
            d for d in entries
            if os.path.isdir(os.path.join(current_path, d)) and not d.startswith(('.', '_'))
        ]
        # Sort directories in alpha-numeric order (case-insensitive)
        directories.sort(key=lambda s: s.lower())

        # Exclude file types from browsing and skip files that start with '.' or '_'
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo"}
        files = [
            f for f in entries
            if os.path.isfile(os.path.join(current_path, f)) and
               not f.startswith(('.', '_')) and
               not any(f.lower().endswith(ext) for ext in excluded_extensions)
        ]
        # Sort files in alpha-numeric order (case-insensitive)
        files.sort(key=lambda s: s.lower())

        parent_dir = os.path.dirname(current_path) if current_path != DATA_DIR else None

        return jsonify({
            "current_path": current_path,
            "directories": directories,
            "files": files,
            "parent": parent_dir
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    
#####################################
#  Move Files/Folders (Drag & Drop) #
#####################################
@app.route('/move', methods=['POST'])
def move():
    """
    Move a file or folder from the source path to the destination.
    Expects a JSON payload with "source" and "destination" keys.
    """
    data = request.get_json()
    source = data.get('source')
    destination = data.get('destination')
    
    if not source or not destination:
        return jsonify({"error": "Missing source or destination"}), 400
    if not os.path.exists(source):
        return jsonify({"error": "Source does not exist"}), 404

    try:
        shutil.move(source, destination)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
#####################################
#     Move Files/Folders UI Page    #
#####################################
@app.route('/files')
def files_page():
    watch = config.get("SETTINGS", "WATCH", fallback="/temp")
    return render_template('files.html', watch=watch)

#####################################
#       Rename Files/Folders        #
#####################################
@app.route('/rename', methods=['POST'])
def rename():
    data = request.get_json()
    old_path = data.get('old')
    new_path = data.get('new')
    
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
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
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
                ['python', script_file, file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
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
            process = subprocess.Popen(
                ['python', script_file, directory],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            for line in process.stdout:
                yield f"data: {line}\n\n"  # Format required by SSE
            for line in process.stderr:
                yield f"data: ERROR: {line}\n\n"
            process.wait()

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

        return Response(generate_logs(), content_type='text/event-stream')

    return Response("Invalid script type.", status=400)

#########################
#       Home Page       #
#########################
@app.route('/')
def index():
    # These environment variables are set/updated by load_config_into_env()
    watch = config.get("SETTINGS", "WATCH", fallback="/temp")
    return render_template('index.html', watch=watch, config=app.config)
    
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

#########################
#   Application Start   #
#########################

if __name__ == '__main__':
    app_logger.info("Flask app is starting up...")
    
    if os.environ.get("MONITOR", "").strip().lower() == "yes":
        app_logger.info("MONITOR=yes detected. Starting monitor.py...")
        threading.Thread(target=run_monitor, daemon=True).start()
    
    app.run(debug=True, threaded=True, host='0.0.0.0', port=5577)