# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Comic Library Utilities (CLU) is a Flask-based web application for managing comic book collections. It provides bulk operations for CBZ/CBR files, metadata editing, file renaming, format conversion, and folder monitoring. Designed to run in Docker, it integrates with comic databases (GCD, ComicVine, Metron) for metadata enrichment.

## Development Commands

```bash
# Run locally (development)
python app.py

# Run with Docker
docker build -t comic-utils .
docker run -p 5577:5577 -v /path/to/comics:/data -v /path/to/downloads:/downloads comic-utils

# Verify Python syntax
python -m py_compile <filename.py>

# Production server (used in Docker)
gunicorn -w 1 --threads 8 -b 0.0.0.0:5577 --timeout 120 app:app
```

## Architecture

### Core Application Flow
- **`api.py`**: Creates the Flask app instance and handles download queue/remote downloads
- **`app.py`**: Main application - imports Flask app from `api.py`, registers blueprints, defines all routes and API endpoints
- **`monitor.py`**: Standalone file watcher for folder monitoring (runs when `MONITOR=yes`)

### Key Modules
| Module | Purpose |
|--------|---------|
| `config.py` | ConfigParser-based settings from `/config/config.ini` |
| `database.py` | SQLite database (`comic_utils.db`) for caching, file index, reading history |
| `rename.py` | Comic file renaming with regex patterns for volume/issue extraction |
| `edit.py` | CBZ editing - image manipulation, file reordering, cropping |
| `convert.py` | CBR to CBZ conversion using `unar` |
| `comicinfo.py` | ComicInfo.xml parsing and generation |
| `wrapped.py` | Yearly reading stats image generation (Spotify Wrapped style) |

### Blueprints
- `favorites_bp` (favorites.py): Reading list/favorites functionality
- `opds_bp` (opds.py): OPDS feed for comic readers

### Data Flow
1. Comics stored in `/data` (mounted volume)
2. Downloads go to `/downloads/temp` then processed to `/downloads/processed`
3. SQLite database in `CACHE_DIR` (default `/cache`)
4. Config persisted in `/config/config.ini`

### Frontend
- Jinja2 templates in `templates/`
- Bootswatch themes (26 themes supported)
- Bootstrap 5 with custom CSS in `static/css/`

## Configuration

Settings in `config.py` define defaults merged with `/config/config.ini`. Key settings:
- `WATCH`/`TARGET`: Folder monitoring paths
- `AUTOCONVERT`: Auto CBR-to-CBZ conversion
- `BOOTSTRAP_THEME`: UI theme name
- API keys: `COMICVINE_API_KEY`, `PIXELDRAIN_API_KEY`, `METRON_USERNAME/PASSWORD`

## File Processing Pipeline

CBZ processing in `edit.py` (`process_cbz_file`):
1. Delete `_MACOSX` folders
2. Remove prefix characters (`.`, `_`, `._`) from filenames
3. Skip/delete files based on configured extensions
4. Normalize image filenames with zero-padded numbering

## Docker Environment

- Base: `python:3.11-slim-bookworm`
- Uses `tini` as PID 1, `gosu` for user switching
- Playwright/Chromium for web scraping features
- `entrypoint.sh` handles PUID/PGID permissions

## Key Patterns

### Logging
Use `app_logger` from `app_logging.py` for application logs, `monitor_logger` for folder monitoring.

### Database Access
```python
from database import get_db_connection
conn = get_db_connection()
# Always use WAL mode - concurrent reads supported
```

### Image Processing
Use `helpers.py` functions: `safe_image_open()`, `create_thumbnail_streaming()` for memory-safe PIL operations.
