# Favorites Documentation

## Database
### 3 Tables (in init_db()):
- favorite_publishers - stores publisher paths with timestamp
- favorite_series - stores series paths with timestamp
- issues_read - stores issue paths with read date

### 13 CRUD Functions:

| Function                        | Description                         |
|---------------------------------|-------------------------------------|
| add_favorite_publisher(path)    | Add publisher to favorites          |
| remove_favorite_publisher(path) | Remove publisher from favorites     |
| get_favorite_publishers()       | Get all favorite publishers         |
| is_favorite_publisher(path)     | Check if publisher is favorited     |
| add_favorite_series(path)       | Add series to favorites             |
| remove_favorite_series(path)    | Remove series from favorites        |
| get_favorite_series()           | Get all favorite series             |
| is_favorite_series(path)        | Check if series is favorited        |
| mark_issue_read(path)           | Mark issue as read (with timestamp) |
| unmark_issue_read(path)         | Remove read status                  |
| get_issues_read()               | Get all read issues                 |
| is_issue_read(path)             | Check if issue has been read        |
| get_issue_read_date(path)       | Get the date an issue was read      |

## API

### New file: favorites.py
- Flask Blueprint at /api/favorites prefix
- 12 endpoints total:

| Endpoint                        | Methods           | Description                                    |
|---------------------------------|-------------------|------------------------------------------------|
| /api/favorites/publishers       | GET, POST, DELETE | List, add, remove favorite publishers          |
| /api/favorites/publishers/check | GET               | Check if publisher is favorited                |
| /api/favorites/series           | GET, POST, DELETE | List, add, remove favorite series              |
| /api/favorites/series/check     | GET               | Check if series is favorited                   |
| /api/favorites/issues           | GET, POST, DELETE | List, mark read, unmark read issues            |
| /api/favorites/issues/check     | GET               | Check if issue is read (includes read_at date) |

### Modified: app.py
- Added import: from favorites import favorites_bp
- Added registration: app.register_blueprint(favorites_bp)