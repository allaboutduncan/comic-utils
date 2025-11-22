from flask import Flask, render_template, request, Response, send_from_directory, send_file, redirect, jsonify, url_for, stream_with_context, render_template_string
from werkzeug.utils import secure_filename
import subprocess
import io
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
from PIL import Image, ImageFilter, ImageDraw
try:
    import pwd
except ImportError:
    pwd = None
from functools import lru_cache
from collections import defaultdict
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import zipfile
import tempfile
from api import app
from config import config, load_flask_config, write_config, load_config
from edit import get_edit_modal, save_cbz, cropCenter, cropLeft, cropRight, cropFreeForm, get_image_data_url, modal_body_template
from memory_utils import initialize_memory_management, cleanup_on_exit, memory_context, get_global_monitor
from app_logging import app_logger, APP_LOG, MONITOR_LOG
from helpers import is_hidden
import threading
from collections import OrderedDict
from version import __version__
import requests
from packaging import version as pkg_version
from database import init_db, get_db_connection, get_recent_files, log_recent_file
from concurrent.futures import ThreadPoolExecutor
from file_watcher import FileWatcher

load_config()

# Initialize Database
init_db()

# Thread pool for thumbnail generation
thumbnail_executor = ThreadPoolExecutor(max_workers=2)

def scan_library_task():
    """Background task to scan library for new/changed files and generate thumbnails."""
    app_logger.info("Starting background library scan for thumbnails...")
    
    conn = get_db_connection()
    if not conn:
        app_logger.error("Could not connect to DB for library scan")
        return

    try:
        # Get all existing jobs to minimize DB queries in loop
        # Map path -> (status, file_mtime)
        cursor = conn.execute("SELECT path, status, file_mtime FROM thumbnail_jobs")
        existing_jobs = {row['path']: (row['status'], row['file_mtime']) for row in cursor.fetchall()}
        
        count_queued = 0
        count_skipped = 0
        
        for root, dirs, files in os.walk(DATA_DIR):
            for file in files:
                if file.lower().endswith(('.cbz', '.cbr', '.zip', '.rar', '.pdf')):
                    full_path = os.path.join(root, file)
                    try:
                        stat = os.stat(full_path)
                        current_mtime = stat.st_mtime
                        
                        should_process = False
                        
                        if full_path not in existing_jobs:
                            should_process = True # New file
                        else:
                            status, stored_mtime = existing_jobs[full_path]
                            # Check if file modified since last scan
                            # stored_mtime might be None if migrated
                            if stored_mtime is None or current_mtime > stored_mtime:
                                should_process = True
                            elif status == 'error':
                                # Optional: Retry errors? Let's skip for now to avoid loops, 
                                # or maybe retry once per startup? 
                                # For now, assume errors are permanent until file changes.
                                pass
                                
                        if should_process:
                            # Update DB to mark as pending/processing and update mtime
                            conn.execute("""
                                INSERT INTO thumbnail_jobs (path, status, file_mtime, updated_at) 
                                VALUES (?, 'processing', ?, CURRENT_TIMESTAMP)
                                ON CONFLICT(path) DO UPDATE SET 
                                    status='processing', 
                                    file_mtime=excluded.file_mtime,
                                    updated_at=CURRENT_TIMESTAMP
                            """, (full_path, current_mtime))
                            
                            # Queue the job
                            # We need to calculate cache_path here or let the task do it?
                            # The task takes (file_path, cache_path).
                            # We need to replicate the cache path logic.
                            import hashlib
                            path_hash = hashlib.md5(full_path.encode('utf-8')).hexdigest()
                            shard_dir = path_hash[:2]
                            filename = f"{path_hash}.jpg"
                            thumbnails_dir = os.path.join(config.get("SETTINGS", "CACHE_DIR", fallback="/cache"), "thumbnails")
                            cache_path = os.path.join(thumbnails_dir, shard_dir, filename)
                            
                            thumbnail_executor.submit(generate_thumbnail_task, full_path, cache_path)
                            count_queued += 1
                        else:
                            count_skipped += 1
                            
                    except OSError as e:
                        app_logger.error(f"Error accessing file {full_path}: {e}")
                        
            # Commit batches or at end? At end is fine for single thread scan
            conn.commit()
            
        app_logger.info(f"Library scan complete. Queued {count_queued} thumbnails, skipped {count_skipped}.")
        
    except Exception as e:
        app_logger.error(f"Error during library scan: {e}")
    finally:
        conn.close()

# Start background scanner
def start_background_scanner():
    # Delay slightly to let app startup finish
    def run():
        time.sleep(5) 
        scan_library_task()
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

start_background_scanner()

# app = Flask(__name__)

DATA_DIR = "/data"  # Directory to browse
TARGET_DIR = config.get("SETTINGS", "TARGET", fallback="/processed")

#########################
#   Recent Files Helper #
#########################

def log_file_if_in_data(file_path):
    """
    Log a file to recent_files if it's in /data and is a comic file.

    Args:
        file_path: Full path to the file
    """
    try:
        # Normalize paths for comparison (handles different path separators)
        normalized_file_path = os.path.normpath(file_path)
        normalized_data_dir = os.path.normpath(DATA_DIR)

        # Check if file is in DATA_DIR directory
        # Use os.path.commonpath to handle Windows/Unix path differences
        try:
            common_path = os.path.commonpath([normalized_file_path, normalized_data_dir])
            is_in_data_dir = os.path.samefile(common_path, normalized_data_dir)
        except (ValueError, OSError):
            # Fallback: Check if normalized path starts with DATA_DIR
            is_in_data_dir = normalized_file_path.startswith(normalized_data_dir)

        if not is_in_data_dir:
            app_logger.debug(f"File not in DATA_DIR ({DATA_DIR}): {file_path}")
            return

        # Check if it's a file (not directory)
        if not os.path.isfile(file_path):
            return

        # Check if it's a comic file
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in ['.cbz', '.cbr']:
            return

        # Log the file
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None
        success = log_recent_file(file_path, file_name, file_size)
        if success:
            app_logger.info(f"📚 Logged recent file to database: {file_name}")
        else:
            app_logger.warning(f"Failed to log recent file: {file_name}")

    except Exception as e:
        app_logger.error(f"Error logging recent file {file_path}: {e}")

def update_recent_files_from_scan(comic_files):
    """
    Update the recent_files database with the 100 most recently modified comic files.
    This is called during index build to capture files added outside the app.

    Args:
        comic_files: List of dicts with keys: path, name, size, mtime
    """
    try:
        if not comic_files:
            app_logger.debug("No comic files found during scan")
            return

        # Get database connection
        conn = get_db_connection()
        if not conn:
            app_logger.error("Could not get database connection for recent files update")
            return

        c = conn.cursor()

        # Check how many files we currently have
        c.execute('SELECT COUNT(*) FROM recent_files')
        current_count = c.fetchone()[0]

        # Only do a full rescan if we have fewer than 100 files
        # This preserves files added via the app (which have accurate timestamps)
        # while still populating the list for files added externally
        if current_count >= 100:
            conn.close()
            app_logger.debug(f"Recent files already has {current_count} entries, skipping scan update")
            return

        app_logger.info(f"Populating recent_files database from {len(comic_files)} scanned files ({current_count} existing)...")

        # Sort by modification time (most recent first) - use heapq for efficiency with large lists
        import heapq
        from datetime import datetime

        # If we have a huge number of files, use heapq.nlargest for better performance
        if len(comic_files) > 10000:
            app_logger.info("Large library detected, using optimized sorting...")
            top_100 = heapq.nlargest(100, comic_files, key=lambda x: x['mtime'])
        else:
            sorted_files = sorted(comic_files, key=lambda x: x['mtime'], reverse=True)
            top_100 = sorted_files[:100]

        # Clear existing entries and insert fresh data
        c.execute('DELETE FROM recent_files')

        # Batch insert for better performance
        records = [
            (
                file_info['path'],
                file_info['name'],
                file_info['size'],
                datetime.fromtimestamp(file_info['mtime']).strftime('%Y-%m-%d %H:%M:%S')
            )
            for file_info in top_100
        ]

        c.executemany('''
            INSERT INTO recent_files (file_path, file_name, file_size, added_at)
            VALUES (?, ?, ?, ?)
        ''', records)

        conn.commit()
        conn.close()

        app_logger.info(f"✅ Recent files database populated with {len(top_100)} files")

    except Exception as e:
        app_logger.error(f"Error updating recent files from scan: {e}")
        import traceback
        app_logger.error(f"Traceback: {traceback.format_exc()}")

#########################
#   GCD Search Helpers  #
#########################

STOPWORDS = {"the", "a", "an", "of", "and", "vol", "volume", "season", "series"}

def normalize_title(s: str) -> str:
    """Normalize a title string for better matching."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)   # remove punctuation/hyphens
    s = " ".join(s.split())              # collapse spaces
    return s

def tokens_for_all_match(s: str):
    """Normalize and drop stopwords for 'all tokens present' matching."""
    norm = normalize_title(s)
    toks = [t for t in norm.split() if t not in STOPWORDS]
    return norm, toks

def lookahead_regex(toks):
    """Build ^(?=.*\bsuperman\b)(?=.*\bsecret\b)(?=.*\byears\b).*$
    Works with MySQL REGEXP and is case-insensitive when we pass 'i' or pre-lowercase."""
    if not toks:
        return r".*"          # match-all fallback
    parts = [rf"(?=.*\\b{re.escape(t)}\\b)" for t in toks]
    return "^" + "".join(parts) + ".*$"

def generate_search_variations(series_name: str, year: str = None):
    """Generate progressive search variations for a comic title."""
    variations = []

    # Original exact search (current behavior)
    variations.append(("exact", f"%{series_name}%"))

    # Remove issue number pattern from title for broader search
    clean_title = re.sub(r'\s+\d{3}\s*$', '', series_name)  # Remove trailing issue numbers like "001"
    clean_title = re.sub(r'\s+#\d+\s*$', '', clean_title)   # Remove trailing issue numbers like "#1"

    if clean_title != series_name:
        variations.append(("no_issue", f"%{clean_title}%"))

    # Remove year from title if present
    title_no_year = re.sub(r'\s*\(\d{4}\)\s*', '', clean_title)
    title_no_year = re.sub(r'\s+\d{4}\s*$', '', title_no_year)

    if title_no_year != clean_title:
        variations.append(("no_year", f"%{title_no_year}%"))

    # Normalize and tokenize for advanced matching
    norm, tokens = tokens_for_all_match(title_no_year)

    # Remove hyphens/dashes for matching (Superman - The Secret Years -> Superman The Secret Years)
    no_dash_title = re.sub(r'\s*-+\s*', ' ', title_no_year).strip()
    if no_dash_title != title_no_year:
        variations.append(("no_dash", f"%{no_dash_title}%"))

    # Remove articles and common words for broader matching
    if len(tokens) > 1:
        regex_pattern = lookahead_regex(tokens)
        variations.append(("tokenized", regex_pattern))

    # Just the main character/franchise name (first significant word)
    if len(tokens) > 0:
        main_word = tokens[0]
        if year:
            variations.append(("main_with_year", f"%{main_word}%"))
        else:
            variations.append(("main_only", f"%{main_word}%"))

    return variations

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

# Global cache for directory listings with thread safety
cache_lock = threading.RLock()
directory_cache = OrderedDict()  # Use OrderedDict for LRU behavior
cache_timestamps = {}
cache_stats = {
    'hits': 0,
    'misses': 0,
    'evictions': 0,
    'invalidations': 0
}
CACHE_DURATION = 5  # Cache for 5 seconds
MAX_CACHE_SIZE = 500  # Increased maximum number of cached directories
CACHE_REBUILD_INTERVAL = 6 * 60 * 60  # 6 hours in seconds
last_cache_rebuild = time.time()
last_cache_invalidation = None  # Track when cache was last invalidated

def get_directory_hash(path):
    """Generate a more robust hash for directory contents to detect changes."""
    try:
        stat = os.stat(path)
        # Include inode and creation time for better change detection
        inode = getattr(stat, 'st_ino', 0)
        ctime = getattr(stat, 'st_ctime', 0)
        # Use modification time, size, inode, and creation time
        return f"{stat.st_mtime}_{stat.st_size}_{inode}_{ctime}"
    except Exception as e:
        app_logger.debug(f"Error generating hash for {path}: {e}")
        return "error"

def is_cache_valid(path):
    """Check if cached data is still valid with thread safety."""
    with cache_lock:
        if path not in cache_timestamps:
            return False

        # Check if cache has expired
        if time.time() - cache_timestamps[path] > CACHE_DURATION:
            return False

        # Check if directory has changed
        current_hash = get_directory_hash(path)
        cached_data = directory_cache.get(path, {})
        cached_hash = cached_data.get('hash') if isinstance(cached_data, dict) else None
        return current_hash == cached_hash

def cleanup_cache():
    """Remove expired entries from cache with improved LRU management."""
    with cache_lock:
        current_time = time.time()
        expired_paths = [
            path for path, timestamp in cache_timestamps.items()
            if current_time - timestamp > CACHE_DURATION
        ]

        for path in expired_paths:
            if path in directory_cache:
                directory_cache.pop(path, None)
                cache_timestamps.pop(path, None)
                cache_stats['evictions'] += 1

        # Enforce size limit with LRU eviction
        while len(directory_cache) > MAX_CACHE_SIZE:
            # Remove oldest item (first in OrderedDict)
            oldest_path = next(iter(directory_cache))
            directory_cache.pop(oldest_path, None)
            cache_timestamps.pop(oldest_path, None)
            cache_stats['evictions'] += 1

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
    """Get directory listing with optimized file system operations and memory awareness."""
    try:
        # Check memory before operation and adjust cache size if needed
        monitor = get_global_monitor()
        memory_usage = monitor.get_memory_usage()

        # Reduce cache size if memory is high
        if memory_usage > 800:  # 800MB threshold
            with cache_lock:
                target_size = max(50, MAX_CACHE_SIZE // 2)
                while len(directory_cache) > target_size:
                    oldest_path = next(iter(directory_cache))
                    directory_cache.pop(oldest_path, None)
                    cache_timestamps.pop(oldest_path, None)
                    cache_stats['evictions'] += 1

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
    """Invalidate cache for a specific path and its parent with improved tracking."""
    global last_cache_invalidation, _data_dir_stats_last_update

    # Skip cache invalidation for WATCH and TARGET directories
    if is_critical_path(path):
        app_logger.debug(f"Skipping cache invalidation for critical path: {path}")
        return

    with cache_lock:
        invalidated_count = 0

        # Invalidate the specific path
        if path in directory_cache:
            directory_cache.pop(path, None)
            cache_timestamps.pop(path, None)
            invalidated_count += 1

        # Also invalidate parent directory cache
        parent = os.path.dirname(path)
        if parent and parent in directory_cache:
            directory_cache.pop(parent, None)
            cache_timestamps.pop(parent, None)
            invalidated_count += 1

        # Invalidate any child directory caches
        paths_to_invalidate = []
        for cached_path in directory_cache.keys():
            if cached_path.startswith(path + os.sep):
                paths_to_invalidate.append(cached_path)

        for cached_path in paths_to_invalidate:
            directory_cache.pop(cached_path, None)
            cache_timestamps.pop(cached_path, None)
            invalidated_count += 1

        cache_stats['invalidations'] += invalidated_count

    # Also invalidate directory stats cache when files change
    _data_dir_stats_last_update = 0

    # Track when cache invalidation occurred
    last_cache_invalidation = time.time()

    if invalidated_count > 0:
        app_logger.debug(f"Invalidated {invalidated_count} cache entries for path: {path}")

def rebuild_entire_cache():
    """Rebuild the entire directory cache and search index."""
    global directory_cache, cache_timestamps, last_cache_rebuild, last_cache_invalidation

    app_logger.info("🔄 Starting scheduled cache rebuild...")
    start_time = time.time()

    with cache_lock:
        cleared_count = len(directory_cache)
        # Clear all caches
        directory_cache.clear()
        cache_timestamps.clear()
        # Keep performance stats but mark rebuild
        cache_stats['evictions'] += cleared_count

    # Rebuild search index
    invalidate_file_index()
    build_file_index()

    # Update rebuild timestamp and reset invalidation
    last_cache_rebuild = time.time()
    last_cache_invalidation = None  # Reset invalidation tracking after rebuild

    rebuild_time = time.time() - start_time
    app_logger.info(f"✅ Cache rebuild completed in {rebuild_time:.2f} seconds ({cleared_count} entries cleared)")

    # Warm up cache with frequently accessed directories
    warmup_cache()

    return rebuild_time

def warmup_cache():
    """Proactively cache frequently accessed directories."""
    warmup_paths = [DATA_DIR, TARGET_DIR]

    # Add common subdirectories
    for base_path in [DATA_DIR, TARGET_DIR]:
        try:
            if os.path.exists(base_path):
                subdirs = [d for d in os.listdir(base_path)
                          if os.path.isdir(os.path.join(base_path, d)) and not d.startswith('.') and not d.startswith('_')]
                # Add first few subdirectories to warmup
                for subdir in subdirs[:5]:
                    warmup_paths.append(os.path.join(base_path, subdir))
        except (OSError, IOError):
            continue

    # Pre-cache these directories
    warmed_count = 0
    for path in warmup_paths:
        try:
            if os.path.exists(path) and path not in directory_cache:
                listing_data = get_directory_listing(path)
                with cache_lock:
                    directory_cache[path] = listing_data
                    cache_timestamps[path] = time.time()
                warmed_count += 1

                # Don't warm up too many at once
                if warmed_count >= 10:
                    break
        except Exception as e:
            app_logger.debug(f"Failed to warm up cache for {path}: {e}")

    if warmed_count > 0:
        app_logger.info(f"🔥 Warmed up cache with {warmed_count} frequently accessed directories")

@app.route('/warmup-cache', methods=['POST'])
def warmup_cache_endpoint():
    """Manually trigger cache warmup."""
    try:
        warmup_cache()
        return jsonify({"success": True, "message": "Cache warmup completed"})
    except Exception as e:
        app_logger.error(f"Error during cache warmup: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

def should_rebuild_cache():
    """Check if it's time to rebuild the cache based on the interval."""
    global last_cache_rebuild
    return time.time() - last_cache_rebuild >= CACHE_REBUILD_INTERVAL

@app.route('/clear-cache', methods=['POST'])
def clear_cache():
    """Manually clear the directory cache."""
    global directory_cache, cache_timestamps, last_cache_invalidation, _data_dir_stats_last_update

    with cache_lock:
        cleared_count = len(directory_cache)
        directory_cache.clear()
        cache_timestamps.clear()
        # Reset stats
        cache_stats['hits'] = 0
        cache_stats['misses'] = 0
        cache_stats['evictions'] = 0
        cache_stats['invalidations'] = 0

    last_cache_invalidation = time.time()
    _data_dir_stats_last_update = 0  # Also invalidate directory stats cache
    app_logger.info(f"Directory cache cleared manually ({cleared_count} entries)")
    return jsonify({"success": True, "message": f"Cache cleared ({cleared_count} entries)"})

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
    
    # Calculate cache hit rate
    total_requests = cache_stats['hits'] + cache_stats['misses']
    hit_rate = (cache_stats['hits'] / total_requests * 100) if total_requests > 0 else 0

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
        "cache_stats": {
            "hits": cache_stats['hits'],
            "misses": cache_stats['misses'],
            "hit_rate": round(hit_rate, 2),
            "evictions": cache_stats['evictions'],
            "invalidations": cache_stats['invalidations']
        },
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
    
    # Calculate cache hit rate for light status
    total_requests = cache_stats['hits'] + cache_stats['misses']
    hit_rate = (cache_stats['hits'] / total_requests * 100) if total_requests > 0 else 0

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
        "max_cache_size": MAX_CACHE_SIZE,
        "hit_rate": round(hit_rate, 2)
    })

@app.route('/cache-debug', methods=['GET'])
def get_cache_debug():
    """Debug endpoint to show current cache state and performance metrics."""
    global directory_cache, cache_timestamps, last_cache_rebuild, last_cache_invalidation

    current_time = time.time()

    with cache_lock:
        # Get some sample cache entries
        sample_cache = {}
        for i, (path, timestamp) in enumerate(list(cache_timestamps.items())[:5]):
            age = current_time - timestamp
            sample_cache[path] = {
                "age_seconds": round(age, 2),
                "cached_data": bool(path in directory_cache)
            }

        # Calculate memory usage more accurately
        cache_memory_mb = round(sys.getsizeof(directory_cache) / (1024 * 1024), 2)

    return jsonify({
        "current_time": current_time,
        "cache_size": len(directory_cache),
        "cache_timestamps_count": len(cache_timestamps),
        "last_rebuild": last_cache_rebuild,
        "last_invalidation": last_cache_invalidation,
        "sample_cache_entries": sample_cache,
        "memory_usage_mb": cache_memory_mb,
        "cache_performance": {
            "hits": cache_stats['hits'],
            "misses": cache_stats['misses'],
            "hit_rate_percent": round((cache_stats['hits'] / max(1, cache_stats['hits'] + cache_stats['misses'])) * 100, 2),
            "evictions": cache_stats['evictions'],
            "invalidations": cache_stats['invalidations']
        }
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

    # Track comic files for recent files database
    comic_files = []

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

                    # Track comic files for recent files list
                    if name.lower().endswith(('.cbz', '.cbr')):
                        try:
                            mtime = os.path.getmtime(full_path)
                            comic_files.append({
                                'path': full_path,
                                'name': name,
                                'size': file_size,
                                'mtime': mtime
                            })
                        except (OSError, IOError):
                            pass

                except (OSError, IOError):
                    continue

    except Exception as e:
        app_logger.error(f"Error building file index: {e}")
        return

    build_time = time.time() - start_time
    app_logger.info(f"File index built successfully: {len(file_index)} items in {build_time:.2f} seconds")
    index_built = True

    # Update recent files database with 100 most recently modified comic files
    update_recent_files_from_scan(comic_files)

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

# app_logger, APP_LOG, and MONITOR_LOG are now imported from app_logging module
# Set log level from config (default to INFO = debug disabled)
debug_enabled = config.get("SETTINGS", "ENABLE_DEBUG_LOGGING", fallback="False") == "True"
app_logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
app_logger.info(f"App started successfully! (Debug logging: {'enabled' if debug_enabled else 'disabled'})")

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

        # Cache the result with thread safety
        with cache_lock:
            cache_stats['misses'] += 1
            directory_cache[current_path] = listing_data
            cache_timestamps[current_path] = time.time()

            # LRU eviction is handled by cleanup_cache()
            if len(directory_cache) > MAX_CACHE_SIZE:
                cleanup_cache()

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
#    List New Files     #
#########################
@app.route('/list-new-files', methods=['GET'])
def list_new_files():
    """List files created in the past 7 days in the given directory and its subdirectories.
    Optimized for large file counts with early termination and result limits."""
    current_path = request.args.get('path', DATA_DIR)  # Default to /data
    days = int(request.args.get('days', 7))  # Default to 7 days
    max_results = int(request.args.get('max_results', 500))  # Limit results to prevent timeout

    if not os.path.exists(current_path):
        return jsonify({"error": "Directory not found"}), 404

    try:
        from datetime import datetime, timedelta
        import time as time_module

        # Calculate cutoff time (7 days ago)
        cutoff_time = datetime.now() - timedelta(days=days)
        cutoff_timestamp = cutoff_time.timestamp()

        # List to store new files
        new_files = []
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db"}

        # Track scan stats
        files_scanned = 0
        dirs_scanned = 0
        start_time = time_module.time()
        max_scan_time = 30  # Maximum 30 seconds scan time

        # Generator function for efficient scanning
        def scan_for_new_files():
            nonlocal files_scanned, dirs_scanned

            for root, dirs, files in os.walk(current_path):
                # Check timeout
                if time_module.time() - start_time > max_scan_time:
                    app_logger.warning(f"New files scan timed out after {max_scan_time}s")
                    break

                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))]
                dirs_scanned += 1

                for filename in files:
                    files_scanned += 1

                    # Skip hidden files and excluded extensions
                    if filename.startswith(('.', '_')):
                        continue

                    if any(filename.lower().endswith(ext) for ext in excluded_extensions):
                        continue

                    full_path = os.path.join(root, filename)

                    try:
                        # Use lstat for faster access (doesn't follow symlinks)
                        stat = os.lstat(full_path)

                        # Check if file was created within the time window
                        if stat.st_ctime >= cutoff_timestamp:
                            yield {
                                "name": filename,
                                "size": stat.st_size,
                                "path": full_path,
                                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                                "created_ts": stat.st_ctime
                            }
                    except (OSError, IOError):
                        # Skip files we can't access
                        continue

        # Collect files up to max_results
        for file_info in scan_for_new_files():
            new_files.append(file_info)

            # Stop if we've reached the limit
            if len(new_files) >= max_results:
                app_logger.info(f"Reached max_results limit of {max_results}")
                break

        # Sort by creation time (newest first)
        new_files.sort(key=lambda f: f["created_ts"], reverse=True)

        # Remove the timestamp field from results (was only used for sorting)
        for file_info in new_files:
            del file_info["created_ts"]

        elapsed_time = time_module.time() - start_time
        app_logger.info(f"New files scan completed: {len(new_files)} found, {files_scanned} files scanned, {dirs_scanned} dirs, {elapsed_time:.2f}s")

        return jsonify({
            "current_path": current_path,
            "files": new_files,
            "total_count": len(new_files),
            "days": days,
            "cutoff_date": cutoff_time.isoformat(),
            "limited": len(new_files) >= max_results,
            "max_results": max_results,
            "scan_stats": {
                "files_scanned": files_scanned,
                "dirs_scanned": dirs_scanned,
                "elapsed_seconds": round(elapsed_time, 2)
            }
        })

    except Exception as e:
        app_logger.error(f"Error in list_new_files for {current_path}: {e}")
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

        # Cache the result with thread safety
        with cache_lock:
            cache_stats['misses'] += 1
            directory_cache[current_path] = listing_data
            cache_timestamps[current_path] = time.time()

            # LRU eviction is handled by cleanup_cache()
            if len(directory_cache) > MAX_CACHE_SIZE:
                cleanup_cache()

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

#########################
#    Recent Files      #
#########################
@app.route('/list-recent-files', methods=['GET'])
def list_recent_files():
    """Get the last 100 files added to the /data directory (tracked by file watcher)."""
    try:
        limit = request.args.get('limit', 100, type=int)
        if limit > 100:
            limit = 100  # Cap at 100 files

        recent_files = get_recent_files(limit=limit)

        # Calculate date range
        date_range = None
        if recent_files:
            oldest_date = recent_files[-1]['added_at']
            newest_date = recent_files[0]['added_at']
            date_range = {
                'oldest': oldest_date,
                'newest': newest_date
            }

        return jsonify({
            "success": True,
            "files": recent_files,
            "total_count": len(recent_files),
            "date_range": date_range
        })

    except Exception as e:
        app_logger.error(f"Error in list_recent_files: {e}")
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

    # Prevent moving a directory into itself or its subdirectories
    if os.path.isdir(source):
        # Normalize paths for comparison
        source_normalized = os.path.normpath(source)
        destination_normalized = os.path.normpath(destination)
        
        # Check if destination is the same as source or a subdirectory of source
        if (destination_normalized == source_normalized or 
            destination_normalized.startswith(source_normalized + os.sep)):
            app_logger.error(f"Attempted to move directory into itself: {source} -> {destination}")
            return jsonify({"success": False, "error": "Cannot move a directory into itself or its subdirectories"}), 400

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

                        # Log file to recent_files if it's a comic file moved to /data
                        log_file_if_in_data(destination)

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

                        # Log all comic files in the moved directory to recent_files
                        try:
                            for root, _, files_in_dir in os.walk(destination):
                                for file in files_in_dir:
                                    file_path = os.path.join(root, file)
                                    log_file_if_in_data(file_path)
                        except Exception as e:
                            app_logger.warning(f"Error logging files from directory {destination}: {e}")

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
            "Connection": "keep-alive"
        }
        return Response(stream_with_context(generate()), headers=headers)

    else:
        # Non-streaming move for folders or when streaming is disabled
        with memory_context("file_move"):
            try:
                is_file = os.path.isfile(source)

                if is_file:
                    shutil.move(source, destination)
                else:
                    shutil.move(source, destination)
                app_logger.info(f"Move complete: {source} -> {destination}")

                # Log file to recent_files if it's a comic file moved to /data
                if is_file:
                    log_file_if_in_data(destination)
                else:
                    # For directories, log all comic files inside
                    try:
                        for root, _, files in os.walk(destination):
                            for file in files:
                                file_path = os.path.join(root, file)
                                log_file_if_in_data(file_path)
                    except Exception as e:
                        app_logger.warning(f"Error logging files from directory {destination}: {e}")

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
                        if ext.endswith(('.cbz', '.cbr', '.zip')):
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
#       Upload Files to Folder      #
#####################################
@app.route('/upload-to-folder', methods=['POST'])
def upload_to_folder():
    """
    Upload files to a specific folder.
    Accepts multiple files and a target directory path.
    Only allows image files, CBZ, and CBR files.
    """
    try:
        # Get target directory from form data
        target_dir = request.form.get('target_dir')

        if not target_dir:
            return jsonify({"success": False, "error": "No target directory specified"}), 400

        # Validate target directory exists
        if not os.path.exists(target_dir):
            return jsonify({"success": False, "error": "Target directory does not exist"}), 404

        if not os.path.isdir(target_dir):
            return jsonify({"success": False, "error": "Target path is not a directory"}), 400

        # Check if files were uploaded
        if 'files' not in request.files:
            return jsonify({"success": False, "error": "No files provided"}), 400

        files = request.files.getlist('files')

        if not files or all(f.filename == '' for f in files):
            return jsonify({"success": False, "error": "No files selected"}), 400

        # Allowed file extensions
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.cbz', '.cbr'}

        uploaded_files = []
        skipped_files = []
        errors = []

        for file in files:
            if file.filename == '':
                continue

            # Get file extension
            filename = secure_filename(file.filename)
            file_ext = os.path.splitext(filename)[1].lower()

            # Validate file type
            if file_ext not in allowed_extensions:
                skipped_files.append({
                    'name': filename,
                    'reason': f'File type not allowed ({file_ext})'
                })
                continue

            # Construct full path
            file_path = os.path.join(target_dir, filename)

            # Check if file already exists
            if os.path.exists(file_path):
                # Add a number to make it unique
                base_name, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(os.path.join(target_dir, f"{base_name}_{counter}{ext}")):
                    counter += 1
                filename = f"{base_name}_{counter}{ext}"
                file_path = os.path.join(target_dir, filename)

            try:
                # Save the file
                file.save(file_path)
                file_size = os.path.getsize(file_path)

                uploaded_files.append({
                    'name': filename,
                    'path': file_path,
                    'size': file_size
                })

                # Log to recent files if it's a comic file in /data
                log_file_if_in_data(file_path)

                app_logger.info(f"Uploaded file: {filename} to {target_dir}")

            except Exception as e:
                errors.append({
                    'name': filename,
                    'error': str(e)
                })
                app_logger.error(f"Error uploading file {filename}: {e}")

        # Invalidate cache for the target directory
        invalidate_cache_for_path(target_dir)

        # Return results
        response = {
            "success": True,
            "uploaded": uploaded_files,
            "skipped": skipped_files,
            "errors": errors,
            "total_uploaded": len(uploaded_files),
            "total_skipped": len(skipped_files),
            "total_errors": len(errors)
        }

        return jsonify(response)

    except Exception as e:
        app_logger.error(f"Error in upload_to_folder: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
    
    if not file_path.lower().endswith(('.cbz', '.zip')):
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
    
    if not file_path.lower().endswith(('.cbz', '.zip')):
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

@app.route('/cbz-clear-comicinfo', methods=['POST'])
def cbz_clear_comicinfo():
    """Delete ComicInfo.xml from a CBZ file"""
    data = request.get_json()
    file_path = data.get('path')

    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "error": "Invalid file path"}), 400

    if not file_path.lower().endswith('.cbz'):
        return jsonify({"success": False, "error": "File is not a CBZ"}), 400

    try:
        import zipfile

        # Create a temporary file for the new CBZ
        temp_zip_path = file_path + ".tmpzip"
        comicinfo_found = False

        # Open the original CBZ and create a new one without ComicInfo.xml
        with zipfile.ZipFile(file_path, 'r') as old_zip, \
             zipfile.ZipFile(temp_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as new_zip:

            for item in old_zip.infolist():
                if item.filename.lower() == "comicinfo.xml":
                    comicinfo_found = True
                    app_logger.info(f"Removing ComicInfo.xml from {file_path}")
                    # Skip this file (don't write it to new zip)
                    continue
                else:
                    # Copy all other files as-is
                    new_zip.writestr(item, old_zip.read(item.filename))

        if not comicinfo_found:
            # Clean up temp file if ComicInfo.xml wasn't found
            os.remove(temp_zip_path)
            return jsonify({"success": False, "error": "ComicInfo.xml not found in CBZ"}), 404

        # Replace the original CBZ with the updated one
        os.replace(temp_zip_path, file_path)

        app_logger.info(f"Successfully removed ComicInfo.xml from {file_path}")
        return jsonify({"success": True})

    except Exception as e:
        app_logger.error(f"Error removing ComicInfo.xml from {file_path}: {e}")
        # Clean up temp file if it exists
        if os.path.exists(file_path + ".tmpzip"):
            os.remove(file_path + ".tmpzip")
        return jsonify({"success": False, "error": str(e)}), 500

#####################################
#     Move Files/Folders UI Page    #
#####################################
@app.route('/files')
def files_page():
    watch = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_dir = config.get("SETTINGS", "TARGET", fallback="/processed")
    return render_template('files.html', watch=watch, target_dir=target_dir)

#####################################
#           Collection Page             #
#####################################

@app.route('/collection')
def collection():
    """Render the visual browse page."""
    return render_template('collection.html')

def find_folder_thumbnail(folder_path):
    """Find a folder/cover image in the given directory.

    Args:
        folder_path: Path to the directory to search

    Returns:
        Path to the thumbnail image if found, None otherwise
    """
    allowed_extensions = {'.png', '.gif', '.jpg', '.jpeg', '.webp'}
    allowed_names = {'folder', 'cover'}

    try:
        entries = os.listdir(folder_path)
        for entry in entries:
            name_without_ext, ext = os.path.splitext(entry.lower())
            if name_without_ext in allowed_names and ext in allowed_extensions:
                return os.path.join(folder_path, entry)
    except (OSError, IOError):
        pass

    return None

@app.route('/api/browse')
def api_browse():
    """Get directory listing for the browse page."""
    path = request.args.get('path')
    if not path:
        path = DATA_DIR

    if not os.path.exists(path):
        return jsonify({"error": "Directory not found"}), 404

    try:
        # Use existing list logic
        listing = get_directory_listing(path)

        # Define excluded extensions and prefixes
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db", ".xml"}

        # Process directories to add folder thumbnail info and check for files
        processed_directories = []
        for dir_name in listing['directories']:
            dir_path = os.path.join(path, dir_name)
            folder_thumb = find_folder_thumbnail(dir_path)

            # Check if this folder directly contains files (not in subfolders)
            has_files = False
            try:
                items = os.listdir(dir_path)
                for item in items:
                    item_path = os.path.join(dir_path, item)
                    if os.path.isfile(item_path):
                        # Check if it's a valid file (not excluded)
                        _, ext = os.path.splitext(item.lower())
                        if ext not in excluded_extensions and item.lower() != "cvinfo" and not item.startswith(('.', '-', '_')):
                            has_files = True
                            break
            except Exception:
                pass

            dir_info = {
                'name': dir_name,
                'has_thumbnail': folder_thumb is not None,
                'has_files': has_files
            }

            if folder_thumb:
                dir_info['thumbnail_url'] = url_for('serve_folder_thumbnail', path=folder_thumb)

            processed_directories.append(dir_info)

        # Filter files
        files = listing['files']
        filtered_files = []
        for f in files:
            filename = f['name']
            # Get file extension (lowercase)
            _, ext = os.path.splitext(filename.lower())

            # Check if file should be excluded
            if ext in excluded_extensions or filename.lower() == "cvinfo":
                continue
            if filename.startswith(('.', '-', '_')):
                continue

            # Add thumbnail info
            if filename.lower().endswith(('.cbz', '.cbr', '.zip')):
                f['has_thumbnail'] = True
                f['thumbnail_url'] = url_for('get_thumbnail', path=os.path.join(path, filename))
            else:
                f['has_thumbnail'] = False

            filtered_files.append(f)

        return jsonify({
            "current_path": path,
            "directories": processed_directories,
            "files": filtered_files,
            "parent": os.path.dirname(path) if path != DATA_DIR else None
        })
    except Exception as e:
        app_logger.error(f"Error browsing {path}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/browse-recursive')
def api_browse_recursive():
    """Get all files recursively from a directory and subdirectories."""
    import re

    path = request.args.get('path', '')

    # Use the path directly (like /api/browse does)
    if not path:
        full_path = DATA_DIR
    else:
        full_path = path

    if not os.path.exists(full_path) or not os.path.isdir(full_path):
        return jsonify({"error": "Invalid path"}), 400

    # Define excluded extensions and prefixes
    excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db", ".xml"}

    files = []

    # Recursively walk directory
    for root, dirs, filenames in os.walk(full_path):
        for filename in filenames:
            # Get file extension (lowercase)
            _, ext = os.path.splitext(filename.lower())

            # Check if file should be excluded
            if ext in excluded_extensions or filename.lower() == "cvinfo":
                continue
            if filename.startswith(('.', '-', '_')):
                continue

            file_path = os.path.join(root, filename)

            # Calculate relative path from DATA_DIR for consistency
            if full_path == DATA_DIR:
                rel_path = os.path.relpath(file_path, DATA_DIR)
            else:
                rel_path = os.path.relpath(file_path, DATA_DIR)

            try:
                stat_info = os.stat(file_path)
                file_info = {
                    "name": filename,  # Just filename, not full path
                    "path": rel_path,
                    "size": stat_info.st_size,
                    "modified": stat_info.st_mtime,
                    "type": "file"
                }

                # Add thumbnail info for comic files
                if filename.lower().endswith(('.cbz', '.cbr', '.zip')):
                    file_info['has_thumbnail'] = True
                    file_info['thumbnail_url'] = url_for('get_thumbnail', path=file_path)
                else:
                    file_info['has_thumbnail'] = False

                files.append(file_info)
            except Exception as e:
                app_logger.warning(f"Error processing file {file_path}: {e}")
                continue
    
    # Sort files by series name, year, then issue number
    def natural_sort_key(item):
        """
        Sort comic files by series name, year, then issue number.
        Example: 'Batgirl 002 (2000).cbz' -> ('batgirl', 2000, 2, 'batgirl 002 (2000).cbz')
        Falls back to natural sorting if pattern doesn't match.
        """
        filename = item['name']

        # Try to extract series name, issue number, and year
        # Pattern: "Series Name 123 (2000).ext" or "Series Name #123 (2000).ext"
        match = re.match(r'^(.+?)\s+#?(\d+)\s*\((\d{4})\)', filename, re.IGNORECASE)

        if match:
            series_name = match.group(1).strip().lower()
            issue_number = int(match.group(2))
            year = int(match.group(3))
            # Return tuple: (series_name, year, issue_number, original_name_for_secondary_sort)
            return (series_name, year, issue_number, filename.lower())

        # Fallback to natural sorting for non-standard formats
        return ('', 0, 0, [int(text) if text.isdigit() else text.lower()
                           for text in re.split('([0-9]+)', filename)])

    files.sort(key=natural_sort_key)
    
    return jsonify({
        "current_path": path,
        "files": files,
        "total": len(files)
    })

@app.route('/api/folder-thumbnail')
def serve_folder_thumbnail():
    """Serve a folder thumbnail image."""
    image_path = request.args.get('path')

    if not image_path:
        app_logger.error("No path provided for folder thumbnail")
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

    # Normalize the path
    image_path = os.path.normpath(image_path)

    if not os.path.exists(image_path):
        app_logger.error(f"Folder thumbnail path does not exist: {image_path}")
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

    if not os.path.isfile(image_path):
        app_logger.error(f"Folder thumbnail path is not a file: {image_path}")
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

    try:
        # Determine mime type based on extension
        ext = os.path.splitext(image_path)[1].lower()
        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        mime_type = mime_types.get(ext, 'image/jpeg')

        return send_file(image_path, mimetype=mime_type)
    except Exception as e:
        app_logger.error(f"Error serving folder thumbnail {image_path}: {e}")
        import traceback
        app_logger.error(traceback.format_exc())
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

@app.route('/api/read/<path:comic_path>/page/<int:page_num>')
def read_comic_page(comic_path, page_num):
    """Serve a specific page from a comic file."""
    import zipfile
    import rarfile
    from PIL import Image

    # Add leading slash if missing (for absolute paths on Unix systems)
    if not comic_path.startswith('/'):
        comic_path = '/' + comic_path

    if not os.path.exists(comic_path):
        app_logger.error(f"Comic file not found: {comic_path}")
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

    try:
        # Determine archive type
        ext = os.path.splitext(comic_path)[1].lower()

        # Get list of image files from archive
        image_files = []
        archive = None

        if ext in ['.cbz', '.zip']:
            archive = zipfile.ZipFile(comic_path, 'r')
            all_files = archive.namelist()
        elif ext == '.cbr':
            archive = rarfile.RarFile(comic_path, 'r')
            all_files = archive.namelist()
        else:
            return jsonify({"error": "Unsupported file format"}), 400

        # Filter for image files
        image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
        for filename in all_files:
            if filename.lower().endswith(image_extensions):
                # Skip macOS metadata files
                if not filename.startswith('__MACOSX') and not os.path.basename(filename).startswith('.'):
                    image_files.append(filename)

        # Sort naturally
        import re
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]
        image_files.sort(key=natural_sort_key)

        # Check if page number is valid
        if page_num < 0 or page_num >= len(image_files):
            return jsonify({"error": "Invalid page number"}), 400

        # Read the requested page
        target_file = image_files[page_num]
        image_data = archive.read(target_file)

        # Close archive
        archive.close()

        # Determine mime type
        file_ext = os.path.splitext(target_file)[1].lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp'
        }
        mime_type = mime_types.get(file_ext, 'image/jpeg')

        # Return image
        return Response(image_data, mimetype=mime_type)

    except Exception as e:
        app_logger.error(f"Error reading comic page {page_num} from {comic_path}: {e}")
        import traceback
        app_logger.error(traceback.format_exc())
        if archive:
            archive.close()
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

@app.route('/api/read/<path:comic_path>/info')
def read_comic_info(comic_path):
    """Get information about a comic file (page count, etc.)."""
    import zipfile
    import rarfile

    # Add leading slash if missing (for absolute paths on Unix systems)
    if not comic_path.startswith('/'):
        comic_path = '/' + comic_path

    if not os.path.exists(comic_path):
        return jsonify({"error": "Comic file not found"}), 404

    try:
        # Determine archive type
        ext = os.path.splitext(comic_path)[1].lower()

        # Get list of image files from archive
        image_files = []

        if ext in ['.cbz', '.zip']:
            with zipfile.ZipFile(comic_path, 'r') as archive:
                all_files = archive.namelist()
        elif ext == '.cbr':
            with rarfile.RarFile(comic_path, 'r') as archive:
                all_files = archive.namelist()
        else:
            return jsonify({"error": "Unsupported file format"}), 400

        # Filter for image files
        image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
        for filename in all_files:
            if filename.lower().endswith(image_extensions):
                # Skip macOS metadata files
                if not filename.startswith('__MACOSX') and not os.path.basename(filename).startswith('.'):
                    image_files.append(filename)

        return jsonify({
            "success": True,
            "page_count": len(image_files),
            "filename": os.path.basename(comic_path)
        })

    except Exception as e:
        app_logger.error(f"Error getting comic info for {comic_path}: {e}")
        import traceback
        app_logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

def generate_thumbnail_task(file_path, cache_path):
    """Background task to generate thumbnail."""
    app_logger.info(f"Starting thumbnail generation for {file_path}")
    try:
        # Extract and resize
        import zipfile
        from PIL import Image
        
        # Ensure cache directory exists
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        
        with zipfile.ZipFile(file_path, 'r') as zf:
            file_list = zf.namelist()
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
            image_files = sorted([f for f in file_list if os.path.splitext(f.lower())[1] in image_extensions], key=str.lower)
            
            if image_files:
                with zf.open(image_files[0]) as image_file:
                    img = Image.open(image_file)
                    if img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGB')
                    
                    # Resize to 300px height
                    aspect_ratio = img.width / img.height
                    new_height = 300
                    new_width = int(new_height * aspect_ratio)
                    img.thumbnail((new_width, new_height), Image.Resampling.LANCZOS)
                    
                    img.save(cache_path, format='JPEG', quality=85)
                    
                    # Update DB success
                    conn = get_db_connection()
                    if conn:
                        conn.execute('UPDATE thumbnail_jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?', ('completed', file_path))
                        conn.commit()
                        conn.close()
                        app_logger.info(f"Thumbnail generated successfully for {file_path}")
            else:
                raise Exception("No images found in archive")
                
    except Exception as e:
        app_logger.error(f"Error generating thumbnail for {file_path}: {e}")
        conn = get_db_connection()
        if conn:
            conn.execute('UPDATE thumbnail_jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?', ('error', file_path))
            conn.commit()
            conn.close()

@app.route('/api/thumbnail')
def get_thumbnail():
    """Serve or generate thumbnail for a file."""
    file_path = request.args.get('path')
    if not file_path:
        return jsonify({"error": "Missing path"}), 400
        
    # Calculate cache path
    cache_dir = config.get("SETTINGS", "CACHE_DIR", fallback="/cache")
    thumbnails_dir = os.path.join(cache_dir, "thumbnails")
    
    # Create a hash of the file path to use as filename
    import hashlib
    path_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
    
    # Sharding: use first 2 chars of hash as subdirectory to avoid too many files in one folder
    shard_dir = path_hash[:2]
    filename = f"{path_hash}.jpg"
    
    # Full path for checking existence / generation
    cache_path = os.path.join(thumbnails_dir, shard_dir, filename)
    
    # Check if thumbnail exists
    if os.path.exists(cache_path):
        return send_from_directory(os.path.join(thumbnails_dir, shard_dir), filename)
        
    # Check DB status
    conn = get_db_connection()
    job = None
    if conn:
        job = conn.execute('SELECT * FROM thumbnail_jobs WHERE path = ?', (file_path,)).fetchone()
        conn.close()
        
    if job and job['status'] == 'completed' and os.path.exists(cache_path):
        return send_from_directory(os.path.join(thumbnails_dir, shard_dir), filename)
        
    if job and job['status'] == 'processing':
        return redirect(url_for('static', filename='images/loading.svg'))
        
    if job and job['status'] == 'error':
        return redirect(url_for('static', filename='images/error.svg'))
        
    # Insert 'processing' status synchronously to prevent race conditions
    conn = get_db_connection()
    if conn:
        conn.execute('INSERT OR REPLACE INTO thumbnail_jobs (path, status) VALUES (?, ?)', (file_path, 'processing'))
        conn.commit()
        conn.close()

    # Submit task
    thumbnail_executor.submit(generate_thumbnail_task, file_path, cache_path)

    return redirect(url_for('static', filename='images/loading.svg'))


@app.route('/api/generate-folder-thumbnail', methods=['POST'])
def generate_folder_thumbnail():
    """Generate a fanned stack thumbnail for a folder using cached thumbnails."""
    data = request.get_json()
    folder_path = data.get('folder_path')

    if not folder_path:
        return jsonify({"error": "Missing folder_path"}), 400

    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        return jsonify({"error": "Invalid folder path"}), 400

    try:
        # Get cache directory
        cache_dir = config.get("SETTINGS", "CACHE_DIR", fallback="/cache")
        thumbnails_dir = os.path.join(cache_dir, "thumbnails")

        # Define excluded extensions
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".txt", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db", ".xml"}

        # Find comic files in the folder
        comic_files = []
        for item in sorted(os.listdir(folder_path)):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path):
                _, ext = os.path.splitext(item.lower())
                if ext not in excluded_extensions and not item.startswith(('.', '-', '_')):
                    if ext in ['.cbz', '.cbr', '.zip']:
                        comic_files.append(item_path)

        if not comic_files:
            return jsonify({"error": "No comic files found in folder"}), 400

        # Get cached thumbnail paths for the first 4 comics
        MAX_COVERS = 4
        selected_files = comic_files[:MAX_COVERS]
        cached_thumbs = []

        for file_path in selected_files:
            # Calculate cache path using same method as get_thumbnail
            path_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
            shard_dir = path_hash[:2]
            filename = f"{path_hash}.jpg"
            cache_path = os.path.join(thumbnails_dir, shard_dir, filename)

            if os.path.exists(cache_path):
                cached_thumbs.append(cache_path)

        if not cached_thumbs:
            return jsonify({"error": "No cached thumbnails found. Please wait for thumbnails to generate."}), 400

        # Create fanned stack thumbnail
        CANVAS_SIZE = (200, 300)
        THUMB_SIZE = (160, 245)

        final_canvas = Image.new('RGBA', CANVAS_SIZE, (0, 0, 0, 0))

        # Rotation angles for back files (more rotation) - these will be pasted first
        # Process in reverse so file 001 is pasted LAST (appears on top)
        angles = [12, -8, 5, 0]  # Back files rotated more, front file at 0°
        angles = angles[-len(cached_thumbs):]  # Take last N angles

        # Reverse cached_thumbs so we paste from back to front (001 pasted last)
        reversed_thumbs = list(reversed(cached_thumbs))

        for i, thumb_path in enumerate(reversed_thumbs):
            try:
                # Open and resize cached thumbnail
                img = Image.open(thumb_path).convert("RGBA")

                # Fit to thumb size
                img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)

                # Create centered image
                fitted_img = Image.new('RGBA', THUMB_SIZE, (0, 0, 0, 0))
                paste_x = (THUMB_SIZE[0] - img.width) // 2
                paste_y = (THUMB_SIZE[1] - img.height) // 2
                fitted_img.paste(img, (paste_x, paste_y), img if img.mode == 'RGBA' else None)

                # Create layer for rotation and shadow
                layer_size = (int(THUMB_SIZE[0] * 1.5), int(THUMB_SIZE[1] * 1.5))
                layer = Image.new('RGBA', layer_size, (0, 0, 0, 0))

                # Calculate center position
                layer_paste_x = (layer_size[0] - THUMB_SIZE[0]) // 2
                layer_paste_y = (layer_size[1] - THUMB_SIZE[1]) // 2

                # Add drop shadow
                shadow = Image.new('RGBA', layer_size, (0, 0, 0, 0))
                shadow_box = (layer_paste_x + 4, layer_paste_y + 4,
                             layer_paste_x + THUMB_SIZE[0] + 4, layer_paste_y + THUMB_SIZE[1] + 4)

                d = ImageDraw.Draw(shadow)
                d.rectangle(shadow_box, fill=(0, 0, 0, 120))
                shadow = shadow.filter(ImageFilter.GaussianBlur(radius=5))

                # Composite shadow + image
                layer = Image.alpha_composite(layer, shadow)
                layer.paste(fitted_img, (layer_paste_x, layer_paste_y), fitted_img)

                # Rotate the layer
                angle = angles[i]
                rotated_layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)

                # Position on canvas
                final_x = (CANVAS_SIZE[0] - rotated_layer.width) // 2
                final_y = (CANVAS_SIZE[1] - rotated_layer.height) // 2

                # Slight Y offset for stack effect
                y_offset = (i - len(cached_thumbs)) * 5

                final_canvas.paste(rotated_layer, (final_x, final_y + y_offset), rotated_layer)

            except Exception as e:
                app_logger.error(f"Error processing thumbnail {thumb_path}: {e}")

        # Remove any existing folder thumbnail files to allow regeneration
        for ext in ['folder.png', 'folder.jpg', 'folder.jpeg', 'folder.gif']:
            existing_thumb = os.path.join(folder_path, ext)
            if os.path.exists(existing_thumb):
                try:
                    os.remove(existing_thumb)
                    app_logger.info(f"Removed existing thumbnail: {existing_thumb}")
                except Exception as e:
                    app_logger.error(f"Error removing existing thumbnail {existing_thumb}: {e}")

        # Save to folder
        output_path = os.path.join(folder_path, "folder.png")
        final_canvas.save(output_path, "PNG")

        app_logger.info(f"Generated folder thumbnail: {output_path}")

        # Invalidate cache to show new thumbnail
        invalidate_cache_for_path(folder_path)

        return jsonify({"success": True, "thumbnail_path": output_path})

    except Exception as e:
        app_logger.error(f"Error generating folder thumbnail: {e}")
        return jsonify({"error": str(e)}), 500


#####################################
#       Rename Files/Folders        #
#####################################
@app.route('/rename', methods=['POST'])
def rename():
    data = request.get_json()
    old_path = data.get('old')
    new_path = data.get('new')
    
    app_logger.info(f"Renaming: {old_path} to {new_path}")  

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

    # Check if the new path already exists to avoid overwriting
    # Allow case-only changes (e.g., "file.txt" -> "File.txt") on case-insensitive filesystems
    if os.path.exists(new_path):
        # Check if this is a case-only rename by checking if they're the same file
        try:
            if not os.path.samefile(old_path, new_path):
                return jsonify({"error": "Destination already exists"}), 400
        except (OSError, ValueError):
            # If samefile fails, fall back to normcase comparison
            if os.path.normcase(os.path.abspath(old_path)) != os.path.normcase(os.path.abspath(new_path)):
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


@app.route('/get-image-data', methods=['POST'])
def get_full_image_data():
    """Get full-size image data as base64 for display in modal"""
    try:
        data = request.json
        file_path = data.get('target')

        if not file_path:
            return jsonify({'success': False, 'error': 'Missing file path'}), 400

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'File not found'}), 404

        # Read the image and encode as base64
        from PIL import Image
        import io
        import base64

        with Image.open(file_path) as img:
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = rgb_img

            # Encode as JPEG
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG", quality=95)
            encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
            image_data = f"data:image/jpeg;base64,{encoded}"

        return jsonify({
            'success': True,
            'imageData': image_data
        })

    except Exception as e:
        app_logger.error(f"Error getting image data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/crop-freeform', methods=['POST'])
def crop_image_freeform():
    """Handle free form crop with custom coordinates"""
    try:
        data = request.json
        file_path = data.get('target')
        x = data.get('x')
        y = data.get('y')
        width = data.get('width')
        height = data.get('height')

        app_logger.info("********************// Free Form Crop Image //********************")
        app_logger.info(f"File Path: {file_path}")
        app_logger.info(f"Crop coords: x={x}, y={y}, width={width}, height={height}")

        # Validate input
        if not file_path or x is None or y is None or width is None or height is None:
            return jsonify({'success': False, 'error': 'Missing file path or crop coordinates'}), 400

        # Perform the crop
        new_image_path, backup_path = cropFreeForm(file_path, x, y, width, height)

        # Return the updated image data and backup image data
        return jsonify({
            'success': True,
            'newImagePath': new_image_path,
            'newImageData': get_image_data_url(new_image_path),
            'backupImagePath': backup_path,
            'backupImageData': get_image_data_url(backup_path),
            'message': 'Free form crop completed.'
        })

    except Exception as e:
        app_logger.error(f"Free form crop error: {e}")
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

@app.route('/api/delete-file', methods=['POST'])
def api_delete_file():
    """Delete a file from the collection view (handles relative paths from DATA_DIR)"""
    data = request.get_json()
    relative_path = data.get('path')

    if not relative_path:
        return jsonify({"error": "Missing file path"}), 400

    # Convert relative path to absolute path
    if os.path.isabs(relative_path):
        target = relative_path
    else:
        target = os.path.join(DATA_DIR, relative_path)

    if not os.path.exists(target):
        return jsonify({"error": "File does not exist"}), 404

    # Check if trying to delete critical folders
    if is_critical_path(target):
        app_logger.error(f"Attempted to delete critical folder: {target}")
        return jsonify({"error": get_critical_path_error_message(target, "delete")}), 403

    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
            app_logger.info(f"Deleted directory: {target}")
        else:
            os.remove(target)
            app_logger.info(f"Deleted file: {target}")

        # Invalidate cache for the directory containing the deleted item
        invalidate_cache_for_path(os.path.dirname(target))
        # Invalidate search index since files have been deleted
        invalidate_file_index()

        return jsonify({"success": True})
    except Exception as e:
        app_logger.error(f"Error deleting file {target}: {e}")
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
# Version Check         #
#########################
# Cache for version check (avoid hitting GitHub API too frequently)
version_check_cache = {
    "last_check": 0,
    "latest_version": None,
    "error": None
}
VERSION_CACHE_DURATION = 21600  # 6 hours in seconds

@app.route('/api/version-check')
def version_check():
    """
    Check for updates by comparing current version with latest GitHub release.
    Caches the result for 6 hours to respect GitHub API rate limits.
    """
    current_time = time.time()

    # Return cached result if within cache duration
    if current_time - version_check_cache["last_check"] < VERSION_CACHE_DURATION:
        if version_check_cache["error"]:
            return jsonify({
                "current_version": __version__,
                "error": version_check_cache["error"]
            }), 200

        return jsonify({
            "current_version": __version__,
            "latest_version": version_check_cache["latest_version"],
            "update_available": pkg_version.parse(version_check_cache["latest_version"]) > pkg_version.parse(__version__),
            "release_url": f"https://github.com/allaboutduncan/comic-utils/releases/tag/v{version_check_cache['latest_version']}"
        }), 200

    # Fetch latest version from GitHub
    try:
        response = requests.get(
            "https://api.github.com/repos/allaboutduncan/comic-utils/releases/latest",
            timeout=5
        )
        response.raise_for_status()

        release_data = response.json()
        latest_version = release_data.get("tag_name", "").lstrip("v")

        # Update cache
        version_check_cache["last_check"] = current_time
        version_check_cache["latest_version"] = latest_version
        version_check_cache["error"] = None

        return jsonify({
            "current_version": __version__,
            "latest_version": latest_version,
            "update_available": pkg_version.parse(latest_version) > pkg_version.parse(__version__),
            "release_url": f"https://github.com/allaboutduncan/comic-utils/releases/tag/v{latest_version}"
        }), 200

    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to check for updates: {str(e)}"
        app_logger.warning(error_msg)

        # Update cache with error
        version_check_cache["last_check"] = current_time
        version_check_cache["error"] = error_msg

        return jsonify({
            "current_version": __version__,
            "error": error_msg
        }), 200

#########################
#   Scrape Page Routes  #
#########################
import uuid
import threading
from queue import Queue
from scrape.scrape_readcomiconline import scrape_series
from scrape.scrape_ehentai import scrape_urls as scrape_ehentai_urls
from scrape.scrape_erofus import scrape as scrape_erofus_url

# Store active scrape tasks
# Each task has: log_queue, progress_queue, status, buffered_logs
scrape_tasks = {}

@app.route("/scrape")
def scrape_page():
    """Render the scrape page"""
    # Use TARGET_DIR directly to avoid file monitor processing
    target_dir = config.get("SETTINGS", "TARGET", fallback="/processed")
    # Get active tasks for status display
    active_tasks = []
    for task_id, task_info in scrape_tasks.items():
        if task_info["status"] == "running":
            active_tasks.append({
                "task_id": task_id,
                "status": task_info["status"]
            })
    return render_template("scrape.html", target_dir=target_dir, active_tasks=active_tasks)

@app.route("/scrape-readcomiconline", methods=["POST"])
def scrape_readcomiconline():
    """Start scraping readcomiconline URLs"""
    try:
        data = request.json
        urls = data.get("urls", [])
        # Use TARGET_DIR directly to avoid file monitor processing and overwrites
        output_dir = data.get("output_dir", config.get("SETTINGS", "TARGET", fallback="/processed"))

        if not urls:
            return jsonify({"success": False, "error": "No URLs provided"}), 400

        # Create a unique task ID
        task_id = str(uuid.uuid4())

        # Create a queue for logs and progress
        log_queue = Queue()
        progress_queue = Queue()

        # Store task info with buffered logs for reconnection
        scrape_tasks[task_id] = {
            "log_queue": log_queue,
            "progress_queue": progress_queue,
            "status": "running",
            "buffered_logs": [],
            "last_progress": {}
        }

        # Start scraping in a background thread
        def scrape_worker():
            def log_callback(msg):
                log_queue.put(msg)

            def progress_callback(data):
                progress_queue.put(data)

            try:
                for url in urls:
                    log_queue.put(f"\n{'='*60}")
                    log_queue.put(f"Processing: {url}")
                    log_queue.put('='*60)

                    scrape_series(url, output_dir, log_callback, progress_callback)

                log_queue.put("\n=== All URLs processed ===")
                scrape_tasks[task_id]["status"] = "completed"
                log_queue.put("__COMPLETED__")  # Signal completion

            except Exception as e:
                log_queue.put(f"\n=== Error: {str(e)} ===")
                scrape_tasks[task_id]["status"] = "error"
                log_queue.put("__ERROR__")  # Signal error

        thread = threading.Thread(target=scrape_worker, daemon=True)
        thread.start()

        return jsonify({"success": True, "task_id": task_id}), 200

    except Exception as e:
        app_logger.error(f"Error starting scrape: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/scrape-ehentai", methods=["POST"])
def scrape_ehentai():
    """Start scraping E-Hentai URLs"""
    try:
        data = request.json
        urls = data.get("urls", [])
        # Use TARGET_DIR directly to avoid file monitor processing and overwrites
        output_dir = data.get("output_dir", config.get("SETTINGS", "TARGET", fallback="/processed"))

        if not urls:
            return jsonify({"success": False, "error": "No URLs provided"}), 400

        # Create a unique task ID
        task_id = str(uuid.uuid4())

        # Create a queue for logs and progress
        log_queue = Queue()
        progress_queue = Queue()

        # Store task info with buffered logs for reconnection
        scrape_tasks[task_id] = {
            "log_queue": log_queue,
            "progress_queue": progress_queue,
            "status": "running",
            "buffered_logs": [],
            "last_progress": {}
        }

        # Start scraping in a background thread
        def scrape_worker():
            def log_callback(msg):
                log_queue.put(msg)

            def progress_callback(data):
                progress_queue.put(data)

            try:
                # Scrape all URLs
                scrape_ehentai_urls(urls, output_dir, log_callback, progress_callback)

                log_queue.put("\n=== All URLs processed ===")
                scrape_tasks[task_id]["status"] = "completed"
                log_queue.put("__COMPLETED__")  # Signal completion

            except Exception as e:
                log_queue.put(f"\n=== Error: {str(e)} ===")
                scrape_tasks[task_id]["status"] = "error"
                log_queue.put("__ERROR__")  # Signal error

        thread = threading.Thread(target=scrape_worker, daemon=True)
        thread.start()

        return jsonify({"success": True, "task_id": task_id}), 200

    except Exception as e:
        app_logger.error(f"Error starting E-Hentai scrape: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/scrape-erofus", methods=["POST"])
def scrape_erofus():
    """Start scraping Erofus URLs"""
    try:
        data = request.json
        urls = data.get("urls", [])
        # Use TARGET_DIR directly to avoid file monitor processing and overwrites
        output_dir = data.get("output_dir", config.get("SETTINGS", "TARGET", fallback="/processed"))

        if not urls:
            return jsonify({"success": False, "error": "No URLs provided"}), 400

        # Create a unique task ID
        task_id = str(uuid.uuid4())

        # Create a queue for logs and progress
        log_queue = Queue()
        progress_queue = Queue()

        # Store task info with buffered logs for reconnection
        scrape_tasks[task_id] = {
            "log_queue": log_queue,
            "progress_queue": progress_queue,
            "status": "running",
            "buffered_logs": [],
            "last_progress": {}
        }

        # Start scraping in a background thread
        def scrape_worker():
            def log_callback(msg):
                log_queue.put(msg)

            def progress_callback(data):
                progress_queue.put(data)

            try:
                for url in urls:
                    log_queue.put(f"\n{'='*60}")
                    log_queue.put(f"Processing: {url}")
                    log_queue.put('='*60)

                    scrape_erofus_url(url, output_dir, log_callback, progress_callback)

                log_queue.put("\n=== All URLs processed ===")
                scrape_tasks[task_id]["status"] = "completed"
                log_queue.put("__COMPLETED__")  # Signal completion

            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                log_queue.put(f"\n=== Error: {str(e)} ===")
                log_queue.put(f"Exception type: {type(e).__name__}")
                log_queue.put(f"Traceback:\n{error_details}")
                app_logger.error(f"Erofus scrape error: {e}\n{error_details}")
                scrape_tasks[task_id]["status"] = "error"
                log_queue.put("__ERROR__")  # Signal error

        thread = threading.Thread(target=scrape_worker, daemon=True)
        thread.start()

        return jsonify({"success": True, "task_id": task_id}), 200

    except Exception as e:
        app_logger.error(f"Error starting Erofus scrape: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/scrape-stream/<task_id>")
def scrape_stream(task_id):
    """Server-Sent Events stream for scrape logs and progress"""
    def generate():
        if task_id not in scrape_tasks:
            yield f"data: Task not found\n\n"
            return

        task = scrape_tasks[task_id]
        log_queue = task["log_queue"]
        progress_queue = task["progress_queue"]

        # Send buffered logs first (for reconnections)
        for buffered_log in task["buffered_logs"]:
            yield f"data: {buffered_log}\n\n"

        # Send last known progress
        if task["last_progress"]:
            import json
            yield f"event: progress\ndata: {json.dumps(task['last_progress'])}\n\n"

        # Keepalive counter - send keepalive every 15 seconds
        keepalive_counter = 0
        keepalive_interval = 150  # 150 * 0.1s = 15 seconds

        while True:
            has_activity = False

            # Check for log messages
            if not log_queue.empty():
                msg = log_queue.get()
                has_activity = True

                if msg == "__COMPLETED__":
                    yield f"event: completed\ndata: {{}}\n\n"
                    break
                elif msg == "__ERROR__":
                    yield f"event: error\ndata: {{}}\n\n"
                    break
                else:
                    # Buffer the log for reconnections
                    task["buffered_logs"].append(msg)
                    # Keep only last 100 log lines
                    if len(task["buffered_logs"]) > 100:
                        task["buffered_logs"].pop(0)
                    yield f"data: {msg}\n\n"

            # Check for progress updates
            if not progress_queue.empty():
                progress_data = progress_queue.get()
                has_activity = True
                # Store last progress for reconnections
                task["last_progress"] = progress_data
                import json
                yield f"event: progress\ndata: {json.dumps(progress_data)}\n\n"

            # Send keepalive if no activity
            if not has_activity:
                keepalive_counter += 1
                if keepalive_counter >= keepalive_interval:
                    yield f": keepalive\n\n"
                    keepalive_counter = 0
            else:
                keepalive_counter = 0

            time.sleep(0.1)

        # Clean up task after streaming completes
        if task_id in scrape_tasks:
            del scrape_tasks[task_id]

    return Response(generate(), mimetype="text/event-stream")

@app.route("/scrape-status")
def scrape_status():
    """Get current scrape status for badge display"""
    try:
        # Check if there are any active scrape tasks
        active_count = 0
        total_progress = 0

        for task_id, task_info in scrape_tasks.items():
            if task_info["status"] == "running":
                active_count += 1
                # Get last known progress
                if "last_progress" in task_info and "progress" in task_info["last_progress"]:
                    total_progress += task_info["last_progress"]["progress"]

        if active_count > 0:
            avg_progress = int(total_progress / active_count)
            return jsonify({
                "active": active_count,
                "progress": avg_progress
            })
        else:
            return jsonify({
                "active": 0,
                "progress": 0
            })
    except Exception as e:
        app_logger.error(f"Error getting scrape status: {e}")
        return jsonify({"active": 0, "progress": 0})

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
        config["SETTINGS"]["COMICVINE_API_KEY"] = request.form.get("comicvineApiKey", "")
        config["SETTINGS"]["GCD_METADATA_LANGUAGES"] = request.form.get("gcdLanguages", "en")
        config["SETTINGS"]["ENABLE_CUSTOM_RENAME"] = str(request.form.get("enableCustomRename") == "on")
        config["SETTINGS"]["CUSTOM_RENAME_PATTERN"] = request.form.get("customRenamePattern", "")
        config["SETTINGS"]["ENABLE_DEBUG_LOGGING"] = str(request.form.get("enableDebugLogging") == "on")

        write_config()  # Save changes to config.ini
        load_flask_config(app)  # Reload into Flask config

        # Update logger level dynamically
        import logging
        if config["SETTINGS"]["ENABLE_DEBUG_LOGGING"] == "True":
            app_logger.setLevel(logging.DEBUG)
            app_logger.info("Debug logging enabled")
        else:
            app_logger.setLevel(logging.INFO)
            app_logger.info("Debug logging disabled")

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
        comicvineApiKey=settings.get("COMICVINE_API_KEY", ""),
        gcdLanguages=settings.get("GCD_METADATA_LANGUAGES", "en"),
        enableCustomRename=settings.get("ENABLE_CUSTOM_RENAME", "False") == "True",
        customRenamePattern=settings.get("CUSTOM_RENAME_PATTERN", ""),
        enableDebugLogging=settings.get("ENABLE_DEBUG_LOGGING", "False") == "True",
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

# Function to stream logs in real-time (tail last 1000 lines to prevent timeout)
def stream_logs_file(log_file):
    with open(log_file, "r") as file:
        # Tail approach: read last N lines efficiently
        MAX_LINES = 1000
        lines = []

        # Seek to end and work backwards to find last N lines
        file.seek(0, 2)  # Go to end of file
        file_size = file.tell()

        if file_size > 0:
            # Read in chunks from the end
            buffer_size = 8192
            position = file_size

            while position > 0 and len(lines) < MAX_LINES:
                # Move back by buffer_size or to start of file
                position = max(0, position - buffer_size)
                file.seek(position)
                chunk = file.read(min(buffer_size, file_size - position))
                lines = chunk.splitlines() + lines

            # Keep only last MAX_LINES
            lines = lines[-MAX_LINES:]

            # Yield initial lines
            for line in lines:
                yield f"data: {line}\n\n"

        # Now stream new lines as they're added
        file.seek(0, 2)  # Move to end for new content
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

@app.route('/health')
def health_check():
    """Health check endpoint for Docker health check"""
    try:
        # Simple health check - verify app is responding
        return jsonify({
            "status": "healthy",
            "message": "CLU is running",
            "version": "1.0"
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500

@app.route('/gcd-status')
def gcd_status():
    """Check GCD data status"""
    try:
        gcd_data_dir = "/app/gcd_data"
        metadata_file = os.path.join(gcd_data_dir, "metadata.txt")

        status = {
            "gcd_enabled": os.environ.get('GCD_ENABLED', 'false').lower() == 'true',
            "database_configured": bool(os.environ.get('DATABASE_URL')),
            "metadata_exists": os.path.exists(metadata_file),
            "gcd_data_dir": gcd_data_dir
        }

        if status["metadata_exists"]:
            with open(metadata_file, 'r') as f:
                status["metadata"] = f.read()

        # Check for GCD data files
        if os.path.exists(gcd_data_dir):
            gcd_files = []
            for filename in os.listdir(gcd_data_dir):
                if any(pattern in filename.lower() for pattern in ['gcd', 'comics']):
                    if filename.endswith(('.sql', '.sql.gz', '.zip')):
                        file_path = os.path.join(gcd_data_dir, filename)
                        gcd_files.append({
                            "name": filename,
                            "size": os.path.getsize(file_path),
                            "modified": datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
                        })
            status["gcd_files"] = gcd_files

        return jsonify(status)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/gcd-mysql-status')
def gcd_mysql_status():
    """Check if GCD MySQL database is configured"""
    try:
        gcd_host = os.environ.get('GCD_MYSQL_HOST')
        gcd_available = bool(gcd_host and gcd_host.strip())

        return jsonify({
            "gcd_mysql_available": gcd_available,
            "gcd_host_configured": gcd_available
        })
    except Exception as e:
        return jsonify({
            "gcd_mysql_available": False,
            "gcd_host_configured": False,
            "error": str(e)
        }), 500

@app.route('/gcd-import', methods=['POST'])
def trigger_gcd_import():
    """Trigger GCD data import"""
    try:
        import subprocess

        # Run the import script
        result = subprocess.run([
            'python3', '/app/scripts/download_gcd.py', '--import'
        ], capture_output=True, text=True, timeout=3600)  # 1 hour timeout

        return jsonify({
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "Import operation timed out (1 hour limit)"
        }), 408
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/search-gcd-metadata', methods=['POST'])
def search_gcd_metadata():
    """Search GCD database for comic metadata and add to CBZ file"""
    try:
        import mysql.connector
        from datetime import datetime
        import tempfile
        import zipfile

        app_logger.info(f"🔍 GCD search started")
        data = request.get_json()
        app_logger.info(f"GCD Request data: {data}")
        file_path = data.get('file_path')
        file_name = data.get('file_name')
        is_directory_search = data.get('is_directory_search', False)
        directory_path = data.get('directory_path')
        directory_name = data.get('directory_name')
        total_files = data.get('total_files', 1)
        parent_series_name = data.get('parent_series_name')  # For nested volume processing
        volume_year = data.get('volume_year')  # For volume year parsing
        app_logger.debug(f"DEBUG: file_path={file_path}, file_name={file_name}, is_directory_search={is_directory_search}")
        app_logger.debug(f"DEBUG: directory_path={directory_path}, directory_name={directory_name}")
        app_logger.debug(f"DEBUG: parent_series_name={parent_series_name}, volume_year={volume_year}")

        if not file_path or not file_name:
            return jsonify({
                "success": False,
                "error": "Missing file_path or file_name"
            }), 400

        # For directory search, prefer directory name parsing, fallback to file name
        if is_directory_search and directory_name:
            name_without_ext = directory_name
            app_logger.debug(f"DEBUG: Using directory name for parsing: {name_without_ext}")
        else:
            # Parse series name and issue from filename
            name_without_ext = file_name
            for ext in ('.cbz', '.cbr', '.zip'):
                name_without_ext = name_without_ext.replace(ext, '')

            app_logger.debug(f"DEBUG: Using file name for parsing: {name_without_ext}")

        # Try to parse series and issue from common formats
        series_name = None
        issue_number = None
        year = None
        issue_number_was_defaulted = False  # Track if we defaulted the issue number

        if is_directory_search:
            # Check if this is a volume directory (e.g., v2015) that needs parent series name
            volume_directory_match = re.match(r'^v(\d{4})$', name_without_ext, re.IGNORECASE)

            if volume_directory_match and parent_series_name:
                # Approach 2: Volume directory getting series name from parent
                series_name = parent_series_name
                year = int(volume_directory_match.group(1))
                app_logger.debug(f"DEBUG: Volume directory detected - using parent series '{series_name}' with year {year}")
            elif parent_series_name and volume_year:
                # Approach 1: Nested volume processing with explicit parent name and year
                series_name = parent_series_name
                year = int(volume_year)
                app_logger.debug(f"DEBUG: Nested volume processing - series='{series_name}', year={year}")
            else:
                # Standard directory processing
                directory_patterns = [
                    r'^(.+?)\s+\((\d{4})\)',  # "Series Name (2020)"
                    r'^(.+?)\s+(\d{4})',      # "Series Name 2020"
                    r'^(.+?)\s+v\d+\s+\((\d{4})\)', # "Series v1 (2020)"
                ]

                for pattern in directory_patterns:
                    match = re.match(pattern, name_without_ext, re.IGNORECASE)
                    if match:
                        series_name = match.group(1).strip()
                        year = int(match.group(2)) if len(match.groups()) >= 2 else None
                        app_logger.debug(f"DEBUG: Directory parsed - series_name={series_name}, year={year}")
                        break

                # If no year pattern matched, just use the whole directory name as series
                if not series_name:
                    series_name = name_without_ext.strip()
                    app_logger.debug(f"DEBUG: Directory fallback - series_name={series_name}")

            # For directory search, parse issue number from the first file name
            file_name_without_ext = file_name
            for ext in ('.cbz', '.cbr', '.zip'):
                file_name_without_ext = file_name_without_ext.replace(ext, '')
            app_logger.debug(f"DEBUG: Parsing issue number from first file: {file_name_without_ext}")

            # Try multiple patterns to extract issue number from the first file
            issue_patterns = [
                r'(?:^|\s)(\d{1,4})(?:\s*\(|\s*$|\s*\.)',     # Standard: "Series 123 (year)" or "Series 123.cbz"
                r'(?:^|\s)#(\d{1,4})(?:\s|$)',                 # Hash prefix: "Series #123"
                r'(?:issue\s*)(\d{1,4})',                      # Issue prefix: "Series Issue 123"
                r'(?:no\.?\s*)(\d{1,4})',                      # No. prefix: "Series No. 123"
                r'(?:vol\.\s*\d+\s+)(\d{1,4})',                # Volume and issue: "Series Vol. 1 123"
            ]

            for pattern in issue_patterns:
                match = re.search(pattern, file_name_without_ext, re.IGNORECASE)
                if match:
                    issue_number = int(match.group(1))  # Handles '0', '00', '000' -> 0
                    if issue_number == 0:
                        app_logger.debug(f"DEBUG: Extracted issue number {issue_number} (zero/variant issue) from filename using pattern: {pattern}")
                    else:
                        app_logger.debug(f"DEBUG: Extracted issue number {issue_number} from filename using pattern: {pattern}")
                    break

            if issue_number is None:
                issue_number = 1  # Ultimate fallback
                app_logger.debug(f"DEBUG: Could not parse issue number from filename, defaulting to 1")
        else:
            # Pattern matching for common comic filename formats
            patterns = [
                r'^(.+?)\s+(\d{3,4})\s+\((\d{4})\)',  # "Series 001 (2020)"
                r'^(.+?)\s+#?(\d{1,4})\s*\((\d{4})\)', # "Series #1 (2020)" or "Series 1 (2020)"
                r'^(.+?)\s+v\d+\s+(\d{1,4})\s*\((\d{4})\)', # "Series v1 001 (2020)"
                r'^(.+?)\s+(\d{1,4})\s+\(of\s+\d+\)\s+\((\d{4})\)', # "Series 05 (of 12) (2020)"
                r'^(.+?)\s+#?(\d{1,4})$',  # "Series 169" or "Series #169" (no year)
            ]

            for pattern in patterns:
                match = re.match(pattern, name_without_ext, re.IGNORECASE)
                if match:
                    series_name = match.group(1).strip()
                    issue_number = int(match.group(2))  # Handles '0', '00', '000' -> 0
                    year = int(match.group(3)) if len(match.groups()) >= 3 else None
                    if issue_number == 0:
                        app_logger.debug(f"DEBUG: File parsed - series_name={series_name}, issue_number={issue_number} (zero/variant issue), year={year}")
                    else:
                        app_logger.debug(f"DEBUG: File parsed - series_name={series_name}, issue_number={issue_number}, year={year}")
                    break

            # If no pattern matched, try to parse as single-issue/graphic novel with just year
            if not series_name:
                # Pattern for single-issue series: "Series Name (2020)" or "Series Name: Subtitle (2020)"
                single_issue_pattern = r'^(.+?)\s*\((\d{4})\)$'
                match = re.match(single_issue_pattern, name_without_ext, re.IGNORECASE)
                if match:
                    series_name = match.group(1).strip()
                    year = int(match.group(2))
                    issue_number = 1  # Default to issue 1 for single-issue series/graphic novels
                    issue_number_was_defaulted = True  # Mark that we defaulted this
                    app_logger.debug(f"DEBUG: Single-issue/graphic novel parsed - series_name={series_name}, year={year}, issue_number={issue_number} (defaulted)")

            # Ultimate fallback: if still no series_name, use the entire filename as series name
            if not series_name:
                series_name = name_without_ext.strip()
                issue_number = 1  # Default to issue 1
                issue_number_was_defaulted = True
                app_logger.debug(f"DEBUG: Fallback parsing - using entire filename as series_name={series_name}, issue_number={issue_number} (defaulted)")

        if not series_name or (not is_directory_search and issue_number is None):
            app_logger.debug(f"DEBUG: Failed to parse: {name_without_ext}")
            return jsonify({
                "success": False,
                "error": f"Could not parse series name from: {name_without_ext}"
            }), 400

        app_logger.debug(f"DEBUG: About to connect to database...")
        # Connect to GCD MySQL database
        try:
            # Get database connection details from environment variables
            gcd_host = os.environ.get('GCD_MYSQL_HOST')
            gcd_port = int(os.environ.get('GCD_MYSQL_PORT'))
            gcd_database = os.environ.get('GCD_MYSQL_DATABASE')
            gcd_user = os.environ.get('GCD_MYSQL_USER')
            gcd_password = os.environ.get('GCD_MYSQL_PASSWORD')

            connection = mysql.connector.connect(
                host=gcd_host,
                port=gcd_port,
                database=gcd_database,
                user=gcd_user,
                password=gcd_password,
                charset='utf8mb4',
                connection_timeout=30,  # 30 second connection timeout
                autocommit=True
            )
            app_logger.debug(f"DEBUG: Database connection successful!")
            cursor = connection.cursor(dictionary=True)
            # Set query timeout to 30 seconds
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=30000")  # 30000 milliseconds = 30 seconds

            # Helper: build safe IN (...) placeholder list + params
            def build_in_clause(codes):
                codes = list(codes or [])
                if not codes:
                    return 'NULL', []            # produces "IN (NULL)" -> matches nothing
                return ','.join(['%s'] * len(codes)), codes

            # Progressive search strategy for GCD database
            app_logger.debug(f"DEBUG: Starting progressive search for series: '{series_name}' with year: {year}")

            # Generate search variations
            search_variations = generate_search_variations(series_name, year)
            app_logger.debug(f"DEBUG: Generated {len(search_variations)} search variations")
            app_logger.debug(f"DEBUG: Checkpoint 1 - About to initialize variables")

            series_results = []
            search_success_method = None
            app_logger.debug(f"DEBUG: Checkpoint 2 - Variables initialized")

            # Language filter
            languages = [language.strip().lower() for language in config.get("SETTINGS", "GCD_METADATA_LANGUAGES", fallback="en").split(",")]
            app_logger.debug(f"DEBUG: Checkpoint 3 - languages set")
            app_logger.debug(f"DEBUG: Building IN clause for language filter with codes: {languages}")
            in_clause, in_params = build_in_clause(languages)
            app_logger.debug(f"DEBUG: IN clause built: {in_clause}, params: {in_params}")

            # Base queries for LIKE and REGEXP matching
            like_query = f"""
                SELECT
                    s.id,
                    s.name,
                    s.year_began,
                    s.year_ended,
                    s.publisher_id,
                    l.code AS language,
                    p.name AS publisher_name,
                    (SELECT COUNT(*) FROM gcd_issue i WHERE i.series_id = s.id) AS issue_count
                FROM gcd_series s
                JOIN stddata_language l ON s.language_id = l.id
                LEFT JOIN gcd_publisher p ON s.publisher_id = p.id
                WHERE s.name LIKE %s
                    AND l.code IN ({in_clause})
                ORDER BY s.year_began DESC
            """

            like_query_with_year = f"""
                SELECT
                    s.id,
                    s.name,
                    s.year_began,
                    s.year_ended,
                    s.publisher_id,
                    l.code AS language,
                    p.name AS publisher_name,
                    (SELECT COUNT(*) FROM gcd_issue i WHERE i.series_id = s.id) AS issue_count
                FROM gcd_series s
                JOIN stddata_language l ON s.language_id = l.id
                LEFT JOIN gcd_publisher p ON s.publisher_id = p.id
                WHERE s.name LIKE %s
                    AND s.year_began <= %s
                    AND (s.year_ended IS NULL OR s.year_ended >= %s)
                    AND l.code IN ({in_clause})
                ORDER BY s.year_began DESC
            """

            regexp_query = f"""
                SELECT
                    s.id,
                    s.name,
                    s.year_began,
                    s.year_ended,
                    s.publisher_id,
                    l.code AS language,
                    p.name AS publisher_name,
                    (SELECT COUNT(*) FROM gcd_issue i WHERE i.series_id = s.id) AS issue_count
                FROM gcd_series s
                JOIN stddata_language l ON s.language_id = l.id
                LEFT JOIN gcd_publisher p ON s.publisher_id = p.id
                WHERE LOWER(s.name) REGEXP %s
                    AND l.code IN ({in_clause})
                ORDER BY s.year_began DESC
            """

            # Try each search variation progressively
            app_logger.debug(f"DEBUG: Starting search loop with {len(search_variations)} variations")
            for search_type, search_pattern in search_variations:
                app_logger.debug(f"DEBUG: Trying {search_type} search with pattern: {search_pattern}")

                try:
                    if search_type == "tokenized":
                        # Use REGEXP for tokenized search (pattern should be lowercase for LOWER(s.name))
                        cursor.execute(regexp_query, (search_pattern.lower(), *in_params))

                    elif year and search_type in ["exact", "no_issue", "no_year", "no_dash"]:
                        # Year-constrained search when year is available
                        cursor.execute(like_query_with_year, (search_pattern, year, year, *in_params))

                    else:
                        # Regular LIKE search
                        cursor.execute(like_query, (search_pattern, *in_params))

                    current_results = cursor.fetchall()
                    app_logger.debug(f"DEBUG: {search_type} search found {len(current_results)} results")

                    if current_results:
                        series_results = current_results
                        search_success_method = search_type
                        app_logger.debug(f"DEBUG: Success with {search_type} search method!")
                        break

                except Exception as e:
                    app_logger.debug(f"DEBUG: Error in {search_type} search: {str(e)}")
                    continue

            # If we still have no results, collect all partial matches for user selection
            if not series_results:
                app_logger.debug(f"DEBUG: No matches found with any search method, collecting partial matches...")
                alternative_matches = []

                # Try broader word-based search as final fallback
                words = series_name.split()
                for word in words:
                    if len(word) > 3 and word.lower() not in STOPWORDS:
                        try:
                            alt_search = f"%{word}%"
                            app_logger.debug(f"DEBUG: Trying fallback word search: {alt_search}")
                            cursor.execute(like_query, (alt_search, *in_params))
                            alt_results = cursor.fetchall()
                            if alt_results:
                                alternative_matches.extend(alt_results)
                        except Exception as e:
                            app_logger.debug(f"DEBUG: Error in fallback search for '{word}': {str(e)}")

                # Remove duplicates and sort
                seen_ids = set()
                unique_matches = []
                for match in alternative_matches:
                    if match['id'] not in seen_ids:
                        unique_matches.append(match)
                        seen_ids.add(match['id'])

                unique_matches.sort(key=lambda x: x['year_began'] or 0, reverse=True)

                if unique_matches:
                    app_logger.debug(f"DEBUG: Found {len(unique_matches)} fallback matches")
                    response_data = {
                        "success": False,
                        "requires_selection": True,
                        "parsed_filename": {
                            "series_name": series_name,
                            "issue_number": issue_number,
                            "year": year
                        },
                        "possible_matches": unique_matches,
                        "message": "Multiple series found. Please select the correct one."
                    }

                    if is_directory_search:
                        response_data["is_directory_search"] = True
                        response_data["directory_path"] = directory_path
                        response_data["directory_name"] = directory_name
                        response_data["total_files"] = total_files

                    return jsonify(response_data), 200

                return jsonify({
                    "success": False,
                    "error": f"No series found matching '{series_name}' in GCD database"
                }), 404

            # Analyze the search results and decide whether to auto-select or prompt user
            app_logger.debug(f"DEBUG: Analyzing {len(series_results)} series results for matching...")
            app_logger.debug(f"DEBUG: Search successful using method: {search_success_method}")

            if len(series_results) == 1:
                # Only one series found - auto-select it
                best_series = series_results[0]
                app_logger.debug(f"DEBUG: Single series match found: {best_series['name']} (ID: {best_series['id']}) using {search_success_method} search")
            elif len(series_results) > 1:
                # Multiple series found - always prompt user to select
                app_logger.debug(f"DEBUG: Multiple series found, showing options for user selection")
                response_data = {
                    "success": False,
                    "requires_selection": True,
                    "parsed_filename": {
                        "series_name": series_name,
                        "issue_number": issue_number,
                        "year": year
                    },
                    "possible_matches": series_results,
                    "search_method": search_success_method,
                    "message": f"Multiple series found for '{series_name}' using {search_success_method} search. Please select the correct one."
                }

                # Add directory info for directory searches
                if is_directory_search:
                    response_data["is_directory_search"] = True
                    response_data["directory_path"] = directory_path
                    response_data["directory_name"] = directory_name
                    response_data["total_files"] = total_files

                return jsonify(response_data), 200
            else:
                # This shouldn't happen since we already checked for no results above
                app_logger.debug(f"DEBUG: No series results found (unexpected)")
                return jsonify({
                    "success": False,
                    "error": f"No series found matching '{series_name}' in GCD database"
                }), 404

            # OPTIMIZED: Split into 3 smaller queries for better performance
            app_logger.debug(f"DEBUG: Searching for issue #{issue_number} in series ID {best_series['id']}...")

            # Query 1: Basic issue information (fast, no subqueries)
            # When issue_number_was_defaulted, also check for [nn] which GCD uses for one-shot comics
            # Note: issue_number can be 0, which is valid and used for variants/special editions
            if issue_number_was_defaulted:
                app_logger.debug(f"DEBUG: Issue number was defaulted, also searching for [nn] (one-shot comics)")
                basic_issue_query = """
                    SELECT
                        i.id,
                        i.title,
                        i.number,
                        i.volume,
                        i.rating AS AgeRating,
                        i.page_count,
                        i.page_count_uncertain,
                        i.key_date,
                        i.on_sale_date,
                        sr.id AS series_id,
                        sr.name AS Series,
                        l.code AS language,
                        COALESCE(ip.name, p.name) AS Publisher,
                        (SELECT COUNT(*) FROM gcd_issue i2 WHERE i2.series_id = i.series_id AND i2.deleted = 0) AS Count
                    FROM gcd_issue i
                    JOIN gcd_series sr ON sr.id = i.series_id
                    JOIN stddata_language l ON l.id = sr.language_id
                    LEFT JOIN gcd_publisher p ON p.id = sr.publisher_id
                    LEFT JOIN gcd_indicia_publisher ip ON ip.id = i.indicia_publisher_id
                    WHERE i.series_id = %s AND (i.number = %s OR i.number = CONCAT('[', %s, ']') OR i.number LIKE CONCAT(%s, ' (%') OR i.number = '[nn]')
                    LIMIT 1
                """
            else:
                basic_issue_query = """
                    SELECT
                        i.id,
                        i.title,
                        i.number,
                        i.volume,
                        i.rating AS AgeRating,
                        i.page_count,
                        i.page_count_uncertain,
                        i.key_date,
                        i.on_sale_date,
                        sr.id AS series_id,
                        sr.name AS Series,
                        l.code AS language,
                        COALESCE(ip.name, p.name) AS Publisher,
                        (SELECT COUNT(*) FROM gcd_issue i2 WHERE i2.series_id = i.series_id AND i2.deleted = 0) AS Count
                    FROM gcd_issue i
                    JOIN gcd_series sr ON sr.id = i.series_id
                    JOIN stddata_language l ON l.id = sr.language_id
                    LEFT JOIN gcd_publisher p ON p.id = sr.publisher_id
                    LEFT JOIN gcd_indicia_publisher ip ON ip.id = i.indicia_publisher_id
                    WHERE i.series_id = %s AND (i.number = %s OR i.number = CONCAT('[', %s, ']') OR i.number LIKE CONCAT(%s, ' (%'))
                    LIMIT 1
                """

            # Convert issue_number to string for SQL query (handles 0 correctly)
            issue_number_str = str(issue_number)
            app_logger.debug(f"DEBUG: Querying for issue_number_str='{issue_number_str}' (includes checks for '{issue_number_str}', '[{issue_number_str}]', '{issue_number_str} (%')")
            cursor.execute(basic_issue_query, (best_series['id'], issue_number_str, issue_number_str, issue_number_str))
            issue_basic = cursor.fetchone()

            if not issue_basic:
                app_logger.debug(f"DEBUG: Issue #{issue_number} not found in series")

                # If the issue number was defaulted and we have exactly one series match,
                # check if this is a single-issue series and get the only issue
                if issue_number_was_defaulted and len(series_results) == 1:
                    app_logger.debug(f"DEBUG: Checking if this is a single-issue series...")

                    # Count total issues in this series
                    count_query = "SELECT COUNT(*) as total FROM gcd_issue WHERE series_id = %s AND deleted = 0"
                    cursor.execute(count_query, (best_series['id'],))
                    count_result = cursor.fetchone()
                    total_issues = count_result['total'] if count_result else 0

                    app_logger.debug(f"DEBUG: Series has {total_issues} total issue(s)")

                    if total_issues == 1:
                        # This is a single-issue series, get the only issue regardless of its number
                        app_logger.debug(f"DEBUG: Single-issue series detected, fetching the only issue...")

                        single_issue_query = """
                            SELECT
                                i.id,
                                i.title,
                                i.number,
                                i.volume,
                                i.rating AS AgeRating,
                                i.page_count,
                                i.page_count_uncertain,
                                i.key_date,
                                i.on_sale_date,
                                sr.id AS series_id,
                                sr.name AS Series,
                                l.code AS language,
                                COALESCE(ip.name, p.name) AS Publisher,
                                (SELECT COUNT(*) FROM gcd_issue i2 WHERE i2.series_id = i.series_id AND i2.deleted = 0) AS Count
                            FROM gcd_issue i
                            JOIN gcd_series sr ON sr.id = i.series_id
                            JOIN stddata_language l ON l.id = sr.language_id
                            LEFT JOIN gcd_publisher p ON p.id = sr.publisher_id
                            LEFT JOIN gcd_indicia_publisher ip ON ip.id = i.indicia_publisher_id
                            WHERE i.series_id = %s AND i.deleted = 0
                            LIMIT 1
                        """

                        cursor.execute(single_issue_query, (best_series['id'],))
                        issue_basic = cursor.fetchone()

                        if issue_basic:
                            app_logger.debug(f"DEBUG: Found single issue with number: {issue_basic['number']}")
                            # Continue with normal processing using this issue
                        else:
                            app_logger.debug(f"DEBUG: Failed to fetch the single issue")
                            issue_result = None
                    else:
                        issue_result = None
                # For directory searches, if the specific issue isn't found, return series info
                # so that other files in the directory can be processed
                elif is_directory_search:
                    app_logger.debug(f"DEBUG: Directory search - issue #{issue_number} not found, but returning series info for continued processing")
                    return jsonify({
                        "success": True,
                        "issue_not_found": True,
                        "series_found": True,
                        "series_id": best_series['id'],
                        "series_name": best_series['name'],
                        "is_directory_search": True,
                        "directory_path": directory_path,
                        "directory_name": directory_name,
                        "total_files": total_files,
                        "message": f"Issue #{issue_number} not found, but series '{best_series['name']}' found. Continuing with other files."
                    }), 200
                else:
                    issue_result = None

            # Process the issue if we found it (either by exact match or single-issue fallback)
            if issue_basic:
                app_logger.debug(f"DEBUG: Basic issue info retrieved for issue #{issue_number}")
                issue_id = issue_basic['id']

                # Query 2: Get all credits in a single query (much faster than multiple subqueries)
                credits_query = """
                    SELECT
                        ct.name AS credit_type,
                        TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS creator_name,
                        s.sequence_number
                    FROM gcd_story s
                    JOIN gcd_story_credit sc ON sc.story_id = s.id
                    JOIN gcd_credit_type ct ON ct.id = sc.credit_type_id
                    LEFT JOIN gcd_creator c ON c.id = sc.creator_id
                    WHERE s.issue_id = %s
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                        AND NULLIF(TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)), '') IS NOT NULL
                    UNION
                    SELECT
                        ct.name AS credit_type,
                        TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS creator_name,
                        NULL AS sequence_number
                    FROM gcd_issue_credit ic
                    JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                    LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                    WHERE ic.issue_id = %s
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                        AND NULLIF(TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)), '') IS NOT NULL
                """

                cursor.execute(credits_query, (issue_id, issue_id))
                credits = cursor.fetchall()

                # Query 3: Story details (title, summary, genre, characters, page count)
                story_query = """
                    SELECT
                        NULLIF(TRIM(s.title), '') AS title,
                        NULLIF(TRIM(s.synopsis), '') AS synopsis,
                        NULLIF(TRIM(s.notes), '') AS notes,
                        NULLIF(TRIM(s.genre), '') AS genre,
                        NULLIF(TRIM(s.characters), '') AS characters,
                        s.page_count,
                        s.sequence_number,
                        st.name AS story_type
                    FROM gcd_story s
                    LEFT JOIN gcd_story_type st ON st.id = s.type_id
                    WHERE s.issue_id = %s
                    ORDER BY
                        CASE WHEN s.sequence_number = 0 THEN 1 ELSE 0 END,
                        CASE
                            WHEN LOWER(st.name) IN ('comic story','story') THEN 0
                            WHEN LOWER(st.name) IN ('text story','text') THEN 1
                            ELSE 3
                        END,
                        s.sequence_number
                """

                cursor.execute(story_query, (issue_id,))
                stories = cursor.fetchall()

                # Query 4: Character names from character table
                characters_query = """
                    SELECT DISTINCT c.name
                    FROM gcd_story s
                    LEFT JOIN gcd_story_character sc ON sc.story_id = s.id
                    LEFT JOIN gcd_character c ON c.id = sc.character_id
                    WHERE s.issue_id = %s AND c.name IS NOT NULL
                """

                cursor.execute(characters_query, (issue_id,))
                character_results = cursor.fetchall()

                # Process credits in Python (faster than 6 separate subqueries)
                credits_dict = {
                    'Writer': set(),
                    'Penciller': set(),
                    'Inker': set(),
                    'Colorist': set(),
                    'Letterer': set(),
                    'CoverArtist': set()
                }

                for credit in credits:
                    ct_lower = credit['credit_type'].lower()
                    seq_num = credit['sequence_number']
                    name = credit['creator_name']

                    # Writer
                    if any(x in ct_lower for x in ['script', 'writer', 'plot']):
                        if seq_num is None or seq_num != 0:
                            credits_dict['Writer'].add(name)
                    # Penciller
                    elif 'pencil' in ct_lower or 'penc' in ct_lower:
                        if seq_num is None or seq_num != 0:
                            credits_dict['Penciller'].add(name)
                    # Inker
                    elif 'ink' in ct_lower:
                        if seq_num is None or seq_num != 0:
                            credits_dict['Inker'].add(name)
                    # Colorist
                    elif 'color' in ct_lower or 'colour' in ct_lower:
                        if seq_num is None or seq_num != 0:
                            credits_dict['Colorist'].add(name)
                    # Letterer
                    elif 'letter' in ct_lower:
                        if seq_num is None or seq_num != 0:
                            credits_dict['Letterer'].add(name)
                    # Cover Artist
                    elif 'cover' in ct_lower or (seq_num == 0 and any(x in ct_lower for x in ['pencil', 'penc', 'ink', 'art'])):
                        credits_dict['CoverArtist'].add(name)

                # Convert sets to sorted comma-separated strings
                for key in credits_dict:
                    credits_dict[key] = ', '.join(sorted(credits_dict[key])) if credits_dict[key] else None

                # Process story details
                title = issue_basic['title']
                summary = None
                genres = set()
                characters_text = set()
                page_count_sum = 0

                for story in stories:
                    # Get title from first non-zero sequence story if issue title is empty
                    if not title and story['title'] and (story['sequence_number'] is None or story['sequence_number'] != 0):
                        title = story['title']

                    # Get summary (prefer synopsis > notes > title)
                    if not summary and (story['sequence_number'] is None or story['sequence_number'] != 0):
                        summary = story['synopsis'] or story['notes'] or story['title']

                    # Collect genres
                    if story['genre']:
                        for g in story['genre'].replace(';', ',').split(','):
                            g = g.strip()
                            if g:
                                genres.add(g)

                    # Collect characters
                    if story['characters']:
                        for ch in story['characters'].replace(';', ',').split(','):
                            ch = ch.strip()
                            if ch:
                                characters_text.add(ch)

                    # Sum page counts
                    if story['page_count']:
                        page_count_sum += float(story['page_count'])

                # Add character names from character table
                for char_row in character_results:
                    if char_row['name']:
                        characters_text.add(char_row['name'])

                # Calculate dates
                date_str = issue_basic['key_date'] or issue_basic['on_sale_date']
                year = None
                month = None
                if date_str and len(date_str) >= 4:
                    year = int(date_str[0:4])
                    if len(date_str) >= 7:
                        month = int(date_str[5:7])

                # Calculate page count
                page_count = None
                if issue_basic['page_count'] and issue_basic['page_count'] > 0 and not issue_basic['page_count_uncertain']:
                    page_count = issue_basic['page_count']
                elif page_count_sum > 0:
                    page_count = round(page_count_sum)

                # Build final result dictionary matching the original structure
                issue_result = {
                    'id': issue_id,
                    'Title': title,
                    'Series': issue_basic['Series'],
                    'Number': issue_basic['number'],
                    'Count': issue_basic['Count'],
                    'Volume': issue_basic['volume'],
                    'Summary': summary,
                    'Year': year,
                    'Month': month,
                    'Writer': credits_dict['Writer'],
                    'Penciller': credits_dict['Penciller'],
                    'Inker': credits_dict['Inker'],
                    'Colorist': credits_dict['Colorist'],
                    'Letterer': credits_dict['Letterer'],
                    'CoverArtist': credits_dict['CoverArtist'],
                    'Publisher': issue_basic['Publisher'],
                    'Genre': ', '.join(sorted(genres)) if genres else None,
                    'Characters': ', '.join(sorted(characters_text)) if characters_text else None,
                    'AgeRating': issue_basic['AgeRating'],
                    'LanguageISO': issue_basic['language'],
                    'PageCount': page_count
                }
            else:
                # If we still don't have issue_basic after all attempts, set issue_result to None
                issue_result = None

            app_logger.debug(f"DEBUG: Issue search result: {'Found' if issue_result else 'Not found'}")
            if issue_result:
                #print(f"DEBUG: Issue result keys: {list(issue_result.keys())}")
                #print(f"DEBUG: Issue result values: {dict(issue_result)}")
                #print(f"DEBUG: Writer value: '{issue_result.get('Writer')}'")
                app_logger.debug(f"DEBUG: Summary value: '{issue_result.get('Summary')}'")
                #print(f"DEBUG: Characters value: '{issue_result.get('Characters')}'")

            matches_found = len(series_results)

            if issue_result:
                app_logger.debug(f"DEBUG: Issue found! Title: {issue_result.get('title', 'N/A')}")

                # Check if ComicInfo.xml already exists and has Notes data
                try:
                    from comicinfo import read_comicinfo_from_zip
                    existing_comicinfo = read_comicinfo_from_zip(file_path)
                    existing_notes = existing_comicinfo.get('Notes', '').strip()

                    if existing_notes:
                        app_logger.info(f"Skipping ComicInfo.xml generation - file already has Notes data: {existing_notes[:50]}...")

                        # For directory searches, return series_id so processing can continue with other files
                        if is_directory_search:
                            response_data = {
                                "success": True,
                                "skipped": True,
                                "message": "ComicInfo.xml already exists with Notes data",
                                "existing_notes": existing_notes,
                                "series_id": best_series['id'],
                                "is_directory_search": True,
                                "directory_path": directory_path,
                                "directory_name": directory_name,
                                "total_files": total_files
                            }
                            return jsonify(response_data), 200
                        else:
                            return jsonify({
                                "success": True,
                                "skipped": True,
                                "message": "ComicInfo.xml already exists with Notes data",
                                "existing_notes": existing_notes
                            }), 200
                except Exception as check_error:
                    app_logger.debug(f"DEBUG: Error checking existing ComicInfo.xml (will proceed with generation): {str(check_error)}")

                # Generate ComicInfo.xml content
                app_logger.debug(f"DEBUG: Generating ComicInfo.xml...")
                try:
                    comicinfo_xml = generate_comicinfo_xml(issue_result, best_series)
                    app_logger.debug(f"DEBUG: ComicInfo.xml generated successfully (length: {len(comicinfo_xml)} chars)")
                except Exception as xml_error:
                    app_logger.debug(f"DEBUG: Error generating ComicInfo.xml: {str(xml_error)}")
                    import traceback
                    app_logger.debug(f"DEBUG: XML Error Traceback: {traceback.format_exc()}")
                    return jsonify({
                        "success": False,
                        "error": f"Failed to generate metadata: {str(xml_error)}"
                    }), 500

                # Add ComicInfo.xml to the CBZ file
                app_logger.debug(f"DEBUG: Adding ComicInfo.xml to CBZ file: {file_path}")
                try:
                    add_comicinfo_to_cbz(file_path, comicinfo_xml)
                    app_logger.debug(f"DEBUG: Successfully added ComicInfo.xml!")
                except Exception as cbz_error:
                    app_logger.debug(f"DEBUG: Error adding ComicInfo.xml: {str(cbz_error)}")
                    import traceback
                    app_logger.debug(f"DEBUG: CBZ Error Traceback: {traceback.format_exc()}")
                    return jsonify({
                        "success": False,
                        "error": f"Failed to add metadata to CBZ file: {str(cbz_error)}"
                    }), 500

                app_logger.debug(f"DEBUG: Returning success response...")
                response_data = {
                    "success": True,
                    "metadata": {
                        "series": issue_result['Series'],
                        "issue": issue_result['Number'],
                        "title": issue_result['Title'],
                        "publisher": issue_result['Publisher'],
                        "year": issue_result['Year'],
                        "month": issue_result['Month'],
                        "page_count": issue_result['PageCount'],
                        "writer": issue_result.get('Writer'),
                        "artist": issue_result.get('Penciller'),
                        "genre": issue_result.get('Genre'),
                        "characters": issue_result.get('Characters')
                    },
                    "matches_found": matches_found
                }

                # Add series_id for directory searches to enable bulk processing
                if is_directory_search:
                    response_data["series_id"] = best_series['id']
                    response_data["is_directory_search"] = True
                    response_data["directory_path"] = directory_path
                    response_data["directory_name"] = directory_name
                    response_data["total_files"] = total_files

                return jsonify(response_data)
            else:
                app_logger.debug(f"DEBUG: Issue #{issue_number} not found for series '{best_series['name']}'")
                app_logger.debug(f"DEBUG: Returning 404 response...")
                return jsonify({
                    "success": False,
                    "error": f"Issue #{issue_number} not found for series '{best_series['name']}' in GCD database",
                    "series_found": best_series['name'],
                    "matches_found": matches_found
                }), 404

        except mysql.connector.Error as db_error:
            import traceback
            app_logger.debug(f"MySQL Error: {str(db_error)}")
            app_logger.debug(f"MySQL Error Traceback: {traceback.format_exc()}")
            return jsonify({
                "success": False,
                "error": f"Database connection error: {str(db_error)}"
            }), 500
        finally:
            if 'connection' in locals() and connection.is_connected():
                cursor.close()
                connection.close()

    except Exception as e:
        import traceback
        error_msg = str(e)
        error_traceback = traceback.format_exc()
        app_logger.error(f"ERROR in search_gcd_metadata: {error_msg}")
        app_logger.debug(f"Full Traceback:\n{error_traceback}")
        return jsonify({
            "success": False,
            "error": f"Server error: {error_msg}"
        }), 500

def _as_text(val):
    if val is None:
        return None
    if isinstance(val, (list, tuple, set)):
        # ComicInfo expects comma-separated for multi-credits
        return ", ".join(str(x) for x in val if x is not None and str(x).strip())
    return str(val)

def generate_comicinfo_xml(issue_data, series_data=None):
    """
    Generate a ComicInfo.xml that ComicRack will actually read.
    - No XML namespaces
    - UTF-8 bytes with XML declaration
    - Only write elements when we have non-empty values
    - Ensure numeric fields are integers-as-text
    """
    root = ET.Element("ComicInfo")  # IMPORTANT: no xmlns/xsi attributes

    def add(tag, value):
        val = _as_text(value)
        if val:
            ET.SubElement(root, tag).text = val

    # Basic
    add("Title",   issue_data.get("Title"))
    add("Series",  issue_data.get("Series"))
    # Number/Count/Volume should be simple numerics-as-text
    if issue_data.get("Number") not in (None, ""):
        add("Number", str(int(float(issue_data["Number"]))) if str(issue_data["Number"]).replace(".","",1).isdigit() else str(issue_data["Number"]))
    if issue_data.get("Count") not in (None, ""):
        add("Count", str(int(issue_data["Count"])) )
    if issue_data.get("Volume") not in (None, ""):
        add("Volume", str(int(issue_data["Volume"])) )

    add("Summary", issue_data.get("Summary"))

    # Dates
    if issue_data.get("Year") not in (None, ""):
        add("Year", str(int(issue_data["Year"])))
    if issue_data.get("Month") not in (None, ""):
        m = int(issue_data["Month"])
        if 1 <= m <= 12:
            add("Month", str(m))

    # Credits
    add("Writer",      issue_data.get("Writer"))
    add("Penciller",   issue_data.get("Penciller"))
    add("Inker",       issue_data.get("Inker"))
    add("Colorist",    issue_data.get("Colorist"))
    add("Letterer",    issue_data.get("Letterer"))
    add("CoverArtist", issue_data.get("CoverArtist"))

    # Publisher/Imprint
    add("Publisher", issue_data.get("Publisher"))

    # Genre/Characters
    add("Genre",      issue_data.get("Genre"))
    add("Characters", issue_data.get("Characters"))

    # Language (ComicRack likes LanguageISO, e.g., 'en')
    add("LanguageISO", issue_data.get("LanguageISO") or "en")

    # Page count (integer)
    if issue_data.get("PageCount") not in (None, ""):
        add("PageCount", str(int(issue_data["PageCount"])))

    # Manga flag: ComicRack expects "Yes" or "No"
    add("Manga", "No")

    # Notes - use provided Notes if available (e.g., from ComicVine), otherwise generate GCD notes
    if issue_data.get("Notes"):
        add("Notes", issue_data.get("Notes"))
    else:
        # Default to GCD format for backward compatibility
        notes = f"Metadata from Grand Comic Database (GCD). Issue ID: {issue_data.get('id', 'Unknown')} — retrieved {datetime.now():%Y-%m-%d}."
        add("Notes", notes)

    # Pretty-print and serialize as UTF-8 BYTES (not a Python str)
    ET.indent(root)  # Python 3.9+
    tree = ET.ElementTree(root)
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()  # BYTES


def add_comicinfo_to_cbz(file_path, comicinfo_xml_bytes):
    """
    Writes ComicInfo.xml at the ROOT of the CBZ.
    - Removes any existing ComicInfo.xml (case-insensitive)
    - Uses UTF-8 bytes for content
    - Rebuilds the entire ZIP by extracting and recompressing (matches single_file.py approach)
    - Handles RAR files incorrectly named as CBZ
    """
    import tempfile, shutil
    from single_file import convert_single_rar_file

    # Safety: ensure bytes
    if isinstance(comicinfo_xml_bytes, str):
        comicinfo_xml_bytes = comicinfo_xml_bytes.encode("utf-8")

    # Create temp directory and file in the same directory as the source file
    file_dir = os.path.dirname(file_path) or '.'
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    # Create temporary extraction directory
    temp_extract_dir = os.path.join(file_dir, f".tmp_extract_{base_name}_{os.getpid()}")
    temp_zip_path = os.path.join(file_dir, f".tmp_{base_name}_{os.getpid()}.cbz")

    try:
        # Step 1: Extract all files to temporary directory
        os.makedirs(temp_extract_dir, exist_ok=True)

        with zipfile.ZipFile(file_path, 'r') as src:
            for filename in src.namelist():
                # Skip any existing ComicInfo.xml
                if os.path.basename(filename).lower() == "comicinfo.xml":
                    continue
                src.extract(filename, temp_extract_dir)

        # Step 2: Write ComicInfo.xml to temp directory
        comicinfo_path = os.path.join(temp_extract_dir, "ComicInfo.xml")
        with open(comicinfo_path, 'wb') as f:
            f.write(comicinfo_xml_bytes)

        # Step 3: Recompress everything into new CBZ (sorted for consistency)
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as dst:
            # Get all files and sort them
            all_files = []
            for root, dirs, files in os.walk(temp_extract_dir):
                for file in files:
                    file_path_full = os.path.join(root, file)
                    arcname = os.path.relpath(file_path_full, temp_extract_dir)
                    all_files.append((file_path_full, arcname))

            # Sort by arcname for consistent ordering
            all_files.sort(key=lambda x: x[1])

            # Write all files
            for file_path_full, arcname in all_files:
                dst.write(file_path_full, arcname)

        # Step 4: Replace original file
        os.replace(temp_zip_path, file_path)

    except zipfile.BadZipFile as e:
        # Handle the case where a .cbz file is actually a RAR file
        if "File is not a zip file" in str(e) or "BadZipFile" in str(e):
            app_logger.warning(f"Detected that {os.path.basename(file_path)} is not a valid ZIP file. Attempting to convert from RAR...")

            # Clean up any partial extraction
            if os.path.exists(temp_extract_dir):
                shutil.rmtree(temp_extract_dir, ignore_errors=True)
            if os.path.exists(temp_zip_path):
                try:
                    os.unlink(temp_zip_path)
                except:
                    pass

            # Rename to .rar for conversion
            rar_file = os.path.join(file_dir, base_name + ".rar")
            shutil.move(file_path, rar_file)

            # Convert RAR to CBZ
            app_logger.info(f"Converting {base_name}.rar to CBZ format...")
            temp_conversion_dir = os.path.join(file_dir, f"temp_{base_name}")
            success = convert_single_rar_file(rar_file, file_path, temp_conversion_dir)

            if success:
                # Delete the RAR file
                if os.path.exists(rar_file):
                    os.remove(rar_file)
                # Clean up temp directory
                if os.path.exists(temp_conversion_dir):
                    shutil.rmtree(temp_conversion_dir, ignore_errors=True)

                app_logger.info(f"Successfully converted RAR to CBZ. Now adding ComicInfo.xml...")

                # Now recursively call this function to add ComicInfo.xml to the newly converted CBZ
                add_comicinfo_to_cbz(file_path, comicinfo_xml_bytes)
            else:
                app_logger.error(f"Failed to convert {base_name}.rar to CBZ")
                # Move the RAR file back to original CBZ name
                if os.path.exists(rar_file):
                    shutil.move(rar_file, file_path)
                raise Exception(f"File is actually a RAR archive and conversion failed")
        else:
            raise

    finally:
        # Clean up temp directory
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
        # Clean up temp zip if it still exists
        if os.path.exists(temp_zip_path):
            try:
                os.unlink(temp_zip_path)
            except:
                pass

@app.route('/validate-gcd-issue', methods=['POST'])
def validate_gcd_issue():
    """Validate that a specific issue number exists in the given series"""
    try:
        import mysql.connector

        data = request.get_json()
        series_id = data.get('series_id')
        issue_number = data.get('issue_number')

        app_logger.debug(f"DEBUG: validate_gcd_issue called - series_id={series_id}, issue={issue_number}")

        # Note: issue_number can be 0, so check for None explicitly
        if series_id is None or issue_number is None:
            app_logger.error(f"ERROR: Missing parameters in validate_gcd_issue - series_id={series_id}, issue_number={issue_number}")
            return jsonify({
                "success": False,
                "error": "Missing required parameters"
            }), 400

        # Connect to GCD MySQL database
        try:
            gcd_host = os.environ.get('GCD_MYSQL_HOST')
            gcd_port = int(os.environ.get('GCD_MYSQL_PORT'))
            gcd_database = os.environ.get('GCD_MYSQL_DATABASE')
            gcd_user = os.environ.get('GCD_MYSQL_USER')
            gcd_password = os.environ.get('GCD_MYSQL_PASSWORD')

            app_logger.debug(f"DEBUG: Connecting to database for validation...")
            connection = mysql.connector.connect(
                host=gcd_host,
                port=gcd_port,
                database=gcd_database,
                user=gcd_user,
                password=gcd_password,
                charset='utf8mb4',
                connection_timeout=30
            )
            cursor = connection.cursor(dictionary=True)

            # Simple query to check if issue exists
            validation_query = "SELECT id, title, number FROM gcd_issue WHERE series_id = %s AND (number = %s OR number = CONCAT('[', %s, ']') OR number LIKE CONCAT(%s, ' (%')) AND deleted = 0 LIMIT 1"
            app_logger.debug(f"DEBUG: Executing validation query...")
            cursor.execute(validation_query, (series_id, str(issue_number), str(issue_number), str(issue_number)))
            issue_result = cursor.fetchone()

            cursor.close()
            connection.close()

            app_logger.debug(f"DEBUG: Validation result: {'Found' if issue_result else 'Not found'}")

            if issue_result:
                return jsonify({
                    "success": True,
                    "issue_id": issue_result['id'],
                    "issue_number": issue_result['number'],
                    "issue_title": issue_result['title']
                })
            else:
                app_logger.debug(f"DEBUG: Issue #{issue_number} not found in series {series_id}")
                return jsonify({
                    "success": False,
                    "error": f"Issue #{issue_number} not found in series"
                })

        except mysql.connector.Error as db_error:
            app_logger.error(f"ERROR: Database error in validate_gcd_issue: {db_error}")
            return jsonify({
                "success": False,
                "error": f"Database error: {str(db_error)}"
            }), 500

    except ImportError:
        app_logger.error(f"ERROR: MySQL connector not available in validate_gcd_issue")
        return jsonify({
            "success": False,
            "error": "MySQL connector not available"
        }), 500
    except Exception as e:
        import traceback
        app_logger.error(f"ERROR: Exception in validate_gcd_issue: {e}")
        app_logger.debug(f"Traceback:\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": f"Validation error: {str(e)}"
        }), 500

@app.route('/search-gcd-metadata-with-selection', methods=['POST'])
def search_gcd_metadata_with_selection():
    """Search GCD database for comic metadata using user-selected series"""
    try:
        import mysql.connector
        from datetime import datetime
        import tempfile
        import zipfile

        data = request.get_json()
        file_path = data.get('file_path')
        file_name = data.get('file_name')
        series_id = data.get('series_id')
        issue_number = data.get('issue_number')

        app_logger.debug(f"DEBUG: search_gcd_metadata_with_selection called - file={file_name}, series_id={series_id}, issue={issue_number}")

        # Note: issue_number can be 0, so check for None explicitly
        if not file_path or not file_name or series_id is None or issue_number is None:
            app_logger.error(f"ERROR: Missing required parameters - file_path={file_path}, file_name={file_name}, series_id={series_id}, issue_number={issue_number}")
            return jsonify({
                "success": False,
                "error": "Missing required parameters"
            }), 400

        # Connect to GCD MySQL database
        try:
            gcd_host = os.environ.get('GCD_MYSQL_HOST')
            gcd_port = int(os.environ.get('GCD_MYSQL_PORT'))
            gcd_database = os.environ.get('GCD_MYSQL_DATABASE')
            gcd_user = os.environ.get('GCD_MYSQL_USER')
            gcd_password = os.environ.get('GCD_MYSQL_PASSWORD')

            connection = mysql.connector.connect(
                host=gcd_host,
                port=gcd_port,
                database=gcd_database,
                user=gcd_user,
                password=gcd_password,
                charset='utf8mb4'
            )
            cursor = connection.cursor(dictionary=True)

            # Get series information
            series_query = """
                SELECT s.id, s.name, s.year_began, s.year_ended, s.publisher_id,
                       p.name as publisher_name
                FROM gcd_series s
                LEFT JOIN gcd_publisher p ON s.publisher_id = p.id
                WHERE s.id = %s
            """
            cursor.execute(series_query, (series_id,))
            series_result = cursor.fetchone()

            if not series_result:
                return jsonify({
                    "success": False,
                    "error": f"Series with ID {series_id} not found"
                }), 404

            # Search for the specific issue using comprehensive query
            issue_query = """
                SELECT
                  i.id,
                  COALESCE(
                    NULLIF(TRIM(i.title), ''),
                    (
                      SELECT NULLIF(TRIM(s.title), '')
                      FROM gcd_story s
                      WHERE s.issue_id = i.id AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
                      ORDER BY s.sequence_number
                      LIMIT 1
                    )
                  )                                                   AS Title,
                  sr.name                                             AS Series,
                  i.number                                            AS Number,
                  (
                    SELECT COUNT(*)
                    FROM gcd_issue i2
                    WHERE i2.series_id = i.series_id AND i2.deleted = 0
                  )                                                   AS `Count`,
                  i.volume                                            AS Volume,
                  (
                    SELECT COALESCE(
                      NULLIF(TRIM(s.synopsis), ''),
                      NULLIF(TRIM(s.notes), ''),
                      NULLIF(TRIM(s.title), '')
                    )
                    FROM gcd_story s
                    WHERE s.issue_id = i.id
                      AND COALESCE(
                        NULLIF(TRIM(s.synopsis), ''),
                        NULLIF(TRIM(s.notes), ''),
                        NULLIF(TRIM(s.title), '')
                      ) IS NOT NULL
                    ORDER BY
                      CASE WHEN s.sequence_number = 0 THEN 1 ELSE 0 END,
                      CASE WHEN NULLIF(TRIM(s.synopsis), '') IS NOT NULL THEN 0 ELSE 1 END,
                      CASE WHEN NULLIF(TRIM(s.notes), '') IS NOT NULL THEN 0 ELSE 1 END,
                      s.sequence_number
                    LIMIT 1
                  )                                                   AS Summary,
                  CASE
                    WHEN COALESCE(i.key_date, i.on_sale_date) IS NOT NULL
                         AND LENGTH(COALESCE(i.key_date, i.on_sale_date)) >= 4
                      THEN CAST(SUBSTRING(COALESCE(i.key_date, i.on_sale_date), 1, 4) AS UNSIGNED)
                  END AS `Year`,
                  CASE
                    WHEN COALESCE(i.key_date, i.on_sale_date) IS NOT NULL
                         AND LENGTH(COALESCE(i.key_date, i.on_sale_date)) >= 7
                      THEN CAST(SUBSTRING(COALESCE(i.key_date, i.on_sale_date), 6, 2) AS UNSIGNED)
                  END AS `Month`,
                  (
                    SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
                    FROM (
                      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_story s
                      JOIN gcd_story_credit sc ON sc.story_id = s.id
                      JOIN gcd_credit_type ct   ON ct.id = sc.credit_type_id
                      LEFT JOIN gcd_creator c   ON c.id = sc.creator_id
                      WHERE s.issue_id = i.id
                        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
                        AND (ct.name LIKE 'script%' OR ct.name LIKE 'writer%' OR ct.name LIKE 'plot%')
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                      UNION
                      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_issue_credit ic
                      JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                      LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                      WHERE ic.issue_id = i.id
                        AND (ct.name LIKE 'script%' OR ct.name LIKE 'writer%' OR ct.name LIKE 'plot%')
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                    ) x
                    WHERE NULLIF(name,'') IS NOT NULL
                  )                                                   AS Writer,
                  (
                    SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
                    FROM (
                      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_story s
                      JOIN gcd_story_credit sc ON sc.story_id = s.id
                      JOIN gcd_credit_type ct   ON ct.id = sc.credit_type_id
                      LEFT JOIN gcd_creator c   ON c.id = sc.creator_id
                      WHERE s.issue_id = i.id
                        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
                        AND (ct.name LIKE 'pencil%' OR ct.name LIKE 'penc%')
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                      UNION
                      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_issue_credit ic
                      JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                      LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                      WHERE ic.issue_id = i.id
                        AND (ct.name LIKE 'pencil%' OR ct.name LIKE 'penc%')
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                    ) x
                    WHERE NULLIF(name,'') IS NOT NULL
                  )                                                   AS Penciller,
                  (
                    SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
                    FROM (
                      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_story s
                      JOIN gcd_story_credit sc ON sc.story_id = s.id
                      JOIN gcd_credit_type ct   ON ct.id = sc.credit_type_id
                      LEFT JOIN gcd_creator c   ON c.id = sc.creator_id
                      WHERE s.issue_id = i.id
                        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
                        AND (ct.name LIKE 'ink%')
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                      UNION
                      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_issue_credit ic
                      JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                      LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                      WHERE ic.issue_id = i.id
                        AND (ct.name LIKE 'ink%')
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                    ) x
                    WHERE NULLIF(name,'') IS NOT NULL
                  )                                                   AS Inker,
                  (
                    SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
                    FROM (
                      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_story s
                      JOIN gcd_story_credit sc ON sc.story_id = s.id
                      JOIN gcd_credit_type ct   ON ct.id = sc.credit_type_id
                      LEFT JOIN gcd_creator c   ON c.id = sc.creator_id
                      WHERE s.issue_id = i.id
                        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
                        AND (ct.name LIKE 'color%' OR ct.name LIKE 'colour%')
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                      UNION
                      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_issue_credit ic
                      JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                      LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                      WHERE ic.issue_id = i.id
                        AND (ct.name LIKE 'color%' OR ct.name LIKE 'colour%')
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                    ) x
                    WHERE NULLIF(name,'') IS NOT NULL
                  )                                                   AS Colorist,
                  (
                    SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
                    FROM (
                      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_story s
                      JOIN gcd_story_credit sc ON sc.story_id = s.id
                      JOIN gcd_credit_type ct   ON ct.id = sc.credit_type_id
                      LEFT JOIN gcd_creator c   ON c.id = sc.creator_id
                      WHERE s.issue_id = i.id
                        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
                        AND (ct.name LIKE 'letter%')
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                      UNION
                      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_issue_credit ic
                      JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                      LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                      WHERE ic.issue_id = i.id
                        AND (ct.name LIKE 'letter%')
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                    ) x
                    WHERE NULLIF(name,'') IS NOT NULL
                  )                                                   AS Letterer,
                  (
                    SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
                    FROM (
                      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_story s
                      JOIN gcd_story_credit sc ON sc.story_id = s.id
                      JOIN gcd_credit_type ct   ON ct.id = sc.credit_type_id
                      LEFT JOIN gcd_creator c   ON c.id = sc.creator_id
                      WHERE s.issue_id = i.id
                        AND (s.sequence_number = 0 OR ct.name LIKE 'cover%')
                        AND (ct.name LIKE 'pencil%' OR ct.name LIKE 'penc%' OR ct.name LIKE 'ink%' OR ct.name LIKE 'art%' OR ct.name LIKE 'cover%')
                        AND (sc.deleted = 0 OR sc.deleted IS NULL)
                      UNION
                      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
                      FROM gcd_issue_credit ic
                      JOIN gcd_credit_type ct ON ct.id = ic.credit_type_id
                      LEFT JOIN gcd_creator c ON c.id = ic.creator_id
                      WHERE ic.issue_id = i.id
                        AND (ct.name LIKE 'cover%')
                        AND (ic.deleted = 0 OR ic.deleted IS NULL)
                    ) z
                    WHERE NULLIF(name,'') IS NOT NULL
                  )                                                   AS CoverArtist,
                  COALESCE(ip.name, p.name)                           AS Publisher,
                  (
                    SELECT TRIM(BOTH ', ' FROM
                           REPLACE(
                             GROUP_CONCAT(DISTINCT NULLIF(TRIM(s.genre), '') SEPARATOR ', '),
                             ';', ','
                           ))
                    FROM gcd_story s
                    WHERE s.issue_id = i.id
                  )                                                   AS Genre,
                  COALESCE(
                    (
                      SELECT NULLIF(GROUP_CONCAT(DISTINCT c.name SEPARATOR ', '), '')
                      FROM gcd_story s
                      LEFT JOIN gcd_story_character sc ON sc.story_id = s.id
                      LEFT JOIN gcd_character c ON c.id = sc.character_id
                      WHERE s.issue_id = i.id
                    ),
                    (
                      SELECT TRIM(BOTH ', ' FROM
                             REPLACE(
                               GROUP_CONCAT(DISTINCT NULLIF(TRIM(s.characters), '') SEPARATOR ', '),
                               ';', ','
                             ))
                      FROM gcd_story s
                      WHERE s.issue_id = i.id
                    )
                  )                                                   AS Characters,
                  i.rating                                            AS AgeRating,
                  l.code                                              AS LanguageISO,
                  i.page_count                                        AS PageCount
                FROM gcd_issue i
                JOIN gcd_series sr                 ON sr.id = i.series_id
                JOIN stddata_language l            ON sr.language_id = l.id
                LEFT JOIN gcd_publisher p          ON p.id = sr.publisher_id
                LEFT JOIN gcd_indicia_publisher ip ON ip.id = i.indicia_publisher_id
                WHERE i.series_id = %s AND (i.number = %s OR i.number = CONCAT('[', %s, ']') OR i.number LIKE CONCAT(%s, ' (%'))
                LIMIT 1
            """

            app_logger.debug(f"DEBUG: Executing issue query for series {series_id}, issue {issue_number}")
            cursor.execute(issue_query, (series_id, str(issue_number), str(issue_number), str(issue_number)))
            issue_result = cursor.fetchone()

            app_logger.debug(f"DEBUG: Issue search result for series {series_id}, issue {issue_number}: {'Found' if issue_result else 'Not found'}")
            if issue_result:
                app_logger.debug(f"DEBUG: Issue result keys: {list(issue_result.keys())}")
                app_logger.debug(f"DEBUG: Issue title: {issue_result.get('Title', 'N/A')}")

            if issue_result:
                # Check if ComicInfo.xml already exists and has Notes data
                try:
                    from comicinfo import read_comicinfo_from_zip
                    existing_comicinfo = read_comicinfo_from_zip(file_path)
                    existing_notes = existing_comicinfo.get('Notes', '').strip()

                    if existing_notes:
                        app_logger.info(f"Skipping ComicInfo.xml generation - file already has Notes data: {existing_notes[:50]}...")
                        return jsonify({
                            "success": True,
                            "skipped": True,
                            "message": "ComicInfo.xml already exists with Notes data",
                            "existing_notes": existing_notes,
                            "metadata": {
                                "issue": issue_result['Number']
                            }
                        }), 200
                except Exception as check_error:
                    app_logger.debug(f"DEBUG: Error checking existing ComicInfo.xml (will proceed with generation): {str(check_error)}")

                # Generate ComicInfo.xml content
                comicinfo_xml = generate_comicinfo_xml(issue_result, series_result)

                # Add ComicInfo.xml to the CBZ file
                add_comicinfo_to_cbz(file_path, comicinfo_xml)

                return jsonify({
                    "success": True,
                    "metadata": {
                        "series": issue_result['Series'],
                        "issue": issue_result['Number'],
                        "title": issue_result['Title'],
                        "publisher": issue_result['Publisher'],
                        "year": issue_result['Year'],
                        "writer": issue_result['Writer'],
                        "penciller": issue_result['Penciller'],
                        "inker": issue_result['Inker'],
                        "colorist": issue_result['Colorist'],
                        "letterer": issue_result['Letterer'],
                        "cover_artist": issue_result['CoverArtist'],
                        "genre": issue_result['Genre'],
                        "characters": issue_result['Characters'],
                        "summary": issue_result['Summary'],
                        "age_rating": issue_result['AgeRating']
                    }
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"Issue #{issue_number} not found for series '{series_result['name']}'"
                }), 404

        except mysql.connector.Error as db_error:
            import traceback
            app_logger.error(f"MySQL Error in search_gcd_metadata_with_selection: {str(db_error)}")
            app_logger.debug(f"MySQL Error Traceback:\n{traceback.format_exc()}")
            return jsonify({
                "success": False,
                "error": f"Database connection error: {str(db_error)}"
            }), 500
        finally:
            if 'connection' in locals() and connection.is_connected():
                cursor.close()
                connection.close()

    except Exception as e:
        import traceback
        app_logger.error(f"ERROR in search_gcd_metadata_with_selection: {str(e)}")
        app_logger.debug(f"Full Traceback:\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": f"Server error: {str(e)}"
        }), 500

@app.route('/search-comicvine-metadata', methods=['POST'])
def search_comicvine_metadata():
    """Search ComicVine API for comic metadata and add to CBZ file"""
    try:
        app_logger.info(f"🔍 ComicVine search started")

        try:
            import comicvine
            app_logger.debug("DEBUG: comicvine module imported successfully")
        except ImportError as import_err:
            app_logger.error(f"Failed to import comicvine module: {str(import_err)}")
            return jsonify({
                "success": False,
                "error": f"ComicVine module import error: {str(import_err)}"
            }), 500

        data = request.get_json()
        app_logger.info(f"ComicVine Request data: {data}")

        file_path = data.get('file_path')
        file_name = data.get('file_name')

        if not file_path or not file_name:
            return jsonify({
                "success": False,
                "error": "Missing file_path or file_name"
            }), 400

        # Check if ComicVine API key is configured
        api_key = app.config.get("COMICVINE_API_KEY", "").strip()
        app_logger.debug(f"DEBUG: ComicVine API key configured: {bool(api_key)}")
        app_logger.debug(f"DEBUG: API key value (first 10 chars): {api_key[:10] if api_key else 'EMPTY'}")
        app_logger.debug(f"DEBUG: All COMICVINE config keys in app.config: {[k for k in app.config.keys() if 'COMIC' in k.upper()]}")

        # Also check the raw config file
        from config import config as raw_config
        raw_key = raw_config.get("SETTINGS", "COMICVINE_API_KEY", fallback="")
        app_logger.debug(f"DEBUG: Raw config.ini value (first 10 chars): {raw_key[:10] if raw_key else 'EMPTY'}")

        if not api_key:
            app_logger.error("ComicVine API key not configured")
            return jsonify({
                "success": False,
                "error": "ComicVine API key not configured. Please add your API key in Settings."
            }), 400

        # Check if Simyan library is available
        app_logger.debug(f"DEBUG: Checking if Simyan is available...")
        if not comicvine.is_simyan_available():
            app_logger.error("Simyan library not available")
            return jsonify({
                "success": False,
                "error": "Simyan library not installed. Please install it with: pip install simyan"
            }), 500
        app_logger.debug(f"DEBUG: Simyan library is available")

        # Parse series name and issue from filename (reuse GCD parsing logic)
        name_without_ext = file_name
        for ext in ('.cbz', '.cbr', '.zip'):
            name_without_ext = name_without_ext.replace(ext, '')

        # Try to parse series and issue from common formats
        series_name = None
        issue_number = None
        year = None

        patterns = [
            r'^(.+?)\s+(\d{3,4})\s+\((\d{4})\)',  # "Series 001 (2020)"
            r'^(.+?)\s+#?(\d{1,4})\s*\((\d{4})\)', # "Series #1 (2020)" or "Series 1 (2020)"
            r'^(.+?)\s+v\d+\s+(\d{1,4})\s*\((\d{4})\)', # "Series v1 001 (2020)"
            r'^(.+?)\s+(\d{1,4})\s+\(of\s+\d+\)\s+\((\d{4})\)', # "Series 05 (of 12) (2020)"
            r'^(.+?)\s+#?(\d{1,4})$',  # "Series 169" or "Series #169" (no year)
        ]

        for pattern in patterns:
            match = re.match(pattern, name_without_ext, re.IGNORECASE)
            if match:
                series_name = match.group(1).strip()
                issue_number = str(int(match.group(2)))  # Convert to int then back to string to remove leading zeros
                year = int(match.group(3)) if len(match.groups()) >= 3 else None
                app_logger.debug(f"DEBUG: File parsed - series_name={series_name}, issue_number={issue_number}, year={year}")
                break

        # If no pattern matched, try to parse as single-issue/graphic novel with just year
        if not series_name:
            single_issue_pattern = r'^(.+?)\s*\((\d{4})\)$'
            match = re.match(single_issue_pattern, name_without_ext, re.IGNORECASE)
            if match:
                series_name = match.group(1).strip()
                year = int(match.group(2))
                issue_number = "1"
                app_logger.debug(f"DEBUG: Single-issue/graphic novel parsed - series_name={series_name}, year={year}, issue_number={issue_number}")

        # Ultimate fallback: use entire filename as series name
        if not series_name:
            series_name = name_without_ext.strip()
            issue_number = "1"
            app_logger.debug(f"DEBUG: Fallback parsing - using entire filename as series_name={series_name}, issue_number={issue_number}")

        if not series_name or not issue_number:
            return jsonify({
                "success": False,
                "error": f"Could not parse series name from: {name_without_ext}"
            }), 400

        # Normalize series name for searching - remove special characters
        normalized_series = re.sub(r'[:\-–—\'\"\.\,\!\?]', ' ', series_name)
        normalized_series = re.sub(r'\s+', ' ', normalized_series).strip()

        # Search ComicVine for volumes using normalized name
        app_logger.info(f"Searching ComicVine for '{normalized_series}' (original: '{series_name}') issue #{issue_number}")
        volumes = comicvine.search_volumes(api_key, normalized_series, year)

        if not volumes:
            return jsonify({
                "success": False,
                "error": f"No volumes found matching '{series_name}' in ComicVine"
            }), 404

        # Check if we have a confident match (all search words present in a single result)
        search_words = set(normalized_series.lower().split())
        confident_match = None

        if len(volumes) > 1:
            # Look for a volume that contains all search words
            for volume in volumes:
                volume_name_lower = volume['name'].lower()
                if all(word in volume_name_lower for word in search_words):
                    confident_match = volume
                    app_logger.info(f"Confident match found: '{volume['name']}' contains all search words: {search_words}")
                    break

        # If we have a confident match, use it; otherwise show modal for multiple volumes
        if confident_match:
            selected_volume = confident_match
            app_logger.info(f"Auto-selected confident match: {selected_volume['name']} ({selected_volume['start_year']})")
        elif len(volumes) > 1:
            # Multiple volumes and no confident match - show selection modal
            return jsonify({
                "success": False,
                "requires_selection": True,
                "parsed_filename": {
                    "series_name": series_name,
                    "issue_number": issue_number,
                    "year": year
                },
                "possible_matches": volumes,
                "message": f"Found {len(volumes)} volume(s). Please select the correct one."
            }), 200
        else:
            # Single volume - auto-select
            selected_volume = volumes[0]
            app_logger.info(f"Auto-selected single volume: {selected_volume['name']} ({selected_volume['start_year']})")

        # Get the issue
        issue_data = comicvine.get_issue_by_number(api_key, selected_volume['id'], issue_number, year)

        if not issue_data:
            return jsonify({
                "success": False,
                "error": f"Issue #{issue_number} not found in volume '{selected_volume['name']}'"
            }), 404

        # Map to ComicInfo format
        comicinfo_data = comicvine.map_to_comicinfo(issue_data, selected_volume)

        # Generate ComicInfo.xml
        comicinfo_xml = generate_comicinfo_xml(comicinfo_data)

        # Add ComicInfo.xml to the CBZ file
        add_comicinfo_to_cbz(file_path, comicinfo_xml)

        # Return success with metadata and rename configuration
        return jsonify({
            "success": True,
            "metadata": comicinfo_data,
            "image_url": issue_data.get('image_url'),
            "volume_info": {
                "id": selected_volume['id'],
                "name": selected_volume['name'],
                "start_year": selected_volume['start_year']
            },
            "rename_config": {
                "enabled": app.config.get("ENABLE_CUSTOM_RENAME", False),
                "pattern": app.config.get("CUSTOM_RENAME_PATTERN", "")
            }
        })

    except Exception as e:
        app_logger.error(f"Error in ComicVine search: {str(e)}")
        import traceback
        app_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/search-comicvine-metadata-with-selection', methods=['POST'])
def search_comicvine_metadata_with_selection():
    """Search ComicVine using user-selected volume"""
    try:
        import comicvine

        data = request.get_json()
        file_path = data.get('file_path')
        file_name = data.get('file_name')
        volume_id = data.get('volume_id')
        publisher_name = data.get('publisher_name')
        issue_number = data.get('issue_number')
        year = data.get('year')

        app_logger.debug(f"DEBUG: search_comicvine_metadata_with_selection called - file={file_name}, volume_id={volume_id}, publisher={publisher_name}, issue={issue_number}")

        # Note: issue_number can be 0, so check for None explicitly
        if not file_path or not file_name or volume_id is None or issue_number is None:
            app_logger.error(f"ERROR: Missing required parameters - file_path={file_path}, file_name={file_name}, volume_id={volume_id}, issue_number={issue_number}")
            return jsonify({
                "success": False,
                "error": "Missing required parameters"
            }), 400

        # Check if ComicVine API key is configured
        api_key = app.config.get("COMICVINE_API_KEY", "").strip()
        if not api_key:
            return jsonify({
                "success": False,
                "error": "ComicVine API key not configured"
            }), 400

        # Get the issue
        issue_data = comicvine.get_issue_by_number(api_key, volume_id, str(issue_number), year)

        if not issue_data:
            return jsonify({
                "success": False,
                "error": f"Issue #{issue_number} not found in selected volume"
            }), 404

        # Create volume_data dict with the volume ID and publisher for metadata
        volume_data = {
            'id': volume_id,
            'publisher_name': publisher_name
        }

        # Map to ComicInfo format
        comicinfo_data = comicvine.map_to_comicinfo(issue_data, volume_data)

        # Generate ComicInfo.xml
        comicinfo_xml = generate_comicinfo_xml(comicinfo_data)

        # Add ComicInfo.xml to the CBZ file
        add_comicinfo_to_cbz(file_path, comicinfo_xml)

        # Return success with metadata and rename configuration
        return jsonify({
            "success": True,
            "metadata": comicinfo_data,
            "image_url": issue_data.get('image_url'),
            "rename_config": {
                "enabled": app.config.get("ENABLE_CUSTOM_RENAME", False),
                "pattern": app.config.get("CUSTOM_RENAME_PATTERN", "")
            }
        })

    except Exception as e:
        app_logger.error(f"Error in ComicVine search with selection: {str(e)}")
        import traceback
        app_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

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

    # Start file watcher for /data directory in background
    def start_file_watcher_background():
        try:
            app_logger.info(f"Initializing file watcher for {DATA_DIR}...")
            file_watcher = FileWatcher(watch_path=DATA_DIR, debounce_seconds=2)
            if file_watcher.start():
                app_logger.info(f"👁️  File watcher started for {DATA_DIR} (tracking recent files)...")
            else:
                app_logger.warning("⚠️  File watcher failed to start")
        except Exception as e:
            app_logger.error(f"❌ Failed to initialize file watcher: {e}")
            import traceback
            app_logger.error(f"Traceback: {traceback.format_exc()}")

    threading.Thread(target=start_file_watcher_background, daemon=True).start()
    app_logger.info("🔄 File watcher initialization started in background...")

    if os.environ.get("MONITOR", "").strip().lower() == "yes":
        app_logger.info("MONITOR=yes detected. Starting monitor.py...")
        threading.Thread(target=run_monitor, daemon=True).start()

    if pwd is not None:
        user_name = pwd.getpwuid(os.geteuid()).pw_name
    else:
        user_name = os.getenv('USERNAME', 'unknown')
    app_logger.info(f"Running as user: {user_name}")
        
    app.run(debug=True, use_reloader=False, threaded=True, host='0.0.0.0', port=5577)