# Large File Conversion Improvements

## Overview

This document describes the improvements made to handle large file conversions (files over 500MB) in the CLU (Comic Library Utilities) application.

## Problem

When attempting to convert very large RAR files (1GB+), the operation would timeout and show "Network timeout" errors in the web interface. This was caused by:

1. **HTTP Connection Timeouts**: The Server-Sent Events (SSE) connection would timeout during long operations
2. **No Progress Feedback**: Users had no visibility into the conversion progress
3. **Blocking Operations**: The process would block until completion without intermediate updates

## Solutions Implemented

### 1. Enhanced Progress Reporting

- **File Size Detection**: Automatically detects files larger than the configurable threshold (default: 500MB)
- **Step-by-Step Progress**: Shows progress through multiple steps:
  - **Convert Operation**: 3 steps (Extract → Count → Compress)
  - **Rebuild Operation**: 4 steps (Prepare → Create folder → Extract → Recompress)
- **Compression Progress**: For large files, shows compression progress every 10% of files processed
- **Extraction Progress**: For rebuild operations, shows extraction progress every 10% of files processed

### 2. Improved Timeout Handling

- **Configurable Timeout**: Users can set operation timeout in settings (default: 1 hour)
- **Keepalive Messages**: Prevents connection timeouts during long operations
- **Non-blocking I/O**: Uses `select()` to handle output streams without blocking

### 3. Better Error Handling

- **Graceful Timeout**: Process is killed and error reported if timeout is exceeded
- **Memory Management**: Better memory handling for large file operations
- **Cleanup**: Ensures temporary directories are properly cleaned up

## Configuration Options

### New Settings in Config Page

1. **Operation Timeout (seconds)**
   - Default: 3600 seconds (1 hour)
   - Range: 300-7200 seconds (5 minutes to 2 hours)
   - Controls maximum time for large file operations

2. **Large File Threshold (MB)**
   - Default: 500 MB
   - Range: 100-2000 MB
   - Files larger than this get enhanced progress reporting

## Usage

### For Large Files (>500MB)

1. The system automatically detects large files and provides enhanced feedback
2. Progress updates show:
   - File size and processing status
   - Step-by-step progress through extraction and compression
   - Compression progress for files with many internal files

### Convert vs Rebuild Operations

**Convert Operation (RAR/CBR → CBZ)**:
- 3-step process: Extract → Count → Compress
- Converts RAR/CBR files to CBZ format
- Deletes original files after successful conversion
- Available in: Directory operations (convert.py, rebuild.py) and Single file operations (single_file.py)

**Rebuild Operation (CBZ → CBZ)**:
- 4-step process: Prepare → Create folder → Extract → Recompress
- Rebuilds CBZ files to ensure proper structure
- Useful for fixing corrupted or improperly structured CBZ files
- Shows both extraction and compression progress for large files
- Available in: Directory operations (rebuild.py) and Single file operations (single_file.py)

### Directory vs Single File Operations

**Directory Operations**:
- Process multiple files in a directory
- Show overall progress across all files
- Available in: convert.py, rebuild.py
- Progress shows: "Processing file: filename (X/Y)"

**Single File Operations**:
- Process one file at a time
- Show detailed progress within the single file
- Available in: single_file.py
- Progress shows: Step-by-step progress within the file
- Ideal for processing individual large files

### Configuration

1. Go to the **Config** page in the web interface
2. Scroll to **Performance & Timeout Settings**
3. Adjust timeout and threshold values as needed
4. Save settings

## Technical Details

### Backend Changes

- **convert.py**: Added `convert_single_rar_file()` function with progress reporting
- **rebuild.py**: Added `rebuild_single_cbz_file()` function with progress reporting and 4-step process
- **single_file.py**: Added both `convert_single_rar_file()` and `rebuild_single_cbz_file()` functions with progress reporting
- **app.py**: Enhanced SSE streaming with timeout handling and keepalive messages
- **Config**: Added timeout and threshold configuration options

### Frontend Changes

- **index.html**: Enhanced progress tracking for large files
- **Progress Bar**: Shows detailed progress for each step
- **Status Messages**: Real-time updates on current operation

### Memory Management

- **Temporary Directory Cleanup**: Ensures temp files are removed after processing
- **Streaming Processing**: Processes files in chunks to manage memory usage
- **Error Recovery**: Graceful handling of memory-related errors

## Testing

Use the provided test scripts to verify improvements:

```bash
# Test conversion improvements
python test_large_file_convert.py

# Test rebuild improvements
python test_large_file_rebuild.py

# Test single file improvements
python test_large_file_single.py
```

These create test large files and verify conversion, rebuild, and single file processes work correctly.

## Performance Impact

- **Small Files (<500MB)**: No performance impact, same behavior as before
- **Large Files (>500MB)**: 
  - Slightly more verbose logging
  - Better user experience with progress feedback
  - More reliable completion for very large files

## Troubleshooting

### Common Issues

1. **Still Getting Timeouts**
   - Increase the Operation Timeout setting
   - Check available disk space
   - Ensure sufficient RAM for large files

2. **Progress Not Updating**
   - Check browser console for JavaScript errors
   - Verify SSE connection is active
   - Refresh page and retry

3. **Memory Issues**
   - Reduce the Large File Threshold setting
   - Ensure adequate system memory
   - Close other applications during large conversions

### Logs

Check the application logs for detailed information:
- **app.log**: General application logs
- **Web interface logs**: Real-time progress and error messages

## Future Improvements

Potential enhancements for future versions:

1. **Resumable Operations**: Allow interrupted conversions to resume
2. **Background Processing**: Move large operations to background tasks
3. **Batch Processing**: Optimize for multiple large files
4. **Compression Options**: Allow users to choose compression levels
5. **Progress Persistence**: Save progress across browser sessions 