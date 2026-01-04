from flask import Flask, render_template, request, Response, send_from_directory, send_file, redirect, jsonify, url_for, stream_with_context, render_template_string
from werkzeug.utils import secure_filename
import subprocess
import io
import os
import shutil
import uuid
import sys
import threading
import time
import json
import logging
import signal
import select
import random
import comicvine
from datetime import datetime, timedelta
import time as time_module
from PIL import Image, ImageFilter, ImageDraw
try:
    import pwd
except ImportError:
    pwd = None
from functools import lru_cache
import hashlib
import re
import xml.etree.ElementTree as ET
import heapq
import hashlib
import zipfile
import rarfile
import traceback
import shutil
import mysql.connector
import base64
from io import BytesIO
from api import app
from favorites import favorites_bp
from opds import opds_bp
from models import gcd
from models import metron
from config import config, load_flask_config, write_config, load_config
from edit import get_edit_modal, save_cbz, cropCenter, cropLeft, cropRight, cropFreeForm, get_image_data_url, modal_body_template
from memory_utils import initialize_memory_management, cleanup_on_exit, memory_context, get_global_monitor
from app_logging import app_logger, APP_LOG, MONITOR_LOG
from helpers import is_hidden
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from version import __version__
import requests
from packaging import version as pkg_version
from database import (init_db, get_db_connection, get_recent_files, log_recent_file,
                      get_file_index_from_db, save_file_index_to_db, update_file_index_entry,
                      add_file_index_entry, delete_file_index_entry, clear_file_index_from_db, search_file_index,
                      get_search_cache, save_search_cache, clear_search_cache,
                      get_rebuild_schedule, save_rebuild_schedule as db_save_rebuild_schedule, update_last_rebuild,
                      get_browse_cache, invalidate_browse_cache, clear_browse_cache,
                      get_path_counts_batch, get_directory_children, clear_stats_cache,
                      clear_stats_cache_keys, mark_issue_read, get_issues_read, get_recent_read_issues)
import recommendations
from models.stats import (get_library_stats, get_file_type_distribution, get_top_publishers,
                          get_reading_history_stats, get_largest_comics, get_top_series_by_count,
                          get_reading_heatmap_data)
from concurrent.futures import ThreadPoolExecutor
from file_watcher import FileWatcher
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

load_config()

# Initialize Database
init_db()

# Register Blueprints
app.register_blueprint(favorites_bp)
app.register_blueprint(opds_bp)
from reading_lists import reading_lists_bp
app.register_blueprint(reading_lists_bp)

# Initialize APScheduler for scheduled file index rebuilds
rebuild_scheduler = BackgroundScheduler(daemon=True)
rebuild_scheduler.start()
app_logger.info("📅 Rebuild scheduler initialized")

# Function to perform scheduled file index rebuild
def scheduled_file_index_rebuild():
    """Rebuild the file index on schedule."""
    try:
        app_logger.info("🔄 Starting scheduled file index rebuild...")
        start_time = time.time()

        # Clear and rebuild the file index
        file_index.clear()
        build_file_index()

        # Update last rebuild timestamp
        update_last_rebuild()

        # Clear and pre-populate stats cache
        clear_stats_cache()
        get_library_stats()
        get_file_type_distribution()
        get_top_publishers()
        get_reading_history_stats()

        elapsed = time.time() - start_time
        app_logger.info(f"✅ Scheduled file index rebuild completed in {elapsed:.2f}s")
    except Exception as e:
        app_logger.error(f"❌ Scheduled file index rebuild failed: {e}")

# Function to configure scheduled rebuild based on database settings
def configure_rebuild_schedule():
    """Configure the rebuild schedule based on database settings."""
    try:
        schedule = get_rebuild_schedule()
        if not schedule:
            app_logger.warning("No rebuild schedule found in database")
            return

        # Remove existing jobs
        rebuild_scheduler.remove_all_jobs()

        if schedule['frequency'] == 'disabled':
            app_logger.info("📅 Scheduled rebuilds are disabled")
            return

        # Parse time
        time_parts = schedule['time'].split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1])

        if schedule['frequency'] == 'daily':
            # Daily at specified time
            trigger = CronTrigger(hour=hour, minute=minute)
            rebuild_scheduler.add_job(
                scheduled_file_index_rebuild,
                trigger=trigger,
                id='file_index_rebuild',
                name='Daily File Index Rebuild',
                replace_existing=True
            )
            app_logger.info(f"📅 Scheduled daily file index rebuild at {schedule['time']}")

        elif schedule['frequency'] == 'weekly':
            # Weekly on specified day at specified time
            weekday = int(schedule['weekday'])
            trigger = CronTrigger(day_of_week=weekday, hour=hour, minute=minute)
            rebuild_scheduler.add_job(
                scheduled_file_index_rebuild,
                trigger=trigger,
                id='file_index_rebuild',
                name='Weekly File Index Rebuild',
                replace_existing=True
            )
            days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            app_logger.info(f"📅 Scheduled weekly file index rebuild on {days[weekday]} at {schedule['time']}")

    except Exception as e:
        app_logger.error(f"Failed to configure rebuild schedule: {e}")

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
        app_logger.error(f"Traceback: {traceback.format_exc()}")

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

def is_cache_valid(cache_key):
    """Check if cached data is still valid with thread safety."""
    with cache_lock:
        if cache_key not in cache_timestamps:
            app_logger.debug(f"Cache key not found in timestamps: {cache_key}")
            return False

        # Check if cache has expired
        age = time.time() - cache_timestamps[cache_key]
        if age > CACHE_DURATION:
            app_logger.debug(f"Cache expired for {cache_key} (age: {age:.1f}s > {CACHE_DURATION}s)")
            return False

        # For browse: cache, just use TTL validation (no hash check)
        # The hash changes on directory access which causes false invalidations
        # We have automatic invalidation on file operations, so TTL is sufficient
        if cache_key.startswith("browse:"):
            app_logger.debug(f"Browse cache valid for {cache_key} (age: {age:.1f}s)")
            return True

        # For regular directory listings, still use hash validation
        actual_path = cache_key
        current_hash = get_directory_hash(actual_path)
        cached_data = directory_cache.get(cache_key, {})
        cached_hash = cached_data.get('hash') if isinstance(cached_data, dict) else None

        if current_hash != cached_hash:
            app_logger.debug(f"Hash mismatch for {cache_key}: current={current_hash}, cached={cached_hash}")
            return False

        return True

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

def cached_search(query):
    """
    Search function with SQLite-backed caching.
    Checks cache first, then queries database, then falls back to filesystem.
    """
    global file_index, index_built

    # Try to get from cache first
    cached_results = get_search_cache(query)
    if cached_results is not None:
        app_logger.debug(f"Search cache hit for query: '{query}'")
        return cached_results

    # If index is built (loaded from DB), search in database
    if index_built:
        app_logger.debug(f"Searching file index database for: '{query}'")
        results = search_file_index(query, limit=100)

        # Cache the results for future queries
        save_search_cache(query, results)
        return results

    # Fallback to filesystem search if index not ready
    app_logger.info("Search index not ready, using filesystem search...")
    results = filesystem_search(query)

    # Don't cache filesystem search results (may be incomplete)
    return results

def filesystem_search(query):
    """Fallback filesystem search when index is not ready"""

    query_lower = query.lower()
    results = []
    excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db"}
    allowed_files = {"missing.txt", "cvinfo"}
    
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
                
                if file_name.lower() not in allowed_files and any(file_name.lower().endswith(ext) for ext in excluded_extensions):
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
    """Get directory listing with optimized file system operations, caching, and memory awareness."""
    try:
        # Check if we have valid cached data
        if is_cache_valid(path):
            with cache_lock:
                cached_data = directory_cache.get(path)
                if cached_data:
                    cache_stats['hits'] += 1
                    app_logger.debug(f"Cache HIT for directory: {path}")
                    # Move to end for LRU
                    directory_cache.move_to_end(path)
                    return cached_data

        # Cache miss - need to read from filesystem
        cache_stats['misses'] += 1
        app_logger.debug(f"Cache MISS for directory: {path}")

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
            excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db"}
            allowed_files = {"missing.txt", "cvinfo"}

            for entry in entries:
                if entry.startswith(('.', '_')):
                    continue

                full_path = os.path.join(path, entry)
                try:
                    stat = os.stat(full_path)
                    if stat.st_mode & 0o40000:  # Directory
                        directories.append(entry)
                    else:  # File
                        # Check if file should be excluded (but allow specific files like missing.txt and cvinfo)
                        if entry.lower() in allowed_files or not any(entry.lower().endswith(ext) for ext in excluded_extensions):
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

            result = {
                "directories": directories,
                "files": files,
                "hash": get_directory_hash(path)
            }

            # Store in cache
            with cache_lock:
                directory_cache[path] = result
                cache_timestamps[path] = time.time()
                # Move to end for LRU
                directory_cache.move_to_end(path)

                # Enforce cache size limit
                while len(directory_cache) > MAX_CACHE_SIZE:
                    oldest_path = next(iter(directory_cache))
                    directory_cache.pop(oldest_path, None)
                    cache_timestamps.pop(oldest_path, None)
                    cache_stats['evictions'] += 1

            # Cleanup expired entries periodically
            if cache_stats['misses'] % 10 == 0:
                cleanup_cache()

            return result
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

    # Invalidate database browse cache
    invalidate_browse_cache(path)

    with cache_lock:
        invalidated_count = 0

        # Invalidate the specific path (both regular and browse: prefixed)
        if path in directory_cache:
            directory_cache.pop(path, None)
            cache_timestamps.pop(path, None)
            invalidated_count += 1

        browse_key = f"browse:{path}"
        if browse_key in directory_cache:
            directory_cache.pop(browse_key, None)
            cache_timestamps.pop(browse_key, None)
            invalidated_count += 1

        # Also invalidate parent directory cache (both regular and browse: prefixed)
        parent = os.path.dirname(path)
        if parent:
            if parent in directory_cache:
                directory_cache.pop(parent, None)
                cache_timestamps.pop(parent, None)
                invalidated_count += 1

            parent_browse_key = f"browse:{parent}"
            if parent_browse_key in directory_cache:
                directory_cache.pop(parent_browse_key, None)
                cache_timestamps.pop(parent_browse_key, None)
                invalidated_count += 1

        # Invalidate any child directory caches (both regular and browse: prefixed)
        paths_to_invalidate = []
        for cached_key in directory_cache.keys():
            # Handle both regular paths and browse: prefixed keys
            if cached_key.startswith("browse:"):
                cached_path = cached_key[7:]
            else:
                cached_path = cached_key

            if cached_path.startswith(path + os.sep):
                paths_to_invalidate.append(cached_key)

        for cached_key in paths_to_invalidate:
            directory_cache.pop(cached_key, None)
            cache_timestamps.pop(cached_key, None)
            invalidated_count += 1

        cache_stats['invalidations'] += invalidated_count

    # Also invalidate directory stats cache when files change
    _data_dir_stats_last_update = 0

    # Track when cache invalidation occurred
    last_cache_invalidation = time.time()

    if invalidated_count > 0:
        app_logger.debug(f"Invalidated {invalidated_count} memory cache entries for path: {path}")

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

    # Also clear stats cache (for insights page charts)
    clear_stats_cache()

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

#########################
#   File Index Routes   #
#########################

@app.route('/api/rebuild-file-index', methods=['POST'])
def api_rebuild_file_index():
    """Manually rebuild the file index."""
    global index_built

    try:
        app_logger.info("🔄 Manual file index rebuild requested...")
        start_time = time.time()

        # Reset the index_built flag to force a full rebuild
        index_built = False

        # Clear the database so build_file_index() will scan the filesystem
        app_logger.info("Clearing file index database...")
        clear_file_index_from_db()

        # Clear the in-memory file index
        file_index.clear()

        # Build from filesystem (will scan since database is now empty)
        app_logger.info("Scanning filesystem to rebuild index...")
        build_file_index()

        # Update last rebuild timestamp
        update_last_rebuild()

        # Clear and pre-populate stats cache
        clear_stats_cache()
        get_library_stats()
        get_file_type_distribution()
        get_top_publishers()
        get_reading_history_stats()

        elapsed = time.time() - start_time
        app_logger.info(f"✅ Manual file index rebuild completed in {elapsed:.2f}s")

        return jsonify({
            "success": True,
            "message": f"File index rebuilt successfully in {elapsed:.2f} seconds",
            "total_files": len([e for e in file_index if e['type'] == 'file']),
            "total_directories": len([e for e in file_index if e['type'] == 'directory'])
        })
    except Exception as e:
        app_logger.error(f"❌ File index rebuild failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/file-index-status', methods=['GET'])
def api_file_index_status():
    """Get the current status of the file index."""
    try:
        schedule = get_rebuild_schedule()

        total_files = len([e for e in file_index if e['type'] == 'file'])
        total_directories = len([e for e in file_index if e['type'] == 'directory'])

        last_rebuild = None
        if schedule and schedule.get('last_rebuild'):
            # Format last rebuild timestamp
            try:
                rebuild_dt = datetime.fromisoformat(schedule['last_rebuild'])
                time_diff = datetime.now() - rebuild_dt
                if time_diff.days > 0:
                    last_rebuild = f"{time_diff.days} day(s) ago"
                elif time_diff.seconds >= 3600:
                    hours = time_diff.seconds // 3600
                    last_rebuild = f"{hours} hour(s) ago"
                elif time_diff.seconds >= 60:
                    minutes = time_diff.seconds // 60
                    last_rebuild = f"{minutes} minute(s) ago"
                else:
                    last_rebuild = "Just now"
            except Exception:
                last_rebuild = schedule['last_rebuild']

        return jsonify({
            "success": True,
            "total_files": total_files,
            "total_directories": total_directories,
            "last_rebuild": last_rebuild,
            "index_built": index_built
        })
    except Exception as e:
        app_logger.error(f"Failed to get file index status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-rebuild-schedule', methods=['GET'])
def api_get_rebuild_schedule():
    """Get the current rebuild schedule configuration."""
    try:
        schedule = get_rebuild_schedule()
        if not schedule:
            return jsonify({
                "success": True,
                "schedule": {
                    "frequency": "disabled",
                    "time": "02:00",
                    "weekday": 0
                },
                "next_run": "Not scheduled"
            })

        # Calculate next run time
        next_run = "Not scheduled"
        if schedule['frequency'] != 'disabled':
            try:
                jobs = rebuild_scheduler.get_jobs()
                if jobs:
                    next_run_time = jobs[0].next_run_time
                    if next_run_time:
                        next_run = next_run_time.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass

        return jsonify({
            "success": True,
            "schedule": {
                "frequency": schedule['frequency'],
                "time": schedule['time'],
                "weekday": schedule['weekday']
            },
            "next_run": next_run
        })
    except Exception as e:
        app_logger.error(f"Failed to get rebuild schedule: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/save-rebuild-schedule', methods=['POST'])
def api_save_rebuild_schedule():
    """Save the rebuild schedule configuration."""
    try:
        data = request.get_json()
        frequency = data.get('frequency', 'disabled')
        time_str = data.get('time', '02:00')
        weekday = int(data.get('weekday', 0))

        # Validate inputs
        if frequency not in ['disabled', 'daily', 'weekly']:
            return jsonify({"success": False, "error": "Invalid frequency"}), 400

        # Save to database
        if not db_save_rebuild_schedule(frequency, time_str, weekday):
            return jsonify({"success": False, "error": "Failed to save schedule to database"}), 500

        # Reconfigure the scheduler
        configure_rebuild_schedule()

        app_logger.info(f"✅ Rebuild schedule saved: {frequency} at {time_str}")

        return jsonify({
            "success": True,
            "message": f"Rebuild schedule saved successfully: {frequency} at {time_str}"
        })
    except Exception as e:
        app_logger.error(f"Failed to save rebuild schedule: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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

    # Try to load from database first
    app_logger.info("Loading file index from database...")
    start_time = time.time()

    db_index = get_file_index_from_db()
    if db_index and len(db_index) > 0:
        file_index = db_index
        index_built = True
        load_time = time.time() - start_time
        app_logger.info(f"✅ File index loaded from database: {len(file_index)} items in {load_time:.2f} seconds")
        return

    # Database empty, build from filesystem
    app_logger.info("Database empty, building file index from filesystem...")
    start_time = time.time()

    file_index.clear()
    excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db"}
    allowed_files = {"missing.txt", "cvinfo"}

    # Track comic files for recent files database
    comic_files = []

    # Helper function to check for folder thumbnail
    def check_has_thumbnail(folder_path):
        for ext in ['.png', '.jpg', '.jpeg']:
            if os.path.exists(os.path.join(folder_path, f'folder{ext}')):
                return 1
        return 0

    try:
        for root, dirs, files in os.walk(DATA_DIR):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]

            # Index directories
            for name in dirs:
                try:
                    full_dir_path = os.path.join(root, name)
                    rel_path = os.path.relpath(full_dir_path, DATA_DIR)
                    file_index.append({
                        "name": name,
                        "path": f"/data/{rel_path}",
                        "type": "directory",
                        "parent": f"/data/{os.path.dirname(rel_path)}" if os.path.dirname(rel_path) else "/data",
                        "has_thumbnail": check_has_thumbnail(full_dir_path)
                    })
                except (OSError, IOError):
                    continue

            # Index files (excluding certain extensions, but allow specific files)
            for name in files:
                if name.startswith('.') or name.startswith('_'):
                    continue

                # Skip excluded file types (but allow specific files like missing.txt and cvinfo)
                if name.lower() not in allowed_files and any(name.lower().endswith(ext) for ext in excluded_extensions):
                    continue

                try:
                    full_path = os.path.join(root, name)
                    rel_path = os.path.relpath(full_path, DATA_DIR)
                    file_size = os.path.getsize(full_path)
                    mtime = os.path.getmtime(full_path)

                    file_index.append({
                        "name": name,
                        "path": f"/data/{rel_path}",
                        "type": "file",
                        "size": file_size,
                        "parent": f"/data/{os.path.dirname(rel_path)}" if os.path.dirname(rel_path) else "/data",
                        "modified_at": mtime
                    })

                    # Track comic files for recent files list (Legacy logic, but strictly speaking we don't need the separate list anymore
                    # since we populate modified_at directly in file_index. But keeping standard structure for now)
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

    # Save index to database for persistence
    app_logger.info("Saving file index to database...")
    save_start = time.time()
    if save_file_index_to_db(file_index):
        save_time = time.time() - save_start
        app_logger.info(f"✅ File index saved to database in {save_time:.2f} seconds")
    else:
        app_logger.warning("Failed to save file index to database")



    # Legacy: update_recent_files_from_scan(comic_files) - Removed as we now use file_index directly

def invalidate_file_index():
    """
    Invalidate the file index search cache.
    Note: With SQLite-backed index, we no longer need full rebuilds.
    This just clears the search cache to reflect updates.
    """
    clear_search_cache()
    app_logger.info("Search cache cleared")

def update_index_on_move(old_path, new_path):
    """
    Update file index when a file or directory is moved.
    Handles three scenarios:
    1. Move from outside /data to /data -> ADD to index
    2. Move within /data -> UPDATE in index
    3. Move from /data to outside /data -> DELETE from index

    Args:
        old_path: Original path
        new_path: New path after move
    """
    try:
        # Normalize paths for comparison
        normalized_old = os.path.normpath(old_path)
        normalized_new = os.path.normpath(new_path)
        normalized_data_dir = os.path.normpath(DATA_DIR)

        # Check if old and new paths are in DATA_DIR using robust comparison
        # Same logic as log_file_if_in_data() for consistency
        def is_in_data_dir(path):
            try:
                common_path = os.path.commonpath([path, normalized_data_dir])
                return os.path.samefile(common_path, normalized_data_dir)
            except (ValueError, OSError):
                # Fallback: Check if normalized path starts with DATA_DIR
                return path.startswith(normalized_data_dir)

        old_in_data = is_in_data_dir(normalized_old)
        new_in_data = is_in_data_dir(normalized_new)

        # Debug logging to help diagnose path comparison issues
        app_logger.debug(f"Path comparison - Old: {normalized_old} (in_data: {old_in_data})")
        app_logger.debug(f"Path comparison - New: {normalized_new} (in_data: {new_in_data})")
        app_logger.debug(f"Path comparison - DATA_DIR: {normalized_data_dir}")

        # Scenario 1: Moving INTO /data (from WATCH/TEMP) -> ADD to index
        if not old_in_data and new_in_data:
            app_logger.info(f"📥 Adding to index (moved into /data): {new_path}")
            update_index_on_create(new_path)
            return

        # Scenario 2: Moving OUT OF /data -> DELETE from index
        if old_in_data and not new_in_data:
            app_logger.info(f"📤 Removing from index (moved out of /data): {old_path}")
            update_index_on_delete(old_path)
            return

        # Scenario 3: Moving WITHIN /data -> UPDATE in index
        if old_in_data and new_in_data:
            app_logger.info(f"🔄 Updating index (moved within /data): {old_path} -> {new_path}")

            excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db", ".xml"}
            allowed_files = {"missing.txt", "cvinfo"}
            is_file = os.path.isfile(new_path)

            if is_file:
                # Update single file entry
                file_name = os.path.basename(new_path)
                _, ext = os.path.splitext(file_name.lower())

                # Skip excluded files (but allow specific files like missing.txt and cvinfo)
                if file_name.lower() not in allowed_files and (ext in excluded_extensions or file_name.startswith(('.', '-', '_'))):
                    return

                parent = os.path.dirname(new_path)
                update_file_index_entry(old_path, name=file_name, new_path=new_path, parent=parent)
                app_logger.debug(f"Updated file index for moved file: {old_path} -> {new_path}")

            else:
                # Update directory and all children
                # First update the directory itself
                dir_name = os.path.basename(new_path)
                parent = os.path.dirname(new_path)
                update_file_index_entry(old_path, name=dir_name, new_path=new_path, parent=parent)

                # Update all children paths
                # We need to update paths that start with old_path to start with new_path
                conn = get_db_connection()
                if conn:
                    c = conn.cursor()
                    # Update all entries whose path starts with old_path
                    c.execute('''
                        UPDATE file_index
                        SET path = ? || SUBSTR(path, ?),
                            parent = ? || SUBSTR(parent, ?)
                        WHERE path LIKE ?
                    ''', (new_path, len(old_path) + 1, new_path, len(old_path) + 1, f"{old_path}/%"))

                    conn.commit()
                    rows_affected = c.rowcount
                    conn.close()
                    app_logger.debug(f"Updated {rows_affected} child entries for moved directory: {old_path} -> {new_path}")

            # Clear search cache
            clear_search_cache()
            return

        # Scenario 4: Both outside /data -> do nothing
        app_logger.debug(f"File moved outside /data, no index update needed: {old_path} -> {new_path}")

    except Exception as e:
        app_logger.error(f"Failed to update index on move {old_path} -> {new_path}: {e}")

def update_index_on_delete(path):
    """
    Update file index when a file or directory is deleted.

    Args:
        path: Path of deleted item
    """
    try:
        delete_file_index_entry(path)
        clear_search_cache()
        app_logger.debug(f"Updated file index for deleted item: {path}")
    except Exception as e:
        app_logger.error(f"Failed to update index on delete {path}: {e}")

def update_index_on_create(path):
    """
    Update file index when a file or directory is created.
    If it's a directory, recursively indexes all contents.

    Args:
        path: Path of new item
    """
    try:
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db", ".xml"}
        allowed_files = {"missing.txt", "cvinfo"}

        is_file = os.path.isfile(path)
        name = os.path.basename(path)
        parent = os.path.dirname(path)

        if is_file:
            # Check if file should be indexed (but allow specific files like missing.txt and cvinfo)
            _, ext = os.path.splitext(name.lower())
            if name.lower() not in allowed_files and (ext in excluded_extensions or name.startswith(('.', '-', '_'))):
                return

            size = os.path.getsize(path) if os.path.exists(path) else None
            mtime = os.path.getmtime(path) if os.path.exists(path) else None
            add_file_index_entry(name, path, 'file', size=size, parent=parent, modified_at=mtime)
            app_logger.debug(f"Added file to index: {path}")
        else:
            # Directory - add it and recursively add all contents
            add_file_index_entry(name, path, 'directory', parent=parent)
            app_logger.debug(f"Added directory to index: {path}")

            # Recursively index all files and subdirectories
            try:
                for root, dirs, files in os.walk(path):
                    # Skip hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]

                    # Index subdirectories
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        dir_parent = os.path.dirname(dir_path)
                        add_file_index_entry(dir_name, dir_path, 'directory', parent=dir_parent)

                    # Index files
                    for file_name in files:
                        if file_name.startswith('.') or file_name.startswith('_'):
                            continue

                        _, ext = os.path.splitext(file_name.lower())
                        if file_name.lower() not in allowed_files and ext in excluded_extensions:
                            continue

                        file_path = os.path.join(root, file_name)
                        file_parent = os.path.dirname(file_path)
                        try:
                            file_size = os.path.getsize(file_path)
                            add_file_index_entry(file_name, file_path, 'file', size=file_size, parent=file_parent)
                        except (OSError, IOError):
                            continue

                app_logger.info(f"Recursively indexed directory and contents: {path}")
            except Exception as e:
                app_logger.error(f"Error recursively indexing directory {path}: {e}")

        clear_search_cache()

    except Exception as e:
        app_logger.error(f"Failed to update index on create {path}: {e}")

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
#  Auto-Fetch ComicVine Metadata    #
#####################################
def auto_fetch_comicvine_metadata(destination_path):
    """
    Automatically fetch ComicVine metadata for moved files if conditions are met.
    Only triggers for non-root /data directories that have a cvinfo file.

    Returns:
        The final file path (renamed path if file was renamed, original path otherwise)
    """
    try:
        from comicvine import auto_fetch_metadata_for_folder

        # Check 1: Is COMICVINE_API_KEY configured?
        api_key = app.config.get("COMICVINE_API_KEY", "")
        if not api_key:
            app_logger.debug("ComicVine API key not configured, skipping auto-metadata")
            return destination_path

        # Determine the folder to check for cvinfo
        if os.path.isfile(destination_path):
            folder_path = os.path.dirname(destination_path)
            target_file = destination_path
        else:
            folder_path = destination_path
            target_file = None

        # Check: Is this a non-root /data directory? (must have at least 2 path components after /data)
        data_dir = DATA_DIR
        rel_path = os.path.relpath(folder_path, data_dir)
        # Normalize path separators for cross-platform compatibility
        rel_path_normalized = rel_path.replace("\\", "/")
        if rel_path == "." or "/" not in rel_path_normalized:
            app_logger.debug(f"Skipping auto-metadata for root-level directory: {folder_path}")
            return destination_path

        # Trigger metadata fetch for the folder
        result = auto_fetch_metadata_for_folder(folder_path, api_key, target_file=target_file)

        if result['processed'] > 0:
            app_logger.info(f"Auto-fetched ComicVine metadata: {result['processed']} processed, {result['skipped']} skipped, {result['errors']} errors")

            # Check if the target file was renamed and return the new path
            if target_file:
                for detail in result.get('details', []):
                    if detail.get('renamed_to') and detail.get('file') == target_file:
                        app_logger.info(f"File was renamed: {target_file} -> {detail['renamed_to']}")
                        return detail['renamed_to']

        return destination_path

    except Exception as e:
        app_logger.error(f"Error in auto-fetch metadata: {e}")
        return destination_path


def auto_fetch_metron_metadata(destination_path):
    """
    Automatically fetch Metron metadata for moved files if conditions are met.
    Only triggers for non-root /data directories that have a cvinfo file.

    Returns:
        The final file path (renamed path if file was renamed, original path otherwise)
    """
    try:
        from models.metron import (
            is_mokkari_available, get_api, get_metron_series_id,
            get_issue_metadata, map_to_comicinfo
        )
        from comicvine import extract_issue_number, generate_comicinfo_xml, add_comicinfo_to_archive
        from comicinfo import read_comicinfo_from_zip
        from rename import load_custom_rename_config

        # Check 1: Is Mokkari library available?
        if not is_mokkari_available():
            app_logger.debug("Mokkari library not available, skipping Metron metadata")
            return destination_path

        # Check 2: Are Metron credentials configured?
        username = app.config.get("METRON_USERNAME", "")
        password = app.config.get("METRON_PASSWORD", "")
        if not username or not password:
            app_logger.debug("Metron credentials not configured, skipping Metron metadata")
            return destination_path

        # Determine the folder to check for cvinfo
        if os.path.isfile(destination_path):
            folder_path = os.path.dirname(destination_path)
            target_file = destination_path
        else:
            folder_path = destination_path
            target_file = None

        # Check 3: Is this a non-root /data directory?
        data_dir = DATA_DIR
        rel_path = os.path.relpath(folder_path, data_dir)
        rel_path_normalized = rel_path.replace("\\", "/")
        if rel_path == "." or "/" not in rel_path_normalized:
            app_logger.debug(f"Skipping Metron metadata for root-level directory: {folder_path}")
            return destination_path

        # Check 4: Does cvinfo exist?
        cvinfo_path = None
        for filename in os.listdir(folder_path):
            if filename.lower() == 'cvinfo':
                cvinfo_path = os.path.join(folder_path, filename)
                break

        if not cvinfo_path:
            app_logger.debug(f"No cvinfo file found in {folder_path}, skipping Metron metadata")
            return destination_path

        # Initialize Metron API
        api = get_api(username, password)
        if not api:
            app_logger.warning("Failed to initialize Metron API")
            return destination_path

        # Get Metron series ID (from cvinfo or lookup by CV ID)
        series_id = get_metron_series_id(cvinfo_path, api)
        if not series_id:
            app_logger.debug("Could not determine Metron series ID")
            return destination_path

        # If target_file specified, only process that file
        if target_file:
            files_to_process = [target_file]
        else:
            # Get all comic files in folder
            files_to_process = [
                os.path.join(folder_path, f) for f in os.listdir(folder_path)
                if f.lower().endswith(('.cbz', '.cbr'))
            ]

        processed = 0
        renamed_path = None

        for file_path in files_to_process:
            # Skip if already has metadata
            existing = read_comicinfo_from_zip(file_path)
            existing_notes = existing.get('Notes', '').strip() if existing else ''
            # Skip if has metadata, unless it's just Amazon scraped data
            if existing_notes and 'Scraped metadata from Amazon' not in existing_notes:
                app_logger.debug(f"Skipping {file_path} - already has metadata")
                continue

            # Extract issue number from filename
            issue_number = extract_issue_number(os.path.basename(file_path))
            if not issue_number:
                app_logger.warning(f"Could not extract issue number from {file_path}")
                continue

            # Fetch metadata from Metron
            issue_data = get_issue_metadata(api, series_id, issue_number)
            if not issue_data:
                continue

            # Map to ComicInfo format
            metadata = map_to_comicinfo(issue_data)

            # Generate and add ComicInfo.xml
            xml_content = generate_comicinfo_xml(metadata)
            if add_comicinfo_to_archive(file_path, xml_content):
                processed += 1
                app_logger.info(f"Added Metron metadata to {file_path}")

                # Auto-rename if enabled
                try:
                    custom_enabled, custom_pattern = load_custom_rename_config()
                    if custom_enabled and custom_pattern:
                        series = metadata.get('Series', '')
                        series = series.replace(':', ' -')
                        series = re.sub(r'[<>"/\\|?*]', '', series)
                        issue_num_padded = str(metadata.get('Number', '')).zfill(3)
                        year = str(metadata.get('Year', ''))

                        new_name = custom_pattern
                        new_name = re.sub(r'\{series_name\}', series, new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\{issue_number\}', issue_num_padded, new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\{year\}|\{YYYY\}', year, new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\{volume_number\}', '', new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\s+', ' ', new_name).strip()
                        new_name = re.sub(r'\s*\(\s*\)', '', new_name).strip()

                        _, ext = os.path.splitext(file_path)
                        new_name = new_name + ext

                        directory = os.path.dirname(file_path)
                        old_name = os.path.basename(file_path)
                        new_path = os.path.join(directory, new_name)

                        if new_name != old_name and not os.path.exists(new_path):
                            os.rename(file_path, new_path)
                            app_logger.info(f"Renamed: {old_name} -> {new_name}")
                            if file_path == target_file:
                                renamed_path = new_path
                except Exception as rename_error:
                    app_logger.error(f"Error during auto-rename: {rename_error}")

        if processed > 0:
            app_logger.info(f"Auto-fetched Metron metadata: {processed} files processed")

        return renamed_path if renamed_path else destination_path

    except Exception as e:
        app_logger.error(f"Error in auto-fetch Metron metadata: {e}")
        return destination_path


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

                        # Auto-fetch metadata (try Metron first, then ComicVine as fallback)
                        final_path = auto_fetch_metron_metadata(destination)
                        # If Metron didn't process, try ComicVine
                        final_path = auto_fetch_comicvine_metadata(final_path)

                        # Log file to recent_files with the final path (renamed or original)
                        log_file_if_in_data(final_path)

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

                        # Auto-fetch metadata (try Metron first, then ComicVine as fallback)
                        auto_fetch_metron_metadata(destination)
                        auto_fetch_comicvine_metadata(destination)

                        # Log all comic files in the moved directory to recent_files
                        try:
                            for root, _, files_in_dir in os.walk(destination):
                                for file in files_in_dir:
                                    file_path = os.path.join(root, file)
                                    log_file_if_in_data(file_path)
                        except Exception as e:
                            app_logger.warning(f"Error logging files from directory {destination}: {e}")

                        # Update file index incrementally (no cache invalidation needed with DB-first approach)
                        update_index_on_move(source, destination)

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

                # Auto-fetch metadata (try Metron first, then ComicVine as fallback)
                final_path = auto_fetch_metron_metadata(destination)
                # If Metron didn't process, try ComicVine
                final_path = auto_fetch_comicvine_metadata(final_path)

                # Log file to recent_files with the final path (renamed or original)
                if is_file:
                    log_file_if_in_data(final_path)
                else:
                    # For directories, log all comic files inside (after any renames)
                    try:
                        for root, _, files in os.walk(destination):
                            for file in files:
                                file_path = os.path.join(root, file)
                                log_file_if_in_data(file_path)
                    except Exception as e:
                        app_logger.warning(f"Error logging files from directory {destination}: {e}")

                # Update file index incrementally (no cache invalidation needed with DB-first approach)
                update_index_on_move(source, final_path if is_file else destination)

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

                # Resize to match existing images in directory
                # Skip resizing for 'header' and 'folder' images
                base_name_check = os.path.splitext(filename)[0].lower()
                if base_name_check not in ('header', 'folder'):
                    resize_upload(file_path, target_dir)

                file_size = os.path.getsize(file_path)  # Get size after resize

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

        # Note: No cache invalidation - file_index is updated via update_index_on_create if needed

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


def resize_upload(file_path, target_dir):
    """
    Resize an uploaded image to match dimensions of existing images in the directory.

    Args:
        file_path: Path to the uploaded file
        target_dir: Directory containing existing images

    Returns:
        True if resized, False if no resize needed or no reference image found
    """
    try:
        # Find first existing image in directory (excluding the just-uploaded file)
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        reference_image = None

        for filename in sorted(os.listdir(target_dir)):
            if filename == os.path.basename(file_path):
                continue  # Skip the uploaded file itself

            ext = os.path.splitext(filename)[1].lower()
            if ext in image_extensions:
                ref_path = os.path.join(target_dir, filename)
                if os.path.isfile(ref_path):
                    reference_image = ref_path
                    break

        if not reference_image:
            app_logger.info(f"No reference image found in {target_dir}, skipping resize")
            return False

        # Get reference dimensions
        with Image.open(reference_image) as ref_img:
            target_width, target_height = ref_img.size

        # Open and resize the uploaded image
        with Image.open(file_path) as img:
            current_width, current_height = img.size

            # Skip if already same size
            if current_width == target_width and current_height == target_height:
                app_logger.info(f"Image {file_path} already matches dimensions ({target_width}x{target_height})")
                return False

            # Convert RGBA/P modes to RGB for JPEG compatibility
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            # Resize to match reference dimensions
            resized = img.resize((target_width, target_height), Image.Resampling.LANCZOS)

            # Save back to same path (preserve format based on extension)
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ('.jpg', '.jpeg'):
                resized.save(file_path, 'JPEG', quality=95)
            elif ext == '.png':
                resized.save(file_path, 'PNG')
            elif ext == '.webp':
                resized.save(file_path, 'WEBP', quality=95)
            else:
                resized.save(file_path)

            app_logger.info(f"Resized {file_path} from {current_width}x{current_height} to {target_width}x{target_height}")
            return True

    except Exception as e:
        app_logger.error(f"Error resizing upload {file_path}: {e}")
        return False


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


@app.route('/api/combine-cbz', methods=['POST'])
def combine_cbz():
    """Combine multiple CBZ files into a single CBZ file."""
    data = request.get_json()
    files = data.get('files', [])
    output_name = data.get('output_name', 'Combined')
    directory = data.get('directory')

    if len(files) < 2:
        return jsonify({"error": "At least 2 files required"}), 400

    if not directory:
        return jsonify({"error": "Directory not specified"}), 400

    # Security: Validate all paths
    watch_dir = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_dir = config.get("SETTINGS", "TARGET", fallback="/processed")

    for f in files:
        normalized = os.path.normpath(f)
        if not (normalized.startswith(os.path.normpath(DATA_DIR)) or
                normalized.startswith(os.path.normpath(watch_dir)) or
                normalized.startswith(os.path.normpath(target_dir))):
            return jsonify({"error": "Access denied"}), 403

    temp_dir = None
    try:
        # Create temp extraction directory
        temp_dir = os.path.join(directory, f'.tmp_combine_{os.getpid()}')
        os.makedirs(temp_dir, exist_ok=True)

        file_counter = {}  # Track duplicate filenames
        extracted_count = 0

        # Extract all files from each CBZ
        for cbz_path in files:
            if not os.path.exists(cbz_path):
                app_logger.warning(f"CBZ file not found, skipping: {cbz_path}")
                continue

            try:
                with zipfile.ZipFile(cbz_path, 'r') as zf:
                    for name in zf.namelist():
                        # Skip directories and metadata files
                        if name.endswith('/') or name.lower() == 'comicinfo.xml':
                            continue

                        # Get base filename (flatten nested directories)
                        base_name = os.path.basename(name)
                        if not base_name:  # Skip empty names
                            continue

                        name_part, ext = os.path.splitext(base_name)

                        # Handle duplicates: append a, b, c, etc.
                        if base_name in file_counter:
                            count = file_counter[base_name]
                            suffix = chr(ord('a') + count)
                            new_name = f"{name_part}{suffix}{ext}"
                            file_counter[base_name] += 1
                        else:
                            new_name = base_name
                            file_counter[base_name] = 1

                        # Extract to temp dir with new name
                        content = zf.read(name)
                        dest_path = os.path.join(temp_dir, new_name)
                        with open(dest_path, 'wb') as f:
                            f.write(content)
                        extracted_count += 1

            except zipfile.BadZipFile:
                app_logger.warning(f"Invalid CBZ file, skipping: {cbz_path}")
                continue

        if extracted_count == 0:
            shutil.rmtree(temp_dir)
            return jsonify({"error": "No files could be extracted from the selected CBZ files"}), 400

        # Create output CBZ
        output_path = os.path.join(directory, f"{output_name}.cbz")

        # Handle existing file - append (1), (2), etc.
        counter = 1
        while os.path.exists(output_path):
            output_path = os.path.join(directory, f"{output_name} ({counter}).cbz")
            counter += 1

        # Compress temp dir to CBZ
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            extracted_files = sorted(os.listdir(temp_dir))
            for filename in extracted_files:
                file_path_full = os.path.join(temp_dir, filename)
                zf.write(file_path_full, filename)

        # Cleanup temp directory
        shutil.rmtree(temp_dir)
        temp_dir = None

        app_logger.info(f"Combined {len(files)} CBZ files into {output_path} ({extracted_count} images)")
        return jsonify({
            "success": True,
            "output_file": os.path.basename(output_path),
            "total_images": extracted_count
        })

    except Exception as e:
        # Cleanup on error
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        app_logger.error(f"Error combining CBZ files: {e}")
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
#           Collection Page             #
#####################################

@app.route('/collection')
@app.route('/collection/<path:subpath>')
def collection(subpath=''):
    """Render the visual browse page with optional path."""
    # Convert URL path to filesystem path (e.g., /collection/Marvel -> /data/Marvel)
    initial_path = f'/data/{subpath}' if subpath else ''
    return render_template('collection.html', 
                           initial_path=initial_path,
                           rec_enabled=config.get("SETTINGS", "REC_ENABLED", fallback="True") == "True")


@app.route('/to-read')
def to_read_page():
    """Render the 'To Read' page showing all items marked as 'want to read'."""
    return render_template('to_read.html')

def find_folder_thumbnail(folder_path):
    """Find a folder thumbnail image in the given directory.

    Args:
        folder_path: Path to the directory to search

    Returns:
        Path to the thumbnail image if found, None otherwise
    """
    allowed_extensions = {'.png', '.gif', '.jpg', '.jpeg'}
    allowed_names = {'folder'}  # Only use folder.* thumbnails, ignore cover.*

    try:
        entries = os.listdir(folder_path)
        for entry in entries:
            name_without_ext, ext = os.path.splitext(entry.lower())
            if name_without_ext in allowed_names and ext in allowed_extensions:
                return os.path.join(folder_path, entry)
    except (OSError, IOError):
        pass

    return None


def find_folder_thumbnails_batch(folder_paths):
    """
    Find folder/cover thumbnails for multiple directories.
    Uses ThreadPoolExecutor to parallelize filesystem checks.

    Note: Cannot use file_index because image files (.png, .jpg, etc.)
    are excluded from the index during build.

    Args:
        folder_paths: List of directory paths to check

    Returns:
        Dict mapping path -> thumbnail_path or None
    """
    if not folder_paths:
        return {}

    results = {}

    # Use thread pool to parallelize filesystem checks
    with ThreadPoolExecutor(max_workers=min(10, len(folder_paths))) as executor:
        future_to_path = {
            executor.submit(find_folder_thumbnail, path): path
            for path in folder_paths
        }
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            try:
                thumb = future.result()
                results[path] = thumb
                if thumb:
                    app_logger.debug(f"Found thumbnail for {path}: {thumb}")
            except Exception as e:
                app_logger.error(f"Error finding thumbnail for {path}: {e}")
                results[path] = None

    # Fill in any missing paths
    for p in folder_paths:
        if p not in results:
            results[p] = None

    return results


@app.route('/api/browse')
def api_browse():
    """
    Get directory listing for the browse page.
    Reads directly from file_index database for instant results.
    """
    request_start = time.time()

    path = request.args.get('path')
    if not path:
        path = DATA_DIR

    try:
        app_logger.info(f"🔍 /api/browse request for path: {path}")

        # Query file_index directly - instant results via indexed query
        directories, files = get_directory_children(path)

        # Build response for directories
        processed_directories = []
        for d in directories:
            dir_info = {
                'name': d['name'],
                'has_thumbnail': d.get('has_thumbnail', False),
                'has_files': None,  # Will be loaded progressively if needed
                'folder_count': None,
                'file_count': None
            }

            if d.get('has_thumbnail'):
                # Check for folder.png first, then folder.jpg
                for ext in ['.png', '.jpg', '.jpeg']:
                    thumb_path = os.path.join(d['path'], f'folder{ext}')
                    if os.path.exists(thumb_path):
                        dir_info['thumbnail_url'] = url_for('serve_folder_thumbnail', path=thumb_path)
                        break

            processed_directories.append(dir_info)

        # Build response for files
        processed_files = []
        for f in files:
            filename = f['name']
            file_path = f['path']

            file_info = {
                'name': filename,
                'size': f.get('size', 0)
            }

            # Add thumbnail info for comic files
            if filename.lower().endswith(('.cbz', '.cbr', '.zip')):
                file_info['has_thumbnail'] = True
                file_info['thumbnail_url'] = url_for('get_thumbnail', path=file_path)
            else:
                file_info['has_thumbnail'] = False

            processed_files.append(file_info)

        result = {
            "current_path": path,
            "directories": processed_directories,
            "files": processed_files,
            "parent": os.path.dirname(path) if path != DATA_DIR else None
        }


        
        # Check for header image
        for ext in ['.jpg', '.png', '.gif', '.jpeg']:
            header_name = f'header{ext}'
            header_path = os.path.join(path, header_name)
            if os.path.exists(header_path):
                result['header_image_url'] = url_for('serve_folder_thumbnail', path=header_path)
                break
        
        # Check for overlay image
        overlay_path = os.path.join(path, 'overlay.png')
        if os.path.exists(overlay_path):
            result['overlay_image_url'] = url_for('serve_folder_thumbnail', path=overlay_path)

        elapsed = time.time() - request_start
        app_logger.info(f"✅ /api/browse returned {len(directories)} dirs, {len(files)} files for {path} in {elapsed:.3f}s")

        return jsonify(result)
    except Exception as e:
        app_logger.error(f"Error browsing {path}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/issues-read-paths')
def api_issues_read_paths():
    """Return list of all read issue paths for client-side caching."""
    from database import get_issues_read
    issues = get_issues_read()
    paths = [issue['issue_path'] for issue in issues]
    return jsonify({"paths": paths})


@app.route('/api/scan-directory', methods=['POST'])
def api_scan_directory():
    """
    Recursively scan a directory and update the file_index.
    Used for manual refresh of directory contents.
    """
    data = request.get_json()
    path = data.get('path')

    if not path:
        return jsonify({"error": "Missing path parameter"}), 400

    # Security check - ensure path is within DATA_DIR
    normalized_path = os.path.normpath(path)
    normalized_data_dir = os.path.normpath(DATA_DIR)
    if not normalized_path.startswith(normalized_data_dir):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.exists(path):
        return jsonify({"error": "Directory not found"}), 404

    if not os.path.isdir(path):
        return jsonify({"error": "Path is not a directory"}), 400

    try:
        app_logger.info(f"🔄 Starting recursive scan of: {path}")
        scan_start = time.time()

        # Excluded extensions (same as build_file_index)
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db", ".xml"}
        allowed_files = {"missing.txt", "cvinfo"}

        # Delete all existing entries under this path (including the path itself)
        delete_file_index_entry(path)

        # Track counts
        dir_count = 0
        file_count = 0

        # Helper function to check for folder thumbnail
        def check_has_thumbnail(folder_path):
            for ext in ['.png', '.jpg', '.jpeg']:
                if os.path.exists(os.path.join(folder_path, f'folder{ext}')):
                    return 1
            return 0

        # Re-add the root directory entry
        parent_dir = os.path.dirname(path)
        add_file_index_entry(
            name=os.path.basename(path),
            path=path,
            entry_type='directory',
            parent=parent_dir,
            has_thumbnail=check_has_thumbnail(path)
        )
        dir_count += 1

        # Recursively scan filesystem
        for root, dirs, files in os.walk(path):
            # Filter hidden directories in-place
            dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))]

            # Add directory entries
            for d in dirs:
                full_path = os.path.join(root, d)
                add_file_index_entry(
                    name=d,
                    path=full_path,
                    entry_type='directory',
                    parent=root,
                    has_thumbnail=check_has_thumbnail(full_path)
                )
                dir_count += 1

            # Add file entries
            for f in files:
                # Skip hidden files
                if f.startswith(('.', '_')):
                    continue

                # Skip excluded extensions (but allow specific files like missing.txt and cvinfo)
                _, ext = os.path.splitext(f.lower())
                if f.lower() not in allowed_files and ext in excluded_extensions:
                    continue

                full_path = os.path.join(root, f)
                try:
                    size = os.path.getsize(full_path)
                except (OSError, IOError):
                    size = 0

                add_file_index_entry(
                    name=f,
                    path=full_path,
                    entry_type='file',
                    parent=root,
                    size=size
                )
                file_count += 1

        elapsed = time.time() - scan_start
        app_logger.info(f"✅ Scan complete: {path} - {dir_count} directories, {file_count} files in {elapsed:.2f}s")

        return jsonify({
            "success": True,
            "message": f"Scanned {path}",
            "directories": dir_count,
            "files": file_count,
            "elapsed": round(elapsed, 2)
        })

    except Exception as e:
        app_logger.error(f"Error scanning directory {path}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/browse-metadata', methods=['POST'])
def api_browse_metadata():
    """
    Batch fetch metadata (counts) for multiple paths.
    Used for progressive loading after initial browse response.
    """
    data = request.get_json()
    paths = data.get('paths', [])

    if not paths:
        return jsonify({"error": "No paths provided"}), 400

    if len(paths) > 100:
        return jsonify({"error": "Too many paths (max 100)"}), 400

    try:
        counts = get_path_counts_batch(paths)

        results = {}
        for path, (folder_count, file_count) in counts.items():
            results[path] = {
                'folder_count': folder_count,
                'file_count': file_count,
                'has_files': file_count > 0
            }

        return jsonify({"metadata": results})

    except Exception as e:
        app_logger.error(f"Error fetching browse metadata: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/browse-thumbnails', methods=['POST'])
def api_browse_thumbnails():
    """
    Batch fetch folder thumbnails for multiple paths.
    Used for progressive loading after initial browse response.
    """
    data = request.get_json()
    paths = data.get('paths', [])

    if not paths:
        return jsonify({"error": "No paths provided"}), 400

    if len(paths) > 50:
        return jsonify({"error": "Too many paths (max 50)"}), 400

    try:
        folder_thumbs = find_folder_thumbnails_batch(paths)

        results = {}
        for path, thumb in folder_thumbs.items():
            if thumb:
                results[path] = {
                    'has_thumbnail': True,
                    'thumbnail_url': url_for('serve_folder_thumbnail', path=thumb)
                }
            else:
                results[path] = {
                    'has_thumbnail': False,
                    'thumbnail_url': None
                }

        return jsonify({"thumbnails": results})

    except Exception as e:
        app_logger.error(f"Error fetching browse thumbnails: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/clear-browse-cache', methods=['POST'])
def api_clear_browse_cache():
    """Clear the browse cache to force refresh on next load."""
    try:
        data = request.get_json() or {}
        path = data.get('path')

        if path:
            # Clear specific path
            invalidate_browse_cache(path)
            app_logger.info(f"Cleared browse cache for: {path}")
            return jsonify({
                "success": True,
                "message": f"Browse cache cleared for {path}"
            })
        else:
            # Clear all browse cache
            clear_browse_cache()
            app_logger.info("Cleared all browse cache")
            return jsonify({
                "success": True,
                "message": "All browse cache cleared"
            })
    except Exception as e:
        app_logger.error(f"Error clearing browse cache: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
    excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", ".json", ".db", ".xml"}
    allowed_files = {"missing.txt", "cvinfo"}

    files = []

    # Recursively walk directory
    for root, dirs, filenames in os.walk(full_path):
        for filename in filenames:
            # Get file extension (lowercase)
            _, ext = os.path.splitext(filename.lower())

            # Check if file should be excluded (but allow specific files like missing.txt and cvinfo)
            if filename.lower() not in allowed_files and ext in excluded_extensions:
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

        # Try pattern without year: "Series Name 123.ext" or "Series Name #123.ext"
        match_no_year = re.match(r'^(.+?)\s+#?(\d+)', filename, re.IGNORECASE)
        if match_no_year:
            series_name = match_no_year.group(1).strip().lower()
            issue_number = int(match_no_year.group(2))
            return (series_name, 0, issue_number, filename.lower())

        # Final fallback - use filename as series name for proper alphabetical sorting
        return (filename.lower(), 0, 0, filename.lower())

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
        app_logger.error(traceback.format_exc())
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

@app.route('/api/read/<path:comic_path>/page/<int:page_num>')
def read_comic_page(comic_path, page_num):
    """Serve a specific page from a comic file."""

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
        app_logger.error(traceback.format_exc())
        if archive:
            archive.close()
        return send_file('static/images/error.svg', mimetype='image/svg+xml')

@app.route('/api/read/<path:comic_path>/info')
def read_comic_info(comic_path):
    """Get information about a comic file (page count, etc.)."""

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
        app_logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route('/api/mark-comic-read', methods=['POST'])
def api_mark_comic_read():
    """Mark a comic as read in the database."""
    data = request.get_json()
    comic_path = data.get('path')
    read_at = data.get('read_at')  # Optional ISO timestamp for backfilling
    page_count = data.get('page_count', 0)
    time_spent = data.get('time_spent', 0)

    if not comic_path:
        return jsonify({"error": "Missing path parameter"}), 400

    try:
        mark_issue_read(comic_path, read_at, page_count, time_spent)
        clear_stats_cache_keys(['library_stats', 'reading_history', 'reading_heatmap'])
        app_logger.info(f"Marked comic as read: {comic_path}" + (f" at {read_at}" if read_at else ""))
        return jsonify({"success": True})
    except Exception as e:
        app_logger.error(f"Error marking comic as read: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/recently-read')
def api_recently_read():
    """Get recently read issues with metadata for display."""
    limit = request.args.get('limit', 20, type=int)
    issues = get_issues_read()[:limit]

    result = []
    for issue in issues:
        path = issue['issue_path']
        if os.path.exists(path):
            result.append({
                'name': os.path.basename(path),
                'path': path,
                'read_at': issue['read_at'],
                'thumbnail_url': url_for('get_thumbnail', path=path)
            })
    return jsonify(result)


@app.route('/api/reading-position', methods=['GET', 'POST', 'DELETE'])
def api_reading_position():
    """
    Manage reading position bookmarks.
    GET: Get saved position for a comic
    POST: Save/update position for a comic
    DELETE: Remove saved position
    """
    from database import save_reading_position, get_reading_position, delete_reading_position

    if request.method == 'GET':
        comic_path = request.args.get('path')
        if not comic_path:
            return jsonify({"error": "Missing path parameter"}), 400

        position = get_reading_position(comic_path)
        if position:
            return jsonify({
                "page_number": position['page_number'],
                "total_pages": position['total_pages'],
                "updated_at": position['updated_at'],
                "time_spent": position.get('time_spent', 0)
            })
        return jsonify({"page_number": None})

    elif request.method == 'POST':
        data = request.get_json()
        comic_path = data.get('comic_path')
        page_number = data.get('page_number')
        total_pages = data.get('total_pages')
        time_spent = data.get('time_spent', 0)

        if not comic_path or page_number is None:
            return jsonify({"error": "Missing comic_path or page_number"}), 400

        success = save_reading_position(comic_path, page_number, total_pages, time_spent)
        return jsonify({"success": success})

    elif request.method == 'DELETE':
        comic_path = request.args.get('path')
        if not comic_path:
            return jsonify({"error": "Missing path parameter"}), 400

        success = delete_reading_position(comic_path)
        return jsonify({"success": success})


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


def create_nested_folder_thumbnail(comic_stack_img, folder_icon_path, canvas_size=(200, 300)):
    """Composite the comic stack behind a folder icon for nested folder thumbnails."""
    folder_icon = Image.open(folder_icon_path).convert("RGBA")

    stack = comic_stack_img.convert("RGBA")

    # Scale stack to 175px wide with proportionate height
    new_w = 190
    aspect_ratio = stack.height / stack.width
    new_h = int(new_w * aspect_ratio)

    stack_resized = stack.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Position stack: centered horizontally, 20px from bottom
    x_pos = (canvas_size[0] - new_w) // 2
    y_pos = canvas_size[1] - new_h - 20  # 20px from bottom

    # Create final canvas
    final_thumb = Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    # Paste stack FIRST (behind)
    final_thumb.paste(stack_resized, (x_pos, y_pos), mask=stack_resized)

    # Paste folder icon ON TOP (in front)
    final_thumb.paste(folder_icon, (0, 0), mask=folder_icon)

    # Resize final image to 167px width with proportionate height
    final_w = 167
    aspect = final_thumb.height / final_thumb.width
    final_h = int(final_w * aspect)
    final_thumb = final_thumb.resize((final_w, final_h), Image.Resampling.LANCZOS)

    return final_thumb


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
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif" ".html", ".css", ".ds_store", "cvinfo", ".json", ".db", ".xml"}

        # Find comic files in the folder
        comic_files = []
        is_nested = False  # Track if we're using nested folder comics

        # First, check for direct comic files
        for item in sorted(os.listdir(folder_path)):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path):
                _, ext = os.path.splitext(item.lower())
                if ext not in excluded_extensions and not item.startswith(('.', '-', '_')):
                    if ext in ['.cbz', '.cbr', '.zip']:
                        comic_files.append(item_path)

        # If no direct comics, scan subfolders
        if not comic_files:
            is_nested = True
            subfolder_comics = {}  # {subfolder_path: [comic_files]}

            for item in sorted(os.listdir(folder_path)):
                item_path = os.path.join(folder_path, item)
                if os.path.isdir(item_path) and not item.startswith(('.', '_')):
                    folder_comics = []
                    for subitem in sorted(os.listdir(item_path)):
                        subitem_path = os.path.join(item_path, subitem)
                        if os.path.isfile(subitem_path):
                            _, ext = os.path.splitext(subitem.lower())
                            if ext in ['.cbz', '.cbr', '.zip']:
                                folder_comics.append(subitem_path)
                    if folder_comics:
                        subfolder_comics[item_path] = folder_comics

            if not subfolder_comics:
                return jsonify({"error": "No comic files found in folder or subfolders"}), 400

            # Distribute 4 slots across subfolders
            MAX_COVERS = 4
            subfolders = list(subfolder_comics.keys())
            num_folders = len(subfolders)

            if num_folders >= MAX_COVERS:
                # 4+ folders: 1 from each of first 4
                for i in range(MAX_COVERS):
                    comic_files.append(subfolder_comics[subfolders[i]][0])
            else:
                # Fewer than 4 folders: distribute evenly with extras going to earlier folders
                per_folder = MAX_COVERS // num_folders
                remainder = MAX_COVERS % num_folders

                for i, folder in enumerate(subfolders):
                    count = per_folder + (1 if i < remainder else 0)
                    comic_files.extend(subfolder_comics[folder][:count])

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
        THUMB_SIZE = (150, 245)
        ROTATION_LIMIT = 10
        Y_OFFSET = 0

        final_canvas = Image.new('RGBA', CANVAS_SIZE, (0, 0, 0, 0))

        # Reverse cached_thumbs so we paste from back to front (001 pasted last/on top)
        reversed_thumbs = list(reversed(cached_thumbs))

        # --- 3. Define Angles ---
        angles = []
        for i in range(len(reversed_thumbs)):
            if i == len(reversed_thumbs) - 1:
                angles.append(0)
            else:
                # Randomize rotation for background images
                angles.append(random.randint(-ROTATION_LIMIT, ROTATION_LIMIT))

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

                # Position on canvas with Y_OFFSET to move stack down
                final_x = (CANVAS_SIZE[0] - rotated_layer.width) // 2
                final_y = ((CANVAS_SIZE[1] - rotated_layer.height) // 2) + Y_OFFSET

                final_canvas.paste(rotated_layer, (final_x, final_y), rotated_layer)

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

        # If using nested folder comics, overlay on folder icon
        if is_nested:
            folder_icon_path = os.path.join(app.static_folder, 'images', 'folder-fill-200x300.png')
            if os.path.exists(folder_icon_path):
                final_canvas = create_nested_folder_thumbnail(final_canvas, folder_icon_path)

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


def generate_folder_thumbnail_internal(folder_path):
    """Internal function to generate folder thumbnail. Returns True on success, False on failure."""
    try:
        # Get cache directory
        cache_dir = config.get("SETTINGS", "CACHE_DIR", fallback="/cache")
        thumbnails_dir = os.path.join(cache_dir, "thumbnails")

        # Define excluded extensions
        excluded_extensions = {".png", ".jpg", ".jpeg", ".gif", ".html", ".css", ".ds_store", "cvinfo", ".json", ".db", ".xml"}

        # Find comic files in the folder
        comic_files = []
        is_nested = False

        # Check for direct comic files
        for item in sorted(os.listdir(folder_path)):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path):
                _, ext = os.path.splitext(item.lower())
                if ext not in excluded_extensions and not item.startswith(('.', '-', '_')):
                    if ext in ['.cbz', '.cbr', '.zip']:
                        comic_files.append(item_path)

        # If no direct comics, scan subfolders
        if not comic_files:
            is_nested = True
            subfolder_comics = {}

            for item in sorted(os.listdir(folder_path)):
                item_path = os.path.join(folder_path, item)
                if os.path.isdir(item_path) and not item.startswith(('.', '_')):
                    folder_comics = []
                    for subitem in sorted(os.listdir(item_path)):
                        subitem_path = os.path.join(item_path, subitem)
                        if os.path.isfile(subitem_path):
                            _, ext = os.path.splitext(subitem.lower())
                            if ext in ['.cbz', '.cbr', '.zip']:
                                folder_comics.append(subitem_path)
                    if folder_comics:
                        subfolder_comics[item_path] = folder_comics

            if not subfolder_comics:
                return False

            # Distribute 4 slots across subfolders
            MAX_COVERS = 4
            subfolders = list(subfolder_comics.keys())
            num_folders = len(subfolders)

            if num_folders >= MAX_COVERS:
                for i in range(MAX_COVERS):
                    comic_files.append(subfolder_comics[subfolders[i]][0])
            else:
                per_folder = MAX_COVERS // num_folders
                remainder = MAX_COVERS % num_folders
                for i, folder in enumerate(subfolders):
                    count = per_folder + (1 if i < remainder else 0)
                    comic_files.extend(subfolder_comics[folder][:count])

        # Get cached thumbnail paths for the first 4 comics
        MAX_COVERS = 4
        selected_files = comic_files[:MAX_COVERS]
        cached_thumbs = []

        for file_path in selected_files:
            path_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
            shard_dir = path_hash[:2]
            filename = f"{path_hash}.jpg"
            cache_path = os.path.join(thumbnails_dir, shard_dir, filename)

            if os.path.exists(cache_path):
                cached_thumbs.append(cache_path)

        if not cached_thumbs:
            return False

        # Create fanned stack thumbnail
        CANVAS_SIZE = (200, 300)
        THUMB_SIZE = (150, 245)
        ROTATION_LIMIT = 10
        Y_OFFSET = 0

        final_canvas = Image.new('RGBA', CANVAS_SIZE, (0, 0, 0, 0))
        reversed_thumbs = list(reversed(cached_thumbs))

        angles = []
        for i in range(len(reversed_thumbs)):
            if i == len(reversed_thumbs) - 1:
                angles.append(0)
            else:
                angles.append(random.randint(-ROTATION_LIMIT, ROTATION_LIMIT))

        for i, thumb_path in enumerate(reversed_thumbs):
            try:
                img = Image.open(thumb_path).convert("RGBA")
                img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)

                fitted_img = Image.new('RGBA', THUMB_SIZE, (0, 0, 0, 0))
                paste_x = (THUMB_SIZE[0] - img.width) // 2
                paste_y = (THUMB_SIZE[1] - img.height) // 2
                fitted_img.paste(img, (paste_x, paste_y), img if img.mode == 'RGBA' else None)

                layer_size = (int(THUMB_SIZE[0] * 1.5), int(THUMB_SIZE[1] * 1.5))
                layer = Image.new('RGBA', layer_size, (0, 0, 0, 0))

                layer_paste_x = (layer_size[0] - THUMB_SIZE[0]) // 2
                layer_paste_y = (layer_size[1] - THUMB_SIZE[1]) // 2

                shadow = Image.new('RGBA', layer_size, (0, 0, 0, 0))
                shadow_box = (layer_paste_x + 4, layer_paste_y + 4,
                             layer_paste_x + THUMB_SIZE[0] + 4, layer_paste_y + THUMB_SIZE[1] + 4)

                d = ImageDraw.Draw(shadow)
                d.rectangle(shadow_box, fill=(0, 0, 0, 120))
                shadow = shadow.filter(ImageFilter.GaussianBlur(radius=5))

                layer = Image.alpha_composite(layer, shadow)
                layer.paste(fitted_img, (layer_paste_x, layer_paste_y), fitted_img)

                angle = angles[i]
                rotated_layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)

                final_x = (CANVAS_SIZE[0] - rotated_layer.width) // 2
                final_y = ((CANVAS_SIZE[1] - rotated_layer.height) // 2) + Y_OFFSET

                final_canvas.paste(rotated_layer, (final_x, final_y), rotated_layer)

            except Exception as e:
                app_logger.error(f"Error processing thumbnail {thumb_path}: {e}")

        # Remove existing folder thumbnails
        for ext in ['folder.png', 'folder.jpg', 'folder.jpeg', 'folder.gif']:
            existing_thumb = os.path.join(folder_path, ext)
            if os.path.exists(existing_thumb):
                try:
                    os.remove(existing_thumb)
                except Exception as e:
                    app_logger.error(f"Error removing existing thumbnail {existing_thumb}: {e}")

        # If nested, overlay on folder icon
        if is_nested:
            folder_icon_path = os.path.join(app.static_folder, 'images', 'folder-fill-200x300.png')
            if os.path.exists(folder_icon_path):
                final_canvas = create_nested_folder_thumbnail(final_canvas, folder_icon_path)

        output_path = os.path.join(folder_path, "folder.png")
        final_canvas.save(output_path, "PNG")

        app_logger.info(f"Generated folder thumbnail: {output_path}")
        invalidate_cache_for_path(folder_path)

        return True

    except Exception as e:
        app_logger.error(f"Error generating folder thumbnail for {folder_path}: {e}")
        return False


@app.route('/api/generate-all-missing-thumbnails', methods=['POST'])
def generate_all_missing_thumbnails():
    """Generate folder thumbnails for all subfolders missing them (recursive)."""
    data = request.get_json()
    root_path = data.get('path')

    if not root_path or not os.path.isdir(root_path):
        return jsonify({"error": "Invalid path"}), 400

    generated = 0
    errors = 0
    skipped = 0

    # Recursively traverse all directories
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(('.', '_'))]

        # Skip the root path itself
        if dirpath == root_path:
            continue

        # Check if folder already has thumbnail
        has_thumb = False
        for ext in ['.png', '.jpg', '.jpeg', '.gif']:
            if os.path.exists(os.path.join(dirpath, f'folder{ext}')):
                has_thumb = True
                break

        if has_thumb:
            skipped += 1
            continue

        # Generate thumbnail using internal function
        try:
            result = generate_folder_thumbnail_internal(dirpath)
            if result:
                generated += 1
            else:
                errors += 1
        except Exception as e:
            app_logger.error(f"Error generating thumbnail for {dirpath}: {e}")
            errors += 1

    message = f"Generated {generated} thumbnails"
    if skipped:
        message += f", skipped {skipped} existing"
    if errors:
        message += f" ({errors} errors)"

    return jsonify({
        "success": True,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "message": message
    })


@app.route('/api/check-missing-files', methods=['POST'])
def check_missing_files():
    """Check for missing comic files in a folder."""
    from missing import check_missing_issues

    data = request.get_json()
    folder_path = data.get('folder_path')

    if not folder_path:
        return jsonify({"error": "Missing folder_path"}), 400

    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        return jsonify({"error": "Invalid folder path"}), 400

    try:
        app_logger.info(f"Running missing file check on: {folder_path}")

        # Run the missing file check
        check_missing_issues(folder_path)

        # Read the missing.txt file to count missing issues
        missing_file_path = os.path.join(folder_path, "missing.txt")
        missing_count = 0
        summary_message = ""

        if os.path.exists(missing_file_path):
            with open(missing_file_path, 'r') as f:
                content = f.read()
                # Count lines that contain '.cbz' or '.cbr' to get missing issue count
                # Exclude lines that are just headers or blank
                lines = content.strip().split('\n')
                for line in lines:
                    if '.cbz' in line or '.cbr' in line:
                        missing_count += 1
                    elif '[Total missing:' in line:
                        # Extract count from condensed format
                        match = re.search(r'\[Total missing: (\d+)\]', line)
                        if match:
                            missing_count += int(match.group(1))

        if missing_count == 0:
            summary_message = "No missing issues found."
        else:
            summary_message = f"Found {missing_count} missing issue(s) in {os.path.basename(folder_path)}."

        app_logger.info(f"Missing file check complete. {summary_message}")

        # Get relative path for the missing.txt file
        relative_missing_file = os.path.relpath(missing_file_path, DATA_DIR)

        return jsonify({
            "success": True,
            "missing_count": missing_count,
            "missing_file": missing_file_path,
            "relative_missing_file": relative_missing_file,
            "folder_name": os.path.basename(folder_path),
            "summary": summary_message
        })

    except Exception as e:
        app_logger.error(f"Error checking missing files: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download')
def download_file():
    """Download or view a file from the server."""
    file_path = request.args.get('path')

    if not file_path:
        return jsonify({"error": "Missing path parameter"}), 400

    # Security: Ensure the file path is within allowed directories
    normalized_path = os.path.normpath(file_path)
    if not (normalized_path.startswith(os.path.normpath(DATA_DIR)) or
            normalized_path.startswith(os.path.normpath(TARGET_DIR))):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return jsonify({"error": "File not found"}), 404

    try:
        # Determine MIME type based on file extension
        ext = os.path.splitext(file_path)[1].lower()
        comic_mime_types = {
            '.cbz': 'application/vnd.comicbook+zip',
            '.cbr': 'application/vnd.comicbook-rar',
            '.pdf': 'application/pdf',
            '.epub': 'application/epub+zip',
        }
        mime_type = comic_mime_types.get(ext, 'application/octet-stream')

        return send_file(file_path, as_attachment=True, mimetype=mime_type)
    except Exception as e:
        app_logger.error(f"Error serving file {file_path}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/read-text-file')
def read_text_file():
    """Read and return the contents of a text file."""
    file_path = request.args.get('path')

    if not file_path:
        return "Missing path parameter", 400

    # Security: Ensure the file path is within allowed directories
    normalized_path = os.path.normpath(file_path)
    if not (normalized_path.startswith(os.path.normpath(DATA_DIR)) or
            normalized_path.startswith(os.path.normpath(TARGET_DIR))):
        return "Access denied", 403

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return "File not found", 404

    # Check if file is a text file
    if not file_path.lower().endswith('.txt'):
        return "Only .txt files are supported", 400

    try:
        # Read the file with UTF-8 encoding, falling back to latin-1 if needed
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # Try with latin-1 encoding if UTF-8 fails
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()

        return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        app_logger.error(f"Error reading text file {file_path}: {e}")
        return f"Error reading file: {str(e)}", 500


@app.route('/api/save-cvinfo', methods=['POST'])
def save_cvinfo():
    """Save a cvinfo file in the specified directory."""
    data = request.get_json()
    directory = data.get('directory')
    content = data.get('content') or data.get('url')  # Support both content and legacy url

    if not directory or not content:
        return jsonify({"error": "Missing directory or content parameter"}), 400

    # Security: Ensure the directory path is within allowed directories
    normalized_path = os.path.normpath(directory)
    if not (normalized_path.startswith(os.path.normpath(DATA_DIR)) or
            normalized_path.startswith(os.path.normpath(TARGET_DIR))):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.exists(directory) or not os.path.isdir(directory):
        return jsonify({"error": "Directory not found"}), 404

    try:
        cvinfo_path = os.path.join(directory, 'cvinfo')
        with open(cvinfo_path, 'w', encoding='utf-8') as f:
            f.write(content.strip())

        app_logger.info(f"Saved cvinfo to {cvinfo_path}")
        return jsonify({"success": True, "path": cvinfo_path})
    except Exception as e:
        app_logger.error(f"Error saving cvinfo to {directory}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/batch-metadata', methods=['POST'])
def batch_metadata():
    """
    Batch fetch metadata for all comics in a folder.
    Returns Server-Sent Events (SSE) for real-time progress updates.

    Process order:
    1. Check for cvinfo in folder
    2. If no cvinfo, create via ComicVine search (using folder name as series)
    3. Add Metron series ID to cvinfo if not present
    4. For each CBZ/CBR without ComicInfo.xml:
       - If year < 2022 or unknown: GCD -> ComicVine -> Metron
       - If year >= 2022: Metron -> ComicVine -> GCD
    """
    from comicinfo import read_comicinfo_from_zip

    try:
        data = request.get_json()
        directory = data.get('directory')

        if not directory:
            return jsonify({"error": "Missing directory parameter"}), 400

        # Security: Ensure the directory path is within allowed directories
        normalized_path = os.path.normpath(directory)
        if not (normalized_path.startswith(os.path.normpath(DATA_DIR)) or
                normalized_path.startswith(os.path.normpath(TARGET_DIR))):
            return jsonify({"error": "Access denied"}), 403

        if not os.path.exists(directory) or not os.path.isdir(directory):
            return jsonify({"error": "Directory not found"}), 404

        # Get API credentials
        comicvine_api_key = app.config.get('COMICVINE_API_KEY', '')
        metron_username = app.config.get('METRON_USERNAME', '')
        metron_password = app.config.get('METRON_PASSWORD', '')

        comicvine_available = bool(comicvine_api_key and comicvine_api_key.strip())
        metron_available = bool(metron_password and metron_password.strip())
        gcd_available = gcd.is_mysql_available() and gcd.check_mysql_status().get('gcd_mysql_available', False)

        app_logger.info(f"Batch metadata: CV={comicvine_available}, Metron={metron_available}, GCD={gcd_available}")

        # Step 1: Check for cvinfo
        cvinfo_path = os.path.join(directory, 'cvinfo')
        cv_volume_id = None
        cvinfo_created = False

        if not os.path.exists(cvinfo_path):
            # Create cvinfo via ComicVine search if ComicVine is available
            if comicvine_available:
                # Use folder name as series name
                series_name = os.path.basename(directory)
                # Clean up common patterns
                series_name = re.sub(r'\s*\(\d{4}\).*$', '', series_name)  # Remove (1994) and everything after
                series_name = re.sub(r'\s*v\d+.*$', '', series_name)  # Remove v1, v2 etc
                series_name = re.sub(r'\s*-\s*complete.*$', '', series_name, flags=re.IGNORECASE)

                app_logger.info(f"Searching ComicVine for series: {series_name}")

                try:
                    volumes = comicvine.search_volumes(comicvine_api_key, series_name)
                    if volumes:
                        cv_volume_id = volumes[0]['id']
                        url = f"https://comicvine.gamespot.com/x/4050-{cv_volume_id}/"
                        with open(cvinfo_path, 'w', encoding='utf-8') as f:
                            f.write(url)
                        cvinfo_created = True
                        app_logger.info(f"Created cvinfo with ComicVine volume ID: {cv_volume_id}")
                except Exception as e:
                    app_logger.error(f"Error searching ComicVine: {e}")
        else:
            # Parse existing cvinfo for volume ID
            cv_volume_id = comicvine.parse_cvinfo_volume_id(cvinfo_path)
            app_logger.info(f"Found existing cvinfo with volume ID: {cv_volume_id}")

        # Step 2: Add Metron series ID if not present
        metron_series_id = None
        metron_api = None
        metron_id_added = False

        if metron_available and os.path.exists(cvinfo_path):
            metron_api = metron.get_api(metron_username, metron_password)
            if metron_api:
                metron_series_id = metron.parse_cvinfo_for_metron_id(cvinfo_path)
                if not metron_series_id:
                    cv_id = metron.parse_cvinfo_for_comicvine_id(cvinfo_path)
                    if cv_id:
                        metron_series_id = metron.get_metron_series_id_by_comicvine_id(metron_api, cv_id)
                        if metron_series_id:
                            metron.update_cvinfo_with_metron_id(cvinfo_path, metron_series_id)
                            metron_id_added = True
                            app_logger.info(f"Added Metron series ID: {metron_series_id}")

        # Step 3: Get list of comic files
        comic_files = []
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isfile(item_path) and item.lower().endswith(('.cbz', '.cbr')):
                comic_files.append(item_path)

        app_logger.info(f"Found {len(comic_files)} comic files to process")

        # Determine service order based on volume year
        # If year < 2022 or no year found, use legacy order: GCD -> ComicVine -> Metron
        # Otherwise use modern order: Metron -> ComicVine -> GCD
        year_match = re.search(r'\((\d{4})\)|v(\d{4})', os.path.basename(directory))
        volume_year = int(year_match.group(1) or year_match.group(2)) if year_match else None
        use_legacy_order = volume_year is None or volume_year < 2022

        if use_legacy_order:
            app_logger.info(f"Using legacy service order (GCD first) - volume year: {volume_year or 'unknown'}")
        else:
            app_logger.info(f"Using modern service order (Metron first) - volume year: {volume_year}")

        def generate():
            """Generator for SSE streaming."""
            result = {
                'cvinfo_created': cvinfo_created,
                'metron_id_added': metron_id_added,
                'processed': 0,
                'skipped': 0,
                'errors': 0,
                'details': []
            }

            total_files = len(comic_files)

            # Emit initial progress
            yield f"data: {json.dumps({'type': 'progress', 'current': 0, 'total': total_files, 'file': 'Starting...'})}\n\n"

            # Step 4: Process each comic file
            for i, file_path in enumerate(comic_files):
                filename = os.path.basename(file_path)

                # Emit progress event
                yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': total_files, 'file': filename})}\n\n"

                try:
                    # Check if already has ComicInfo.xml
                    if file_path.lower().endswith('.cbz'):
                        existing = read_comicinfo_from_zip(file_path)
                        existing_notes = existing.get('Notes', '').strip() if existing else ''

                        # Skip if has metadata, unless it's just Amazon scraped data
                        if existing_notes and 'Scraped metadata from Amazon' not in existing_notes:
                            app_logger.debug(f"Skipping {filename} - already has metadata")
                            result['skipped'] += 1
                            result['details'].append({'file': filename, 'status': 'skipped', 'reason': 'has metadata'})
                            continue
                    elif file_path.lower().endswith('.cbr'):
                        # Skip CBR files - we can't check or modify them without conversion
                        app_logger.debug(f"Skipping {filename} - CBR format not supported for metadata")
                        result['skipped'] += 1
                        result['details'].append({'file': filename, 'status': 'skipped', 'reason': 'CBR format'})
                        continue

                    # Extract issue number from filename
                    issue_number = comicvine.extract_issue_number(filename)
                    if not issue_number:
                        app_logger.warning(f"Could not extract issue number from {filename}")
                        result['errors'] += 1
                        result['details'].append({'file': filename, 'status': 'error', 'reason': 'no issue number'})
                        continue

                    app_logger.info(f"Processing {filename} (issue #{issue_number})")

                    # Try sources based on volume year
                    metadata = None
                    source = None

                    # Helper function for GCD lookup
                    def try_gcd():
                        nonlocal metadata, source
                        if not gcd_available:
                            return False
                        try:
                            # Get series name from directory
                            gcd_series_name = os.path.basename(directory)
                            # Clean up series name
                            gcd_series_name = re.sub(r'\s*\(\d{4}\).*$', '', gcd_series_name)
                            gcd_series_name = re.sub(r'\s*v\d+.*$', '', gcd_series_name)

                            # Use volume_year extracted earlier
                            gcd_series = gcd.search_series(gcd_series_name, volume_year)
                            if gcd_series:
                                metadata = gcd.get_issue_metadata(gcd_series['id'], issue_number)
                                if metadata:
                                    source = 'GCD'
                                    app_logger.info(f"Found metadata from GCD for {filename}")
                                    return True
                        except Exception as e:
                            app_logger.warning(f"GCD lookup failed for {filename}: {e}")
                        return False

                    # Helper function for ComicVine lookup
                    def try_comicvine():
                        nonlocal metadata, source
                        if not (comicvine_available and cv_volume_id):
                            return False
                        try:
                            metadata = comicvine.get_metadata_by_volume_id(comicvine_api_key, cv_volume_id, issue_number)
                            if metadata:
                                source = 'ComicVine'
                                app_logger.info(f"Found metadata from ComicVine for {filename}")
                                return True
                        except Exception as e:
                            app_logger.warning(f"ComicVine lookup failed for {filename}: {e}")
                        return False

                    # Helper function for Metron lookup
                    def try_metron():
                        nonlocal metadata, source
                        if not (metron_available and metron_api and metron_series_id):
                            return False
                        try:
                            issue_data = metron.get_issue_metadata(metron_api, metron_series_id, issue_number)
                            if issue_data:
                                metadata = metron.map_to_comicinfo(issue_data)
                                source = 'Metron'
                                app_logger.info(f"Found metadata from Metron for {filename}")
                                return True
                        except Exception as e:
                            app_logger.warning(f"Metron lookup failed for {filename}: {e}")
                        return False

                    if use_legacy_order:
                        # Legacy order: GCD -> ComicVine -> Metron (for pre-2022 or unknown year)
                        if not try_gcd():
                            if not try_comicvine():
                                try_metron()
                    else:
                        # Modern order: Metron -> ComicVine -> GCD (for 2022+)
                        if not try_metron():
                            if not try_comicvine():
                                try_gcd()

                    if metadata:
                        # Generate and add ComicInfo.xml
                        xml_bytes = comicvine.generate_comicinfo_xml(metadata)
                        add_comicinfo_to_cbz(file_path, xml_bytes)
                        result['processed'] += 1
                        result['details'].append({'file': filename, 'status': 'success', 'source': source})
                        app_logger.info(f"Added metadata to {filename} from {source}")
                    else:
                        result['errors'] += 1
                        result['details'].append({'file': filename, 'status': 'error', 'reason': 'not found'})
                        app_logger.warning(f"No metadata found for {filename}")

                    # Rate limiting - wait between API calls
                    time.sleep(0.5)

                except Exception as e:
                    app_logger.error(f"Error processing {filename}: {e}")
                    result['errors'] += 1
                    result['details'].append({'file': filename, 'status': 'error', 'reason': str(e)})

            # Emit final complete event
            yield f"data: {json.dumps({'type': 'complete', 'result': result})}\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream')

    except Exception as e:
        app_logger.error(f"Error in batch_metadata: {e}")
        app_logger.error(f"Traceback: {traceback.format_exc()}")
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

        # Update file index incrementally (no cache invalidation needed with DB-first approach)
        update_index_on_move(old_path, new_path)

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

        # Note: No cache invalidation - using DB-first approach

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

        # Update file index incrementally (no cache invalidation needed with DB-first approach)
        update_index_on_delete(target)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/update-xml', methods=['POST'])
def update_xml():
    """Update a field in ComicInfo.xml for all CBZ files in a directory."""
    from models.update_xml import update_field_in_cbz_files

    try:
        data = request.get_json()
        directory = data.get('directory')
        field = data.get('field')
        value = data.get('value')

        if not directory or not field or not value:
            return jsonify({"error": "Missing required parameters"}), 400

        # Security check - ensure path is within allowed directories
        normalized_path = os.path.normpath(directory)
        if not (normalized_path.startswith(os.path.normpath(DATA_DIR)) or
                normalized_path.startswith(os.path.normpath(TARGET_DIR))):
            return jsonify({"error": "Access denied"}), 403

        if not os.path.exists(directory) or not os.path.isdir(directory):
            return jsonify({"error": "Directory not found"}), 404

        result = update_field_in_cbz_files(directory, field, value)
        return jsonify(result)

    except Exception as e:
        app_logger.error(f"Error in update_xml: {e}")
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

        # Update file index incrementally (no cache invalidation needed with DB-first approach)
        update_index_on_delete(target)

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

        # Update file index incrementally (no cache invalidation needed with DB-first approach)
        update_index_on_move(old_path, new_path)
        
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

@app.route('/manifest.json')
def serve_manifest():
    """Serve PWA manifest from root URL."""
    return send_from_directory(STATIC_DIR, 'manifest.json')

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
        config["SETTINGS"]["METRON_USERNAME"] = request.form.get("metronUsername", "")
        config["SETTINGS"]["METRON_PASSWORD"] = request.form.get("metronPassword", "")
        config["SETTINGS"]["GCD_METADATA_LANGUAGES"] = request.form.get("gcdLanguages", "en")
        config["SETTINGS"]["ENABLE_CUSTOM_RENAME"] = str(request.form.get("enableCustomRename") == "on")
        config["SETTINGS"]["CUSTOM_RENAME_PATTERN"] = request.form.get("customRenamePattern", "")
        config["SETTINGS"]["ENABLE_AUTO_RENAME"] = str(request.form.get("enableAutoRename") == "on")
        config["SETTINGS"]["ENABLE_AUTO_MOVE"] = str(request.form.get("enableAutoMove") == "on")
        config["SETTINGS"]["CUSTOM_MOVE_PATTERN"] = request.form.get("customMovePattern", "{publisher}/{series_name}/v{year}")
        config["SETTINGS"]["ENABLE_DEBUG_LOGGING"] = str(request.form.get("enableDebugLogging") == "on")
        config["SETTINGS"]["BOOTSTRAP_THEME"] = request.form.get("bootstrapTheme", "default")
        config["SETTINGS"]["TIMEZONE"] = request.form.get("timezone", "UTC")
        
        # Recommendations
        config["SETTINGS"]["REC_ENABLED"] = str(request.form.get("recEnabled") == "on")
        config["SETTINGS"]["REC_PROVIDER"] = request.form.get("recProvider", "gemini")
        config["SETTINGS"]["REC_API_KEY"] = request.form.get("recApiKey", "")
        config["SETTINGS"]["REC_MODEL"] = request.form.get("recModel", "gemini-2.0-flash")

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
        metronUsername=settings.get("METRON_USERNAME", ""),
        metronPassword=settings.get("METRON_PASSWORD", ""),
        gcdLanguages=settings.get("GCD_METADATA_LANGUAGES", "en"),
        enableCustomRename=settings.get("ENABLE_CUSTOM_RENAME", "False") == "True",
        customRenamePattern=settings.get("CUSTOM_RENAME_PATTERN", ""),
        enableAutoRename=settings.get("ENABLE_AUTO_RENAME", "False") == "True",
        enableAutoMove=settings.get("ENABLE_AUTO_MOVE", "False") == "True",
        customMovePattern=settings.get("CUSTOM_MOVE_PATTERN", "{publisher}/{series_name}/v{year}"),
        enableDebugLogging=settings.get("ENABLE_DEBUG_LOGGING", "False") == "True",
        bootstrapTheme=settings.get("BOOTSTRAP_THEME", "default"),
        timezone=settings.get("TIMEZONE", "UTC"),
        config=settings,  # Pass full settings dictionary
        rec_enabled=settings.get("REC_ENABLED", "True") == "True",
        rec_provider=settings.get("REC_PROVIDER", "gemini"),
        rec_api_key=settings.get("REC_API_KEY", ""),
        rec_model=settings.get("REC_MODEL", "gemini-2.0-flash")
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

        # Update file index incrementally (no cache invalidation needed with DB-first approach)
        update_index_on_create(path)

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
    return render_template('collection.html', 
                           watch=watch, 
                           config=app.config, 
                           convertSubdirectories=convert_subdirectories,
                           rec_enabled=config.get("SETTINGS", "REC_ENABLED", fallback="True") == "True")
    
#########################
#        App Logs       #
#########################
# Route for app logs page
@app.route('/logs')
def logs_page():
    """Combined logs page with tabs for app and monitor logs."""
    return render_template('logs.html', config=app.config)

@app.route('/app-logs')
def app_logs_page():
    return redirect(url_for('logs_page'))

# Route for monitor logs page
@app.route('/mon-logs')
def mon_logs_page():
    return redirect(url_for('logs_page'))

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
    return jsonify(gcd.check_mysql_status())

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
            search_variations = gcd.generate_search_variations(series_name, year)
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

    result = gcd.validate_issue(series_id, str(issue_number))

    # Transform response to match expected format
    if result.get('success') and result.get('valid'):
        issue_data = result.get('issue', {})
        return jsonify({
            "success": True,
            "issue_id": issue_data.get('id'),
            "issue_number": issue_data.get('number'),
            "issue_title": issue_data.get('title')
        })
    elif result.get('success') and not result.get('valid'):
        return jsonify({
            "success": False,
            "error": f"Issue #{issue_number} not found in series"
        })
    else:
        return jsonify({
            "success": False,
            "error": result.get('error', 'Validation error')
        }), 500

@app.route('/search-gcd-metadata-with-selection', methods=['POST'])
def search_gcd_metadata_with_selection():
    """Search GCD database for comic metadata using user-selected series"""
    try:

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

        # Auto-move file if enabled
        new_file_path = None
        try:
            new_file_path = comicvine.auto_move_file(file_path, selected_volume, app.config)
        except Exception as move_error:
            app_logger.error(f"Auto-move failed but metadata was added successfully: {str(move_error)}")
            # Continue execution - metadata was added successfully even if move failed

        # Return success with metadata and rename configuration
        # Ensure image_url is a string (Pydantic HttpUrl isn't JSON serializable)
        img_url = issue_data.get('image_url')
        if img_url and not isinstance(img_url, str):
            img_url = str(img_url)

        response_data = {
            "success": True,
            "metadata": comicinfo_data,
            "image_url": img_url,
            "volume_info": {
                "id": selected_volume['id'],
                "name": selected_volume['name'],
                "start_year": selected_volume['start_year']
            },
            "rename_config": {
                "enabled": app.config.get("ENABLE_CUSTOM_RENAME", False),
                "pattern": app.config.get("CUSTOM_RENAME_PATTERN", ""),
                "auto_rename": app.config.get("ENABLE_AUTO_RENAME", False)
            }
        }

        # Add new file path to response if file was moved
        if new_file_path:
            response_data["moved"] = True
            response_data["new_file_path"] = new_file_path
            app_logger.info(f"✅ File moved to: {new_file_path}")

            # Update database caches and file index for the moved file
            log_file_if_in_data(new_file_path)
            invalidate_cache_for_path(os.path.dirname(file_path))
            invalidate_cache_for_path(os.path.dirname(new_file_path))
            update_index_on_move(file_path, new_file_path)

        return jsonify(response_data)

    except Exception as e:
        app_logger.error(f"Error in ComicVine search: {str(e)}")
        app_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/search-comicvine-metadata-with-selection', methods=['POST'])
def search_comicvine_metadata_with_selection():
    """Search ComicVine using user-selected volume"""
    try:
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
        # Also include name and start_year for auto-move functionality
        volume_data = {
            'id': volume_id,
            'publisher_name': publisher_name,
            'name': issue_data.get('volume_name'),  # Series name from issue data
            'start_year': issue_data.get('year')  # Use issue year as fallback for start_year
        }

        # Map to ComicInfo format
        comicinfo_data = comicvine.map_to_comicinfo(issue_data, volume_data)

        # Generate ComicInfo.xml
        comicinfo_xml = generate_comicinfo_xml(comicinfo_data)

        # Add ComicInfo.xml to the CBZ file
        add_comicinfo_to_cbz(file_path, comicinfo_xml)

        # Auto-move file if enabled
        new_file_path = None
        try:
            new_file_path = comicvine.auto_move_file(file_path, volume_data, app.config)
        except Exception as move_error:
            app_logger.error(f"Auto-move failed but metadata was added successfully: {str(move_error)}")
            # Continue execution - metadata was added successfully even if move failed

        # Return success with metadata and rename configuration
        # Ensure image_url is a string (Pydantic HttpUrl isn't JSON serializable)
        img_url = issue_data.get('image_url')
        if img_url and not isinstance(img_url, str):
            img_url = str(img_url)

        response_data = {
            "success": True,
            "metadata": comicinfo_data,
            "image_url": img_url,
            "rename_config": {
                "enabled": app.config.get("ENABLE_CUSTOM_RENAME", False),
                "pattern": app.config.get("CUSTOM_RENAME_PATTERN", ""),
                "auto_rename": app.config.get("ENABLE_AUTO_RENAME", False)
            }
        }

        # Add new file path to response if file was moved
        if new_file_path:
            response_data["moved"] = True
            response_data["new_file_path"] = new_file_path
            app_logger.info(f"✅ File moved to: {new_file_path}")

            # Update database caches and file index for the moved file
            log_file_if_in_data(new_file_path)
            invalidate_cache_for_path(os.path.dirname(file_path))
            invalidate_cache_for_path(os.path.dirname(new_file_path))
            update_index_on_move(file_path, new_file_path)

        return jsonify(response_data)

    except Exception as e:
        app_logger.error(f"Error in ComicVine search with selection: {str(e)}")
        app_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/insights')
def insights_page():
    from database import get_reading_totals
    library_stats = get_library_stats()
    file_types = get_file_type_distribution()
    top_publishers = get_top_publishers()
    reading_history = get_reading_history_stats()
    largest_comics = get_largest_comics()
    top_series = get_top_series_by_count()
    reading_heatmap = get_reading_heatmap_data()
    reading_totals = get_reading_totals()

    return render_template('insights.html',
                           library_stats=library_stats,
                           file_types=file_types,
                           top_publishers=top_publishers,
                           reading_history=reading_history,
                           largest_comics=largest_comics,
                           top_series=top_series,
                           reading_heatmap=reading_heatmap,
                           reading_totals=reading_totals,
                           rec_enabled=config.get("SETTINGS", "REC_ENABLED", fallback="True") == "True")

@app.route('/api/insights')
def api_insights():
    """Return library stats as JSON for Homepage custom API widget."""
    library_stats = get_library_stats()
    if not library_stats:
        return jsonify({"error": "Failed to get stats"}), 500

    return jsonify({
        "total_files": library_stats.get('total_files', 0),
        "total_size": library_stats.get('total_size', 0),
        "issues_read": library_stats.get('total_read', 0),
        "root_folders": library_stats.get('root_folders', 0)
    })

#########################
#   Yearly Wrapped      #
#########################

@app.route('/api/wrapped/years')
def api_wrapped_years():
    """Get list of years with reading data. Defaults to current year if none found."""
    from wrapped import get_years_with_reading_data

    try:
        years = get_years_with_reading_data()
    except Exception as e:
        app_logger.error(f"Error getting wrapped years: {e}")
        years = []

    # Default to current year if no data found
    if not years:
        years = [datetime.now().year]

    return jsonify({"years": years})


@app.route('/api/wrapped/<int:year>')
def api_wrapped_data(year):
    """Return wrapped stats as JSON."""
    from wrapped import get_all_wrapped_stats
    try:
        stats = get_all_wrapped_stats(year)
        return jsonify(stats)
    except Exception as e:
        app_logger.error(f"Error getting wrapped stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/wrapped/<int:year>/image/<int:slide_num>')
def api_wrapped_image(year, slide_num):
    """Return individual wrapped slide as PNG image."""
    from wrapped import (
        generate_summary_slide,
        generate_most_read_series_slide,
        generate_series_highlights_slide
    )

    # Get current theme from config
    # Get current theme from config
    theme = config.get('SETTINGS', 'BOOTSTRAP_THEME', fallback='default')

    try:
        if slide_num == 1:
            image_bytes = generate_summary_slide(year, theme)
        elif slide_num == 2:
            image_bytes = generate_most_read_series_slide(year, theme)
        elif slide_num == 3:
            image_bytes = generate_series_highlights_slide(year, theme)
        else:
            return jsonify({"error": "Invalid slide number (1-3)"}), 400

        return Response(image_bytes, mimetype='image/png')
    except Exception as e:
        app_logger.error(f"Error generating wrapped image: {e}")
        app_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/wrapped/<int:year>/download')
def api_wrapped_download(year):
    """Download all wrapped images as ZIP."""
    from wrapped import generate_all_wrapped_images
    import zipfile

    theme = config.get('SETTINGS', 'BOOTSTRAP_THEME', fallback='default')

    try:
        slides = generate_all_wrapped_images(year, theme)

        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename, image_bytes in slides:
                zf.writestr(f"wrapped_{year}/{filename}", image_bytes)

        zip_buffer.seek(0)

        return Response(
            zip_buffer.getvalue(),
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename=wrapped_{year}.zip'
            }
        )
    except Exception as e:
        app_logger.error(f"Error generating wrapped ZIP: {e}")
        return jsonify({"error": str(e)}), 500


#########################
#   Application Start   #
#########################

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

# Pre-build browse cache for root directory
def prebuild_browse_cache():
    """Pre-build browse cache for DATA_DIR root on startup."""
    try:
        app_logger.info(f"🔄 Pre-building browse cache for {DATA_DIR}...")
        # Trigger a browse request internally to build and cache
        with app.test_request_context(f'/api/browse?path={DATA_DIR}'):
            api_browse()
        app_logger.info(f"✅ Browse cache pre-built for {DATA_DIR}")
    except Exception as e:
        app_logger.error(f"❌ Error pre-building browse cache: {e}")

# Start file watcher for /data directory in background
def start_file_watcher_background():
    try:
        app_logger.info(f"Initializing file watcher for {DATA_DIR}...")
        file_watcher = FileWatcher(watch_path=DATA_DIR, debounce_seconds=2)
        if file_watcher.start():
            app_logger.info(f"👁️ File watcher started for {DATA_DIR} (tracking recent files)...")
        else:
            app_logger.warning("⚠️ File watcher failed to start")
    except Exception as e:
        app_logger.error(f"❌ Failed to initialize file watcher: {e}")
        app_logger.error(f"Traceback: {traceback.format_exc()}")

def start_background_services():
    """Start all background services. Called once on app startup."""
    app_logger.info("Flask app is starting up...")

    # Start index building in background
    threading.Thread(target=build_index_background, daemon=True).start()
    app_logger.info("🔄 Building search index in background...")

    # Pre-build browse cache in background
    threading.Thread(target=prebuild_browse_cache, daemon=True).start()
    app_logger.info("🔄 Pre-building browse cache for root directory...")

    # Start cache maintenance in background
    threading.Thread(target=cache_maintenance_background, daemon=True).start()
    app_logger.info("🔄 Cache maintenance thread started (checks every hour, rebuilds every 6 hours)...")

    # Start file watcher
    threading.Thread(target=start_file_watcher_background, daemon=True).start()
    app_logger.info("🔄 File watcher initialization started in background...")

    # Configure rebuild schedule from database
    configure_rebuild_schedule()

    # Start monitor if enabled
    if os.environ.get("MONITOR", "").strip().lower() == "yes":
        app_logger.info("MONITOR=yes detected. Starting monitor.py...")
        threading.Thread(target=run_monitor, daemon=True).start()

    if pwd is not None:
        user_name = pwd.getpwuid(os.geteuid()).pw_name
    else:
        user_name = os.getenv('USERNAME', 'unknown')
    app_logger.info(f"Running as user: {user_name}")

# Start background services when module is imported (works with Gunicorn)
start_background_services()

@app.route('/api/recommendations', methods=['GET', 'POST'])
def api_recommendations():
    try:
        from config import CONFIG_DIR
        recommendations_file = os.path.join(CONFIG_DIR, 'recommendations.json')

        if request.method == 'GET':
            if os.path.exists(recommendations_file):
                try:
                    with open(recommendations_file, 'r') as f:
                        return jsonify(json.load(f))
                except json.JSONDecodeError:
                    return jsonify([])
            return jsonify([])

        data = request.json or {}
        
        # Get settings from request or config
        provider = data.get('provider') or config["SETTINGS"].get("REC_PROVIDER", "gemini")
        api_key = data.get('api_key') or config["SETTINGS"].get("REC_API_KEY", "")
        model = data.get('model') or config["SETTINGS"].get("REC_MODEL", "gemini-2.0-flash")
        
        # If no API key in request or config, error
        if not api_key:
             return jsonify({"error": "API Key is required. Please configure it in Settings."}), 400

        # Fetch reading history
        reading_history = get_recent_read_issues(limit=200)
        
        if not reading_history:
             return jsonify({"error": "No reading history found to generate recommendations from."}), 404
             
        # Call recommendations module
        recommendations_list = recommendations.get_recommendations(api_key, provider, model, reading_history)
        
        if isinstance(recommendations_list, dict) and "error" in recommendations_list:
             return jsonify(recommendations_list), 500
        
        # Save recommendations
        try:
             with open(recommendations_file, 'w') as f:
                 json.dump(recommendations_list, f)
        except Exception as e:
             app_logger.error(f"Failed to save recommendations: {e}")
             
        return jsonify(recommendations_list)
        
    except Exception as e:
        app_logger.error(f"Error generating recommendations: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Only used for local development (python app.py)
    app.run(debug=True, use_reloader=False, threaded=True, host='0.0.0.0', port=5577)