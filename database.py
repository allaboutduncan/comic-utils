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
