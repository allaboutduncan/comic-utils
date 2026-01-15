 ---
 Selected Approach: Option 1 - Background Worker Queue

 Files to Create/Modify
 ┌─────────────────────┬────────┬─────────────────────────────────────────────────────────┐
 │        File         │ Action │                       Description                       │
 ├─────────────────────┼────────┼─────────────────────────────────────────────────────────┤
 │ metadata_scanner.py │ Create │ New module with PriorityQueue and daemon worker threads │
 ├─────────────────────┼────────┼─────────────────────────────────────────────────────────┤
 │ database.py         │ Modify │ Schema migration, metadata query/update functions       │
 ├─────────────────────┼────────┼─────────────────────────────────────────────────────────┤
 │ app.py              │ Modify │ Startup init, API endpoints for status/trigger          │
 ├─────────────────────┼────────┼─────────────────────────────────────────────────────────┤
 │ file_watcher.py     │ Modify │ Queue new files for scanning after indexing             │
 ├─────────────────────┼────────┼─────────────────────────────────────────────────────────┤
 │ config.py           │ Modify │ Add ENABLE_METADATA_SCAN, METADATA_SCAN_THREADS         │
 └─────────────────────┴────────┴─────────────────────────────────────────────────────────┘
 ---
 1. Database Schema Changes (database.py)

 Add columns via migration in init_db() (~line 81):

 c.execute("PRAGMA table_info(file_index)")
 columns = [col[1] for col in c.fetchall()]

 # Full ComicInfo.xml metadata columns
 metadata_columns = [
     'ci_title',        # Title field from ComicInfo
     'ci_series',       # Series name
     'ci_number',       # Issue number
     'ci_count',        # Total issues in series
     'ci_volume',       # Volume number
     'ci_year',         # Publication year
     'ci_writer',       # Writer(s) - comma-separated
     'ci_penciller',    # Penciller(s) - comma-separated
     'ci_inker',        # Inker(s) - comma-separated
     'ci_colorist',     # Colorist(s) - comma-separated
     'ci_letterer',     # Letterer(s) - comma-separated
     'ci_coverartist',  # Cover artist(s) - comma-separated
     'ci_publisher',    # Publisher name
     'ci_genre',        # Genre(s) - comma-separated
     'ci_characters',   # Characters - comma-separated
     'metadata_scanned_at'  # Timestamp of last scan (REAL)
 ]

 for col in metadata_columns:
     if col not in columns:
         col_type = 'REAL' if col == 'metadata_scanned_at' else 'TEXT'
         c.execute(f'ALTER TABLE file_index ADD COLUMN {col} {col_type}')
         app_logger.info(f"Migrating file_index: adding {col}")

 # Index for efficient pending file queries
 c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_metadata_scan ON file_index(metadata_scanned_at, modified_at)')
 # Index for character/writer searches
 c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_characters ON file_index(ci_characters)')
 c.execute('CREATE INDEX IF NOT EXISTS idx_file_index_writer ON file_index(ci_writer)')

 Column naming: Prefix with ci_ to distinguish from existing columns and indicate ComicInfo source.

 Add functions:
 - update_file_metadata(file_id, metadata_dict, scanned_at) - Update all metadata columns
 - get_files_needing_metadata_scan(limit=1000) - Returns files where metadata_scanned_at IS NULL OR < modified_at
 - get_metadata_scan_stats() - Returns {total, scanned, pending}

 ---
 2. New Module: metadata_scanner.py

 Structure:
 from queue import PriorityQueue
 import threading

 # Priority levels (lower = higher priority)
 PRIORITY_NEW_FILE = 1      # From file_watcher
 PRIORITY_MODIFIED = 2      # modified_at > metadata_scanned_at
 PRIORITY_UNSCANNED = 3     # metadata_scanned_at IS NULL
 PRIORITY_BATCH = 4         # Startup batch

 # Global state
 metadata_queue = PriorityQueue()
 scanner_progress = {
     'total_pending': 0,
     'scanned_count': 0,
     'errors': 0,
     'is_running': False,
     'current_file': None
 }
 worker_threads = []

 Key functions:
 - start_metadata_scanner(num_workers=2) - Spawn daemon workers, queue pending files
 - stop_metadata_scanner() - Graceful shutdown
 - scan_worker() - Worker loop: get task, extract metadata, update DB
 - process_metadata_scan(task) - Call read_comicinfo_from_zip(), update file_index
 - queue_file_for_scan(file_path, priority) - Called by file_watcher
 - queue_pending_files() - Batch queue files needing scan
 - get_scanner_status() - Return progress dict for API

 Worker pattern (similar to api.py download queue):
 def scan_worker():
     while True:
         task = metadata_queue.get()
         if task is None:  # Shutdown signal
             break
         process_metadata_scan(task)
         scanner_progress['scanned_count'] += 1
         metadata_queue.task_done()

 Metadata extraction mapping (ComicInfo.xml → file_index):
 def process_metadata_scan(task):
     metadata = read_comicinfo_from_zip(file_path)

     # Map ComicInfo fields to database columns
     db_metadata = {
         'ci_title': metadata.get('Title', ''),
         'ci_series': metadata.get('Series', ''),
         'ci_number': metadata.get('Number', ''),
         'ci_count': metadata.get('Count', ''),
         'ci_volume': metadata.get('Volume', ''),
         'ci_year': metadata.get('Year', ''),
         'ci_writer': metadata.get('Writer', ''),
         'ci_penciller': metadata.get('Penciller', ''),
         'ci_inker': metadata.get('Inker', ''),
         'ci_colorist': metadata.get('Colorist', ''),
         'ci_letterer': metadata.get('Letterer', ''),
         'ci_coverartist': metadata.get('CoverArtist', ''),
         'ci_publisher': metadata.get('Publisher', ''),
         'ci_genre': metadata.get('Genre', ''),
         'ci_characters': metadata.get('Characters', '')
     }

     update_file_metadata(task.file_id, db_metadata, time.time())

 ---
 3. App Startup Integration (app.py)

 Add to startup sequence (~line 10850):
 from metadata_scanner import start_metadata_scanner

 def start_metadata_scanner_delayed():
     while not index_built:
         time.sleep(1)
     start_metadata_scanner()

 threading.Thread(target=start_metadata_scanner_delayed, daemon=True).start()

 Add API endpoints:
 - GET /api/metadata-scan-status - Returns scanner progress and DB stats
 - POST /api/metadata-scan-trigger - Manually queue pending files

 ---
 4. File Watcher Integration (file_watcher.py)

 In _process_pending_events() after add_file_index_entry():
 from metadata_scanner import queue_file_for_scan, PRIORITY_NEW_FILE
 if file_path.lower().endswith(('.cbz', '.zip')):
     queue_file_for_scan(file_path, PRIORITY_NEW_FILE)

 ---
 5. Configuration (config.py)

 Add to default_settings:
 "ENABLE_METADATA_SCAN": "True",
 "METADATA_SCAN_THREADS": "2",

 ---
 6. Error Handling
 ┌──────────────────────────┬────────────────────────────────┐
 │         Scenario         │            Handling            │
 ├──────────────────────────┼────────────────────────────────┤
 │ File deleted before scan │ Mark scanned (no data), skip   │
 ├──────────────────────────┼────────────────────────────────┤
 │ Invalid ZIP/CBZ          │ Mark scanned, log warning      │
 ├──────────────────────────┼────────────────────────────────┤
 │ No ComicInfo.xml         │ Mark scanned, empty strings    │
 ├──────────────────────────┼────────────────────────────────┤
 │ DB connection fail       │ Skip, will retry on next batch │
 └──────────────────────────┴────────────────────────────────┘
 Key principle: Always mark metadata_scanned_at to prevent infinite retries.

 ---
 Performance Estimates

 - Extraction time: 5-50ms per file
 - With 2 workers: ~40-100 files/second
 - 100,000 files: ~17-40 minutes initial scan
 - Subsequent runs: Only new/modified files (seconds to minutes)

 ---
 Verification Steps

 1. Start app → verify "Started X metadata scanner workers" in logs
 2. Check /api/metadata-scan-status → see progress incrementing
 3. Query DB: SELECT COUNT(*) FROM file_index WHERE metadata_scanned_at IS NOT NULL
 4. Add new CBZ file → verify scanned within seconds (high priority)
 5. Modify existing CBZ → verify rescanned (modified_at > metadata_scanned_at)

 Implementation Notes:
 - 
   Files Modified:
  1. database.py - Added 16 metadata columns (ci_title, ci_series, ci_number, etc.) with schema migration and functions for metadata operations
  2. metadata_scanner.py (new) - Background worker module with PriorityQueue-based task processing
  3. config.py - Added ENABLE_METADATA_SCAN and METADATA_SCAN_THREADS settings
  4. app.py - Added scanner startup and API endpoints (/api/metadata-scan-status, /api/metadata-scan-trigger)
  5. file_watcher.py - Integrated to queue new CBZ files for high-priority scanning

  How It Works:
  - On app startup, after the file index is built, 1-4 worker threads start processing pending files
  - Files are prioritized: new files from file_watcher (priority 1) → modified files (2) → unscanned files (3) → batch startup (4)
  - Each file's ComicInfo.xml is extracted and metadata stored in file_index table
  - Progress is tracked and available via /api/metadata-scan-status

  Performance:
  - ~40-100 files/second with 2 workers
  - 100,000 files takes approximately 17-40 minutes for initial scan
  - Subsequent runs only process new/modified files