import sqlite3
import os
from config import config
from app_logging import app_logger

def get_db_path():
    # Ensure we get the latest config value
    cache_dir = config.get("SETTINGS", "CACHE_DIR", fallback="/cache")
    if not os.path.exists(cache_dir):
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError as e:
            app_logger.error(f"Failed to create cache directory {cache_dir}: {e}")
            # Fallback to a local directory if /cache is not writable (e.g. running locally without docker)
            cache_dir = "cache"
            os.makedirs(cache_dir, exist_ok=True)
            
    return os.path.join(cache_dir, "comic_utils.db")

def init_db():
    """Initialize the SQLite database and create tables if they don't exist."""
    try:
        db_path = get_db_path()
        app_logger.info(f"Initializing database at {db_path}")
        
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # Create thumbnail_jobs table
        c.execute('''
            CREATE TABLE IF NOT EXISTS thumbnail_jobs (
                path TEXT PRIMARY KEY,
                status TEXT,
                file_mtime REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create recent_files table (rotating log of last 100 files added to /data)
        c.execute('''
            CREATE TABLE IF NOT EXISTS recent_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create file_index table (persistent file index for fast search)
        c.execute('''
            CREATE TABLE IF NOT EXISTS file_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                size INTEGER,
                parent TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create indexes for file_index table
        c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_name ON file_index(name)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_parent ON file_index(parent)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_type ON file_index(type)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_path ON file_index(path)')

        # Create search_cache table (cache recent search queries)
        c.execute('''
            CREATE TABLE IF NOT EXISTS search_cache (
                query TEXT PRIMARY KEY,
                results TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create rebuild_schedule table (store file index rebuild schedule)
        c.execute('''
            CREATE TABLE IF NOT EXISTS rebuild_schedule (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                frequency TEXT NOT NULL DEFAULT 'disabled',
                time TEXT NOT NULL DEFAULT '02:00',
                weekday INTEGER DEFAULT 0,
                last_rebuild TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Insert default schedule if not exists
        c.execute('SELECT COUNT(*) FROM rebuild_schedule WHERE id = 1')
        if c.fetchone()[0] == 0:
            c.execute('''
                INSERT INTO rebuild_schedule (id, frequency, time, weekday)
                VALUES (1, 'disabled', '02:00', 0)
            ''')

        # Create browse_cache table (cache pre-computed browse results)
        c.execute('''
            CREATE TABLE IF NOT EXISTS browse_cache (
                path TEXT PRIMARY KEY,
                result TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create index for faster lookups
        c.execute('CREATE INDEX IF NOT EXISTS idx_browse_cache_path ON browse_cache(path)')

        # Migration: Check if file_mtime column exists, add if not
        c.execute("PRAGMA table_info(thumbnail_jobs)")
        columns = [column[1] for column in c.fetchall()]
        if 'file_mtime' not in columns:
            app_logger.info("Migrating database: adding file_mtime column")
            c.execute("ALTER TABLE thumbnail_jobs ADD COLUMN file_mtime REAL")

        # Migration: Drop file_move_history table if it exists (removed feature)
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_move_history'")
        if c.fetchone():
            app_logger.info("Migrating database: dropping file_move_history table (removed feature)")
            c.execute("DROP TABLE file_move_history")

        conn.commit()
        conn.close()
        app_logger.info("Database initialized successfully")
        return True
    except Exception as e:
        app_logger.error(f"Failed to initialize database: {e}")
        return False

def get_db_connection():
    """Get a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(get_db_path(), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        app_logger.error(f"Failed to connect to database: {e}")
        return None

def log_recent_file(file_path, file_name=None, file_size=None):
    """
    Log a recently added file to the database with rotation (keep only last 100).

    Args:
        file_path: Full path to the file
        file_name: Name of the file (optional, will extract from path if not provided)
        file_size: Size of the file in bytes (optional, will calculate if not provided)
    """
    try:
        if file_name is None:
            file_name = os.path.basename(file_path)

        if file_size is None and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)

        conn = get_db_connection()
        if not conn:
            app_logger.error("Could not get database connection to log recent file")
            return False

        c = conn.cursor()

        # Check if file already exists
        c.execute('SELECT id FROM recent_files WHERE file_path = ?', (file_path,))
        existing = c.fetchone()

        if existing:
            # Update existing entry with new timestamp
            c.execute('''
                UPDATE recent_files
                SET file_name = ?, file_size = ?, added_at = CURRENT_TIMESTAMP
                WHERE file_path = ?
            ''', (file_name, file_size, file_path))
        else:
            # Insert new file
            c.execute('''
                INSERT INTO recent_files (file_path, file_name, file_size)
                VALUES (?, ?, ?)
            ''', (file_path, file_name, file_size))

        # Count total files
        c.execute('SELECT COUNT(*) FROM recent_files')
        count = c.fetchone()[0]

        # If we have more than 100, delete the oldest ones
        if count > 100:
            c.execute('''
                DELETE FROM recent_files
                WHERE id IN (
                    SELECT id FROM recent_files
                    ORDER BY added_at ASC
                    LIMIT ?
                )
            ''', (count - 100,))

        conn.commit()
        conn.close()
        app_logger.debug(f"Logged recent file: {file_name}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to log recent file {file_path}: {e}")
        return False

def get_recent_files(limit=100):
    """
    Get the most recent files added to the library.

    Args:
        limit: Maximum number of files to return (default 100)

    Returns:
        List of dictionaries containing file information, or empty list on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            app_logger.error("Could not get database connection to retrieve recent files")
            return []

        c = conn.cursor()
        c.execute('''
            SELECT file_path, file_name, file_size, added_at
            FROM recent_files
            ORDER BY added_at DESC
            LIMIT ?
        ''', (limit,))

        rows = c.fetchall()
        conn.close()

        # Convert to list of dictionaries
        files = []
        for row in rows:
            files.append({
                'file_path': row['file_path'],
                'file_name': row['file_name'],
                'file_size': row['file_size'],
                'added_at': row['added_at']
            })

        return files

    except Exception as e:
        app_logger.error(f"Failed to retrieve recent files: {e}")
        return []

#########################
#   File Index Functions #
#########################

def get_file_index_from_db():
    """
    Load the file index from the database.

    Returns:
        List of dictionaries containing file index entries, or empty list on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            app_logger.error("Could not get database connection to retrieve file index")
            return []

        c = conn.cursor()
        c.execute('''
            SELECT name, path, type, size, parent
            FROM file_index
            ORDER BY type DESC, name ASC
        ''')

        rows = c.fetchall()
        conn.close()

        # Convert to list of dictionaries
        index = []
        for row in rows:
            entry = {
                'name': row['name'],
                'path': row['path'],
                'type': row['type'],
                'parent': row['parent']
            }
            if row['size'] is not None:
                entry['size'] = row['size']
            index.append(entry)

        app_logger.info(f"Loaded {len(index)} entries from file index database")
        return index

    except Exception as e:
        app_logger.error(f"Failed to retrieve file index: {e}")
        return []

def save_file_index_to_db(file_index):
    """
    Save the entire file index to the database (batch operation).

    Args:
        file_index: List of dictionaries with keys: name, path, type, size, parent

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            app_logger.error("Could not get database connection to save file index")
            return False

        c = conn.cursor()

        # Clear existing index
        c.execute('DELETE FROM file_index')

        # Prepare batch insert
        records = [
            (
                entry['name'],
                entry['path'],
                entry['type'],
                entry.get('size'),
                entry['parent']
            )
            for entry in file_index
        ]

        # Batch insert
        c.executemany('''
            INSERT INTO file_index (name, path, type, size, parent)
            VALUES (?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        conn.close()

        app_logger.info(f"Saved {len(records)} entries to file index database")
        return True

    except Exception as e:
        app_logger.error(f"Failed to save file index: {e}")
        return False

def update_file_index_entry(path, name=None, new_path=None, parent=None, size=None):
    """
    Update a single file index entry incrementally.

    Args:
        path: Current path of the entry (used to find the record)
        name: New name (optional)
        new_path: New path (optional, for move/rename operations)
        parent: New parent path (optional)
        size: New size (optional)

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()

        # Build UPDATE query dynamically based on provided fields
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)

        if new_path is not None:
            updates.append("path = ?")
            params.append(new_path)

        if parent is not None:
            updates.append("parent = ?")
            params.append(parent)

        if size is not None:
            updates.append("size = ?")
            params.append(size)

        if not updates:
            conn.close()
            return True  # Nothing to update

        updates.append("last_updated = CURRENT_TIMESTAMP")
        params.append(path)  # WHERE clause parameter

        query = f"UPDATE file_index SET {', '.join(updates)} WHERE path = ?"
        c.execute(query, params)

        conn.commit()
        rows_affected = c.rowcount
        conn.close()

        if rows_affected > 0:
            app_logger.debug(f"Updated file index entry: {path}")
            return True
        else:
            app_logger.warning(f"File index entry not found for update: {path}")
            return False

    except Exception as e:
        app_logger.error(f"Failed to update file index entry {path}: {e}")
        return False

def add_file_index_entry(name, path, entry_type, size=None, parent=None):
    """
    Add a new entry to the file index.

    Args:
        name: File or directory name
        path: Full path
        entry_type: 'file' or 'directory'
        size: File size in bytes (optional, None for directories)
        parent: Parent directory path (optional)

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()

        c.execute('''
            INSERT OR REPLACE INTO file_index (name, path, type, size, parent)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, path, entry_type, size, parent))

        conn.commit()
        conn.close()

        app_logger.debug(f"Added file index entry: {path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to add file index entry {path}: {e}")
        return False

def delete_file_index_entry(path):
    """
    Delete an entry from the file index.

    Args:
        path: Full path of the entry to delete

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()

        # Delete the entry
        c.execute('DELETE FROM file_index WHERE path = ?', (path,))

        # Also delete any children (for directories)
        c.execute('DELETE FROM file_index WHERE parent = ? OR path LIKE ?', (path, f"{path}/%"))

        conn.commit()
        rows_affected = c.rowcount
        conn.close()

        if rows_affected > 0:
            app_logger.debug(f"Deleted {rows_affected} file index entries for: {path}")
            return True
        else:
            app_logger.warning(f"File index entry not found for deletion: {path}")
            return False

    except Exception as e:
        app_logger.error(f"Failed to delete file index entry {path}: {e}")
        return False

def clear_file_index_from_db():
    """
    Clear all entries from the file index database.

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM file_index')

        conn.commit()
        rows_affected = c.rowcount
        conn.close()

        app_logger.info(f"Cleared {rows_affected} entries from file index database")
        return True

    except Exception as e:
        app_logger.error(f"Failed to clear file index database: {e}")
        return False

def search_file_index(query, limit=100):
    """
    Search the file index for entries matching the query.

    Args:
        query: Search query string
        limit: Maximum number of results to return

    Returns:
        List of matching entries
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()

        # Search with LIKE for partial matching (case-insensitive)
        c.execute('''
            SELECT name, path, type, size, parent
            FROM file_index
            WHERE LOWER(name) LIKE LOWER(?)
            ORDER BY type DESC, name ASC
            LIMIT ?
        ''', (f'%{query}%', limit))

        rows = c.fetchall()
        conn.close()

        # Convert to list of dictionaries
        results = []
        for row in rows:
            entry = {
                'name': row['name'],
                'path': row['path'],
                'type': row['type'],
                'parent': row['parent']
            }
            if row['size'] is not None:
                entry['size'] = row['size']
            results.append(entry)

        return results

    except Exception as e:
        app_logger.error(f"Failed to search file index: {e}")
        return []

def get_search_cache(query):
    """
    Get cached search results for a query.

    Args:
        query: Search query string

    Returns:
        List of results if cached, None if not found or expired
    """
    try:
        import json
        from datetime import datetime, timedelta

        conn = get_db_connection()
        if not conn:
            return None

        c = conn.cursor()

        # Get cached results (only if less than 5 minutes old)
        c.execute('''
            SELECT results, created_at
            FROM search_cache
            WHERE query = ? AND created_at > datetime('now', '-5 minutes')
        ''', (query.lower(),))

        row = c.fetchone()
        conn.close()

        if row:
            return json.loads(row['results'])
        return None

    except Exception as e:
        app_logger.error(f"Failed to get search cache for '{query}': {e}")
        return None

def save_search_cache(query, results):
    """
    Save search results to cache.

    Args:
        query: Search query string
        results: List of result dictionaries to cache

    Returns:
        True if successful, False otherwise
    """
    try:
        import json

        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()

        # Save/update cache entry
        c.execute('''
            INSERT OR REPLACE INTO search_cache (query, results, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (query.lower(), json.dumps(results)))

        # Limit cache size to 100 most recent queries
        c.execute('''
            DELETE FROM search_cache
            WHERE query NOT IN (
                SELECT query FROM search_cache
                ORDER BY created_at DESC
                LIMIT 100
            )
        ''')

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        app_logger.error(f"Failed to save search cache for '{query}': {e}")
        return False

def clear_search_cache():
    """
    Clear all cached search results.

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM search_cache')

        conn.commit()
        conn.close()

        app_logger.info("Cleared search cache")
        return True

    except Exception as e:
        app_logger.error(f"Failed to clear search cache: {e}")
        return False

#########################
#   Rebuild Schedule    #
#########################

def get_rebuild_schedule():
    """
    Get the current file index rebuild schedule.

    Returns:
        Dictionary with schedule settings, or None on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return None

        c = conn.cursor()
        c.execute('''
            SELECT frequency, time, weekday, last_rebuild
            FROM rebuild_schedule
            WHERE id = 1
        ''')

        row = c.fetchone()
        conn.close()

        if row:
            return {
                'frequency': row['frequency'],
                'time': row['time'],
                'weekday': row['weekday'],
                'last_rebuild': row['last_rebuild']
            }
        return None

    except Exception as e:
        app_logger.error(f"Failed to get rebuild schedule: {e}")
        return None

def save_rebuild_schedule(frequency, time, weekday=0):
    """
    Save the file index rebuild schedule.

    Args:
        frequency: 'disabled', 'daily', or 'weekly'
        time: Time in HH:MM format
        weekday: Day of week (0=Monday, 6=Sunday)

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            UPDATE rebuild_schedule
            SET frequency = ?, time = ?, weekday = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        ''', (frequency, time, weekday))

        conn.commit()
        conn.close()

        app_logger.info(f"Saved rebuild schedule: {frequency} at {time}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to save rebuild schedule: {e}")
        return False

def update_last_rebuild():
    """
    Update the last_rebuild timestamp to current time.

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            UPDATE rebuild_schedule
            SET last_rebuild = CURRENT_TIMESTAMP
            WHERE id = 1
        ''')

        conn.commit()
        conn.close()

        app_logger.info("Updated last rebuild timestamp")
        return True

    except Exception as e:
        app_logger.error(f"Failed to update last rebuild timestamp: {e}")
        return False

#########################
#   Browse Cache        #
#########################

def get_browse_cache(path):
    """
    Get cached browse result for a path.

    Args:
        path: Directory path

    Returns:
        Dictionary with browse result, or None if not cached
    """
    try:
        import json

        conn = get_db_connection()
        if not conn:
            return None

        c = conn.cursor()
        c.execute('''
            SELECT result, updated_at
            FROM browse_cache
            WHERE path = ?
        ''', (path,))

        row = c.fetchone()
        conn.close()

        if row:
            return json.loads(row['result'])
        return None

    except Exception as e:
        app_logger.error(f"Failed to get browse cache for '{path}': {e}")
        return None

def save_browse_cache(path, result):
    """
    Save browse result to cache.

    Args:
        path: Directory path
        result: Dictionary with browse result

    Returns:
        True if successful, False otherwise
    """
    try:
        import json

        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO browse_cache (path, result, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (path, json.dumps(result)))

        conn.commit()
        conn.close()

        app_logger.debug(f"Saved browse cache for: {path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to save browse cache for '{path}': {e}")
        return False

def invalidate_browse_cache(path):
    """
    Invalidate browse cache for a specific path.

    Args:
        path: Directory path to invalidate

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()

        # Delete the specific path
        c.execute('DELETE FROM browse_cache WHERE path = ?', (path,))

        # Also delete parent path (so parent sees new subdirectory)
        parent = os.path.dirname(path)
        if parent:
            c.execute('DELETE FROM browse_cache WHERE path = ?', (parent,))

        # Delete any child paths
        c.execute('DELETE FROM browse_cache WHERE path LIKE ?', (f"{path}/%",))

        conn.commit()
        rows_affected = c.rowcount
        conn.close()

        if rows_affected > 0:
            app_logger.debug(f"Invalidated {rows_affected} browse cache entries for: {path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to invalidate browse cache for '{path}': {e}")
        return False

def clear_browse_cache():
    """
    Clear all browse cache entries.

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM browse_cache')

        conn.commit()
        count = c.rowcount
        conn.close()

        app_logger.info(f"Cleared browse cache ({count} entries)")
        return True

    except Exception as e:
        app_logger.error(f"Failed to clear browse cache: {e}")
        return False
