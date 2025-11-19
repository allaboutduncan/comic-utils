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
