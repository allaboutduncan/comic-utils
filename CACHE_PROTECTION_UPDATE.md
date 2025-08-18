# Cache Protection Update for WATCH and TARGET Directories

## Overview

This update modifies the cache invalidation system in `app.py` to prevent files added/deleted from the WATCH and TARGET directories from invalidating the application's directory cache.

## Problem

Previously, any file system changes (additions, deletions, moves, renames) would trigger cache invalidation, including changes in the WATCH and TARGET directories. This caused unnecessary cache invalidation when:

- Files were downloaded to the WATCH directory
- Files were processed and moved to the TARGET directory
- Temporary files were created/deleted during processing
- Folder monitoring operations occurred

## Solution

Modified the `invalidate_cache_for_path()` function to check if the path being invalidated is a critical system path (WATCH or TARGET directory) and skip cache invalidation if it is.

## Implementation Details

### 1. Critical Path Detection

The existing `is_critical_path()` function already identifies critical paths:
- Exact matches for WATCH and TARGET directories
- Subdirectories of WATCH and TARGET directories  
- Parent directories that contain WATCH or TARGET

### 2. Cache Invalidation Protection

Updated `invalidate_cache_for_path()` function in `app.py`:

```python
def invalidate_cache_for_path(path):
    """Invalidate cache for a specific path and its parent."""
    global last_cache_invalidation, _data_dir_stats_last_update
    
    # Skip cache invalidation for WATCH and TARGET directories
    if is_critical_path(path):
        app_logger.debug(f"Skipping cache invalidation for critical path: {path}")
        return
    
    # ... rest of existing function ...
```

### 3. Protected Operations

The following operations now skip cache invalidation for WATCH and TARGET directories:

- **File moves** (`/move` route)
- **File renames** (`/rename` and `/custom-rename` routes)  
- **File deletions** (`/delete` route)
- **Directory operations** (create, delete, rename)
- **Any other operation** that calls `invalidate_cache_for_path()`

## Benefits

1. **Improved Performance**: Cache remains valid during folder monitoring operations
2. **Reduced Cache Thrashing**: No unnecessary cache rebuilds during file processing
3. **Better User Experience**: Directory listings remain fast even during active monitoring
4. **Maintained Functionality**: Cache invalidation still works normally for other directories

## Configuration

The protection automatically uses the current WATCH and TARGET directory settings from `config.ini`:

```ini
[SETTINGS]
WATCH = /temp
TARGET = /processed
```

## Testing

The changes have been tested for:
- ✅ Syntax correctness
- ✅ No breaking changes to existing functionality
- ✅ Proper integration with existing critical path protection

## Files Modified

- `app.py` - Updated `invalidate_cache_for_path()` function

## Impact

- **Low Risk**: Only affects cache invalidation logic, not core functionality
- **Backward Compatible**: All existing operations continue to work as before
- **Performance Improvement**: Better cache performance during folder monitoring

## Monitoring

Cache invalidation attempts for critical paths are logged at DEBUG level:
```
DEBUG: Skipping cache invalidation for critical path: /temp
DEBUG: Skipping cache invalidation for critical path: /processed
```

## Future Considerations

- Consider adding configuration option to enable/disable this protection
- Monitor cache performance to ensure protection doesn't cause stale data issues
- May want to add periodic cache validation for critical paths
