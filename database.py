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
        
        # Migration: Check if file_mtime column exists, add if not
        c.execute("PRAGMA table_info(thumbnail_jobs)")
        columns = [column[1] for column in c.fetchall()]
        if 'file_mtime' not in columns:
            app_logger.info("Migrating database: adding file_mtime column")
            c.execute("ALTER TABLE thumbnail_jobs ADD COLUMN file_mtime REAL")

        # Create file_move_history table for tracking last 100 moved files
        c.execute('''
            CREATE TABLE IF NOT EXISTS file_move_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                target_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER,
                moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create index on moved_at for faster cleanup queries
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_moved_at ON file_move_history(moved_at DESC)
        ''')

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

def log_file_move(source_path, target_path, file_size=None):
    """
    Log a file move to the database and maintain only the last 100 records.

    Args:
        source_path: Original file path
        target_path: Destination file path
        file_size: Size of the file in bytes (optional)
    """
    try:
        conn = get_db_connection()
        if not conn:
            return False

        c = conn.cursor()
        filename = os.path.basename(target_path)

        # Insert the new record
        c.execute('''
            INSERT INTO file_move_history (source_path, target_path, filename, file_size)
            VALUES (?, ?, ?, ?)
        ''', (source_path, target_path, filename, file_size))

        # Keep only the last 100 records
        c.execute('''
            DELETE FROM file_move_history
            WHERE id NOT IN (
                SELECT id FROM file_move_history
                ORDER BY moved_at DESC
                LIMIT 100
            )
        ''')

        conn.commit()
        conn.close()

        app_logger.debug(f"Logged file move: {filename}")
        return True
    except Exception as e:
        app_logger.error(f"Failed to log file move: {e}")
        return False

def get_recent_file_moves(limit=100):
    """
    Get the most recent file moves from the database.

    Args:
        limit: Maximum number of records to return (default: 100)

    Returns:
        List of dictionaries containing file move records
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()
        c.execute('''
            SELECT id, source_path, target_path, filename, file_size, moved_at
            FROM file_move_history
            ORDER BY moved_at DESC
            LIMIT ?
        ''', (limit,))

        rows = c.fetchall()
        conn.close()

        # Convert rows to list of dictionaries
        return [dict(row) for row in rows]
    except Exception as e:
        app_logger.error(f"Failed to get recent file moves: {e}")
        return []
