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
from helpers import is_hidden

load_config()

# app = Flask(__name__)

DATA_DIR = "/data"  # Directory to browse
TARGET_DIR = config.get("SETTINGS", "TARGET", fallback="/processed")

#########################
#   Critical Path Check #
#########################

def is_critical_path(path):
    """
    Check if a path is a critical system path (WATCH or TARGET folders).
    Returns True if the path is critical, False otherwise.
    """
    if not path:
        return False
    
    # Get current watch and target folders from config
    watch_folder = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_folder = config.get("SETTINGS", "TARGET", fallback="/processed")
    
    # Check if path is exactly a critical folder
    if path == watch_folder or path == target_folder:
        return True
    
    # Check if path is a parent directory of critical folders
    if (path in watch_folder and watch_folder.startswith(path)) or (path in target_folder and target_folder.startswith(path)):
        return True
    
    return False

def get_critical_path_error_message(path, operation="modify"):
    """
    Generate an error message for critical path operations.
    """
    watch_folder = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_folder = config.get("SETTINGS", "TARGET", fallback="/processed")
    
    if path == watch_folder:
        return f"Cannot {operation} watch folder: {path}. Please use the configuration page to change the watch folder."
    elif path == target_folder:
        return f"Cannot {operation} target folder: {path}. Please use the configuration page to change the target folder."
    else:
        return f"Cannot {operation} parent directory of critical folders: {path}. Please use the configuration page to change watch/target folders."

#########################
#     Cache System      #
#########################

# Global cache for directory listings
directory_cache = {}
cache_timestamps = {}
CACHE_DURATION = 5  # Cache for 5 seconds
MAX_CACHE_SIZE = 100  # Maximum number of cached directories
CACHE_REBUILD_INTERVAL = 6 * 60 * 60  # 6 hours in seconds
last_cache_rebuild = time.time()
last_cache_invalidation = None  # Track when cache was last invalidated

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

# LRU cache for search results (cache last 100 searches)
@lru_cache(maxsize=100)
def cached_search(query):
    """Cached search function for repeated queries"""
    global file_index, index_built
    
    # If index is not built yet, fall back to filesystem search
    if not index_built:
        app_logger.info("Search index not ready, using filesystem search...")
        return filesystem_search(query)
    
    query_lower = query.lower()
    results = []
    
    for item in file_index:
        if query_lower in item["name"].lower():
            results.append(item)
            if len(results) >= 100:  # Limit results
                break
    
    # Sort results: directories first, then files, both alphabetically
    results.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
    
    return results

def filesystem_search(query):
    """Fallback filesystem search when index is not ready"""
    import time
    
    query_lower = query.lower()
    results = []
    excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db"}
    
    try:
        for root, dirs, files in os.walk(DATA_DIR):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]
            
            # Check directories
            for dir_name in dirs:
                if query_lower in dir_name.lower():
                    rel_path = os.path.relpath(root, DATA_DIR)
                    if rel_path == '.':
                        full_path = f"/data/{dir_name}"
                    else:
                        full_path = f"/data/{rel_path}/{dir_name}"
                    
                    results.append({
                        "name": dir_name,
                        "path": full_path,
                        "type": "directory",
                        "parent": f"/data/{rel_path}" if rel_path != '.' else "/data"
                    })
                    
                    if len(results) >= 100:
                        break
            
            if len(results) >= 100:
                break
            
            # Check files
            for file_name in files:
                if file_name.startswith('.') or file_name.startswith('_'):
                    continue
                
                if any(file_name.lower().endswith(ext) for ext in excluded_extensions):
                    continue
                
                if query_lower in file_name.lower():
                    rel_path = os.path.relpath(root, DATA_DIR)
                    if rel_path == '.':
                        full_path = f"/data/{file_name}"
                    else:
                        full_path = f"/data/{rel_path}/{file_name}"
                    
                    try:
                        file_size = os.path.getsize(os.path.join(root, file_name))
                        results.append({
                            "name": file_name,
                            "path": full_path,
                            "type": "file",
                            "size": file_size,
                            "parent": f"/data/{rel_path}" if rel_path != '.' else "/data"
                        })
                        
                        if len(results) >= 100:
                            break
                    except (OSError, IOError):
                        continue
            
            if len(results) >= 100:
                break
        
        # Sort results
        results.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
        return results
        
    except Exception as e:
        app_logger.error(f"Error in filesystem search: {e}")
        return []


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
    global last_cache_invalidation, _data_dir_stats_last_update
    
    if path in directory_cache:
        del directory_cache[path]
        del cache_timestamps[path]
    
    # Also invalidate parent directory cache
    parent = os.path.dirname(path)
    if parent in directory_cache:
        del directory_cache[parent]
        del cache_timestamps[parent]
    
    # Also invalidate directory stats cache when files change
    _data_dir_stats_last_update = 0
    
    # Track when cache invalidation occurred
    last_cache_invalidation = time.time()

def rebuild_entire_cache():
    """Rebuild the entire directory cache and search index."""
    global directory_cache, cache_timestamps, last_cache_rebuild, last_cache_invalidation
    
    app_logger.info("🔄 Starting scheduled cache rebuild...")
    start_time = time.time()
    
    # Clear all caches
    directory_cache.clear()
    cache_timestamps.clear()
    
    # Rebuild search index
    invalidate_file_index()
    build_file_index()
    
    # Update rebuild timestamp and reset invalidation
    last_cache_rebuild = time.time()
    last_cache_invalidation = None  # Reset invalidation tracking after rebuild
    
    rebuild_time = time.time() - start_time
    app_logger.info(f"✅ Cache rebuild completed in {rebuild_time:.2f} seconds")
    
    return rebuild_time

def should_rebuild_cache():
    """Check if it's time to rebuild the cache based on the interval."""
    global last_cache_rebuild
    return time.time() - last_cache_rebuild >= CACHE_REBUILD_INTERVAL

@app.route('/clear-cache', methods=['POST'])
def clear_cache():
    """Manually clear the directory cache."""
    global directory_cache, cache_timestamps, last_cache_invalidation, _data_dir_stats_last_update
    directory_cache.clear()
    cache_timestamps.clear()
    last_cache_invalidation = time.time()
    _data_dir_stats_last_update = 0  # Also invalidate directory stats cache
    app_logger.info("Directory cache cleared manually")
    return jsonify({"success": True, "message": "Cache cleared"})

@app.route('/rebuild-search-index', methods=['POST'])
def rebuild_search_index():
    """Manually rebuild the search index."""
    invalidate_file_index()
    build_file_index()
    return jsonify({"success": True, "message": "Search index rebuilt"})

@app.route('/rebuild-cache', methods=['POST'])
def rebuild_cache():
    """Manually rebuild the entire cache and search index."""
    try:
        rebuild_time = rebuild_entire_cache()
        return jsonify({
            "success": True, 
            "message": f"Cache rebuilt successfully in {rebuild_time:.2f} seconds"
        })
    except Exception as e:
        app_logger.error(f"Error rebuilding cache: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/cache-status', methods=['GET'])
def get_cache_status():
    """Get current cache status and next rebuild time."""
    global last_cache_rebuild
    
    start_time = time.time()
    current_time = time.time()
    time_since_rebuild = current_time - last_cache_rebuild
    time_until_next = CACHE_REBUILD_INTERVAL - time_since_rebuild
    
    # Format times
    def format_time(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
    
    # Get data directory statistics with timeout protection
    try:
        data_dir_stats = get_data_directory_stats()
    except Exception as e:
        app_logger.warning(f"Error getting data directory stats, using cached or default values: {e}")
        # Use cached stats if available, otherwise use defaults
        if _data_dir_stats_cache:
            data_dir_stats = _data_dir_stats_cache
        else:
            data_dir_stats = {
                "subdir_count": 0,
                "total_files": 0,
                "total_dirs": 0,
                "scan_limited": False,
                "max_depth_reached": 0,
                "scan_time": 0
            }
    
    # Check if cache was recently invalidated (within last 30 seconds)
    cache_recently_invalidated = False
    if last_cache_invalidation:
        cache_recently_invalidated = (current_time - last_cache_invalidation) < 30
    
    response_time = time.time() - start_time
    app_logger.debug(f"Full cache status request completed in {response_time:.3f}s")
    
    return jsonify({
        "last_rebuild": last_cache_rebuild,
        "time_since_rebuild": time_since_rebuild,
        "time_until_next": time_until_next,
        "formatted_since": format_time(time_since_rebuild),
        "formatted_until": format_time(max(0, time_until_next)),
        "cache_size": len(directory_cache),
        "total_directories": data_dir_stats.get('total_dirs', 0),
        "index_built": index_built,
        "data_dir_stats": data_dir_stats,
        "cache_invalidated": cache_recently_invalidated,
        "cache_duration": CACHE_DURATION,
        "max_cache_size": MAX_CACHE_SIZE,
        "response_time": round(response_time, 3)
    })

@app.route('/cache-status-light', methods=['GET'])
def get_cache_status_light():
    """Get lightweight cache status without heavy directory statistics."""
    global last_cache_rebuild
    
    current_time = time.time()
    time_since_rebuild = current_time - last_cache_rebuild
    time_until_next = CACHE_REBUILD_INTERVAL - time_since_rebuild
    
    # Format times
    def format_time(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
    
    # Check if cache was recently invalidated (within last 30 seconds)
    cache_recently_invalidated = False
    if last_cache_invalidation:
        cache_recently_invalidated = (current_time - last_cache_invalidation) < 30
    
    app_logger.debug(f"Light cache status request - cache size: {len(directory_cache)}, index built: {index_built}")
    
    return jsonify({
        "last_rebuild": last_cache_rebuild,
        "time_since_rebuild": time_since_rebuild,
        "time_until_next": time_until_next,
        "formatted_since": format_time(time_since_rebuild),
        "formatted_until": format_time(max(0, time_until_next)),
        "cache_size": len(directory_cache),
        "index_built": index_built,
        "cache_invalidated": cache_recently_invalidated,
        "cache_duration": CACHE_DURATION,
        "max_cache_size": MAX_CACHE_SIZE
    })

@app.route('/cache-debug', methods=['GET'])
def get_cache_debug():
    """Debug endpoint to show current cache state and performance metrics."""
    global directory_cache, cache_timestamps, last_cache_rebuild, last_cache_invalidation
    
    current_time = time.time()
    
    # Get some sample cache entries
    sample_cache = {}
    for i, (path, timestamp) in enumerate(list(cache_timestamps.items())[:5]):
        age = current_time - timestamp
        sample_cache[path] = {
            "age_seconds": round(age, 2),
            "cached_data": bool(path in directory_cache)
        }
    
    return jsonify({
        "current_time": current_time,
        "cache_size": len(directory_cache),
        "cache_timestamps_count": len(cache_timestamps),
        "last_rebuild": last_cache_rebuild,
        "last_invalidation": last_cache_invalidation,
        "sample_cache_entries": sample_cache,
        "memory_usage_mb": round(len(str(directory_cache)) / (1024 * 1024), 2)
    })

# Cache for directory statistics to avoid repeated filesystem walks
_data_dir_stats_cache = {}
_data_dir_stats_last_update = 0
DATA_DIR_STATS_CACHE_DURATION = 300  # Cache for 5 minutes

def get_data_directory_stats():
    """Get statistics about the DATA_DIR including subdirectory count and file count."""
    global _data_dir_stats_cache, _data_dir_stats_last_update
    
    current_time = time.time()
    
    # Return cached stats if they're still valid
    if (current_time - _data_dir_stats_last_update) < DATA_DIR_STATS_CACHE_DURATION:
        return _data_dir_stats_cache
    
    try:
        app_logger.debug("Calculating fresh data directory statistics...")
        subdir_count = 0
        total_files = 0
        
        # Use a much more efficient approach with early termination
        max_items = 5000   # Reduced limit for faster response
        max_depth = 3      # Reduced depth for faster response
        start_time = time.time()
        
        for root, dirs, files in os.walk(DATA_DIR):
            # Count subdirectories (excluding the root DATA_DIR)
            if root != DATA_DIR:
                subdir_count += 1
            
            # Count files
            total_files += len(files)
            
            # Early termination if we've counted enough items
            if (subdir_count + total_files) > max_items:
                app_logger.debug(f"Reached item limit ({max_items}), stopping scan early")
                break
            
            # Limit traversal depth to prevent excessive scanning
            current_depth = root.count(os.sep) - DATA_DIR.count(os.sep)
            if current_depth > max_depth:
                dirs.clear()  # Don't traverse deeper
                continue
            
            # Skip hidden directories to speed up traversal
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]
            
            # Timeout protection - don't spend more than 2 seconds on this
            if time.time() - start_time > 2.0:
                app_logger.debug("Directory scan timeout reached, stopping early")
                break
        
        scan_time = time.time() - start_time
        
        # Cache the results
        _data_dir_stats_cache = {
            "subdir_count": subdir_count,
            "total_files": total_files,
            "total_dirs": subdir_count + 1,  # +1 for the root DATA_DIR
            "scan_limited": (subdir_count + total_files) >= max_items,  # Flag if scan was limited
            "max_depth_reached": max_depth,  # Show what depth limit was used
            "scan_time": round(scan_time, 2)  # Show how long the scan took
        }
        _data_dir_stats_last_update = current_time
        
        app_logger.debug(f"Data directory stats updated: {subdir_count} subdirs, {total_files} files (scan limited: {_data_dir_stats_cache['scan_limited']}, time: {scan_time:.2f}s)")
        return _data_dir_stats_cache
        
    except Exception as e:
        app_logger.error(f"Error getting data directory stats: {e}")
        # Return cached stats if available, otherwise return defaults
        if _data_dir_stats_cache:
            return _data_dir_stats_cache
        return {
            "subdir_count": 0,
            "total_files": 0,
            "total_dirs": 0,
            "scan_limited": False,
            "max_depth_reached": 0,
            "scan_time": 0
        }

#########################
#     Global Values     #
#########################

# Global file index for fast searching
file_index = []
index_built = False

def build_file_index():
    """Build an in-memory index of all files and directories for fast searching"""
    global file_index, index_built
    
    if index_built:
        return
    
    app_logger.info("Building file index for fast search...")
    start_time = time.time()
    
    file_index.clear()
    excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db"}
    
    try:
        for root, dirs, files in os.walk(DATA_DIR):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]
            
            # Index directories
            for name in dirs:
                try:
                    rel_path = os.path.relpath(os.path.join(root, name), DATA_DIR)
                    file_index.append({
                        "name": name,
                        "path": f"/data/{rel_path}",
                        "type": "directory",
                        "parent": f"/data/{os.path.dirname(rel_path)}" if os.path.dirname(rel_path) else "/data"
                    })
                except (OSError, IOError):
                    continue
            
            # Index files (excluding certain extensions)
            for name in files:
                if name.startswith('.') or name.startswith('_'):
                    continue
                
                # Skip excluded file types
                if any(name.lower().endswith(ext) for ext in excluded_extensions):
                    continue
                
                try:
                    full_path = os.path.join(root, name)
                    rel_path = os.path.relpath(full_path, DATA_DIR)
                    file_size = os.path.getsize(full_path)
                    
                    file_index.append({
                        "name": name,
                        "path": f"/data/{rel_path}",
                        "type": "file",
                        "size": file_size,
                        "parent": f"/data/{os.path.dirname(rel_path)}" if os.path.dirname(rel_path) else "/data"
                    })
                except (OSError, IOError):
                    continue
    
    except Exception as e:
        app_logger.error(f"Error building file index: {e}")
        return
    
    build_time = time.time() - start_time
    app_logger.info(f"File index built successfully: {len(file_index)} items in {build_time:.2f} seconds")
    index_built = True

def invalidate_file_index():
    """Invalidate the file index to force rebuild"""
    global index_built
    index_built = False
    app_logger.info("File index invalidated")

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
    If the "X-Stream" header is true, streams progress updates as SSE.
    """
    data = request.get_json()
    source = data.get('source')
    destination = data.get('destination')
    stream = request.headers.get('X-Stream', 'false').lower() == 'true'
    
    app_logger.info("********************// Move File //********************")
    app_logger.info(f"Requested move from: {source} to: {destination}")
    app_logger.info(f"Streaming mode: {stream}")
    
    if not source or not destination:
        app_logger.error("Missing source or destination in request")
        return jsonify({"success": False, "error": "Missing source or destination"}), 400

    if not os.path.exists(source):
        app_logger.warning(f"Source path does not exist: {source}")
        return jsonify({"success": False, "error": "Source path does not exist"}), 404

    # Check if trying to move critical folders
    if is_critical_path(source):
        app_logger.error(f"Attempted to move critical folder: {source}")
        return jsonify({"success": False, "error": get_critical_path_error_message(source, "move")}), 403
    
    # Check if destination would overwrite critical folders
    if is_critical_path(destination):
        app_logger.error(f"Attempted to move to critical folder location: {destination}")
        return jsonify({"success": False, "error": get_critical_path_error_message(destination, "move to")}), 403

    if stream:
        app_logger.info(f"Starting streaming move operation")
        # Streaming move for both files and directories
        if os.path.isfile(source):
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
        else:
            # Streaming move for directories
            def generate():
                with memory_context("file_move"):
                    try:
                        app_logger.info(f"Streaming directory move with progress: {source}")
                        
                        # Calculate total size and file count for progress tracking
                        total_size = 0
                        file_count = 0
                        file_list = []
                        try:
                            for root, _, files in os.walk(source):
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    if os.path.exists(file_path):
                                        file_size = os.path.getsize(file_path)
                                        total_size += file_size
                                        file_count += 1
                                        file_list.append((file_path, file_size))
                        except Exception as e:
                            app_logger.warning(f"Could not calculate directory size: {e}")
                        
                        app_logger.info(f"Directory contains {file_count} files, total size: {total_size}")
                        
                        if total_size == 0:
                            # Empty directory or couldn't calculate size
                            shutil.move(source, destination)
                            yield "data: 100\n\n"
                        else:
                            # Create destination directory if it doesn't exist
                            os.makedirs(os.path.dirname(destination), exist_ok=True)
                            
                            # Copy files individually with progress tracking
                            bytes_moved = 0
                            chunk_size = 1024 * 1024  # 1 MB chunks
                            last_progress_update = time.time()
                            start_time = time.time()
                            
                            for i, (file_path, file_size) in enumerate(file_list):
                                # Check for timeout every 100 files
                                if i % 100 == 0 and i > 0:
                                    elapsed = time.time() - start_time
                                    if elapsed > 3600:  # 1 hour timeout
                                        raise Exception(f"Directory move operation timed out after {elapsed:.0f} seconds")
                                
                                # Calculate relative path from source
                                rel_path = os.path.relpath(file_path, source)
                                dest_file_path = os.path.join(destination, rel_path)
                                
                                # Create destination directory structure
                                os.makedirs(os.path.dirname(dest_file_path), exist_ok=True)
                                
                                # Copy file with progress updates
                                try:
                                    with open(file_path, 'rb') as fsrc, open(dest_file_path, 'wb') as fdst:
                                        while True:
                                            chunk = fsrc.read(chunk_size)
                                            if not chunk:
                                                break
                                            fdst.write(chunk)
                                            bytes_moved += len(chunk)
                                            
                                            # Calculate overall progress
                                            progress = int((bytes_moved / total_size) * 100)
                                            current_time = time.time()
                                            
                                            # Send progress update every 2 seconds or when progress changes significantly
                                            if (current_time - last_progress_update > 2.0 or 
                                                progress % 5 == 0):
                                                yield f"data: {progress}\n\n"
                                                last_progress_update = current_time
                                except Exception as e:
                                    app_logger.error(f"Error copying file {file_path}: {e}")
                                    # Try to continue with other files
                                    continue
                                
                                # Send keepalive every 10 files to prevent connection timeout
                                if i % 10 == 0:
                                    yield f"data: keepalive: {i+1}/{file_count} files processed\n\n"
                                
                                # Update status every few files
                                if i % 10 == 0 or i == len(file_list) - 1:
                                    app_logger.info(f"Copied {i+1}/{file_count} files ({bytes_moved}/{total_size} bytes)")
                            
                            # Remove source directory after successful copy
                            try:
                                shutil.rmtree(source)
                            except Exception as e:
                                app_logger.warning(f"Could not remove source directory {source}: {e}")
                                # Continue anyway since files were copied successfully
                            
                            yield "data: 100\n\n"
                        
                        app_logger.info(f"Directory move complete: {source} -> {destination}")
                        
                        # Invalidate cache for affected directories
                        invalidate_cache_for_path(os.path.dirname(source))
                        invalidate_cache_for_path(os.path.dirname(destination))
                        # Invalidate search index since files have moved
                        invalidate_file_index()
                        
                    except Exception as e:
                        app_logger.exception(f"Error during streaming directory move from {source} to {destination}")
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
                # Invalidate search index since files have moved
                invalidate_file_index()
                
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
#       Search Files in /data       #
#####################################
@app.route('/search-files', methods=['GET'])
def search_files():
    """Search for files and directories in /data directory using cached index"""
    query = request.args.get('query', '').strip()
    
    if not query:
        return jsonify({"error": "No search query provided"}), 400
    
    if len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters"}), 400
    
    try:
        # Use cached search function
        results = cached_search(query)
        
        return jsonify({
            "success": True,
            "results": results,
            "total_found": len(results),
            "query": query,
            "cached": True,
            "index_ready": index_built
        })
        
    except Exception as e:
        app_logger.error(f"Error searching files: {e}")
        return jsonify({"error": str(e)}), 500

#####################################
#       Count Files in Directory    #
#####################################
@app.route('/count-files', methods=['GET'])
def count_files():
    """Count the total number of files in a directory (recursive)"""
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return jsonify({"error": "Invalid path"}), 400

    try:
        file_count = 0
        for root, _, files in os.walk(path):
            file_count += len(files)
        
        return jsonify({
            "file_count": file_count,
            "path": path
        })
    except Exception as e:
        app_logger.error(f"Error counting files in {path}: {e}")
        return jsonify({"error": str(e)}), 500

#####################################
#       CBZ Preview & Metadata      #
#####################################
@app.route('/cbz-preview', methods=['GET'])
def cbz_preview():
    """Extract and return the first image from a CBZ file as base64"""
    file_path = request.args.get('path')
    size = request.args.get('size', 'large')  # 'small' or 'large'
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "Invalid file path"}), 400
    
    if not file_path.lower().endswith('.cbz'):
        return jsonify({"error": "File is not a CBZ"}), 400
    
    try:
        import zipfile
        import base64
        from io import BytesIO
        from PIL import Image
        
        # Open the CBZ file
        with zipfile.ZipFile(file_path, 'r') as zf:
            # Get list of files in the archive
            file_list = zf.namelist()
            
            # Filter for image files and sort
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
            image_files = []
            
            for file_name in file_list:
                ext = os.path.splitext(file_name.lower())[1]
                if ext in image_extensions:
                    image_files.append(file_name)
            
            if not image_files:
                return jsonify({"error": "No image files found in CBZ"}), 404
            
            # Sort files to get the first one
            image_files.sort()
            first_image = image_files[0]
            
            # Read the first image
            with zf.open(first_image) as image_file:
                # Open with PIL to resize if needed
                img = Image.open(image_file)
                
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                
                # Store original size before resizing
                original_width, original_height = img.width, img.height
                
                # Resize based on size parameter
                if size == 'small':
                    max_size = 300
                else:  # large
                    max_size = 1200  # Much larger for modal display
                
                if img.width > max_size or img.height > max_size:
                    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                
                # Convert to base64
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=90)  # Higher quality for large images
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                
                return jsonify({
                    "success": True,
                    "preview": f"data:image/jpeg;base64,{img_base64}",
                    "original_size": {"width": original_width, "height": original_height},
                    "display_size": {"width": img.width, "height": img.height},
                    "file_name": first_image,
                    "total_images": len(image_files)
                })
                
    except Exception as e:
        app_logger.error(f"Error previewing CBZ {file_path}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/cbz-metadata', methods=['GET'])
def cbz_metadata():
    """Extract metadata from a CBZ file"""
    file_path = request.args.get('path')
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "Invalid file path"}), 400
    
    if not file_path.lower().endswith('.cbz'):
        return jsonify({"error": "File is not a CBZ"}), 400
    
    try:
        import zipfile
        from comicinfo import read_comicinfo_xml
        
        metadata = {
            "file_size": os.path.getsize(file_path),
            "total_files": 0,
            "image_files": 0,
            "comicinfo": None,
            "file_list": []
        }
        
        # Open the CBZ file
        with zipfile.ZipFile(file_path, 'r') as zf:
            file_list = zf.namelist()
            metadata["total_files"] = len(file_list)
            
            # Count image files
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
            image_files = []
            
            for file_name in file_list:
                ext = os.path.splitext(file_name.lower())[1]
                if ext in image_extensions:
                    image_files.append(file_name)
            
            metadata["image_files"] = len(image_files)
            
            # Look for ComicInfo.xml
            comicinfo_files = [f for f in file_list if f.lower().endswith('comicinfo.xml')]
            
            if comicinfo_files:
                try:
                    with zf.open(comicinfo_files[0]) as xml_file:
                        xml_data = xml_file.read()
                        app_logger.info(f"Found ComicInfo.xml in {file_path}, size: {len(xml_data)} bytes")
                        comicinfo = read_comicinfo_xml(xml_data)
                        if comicinfo:
                            app_logger.info(f"Successfully parsed ComicInfo.xml with {len(comicinfo)} fields")
                            metadata["comicinfo"] = comicinfo
                        else:
                            app_logger.warning(f"ComicInfo.xml parsed but returned empty data")
                except Exception as e:
                    app_logger.warning(f"Error reading ComicInfo.xml: {e}")
            else:
                app_logger.info(f"No ComicInfo.xml found in {file_path}")
            
            # Get first few files for preview
            metadata["file_list"] = sorted(file_list)[:10]  # First 10 files
        
        return jsonify(metadata)
        
    except Exception as e:
        app_logger.error(f"Error reading CBZ metadata {file_path}: {e}")
        return jsonify({"error": str(e)}), 500

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

    # Check if trying to rename critical folders
    if is_critical_path(old_path):
        app_logger.error(f"Attempted to rename critical folder: {old_path}")
        return jsonify({"error": get_critical_path_error_message(old_path, "rename")}), 403
    
    # Check if new path would be a critical folder
    if is_critical_path(new_path):
        app_logger.error(f"Attempted to rename to critical folder location: {new_path}")
        return jsonify({"error": get_critical_path_error_message(new_path, "rename to")}), 403

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


@app.route('/rename-directory', methods=['POST'])
def rename_directory():
    """Rename all files in a directory using rename.py patterns"""
    try:
        data = request.get_json()
        directory_path = data.get('directory')
        
        app_logger.info("********************// Rename Directory Files //********************")
        app_logger.info(f"Directory: {directory_path}")
        
        # Validate input
        if not directory_path:
            return jsonify({"error": "Missing directory path"}), 400
        
        # Check if the directory exists
        if not os.path.exists(directory_path):
            return jsonify({"error": "Directory does not exist"}), 404
        
        if not os.path.isdir(directory_path):
            return jsonify({"error": "Path is not a directory"}), 400
        
        # Check if trying to rename files in critical folders
        if is_critical_path(directory_path):
            app_logger.error(f"Attempted to rename files in critical folder: {directory_path}")
            return jsonify({"error": get_critical_path_error_message(directory_path, "rename files in")}), 403
        
        # Import and call the rename_files function from rename.py
        from rename import rename_files
        
        # Call the rename function
        rename_files(directory_path)
        
        # Invalidate cache for the directory
        invalidate_cache_for_path(directory_path)
        
        app_logger.info(f"Successfully renamed files in directory: {directory_path}")
        return jsonify({"success": True, "message": f"Successfully renamed files in {os.path.basename(directory_path)}"})
        
    except ImportError as e:
        app_logger.error(f"Failed to import rename module: {e}")
        return jsonify({"error": "Rename module not available"}), 500
    except Exception as e:
        app_logger.error(f"Error renaming files in directory {directory_path}: {e}")
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

    # Check if trying to delete critical folders
    if is_critical_path(target):
        app_logger.error(f"Attempted to delete critical folder: {target}")
        return jsonify({"error": get_critical_path_error_message(target, "delete")}), 403

    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        
        # Invalidate cache for the directory containing the deleted item
        invalidate_cache_for_path(os.path.dirname(target))
        # Invalidate search index since files have been deleted
        invalidate_file_index()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

#####################################
#        Custom Rename Route        #
#####################################
@app.route('/custom-rename', methods=['POST'])
def custom_rename():
    """
    Custom rename route that handles bulk renaming operations
    specifically for removing text from filenames.
    """
    data = request.get_json()
    old_path = data.get('old')
    new_path = data.get('new')
    
    app_logger.info(f"Custom rename request: {old_path} -> {new_path}")

    # Validate input
    if not old_path or not new_path:
        return jsonify({"error": "Missing old or new path"}), 400
    
    # Check if the old path exists
    if not os.path.exists(old_path):
        return jsonify({"error": "Source file does not exist"}), 404

    # Check if trying to rename critical folders
    if is_critical_path(old_path):
        app_logger.error(f"Attempted to rename critical folder: {old_path}")
        return jsonify({"error": get_critical_path_error_message(old_path, "rename")}), 403
    
    # Check if new path would be a critical folder
    if is_critical_path(new_path):
        app_logger.error(f"Attempted to rename to critical folder location: {new_path}")
        return jsonify({"error": get_critical_path_error_message(new_path, "rename to")}), 403

    # Check if the new path already exists to avoid overwriting
    if os.path.exists(new_path):
        return jsonify({"error": "Destination already exists"}), 400

    try:
        os.rename(old_path, new_path)
        
        # Invalidate cache for affected directories
        invalidate_cache_for_path(os.path.dirname(old_path))
        invalidate_cache_for_path(os.path.dirname(new_path))
        # Invalidate search index since files have been renamed
        invalidate_file_index()
        
        app_logger.info(f"Custom rename successful: {old_path} -> {new_path}")
        return jsonify({"success": True})
    except Exception as e:
        app_logger.error(f"Error in custom rename: {e}")
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
        new_watch = request.form.get("watch", "/temp")
        new_target = request.form.get("target", "/processed")
        
        # Validate that watch and target are not the same
        if new_watch == new_target:
            return jsonify({"error": "Watch and target folders cannot be the same"}), 400
        
        # Validate that watch and target are not subdirectories of each other
        if new_watch.startswith(new_target + "/") or new_target.startswith(new_watch + "/"):
            return jsonify({"error": "Watch and target folders cannot be subdirectories of each other"}), 400
        
        config["SETTINGS"]["WATCH"] = new_watch
        config["SETTINGS"]["TARGET"] = new_target
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
        config["SETTINGS"]["AUTO_CLEANUP_ORPHAN_FILES"] = str(request.form.get("autoCleanupOrphanFiles") == "on")
        config["SETTINGS"]["CLEANUP_INTERVAL_HOURS"] = request.form.get("cleanupIntervalHours", "1")
        config["SETTINGS"]["HEADERS"] = request.form.get("customHeaders", "")
        config["SETTINGS"]["SKIPPED_FILES"] = request.form.get("skippedFiles", "")
        config["SETTINGS"]["DELETED_FILES"] = request.form.get("deletedFiles", "")
        config["SETTINGS"]["OPERATION_TIMEOUT"] = request.form.get("operationTimeout", "3600")
        config["SETTINGS"]["LARGE_FILE_THRESHOLD"] = request.form.get("largeFileThreshold", "500")
        config["SETTINGS"]["PIXELDRAIN_API_KEY"] = request.form.get("pixeldrainApiKey", "")

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
        autoCleanupOrphanFiles=settings.get("AUTO_CLEANUP_ORPHAN_FILES", "False") == "True",
        cleanupIntervalHours=settings.get("CLEANUP_INTERVAL_HOURS", "1"),
        skippedFiles=settings.get("SKIPPED_FILES", ""),
        deletedFiles=settings.get("DELETED_FILES", ""),
        customHeaders=settings.get("HEADERS", ""),
        operationTimeout=settings.get("OPERATION_TIMEOUT", "3600"),
        largeFileThreshold=settings.get("LARGE_FILE_THRESHOLD", "500"),
        pixeldrainApiKey=settings.get("PIXELDRAIN_API_KEY", ""),
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
    
    # Check if trying to create folder inside critical paths
    if is_critical_path(path):
        app_logger.error(f"Attempted to create folder in critical path: {path}")
        return jsonify({"success": False, "error": get_critical_path_error_message(path, "create folder in")}), 403
    
    try:
        os.makedirs(path)
        
        # Invalidate cache for the parent directory
        invalidate_cache_for_path(os.path.dirname(path))
        
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

#########################
#    Cleanup Orphan Files    #
#########################
@app.route('/cleanup-orphan-files', methods=['POST'])
def cleanup_orphan_files():
    """
    Clean up orphan temporary download files in the WATCH directory.
    This endpoint allows manual cleanup of files that shouldn't be there.
    """
    try:
        watch_directory = config.get("SETTINGS", "WATCH", fallback="/temp")
        
        if not os.path.exists(watch_directory):
            return jsonify({"success": False, "error": "Watch directory does not exist"}), 400
        
        cleaned_count = 0
        total_size_cleaned = 0
        cleaned_files = []
        
        # Define temporary download file patterns
        temp_patterns = [
            '.crdownload', '.tmp', '.part', '.mega', '.bak',
            '.download', '.downloading', '.incomplete'
        ]
        
        def is_temporary_download_file(filename):
            """Check if a filename indicates a temporary download file"""
            filename_lower = filename.lower()
            
            # Check for common temporary download patterns
            for pattern in temp_patterns:
                if pattern in filename_lower:
                    return True
            
            # Check for numbered temporary files (e.g., .0, .1, .2)
            import re
            if re.search(r'\.\d+\.(crdownload|tmp|part|download)$', filename_lower):
                return True
            
            # Check for files that look like incomplete downloads
            if re.search(r'\.(crdownload|tmp|part|download)$', filename_lower):
                return True
                
            return False
        
        def format_size(size_bytes):
            """Helper function to format file sizes in human-readable format"""
            if size_bytes == 0:
                return "0B"
            
            import math
            size_names = ["B", "KB", "MB", "GB", "TB"]
            i = int(math.floor(math.log(size_bytes, 1024)))
            p = math.pow(1024, i)
            s = round(size_bytes / p, 2)
            return f"{s} {size_names[i]}"
        
        # Walk through watch directory and clean up orphan files
        for root, dirs, files in os.walk(watch_directory):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not is_hidden(os.path.join(root, d))]
            
            for file in files:
                file_path = os.path.join(root, file)
                
                # Skip hidden files
                if is_hidden(file_path):
                    continue
                
                # Check if this is a temporary download file
                if is_temporary_download_file(file):
                    try:
                        file_size = os.path.getsize(file_path)
                        os.remove(file_path)
                        cleaned_count += 1
                        total_size_cleaned += file_size
                        
                        # Add to cleaned files list for reporting
                        rel_path = os.path.relpath(file_path, watch_directory)
                        cleaned_files.append({
                            "file": rel_path,
                            "size": format_size(file_size)
                        })
                        
                        app_logger.info(f"Cleaned up orphan file: {file_path} ({format_size(file_size)})")
                    except Exception as e:
                        app_logger.error(f"Error cleaning up orphan file {file_path}: {e}")
        
        if cleaned_count > 0:
            app_logger.info(f"Manual cleanup completed: {cleaned_count} files removed, {format_size(total_size_cleaned)} freed")
            return jsonify({
                "success": True,
                "message": f"Cleanup completed: {cleaned_count} files removed, {format_size(total_size_cleaned)} freed",
                "cleaned_count": cleaned_count,
                "total_size_cleaned": format_size(total_size_cleaned),
                "cleaned_files": cleaned_files
            })
        else:
            app_logger.info("No orphan files found during manual cleanup")
            return jsonify({
                "success": True,
                "message": "No orphan files found",
                "cleaned_count": 0,
                "total_size_cleaned": "0B",
                "cleaned_files": []
            })
            
    except Exception as e:
        app_logger.error(f"Error during manual orphan file cleanup: {e}")
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
    
    # Build search index in background thread
    def build_index_background():
        try:
            build_file_index()
            app_logger.info("✅ Search index built successfully and ready for use")
        except Exception as e:
            app_logger.error(f"❌ Error building search index: {e}")
    
    # Cache maintenance background thread
    def cache_maintenance_background():
        """Background thread that checks and rebuilds cache every hour."""
        while True:
            try:
                time.sleep(60 * 60)  # Check every hour
                if should_rebuild_cache():
                    rebuild_entire_cache()
            except Exception as e:
                app_logger.error(f"Error in cache maintenance thread: {e}")
    
    # Start index building in background
    threading.Thread(target=build_index_background, daemon=True).start()
    app_logger.info("🔄 Building search index in background...")
    
    # Start cache maintenance in background
    threading.Thread(target=cache_maintenance_background, daemon=True).start()
    app_logger.info("🔄 Cache maintenance thread started (checks every hour, rebuilds every 6 hours)...")
    
    if os.environ.get("MONITOR", "").strip().lower() == "yes":
        app_logger.info("MONITOR=yes detected. Starting monitor.py...")
        threading.Thread(target=run_monitor, daemon=True).start()

    user_name = pwd.getpwuid(os.geteuid()).pw_name
    app_logger.info(f"Running as user: {user_name}")
        
    app.run(debug=True, use_reloader=False, threaded=True, host='0.0.0.0', port=5577)