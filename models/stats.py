import sqlite3
import os
from database import get_db_connection, get_db_path, get_cached_stats, save_cached_stats
from app_logging import app_logger

def get_library_stats():
    """
    Get high-level statistics about the library.
    """
    # Check cache first
    cached = get_cached_stats('library_stats')
    if cached:
        return cached

    try:
        conn = get_db_connection()
        if not conn:
            return None

        c = conn.cursor()

        stats = {}

        # Total files and size
        c.execute("SELECT COUNT(*), SUM(size) FROM file_index WHERE type = 'file'")
        row = c.fetchone()
        stats['total_files'] = row[0] or 0
        stats['total_size'] = row[1] or 0

        # Total directories
        c.execute("SELECT COUNT(*) FROM file_index WHERE type = 'directory'")
        stats['total_directories'] = c.fetchone()[0] or 0

        # Root folders (publishers - top-level directories under /data)
        c.execute("SELECT COUNT(*) FROM file_index WHERE type = 'directory' AND parent = '/data'")
        stats['root_folders'] = c.fetchone()[0] or 0

        # Total read issues
        c.execute("SELECT COUNT(*) FROM issues_read")
        stats['total_read'] = c.fetchone()[0] or 0

        # Total to-read
        c.execute("SELECT COUNT(*) FROM to_read")
        stats['total_to_read'] = c.fetchone()[0] or 0

        conn.close()

        # Save to cache
        save_cached_stats('library_stats', stats)

        return stats
    except Exception as e:
        app_logger.error(f"Error getting library stats: {e}")
        return None

def get_file_type_distribution():
    """
    Get the distribution of file types in the library.
    """
    # Check cache first
    cached = get_cached_stats('file_type_distribution')
    if cached:
        return cached

    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()

        # We need to extract extension from path since type is just 'file'
        # SQLite doesn't have great string manipulation, but we can try
        # This is a bit expensive, might need optimization for huge libraries
        c.execute("SELECT path FROM file_index WHERE type = 'file'")
        rows = c.fetchall()

        extensions = {}
        for row in rows:
            path = row['path']
            ext = os.path.splitext(path)[1].lower().replace('.', '')
            if not ext:
                ext = 'unknown'
            extensions[ext] = extensions.get(ext, 0) + 1

        # Convert to list for chart
        data = [{'type': k, 'count': v} for k, v in extensions.items()]
        data.sort(key=lambda x: x['count'], reverse=True)

        conn.close()

        # Save to cache
        save_cached_stats('file_type_distribution', data)

        return data
    except Exception as e:
        app_logger.error(f"Error getting file type distribution: {e}")
        return []

def get_top_publishers(limit=10):
    """
    Get the top publishers (root folders) by file count.
    """
    # Check cache first
    cached = get_cached_stats('top_publishers')
    if cached:
        return cached[:limit]

    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()

        # This assumes publishers are the top-level folders in /data
        # We look for files whose parent starts with /data/PublisherName

        # First get all top level directories
        c.execute("SELECT path, name FROM file_index WHERE parent = '/data' AND type = 'directory'")
        publishers = c.fetchall()

        publisher_stats = []

        for pub in publishers:
            pub_path = pub['path']
            pub_name = pub['name']

            # Count files recursively
            # We can use the existing get_path_counts logic or do it here
            c.execute("SELECT COUNT(*) FROM file_index WHERE path LIKE ? AND type = 'file'", (f"{pub_path}%",))
            count = c.fetchone()[0]

            if count > 0:
                publisher_stats.append({'name': pub_name, 'count': count})

        publisher_stats.sort(key=lambda x: x['count'], reverse=True)

        conn.close()

        # Save to cache (full list, limit applied on return)
        save_cached_stats('top_publishers', publisher_stats)

        return publisher_stats[:limit]
    except Exception as e:
        app_logger.error(f"Error getting top publishers: {e}")
        return []

def get_reading_history_stats():
    """
    Get reading history statistics grouped by day (MM-DD-YYYY format).
    Returns daily read counts for the last 90 days.
    """
    # Check cache first
    cached = get_cached_stats('reading_history')
    if cached:
        return cached

    try:
        conn = get_db_connection()
        if not conn:
            return []

        c = conn.cursor()

        # Extract date from read_at timestamp in MM-DD-YYYY format
        c.execute("""
            SELECT strftime('%m-%d-%Y', read_at) as date, COUNT(*) as count
            FROM issues_read
            GROUP BY date
            ORDER BY read_at DESC
            LIMIT 90
        """)

        rows = c.fetchall()

        history = [{'date': row['date'], 'count': row['count']} for row in rows]
        # Reverse to show chronological order for charts
        history.reverse()

        conn.close()

        # Save to cache
        save_cached_stats('reading_history', history)

        return history
    except Exception as e:
        app_logger.error(f"Error getting reading history: {e}")
        return []
