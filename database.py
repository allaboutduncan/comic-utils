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

        conn = sqlite3.connect(db_path, timeout=30)
        c = conn.cursor()

        # Enable WAL mode for better concurrency (allows reads during writes)
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA busy_timeout=30000')
        
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
                has_thumbnail INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Migration: Add has_thumbnail column if it doesn't exist (for existing databases)
        c.execute("PRAGMA table_info(file_index)")
        columns = [col[1] for col in c.fetchall()]
        if 'has_thumbnail' not in columns:
            c.execute('ALTER TABLE file_index ADD COLUMN has_thumbnail INTEGER DEFAULT 0')

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

        # Create favorite_publishers table (root-level folders off /data)
        c.execute('''
            CREATE TABLE IF NOT EXISTS favorite_publishers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                publisher_path TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_favorite_publishers_path ON favorite_publishers(publisher_path)')

        # Create favorite_series table (folders within publishers)
        c.execute('''
            CREATE TABLE IF NOT EXISTS favorite_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_path TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_favorite_series_path ON favorite_series(series_path)')

        # Create issues_read table (comic files marked as read)
        c.execute('''
            CREATE TABLE IF NOT EXISTS issues_read (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_path TEXT NOT NULL UNIQUE,
                read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_issues_read_path ON issues_read(issue_path)')

        # Create to_read table (files and folders marked as "want to read")
        c.execute('''
            CREATE TABLE IF NOT EXISTS to_read (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_to_read_path ON to_read(path)')

        # Create stats_cache table (cache computed statistics)
        c.execute('''
            CREATE TABLE IF NOT EXISTS stats_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

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
        # Ensure WAL mode and busy timeout for better concurrency
        conn.execute('PRAGMA busy_timeout=30000')
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


def get_directory_children(parent_path, max_retries=3):
    """
    Get all direct children of a directory from file_index.
    Used for fast directory browsing without filesystem access.

    Args:
        parent_path: The parent directory path to query
        max_retries: Number of times to retry on database lock

    Returns:
        Tuple of (directories, files) where each is a list of dictionaries
    """
    import time

    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            if not conn:
                app_logger.error("Could not get database connection for directory children")
                return [], []

            c = conn.cursor()
            c.execute('''
                SELECT name, path, type, size, has_thumbnail
                FROM file_index
                WHERE parent = ?
                ORDER BY type DESC, name COLLATE NOCASE ASC
            ''', (parent_path,))

            rows = c.fetchall()
            conn.close()

            directories = []
            files = []
            for row in rows:
                entry = {
                    'name': row['name'],
                    'path': row['path'],
                    'type': row['type']
                }
                if row['type'] == 'directory':
                    entry['has_thumbnail'] = bool(row['has_thumbnail']) if row['has_thumbnail'] else False
                    directories.append(entry)
                else:
                    entry['size'] = row['size'] if row['size'] else 0
                    files.append(entry)

            return directories, files

        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retries - 1:
                app_logger.warning(f"Database locked, retrying ({attempt + 1}/{max_retries})...")
                if conn:
                    conn.close()
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                continue
            app_logger.error(f"Failed to get directory children for {parent_path}: {e}")
            return [], []
        except Exception as e:
            app_logger.error(f"Failed to get directory children for {parent_path}: {e}")
            if conn:
                conn.close()
            return [], []


def save_file_index_to_db(file_index):
    """
    Save the entire file index to the database (batch operation).

    Args:
        file_index: List of dictionaries with keys: name, path, type, size, parent, has_thumbnail

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
                entry['parent'],
                entry.get('has_thumbnail', 0)
            )
            for entry in file_index
        ]

        # Batch insert
        c.executemany('''
            INSERT INTO file_index (name, path, type, size, parent, has_thumbnail)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        conn.close()

        app_logger.info(f"Saved {len(records)} entries to file index database")
        return True

    except Exception as e:
        app_logger.error(f"Failed to save file index: {e}")
        return False


def get_path_counts(path):
    """
    Get recursive folder and file counts for a path using file_index.

    Args:
        path: Directory path (e.g., '/data/Marvel')

    Returns:
        Tuple of (folder_count, file_count) or (0, 0) on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return (0, 0)

        c = conn.cursor()
        # Use path prefix to count all descendants (recursive)
        # Ensure trailing slash to avoid matching partial names (e.g., /data/Marvel vs /data/MarvelMax)
        path_prefix = path.rstrip('/') + '/'

        c.execute('''
            SELECT
                SUM(CASE WHEN type = 'directory' THEN 1 ELSE 0 END) as folder_count,
                SUM(CASE WHEN type = 'file' THEN 1 ELSE 0 END) as file_count
            FROM file_index
            WHERE path LIKE ? || '%'
        ''', (path_prefix,))

        row = c.fetchone()
        conn.close()

        if row:
            return (row['folder_count'] or 0, row['file_count'] or 0)
        return (0, 0)

    except Exception as e:
        app_logger.error(f"Failed to get path counts for '{path}': {e}")
        return (0, 0)


def get_path_counts_batch(paths):
    """
    Get recursive folder and file counts for multiple paths in ONE query.
    Much faster than calling get_path_counts() N times.

    Args:
        paths: List of directory paths (e.g., ['/data/Marvel', '/data/DC'])

    Returns:
        Dict mapping path -> (folder_count, file_count)
    """
    if not paths:
        return {}

    try:
        conn = get_db_connection()
        if not conn:
            return {p: (0, 0) for p in paths}

        c = conn.cursor()
        results = {}

        # Process in batches of 100 to avoid SQLite parameter limits
        BATCH_SIZE = 100
        for i in range(0, len(paths), BATCH_SIZE):
            batch = paths[i:i + BATCH_SIZE]
            path_prefixes = [p.rstrip('/') + '/' for p in batch]

            # Build UNION ALL query for batch - one query instead of N
            query_parts = []
            params = []
            for path, prefix in zip(batch, path_prefixes):
                query_parts.append('''
                    SELECT ? as path,
                        SUM(CASE WHEN type = 'directory' THEN 1 ELSE 0 END) as folder_count,
                        SUM(CASE WHEN type = 'file' THEN 1 ELSE 0 END) as file_count
                    FROM file_index WHERE path LIKE ? || '%'
                ''')
                params.extend([path, prefix])

            c.execute(' UNION ALL '.join(query_parts), params)
            for row in c.fetchall():
                results[row['path']] = (row['folder_count'] or 0, row['file_count'] or 0)

        conn.close()

        # Fill missing paths with (0, 0)
        for p in paths:
            if p not in results:
                results[p] = (0, 0)

        return results

    except Exception as e:
        app_logger.error(f"Failed to get batch path counts: {e}")
        return {p: (0, 0) for p in paths}


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

def add_file_index_entry(name, path, entry_type, size=None, parent=None, has_thumbnail=0):
    """
    Add a new entry to the file index.

    Args:
        name: File or directory name
        path: Full path
        entry_type: 'file' or 'directory'
        size: File size in bytes (optional, None for directories)
        parent: Parent directory path (optional)
        has_thumbnail: 1 if directory has folder.png/jpg, 0 otherwise (optional)

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()

        c.execute('''
            INSERT OR REPLACE INTO file_index (name, path, type, size, parent, has_thumbnail)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, path, entry_type, size, parent, has_thumbnail))

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
    Save browse result to cache with retry logic for database locks.

    Args:
        path: Directory path
        result: Dictionary with browse result

    Returns:
        True if successful, False otherwise
    """
    import json
    import time

    max_retries = 3
    retry_delay = 0.5  # seconds

    for attempt in range(max_retries):
        try:
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

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                app_logger.warning(f"Database locked, retrying save_browse_cache for '{path}' (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                continue
            app_logger.error(f"Failed to save browse cache for '{path}': {e}")
            return False
        except Exception as e:
            app_logger.error(f"Failed to save browse cache for '{path}': {e}")
            return False

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


# =============================================================================
# Favorite Publishers CRUD Operations
# =============================================================================

def add_favorite_publisher(publisher_path):
    """
    Add a publisher to favorites.

    Args:
        publisher_path: Full path to the publisher folder

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO favorite_publishers (publisher_path)
            VALUES (?)
        ''', (publisher_path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Added favorite publisher: {publisher_path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to add favorite publisher '{publisher_path}': {e}")
        return False


def remove_favorite_publisher(publisher_path):
    """
    Remove a publisher from favorites.

    Args:
        publisher_path: Full path to the publisher folder

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM favorite_publishers WHERE publisher_path = ?', (publisher_path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Removed favorite publisher: {publisher_path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to remove favorite publisher '{publisher_path}': {e}")
        return False


def get_favorite_publishers():
    """
    Get all favorite publishers.

    Returns:
        List of dicts with publisher_path and created_at, or empty list on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()
        c.execute('SELECT publisher_path, created_at FROM favorite_publishers ORDER BY publisher_path')
        rows = c.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception as e:
        app_logger.error(f"Failed to get favorite publishers: {e}")
        return []


def is_favorite_publisher(publisher_path):
    """
    Check if a publisher is favorited.

    Args:
        publisher_path: Full path to the publisher folder

    Returns:
        True if favorited, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('SELECT 1 FROM favorite_publishers WHERE publisher_path = ?', (publisher_path,))
        result = c.fetchone()
        conn.close()

        return result is not None

    except Exception as e:
        app_logger.error(f"Failed to check favorite publisher '{publisher_path}': {e}")
        return False


# =============================================================================
# Favorite Series CRUD Operations
# =============================================================================

def add_favorite_series(series_path):
    """
    Add a series to favorites.

    Args:
        series_path: Full path to the series folder

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO favorite_series (series_path)
            VALUES (?)
        ''', (series_path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Added favorite series: {series_path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to add favorite series '{series_path}': {e}")
        return False


def remove_favorite_series(series_path):
    """
    Remove a series from favorites.

    Args:
        series_path: Full path to the series folder

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM favorite_series WHERE series_path = ?', (series_path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Removed favorite series: {series_path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to remove favorite series '{series_path}': {e}")
        return False


def get_favorite_series():
    """
    Get all favorite series.

    Returns:
        List of dicts with series_path and created_at, or empty list on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()
        c.execute('SELECT series_path, created_at FROM favorite_series ORDER BY series_path')
        rows = c.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception as e:
        app_logger.error(f"Failed to get favorite series: {e}")
        return []


def is_favorite_series(series_path):
    """
    Check if a series is favorited.

    Args:
        series_path: Full path to the series folder

    Returns:
        True if favorited, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('SELECT 1 FROM favorite_series WHERE series_path = ?', (series_path,))
        result = c.fetchone()
        conn.close()

        return result is not None

    except Exception as e:
        app_logger.error(f"Failed to check favorite series '{series_path}': {e}")
        return False


# =============================================================================
# Issues Read CRUD Operations
# =============================================================================

def mark_issue_read(issue_path):
    """
    Mark an issue as read (records current timestamp).

    Args:
        issue_path: Full path to the issue file

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO issues_read (issue_path, read_at)
            VALUES (?, CURRENT_TIMESTAMP)
        ''', (issue_path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Marked issue as read: {issue_path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to mark issue as read '{issue_path}': {e}")
        return False


def unmark_issue_read(issue_path):
    """
    Remove read status from an issue.

    Args:
        issue_path: Full path to the issue file

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM issues_read WHERE issue_path = ?', (issue_path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Unmarked issue as read: {issue_path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to unmark issue as read '{issue_path}': {e}")
        return False


def get_issues_read():
    """
    Get all read issues.

    Returns:
        List of dicts with issue_path and read_at, or empty list on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()
        c.execute('SELECT issue_path, read_at FROM issues_read ORDER BY read_at DESC')
        rows = c.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception as e:
        app_logger.error(f"Failed to get read issues: {e}")
        return []


def is_issue_read(issue_path):
    """
    Check if an issue has been read.

    Args:
        issue_path: Full path to the issue file

    Returns:
        True if read, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('SELECT 1 FROM issues_read WHERE issue_path = ?', (issue_path,))
        result = c.fetchone()
        conn.close()

        return result is not None

    except Exception as e:
        app_logger.error(f"Failed to check if issue is read '{issue_path}': {e}")
        return False


def get_issue_read_date(issue_path):
    """
    Get the date an issue was read.

    Args:
        issue_path: Full path to the issue file

    Returns:
        Read date as string, or None if not read or on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return None

        c = conn.cursor()
        c.execute('SELECT read_at FROM issues_read WHERE issue_path = ?', (issue_path,))
        result = c.fetchone()
        conn.close()

        return result['read_at'] if result else None

    except Exception as e:
        app_logger.error(f"Failed to get read date for issue '{issue_path}': {e}")
        return None


# =============================================================================
# To Read Functions
# =============================================================================

def add_to_read(path, item_type='file'):
    """
    Add an item to the 'to read' list.

    Args:
        path: Full path to the file or folder
        item_type: 'file' or 'folder'

    Returns:
        True on success, False on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO to_read (path, type)
            VALUES (?, ?)
        ''', (path, item_type))

        conn.commit()
        conn.close()

        app_logger.info(f"Added to 'to read': {path} ({item_type})")
        return True

    except Exception as e:
        app_logger.error(f"Failed to add to 'to read' '{path}': {e}")
        return False


def remove_to_read(path):
    """
    Remove an item from the 'to read' list.

    Args:
        path: Full path to the file or folder

    Returns:
        True on success, False on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM to_read WHERE path = ?', (path,))

        conn.commit()
        conn.close()

        app_logger.info(f"Removed from 'to read': {path}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to remove from 'to read' '{path}': {e}")
        return False


def get_to_read_items(limit=None):
    """
    Get all 'to read' items.

    Args:
        limit: Optional limit on number of items returned

    Returns:
        List of dicts with path, type, created_at keys, or empty list on error
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()
        if limit:
            c.execute('SELECT path, type, created_at FROM to_read ORDER BY created_at DESC LIMIT ?', (limit,))
        else:
            c.execute('SELECT path, type, created_at FROM to_read ORDER BY created_at DESC')

        results = [dict(row) for row in c.fetchall()]
        conn.close()

        return results

    except Exception as e:
        app_logger.error(f"Failed to get 'to read' items: {e}")
        return []


def is_to_read(path):
    """
    Check if an item is in the 'to read' list.

    Args:
        path: Full path to the file or folder

    Returns:
        True if in list, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('SELECT 1 FROM to_read WHERE path = ?', (path,))
        result = c.fetchone()
        conn.close()

        return result is not None

    except Exception as e:
        app_logger.error(f"Failed to check 'to read' status for '{path}': {e}")
        return False


# =============================================================================
# Stats Cache Functions
# =============================================================================

def get_cached_stats(key):
    """
    Get cached stats by key.

    Args:
        key: Cache key (e.g., 'library_stats', 'file_type_distribution')

    Returns:
        Cached value (parsed from JSON) or None if not found
    """
    try:
        import json

        conn = get_db_connection()
        if not conn:
            return None

        c = conn.cursor()
        c.execute('SELECT value FROM stats_cache WHERE key = ?', (key,))
        row = c.fetchone()
        conn.close()

        if row:
            return json.loads(row['value'])
        return None

    except Exception as e:
        app_logger.error(f"Failed to get cached stats for '{key}': {e}")
        return None


def save_cached_stats(key, value):
    """
    Save stats to cache.

    Args:
        key: Cache key
        value: Value to cache (will be JSON-encoded)

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
            INSERT OR REPLACE INTO stats_cache (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, json.dumps(value)))

        conn.commit()
        conn.close()

        app_logger.debug(f"Saved stats cache for: {key}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to save stats cache for '{key}': {e}")
        return False


def clear_stats_cache():
    """
    Clear all cached stats.

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        c.execute('DELETE FROM stats_cache')

        conn.commit()
        count = c.rowcount
        conn.close()

        app_logger.info(f"Cleared stats cache ({count} entries)")
        return True

    except Exception as e:
        app_logger.error(f"Failed to clear stats cache: {e}")
        return False


def clear_stats_cache_keys(keys):
    """
    Clear specific cache keys while preserving others.

    Args:
        keys: List of cache keys to invalidate (e.g., ['library_stats', 'reading_history'])

    Returns:
        True if successful, False otherwise
    """
    if not keys:
        return True

    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        placeholders = ','.join('?' * len(keys))
        c.execute(f'DELETE FROM stats_cache WHERE key IN ({placeholders})', keys)

        conn.commit()
        count = c.rowcount
        conn.close()

        app_logger.info(f"Cleared stats cache keys {keys} ({count} entries)")
        return True

    except Exception as e:
        app_logger.error(f"Failed to clear stats cache keys {keys}: {e}")
        return False
