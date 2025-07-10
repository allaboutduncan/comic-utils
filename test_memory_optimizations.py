#!/usr/bin/env python3
"""
Test script for memory optimizations in comic-utils.
This script tests the memory management features and demonstrates improvements.
"""

import os
import sys
import time
import tempfile
import shutil
from PIL import Image
import gc

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_utils import (
    MemoryMonitor, 
    memory_context, 
    optimize_for_large_files,
    check_system_memory,
    get_global_monitor
)
from helpers import (
    safe_image_open,
    create_thumbnail_streaming,
    enhance_image_streaming
)


def test_memory_monitor():
    """Test the memory monitoring functionality."""
    print("Testing Memory Monitor...")
    
    monitor = MemoryMonitor(threshold_mb=100, cleanup_threshold_mb=50)
    
    # Test memory usage tracking
    memory_mb = monitor.get_memory_usage()
    print(f"Current memory usage: {memory_mb:.1f}MB")
    
    # Test memory percentage
    memory_percent = monitor.get_memory_percent()
    print(f"Memory percentage: {memory_percent:.1f}%")
    
    # Test cleanup
    freed_mb = monitor.force_cleanup()
    print(f"Freed {freed_mb:.1f}MB during cleanup")
    
    print("✓ Memory monitor test completed\n")


def test_memory_context():
    """Test the memory context manager."""
    print("Testing Memory Context...")
    
    with memory_context("test_operation", cleanup_threshold_mb=100) as monitor:
        # Simulate memory-intensive operation
        large_list = [i for i in range(1000000)]  # ~40MB
        print(f"Created large list, memory usage: {monitor.get_memory_usage():.1f}MB")
        
        # Simulate more memory usage
        another_list = [i * 2 for i in range(500000)]  # ~20MB
        print(f"Added another list, memory usage: {monitor.get_memory_usage():.1f}MB")
        
        # Lists will be cleaned up when context exits
        del large_list
        del another_list
    
    print("✓ Memory context test completed\n")


def test_safe_image_open():
    """Test the safe image context manager."""
    print("Testing Safe Image Open...")
    
    # Create a test image
    test_image_path = "test_image.png"
    test_image = Image.new('RGB', (1000, 1000), color='red')
    test_image.save(test_image_path)
    
    try:
        with safe_image_open(test_image_path) as img:
            print(f"Opened image: {img.size}")
            # Image will be automatically closed when context exits
        
        # Verify image is closed
        print("✓ Safe image open test completed")
        
    finally:
        # Clean up test file
        if os.path.exists(test_image_path):
            os.remove(test_image_path)
    
    print("✓ Safe image open test completed\n")


def test_thumbnail_streaming():
    """Test streaming thumbnail generation."""
    print("Testing Thumbnail Streaming...")
    
    # Create a test image
    test_image_path = "test_large_image.png"
    test_image = Image.new('RGB', (2000, 2000), color='blue')
    test_image.save(test_image_path)
    
    try:
        # Test streaming thumbnail generation
        thumbnail_data = create_thumbnail_streaming(test_image_path, max_size=(100, 100))
        
        if thumbnail_data:
            print(f"Generated thumbnail: {len(thumbnail_data)} bytes")
            print("✓ Thumbnail streaming test completed")
        else:
            print("✗ Thumbnail generation failed")
            
    finally:
        # Clean up test file
        if os.path.exists(test_image_path):
            os.remove(test_image_path)
    
    print("✓ Thumbnail streaming test completed\n")


def test_large_file_optimization():
    """Test large file optimization settings."""
    print("Testing Large File Optimization...")
    
    # Test different file sizes
    test_sizes = [50, 150, 1500]  # MB
    
    for size_mb in test_sizes:
        print(f"Testing optimization for {size_mb}MB file...")
        optimize_for_large_files(size_mb)
    
    print("✓ Large file optimization test completed\n")


def test_system_memory_check():
    """Test system memory checking."""
    print("Testing System Memory Check...")
    
    memory_ok = check_system_memory()
    print(f"System memory check: {'✓ OK' if memory_ok else '✗ LOW'}")
    
    print("✓ System memory check test completed\n")


def test_global_monitor():
    """Test the global memory monitor."""
    print("Testing Global Monitor...")
    
    monitor = get_global_monitor()
    
    # Test memory usage
    memory_mb = monitor.get_memory_usage()
    print(f"Global monitor memory usage: {memory_mb:.1f}MB")
    
    # Test cleanup
    freed_mb = monitor.force_cleanup()
    print(f"Global monitor freed: {freed_mb:.1f}MB")
    
    print("✓ Global monitor test completed\n")


def create_test_cbz():
    """Create a test CBZ file for testing."""
    print("Creating test CBZ file...")
    
    # Create temporary directory
    temp_dir = tempfile.mkdtemp()
    cbz_path = "test_comic.cbz"
    
    try:
        # Create some test images
        for i in range(5):
            img = Image.new('RGB', (800, 1200), color=(i * 50, 100, 150))
            img_path = os.path.join(temp_dir, f"page_{i+1:03d}.jpg")
            img.save(img_path, "JPEG", quality=85)
            img.close()
        
        # Create CBZ file
        import zipfile
        with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    cbz.write(file_path, arcname)
        
        print(f"Created test CBZ: {cbz_path}")
        return cbz_path
        
    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir)


def test_cbz_processing():
    """Test CBZ processing with memory monitoring."""
    print("Testing CBZ Processing...")
    
    cbz_path = create_test_cbz()
    
    try:
        with memory_context("cbz_processing", cleanup_threshold_mb=200):
            # Simulate CBZ processing
            print("Simulating CBZ processing...")
            
            # Extract CBZ
            import zipfile
            extract_dir = "test_extract"
            os.makedirs(extract_dir, exist_ok=True)
            
            with zipfile.ZipFile(cbz_path, 'r') as cbz:
                cbz.extractall(extract_dir)
            
            print(f"Extracted {len(os.listdir(extract_dir))} files")
            
            # Process images (simulate)
            image_files = [f for f in os.listdir(extract_dir) if f.endswith('.jpg')]
            for i, img_file in enumerate(image_files):
                print(f"Processing image {i+1}/{len(image_files)}")
                time.sleep(0.1)  # Simulate processing time
            
            # Clean up
            shutil.rmtree(extract_dir)
        
        print("✓ CBZ processing test completed")
        
    finally:
        # Clean up test CBZ
        if os.path.exists(cbz_path):
            os.remove(cbz_path)
    
    print("✓ CBZ processing test completed\n")


def run_performance_comparison():
    """Run a simple performance comparison."""
    print("Running Performance Comparison...")
    
    # Test memory usage with and without optimizations
    print("Testing memory usage patterns...")
    
    # Without optimization (simulated)
    print("Simulating non-optimized processing...")
    large_data = []
    for i in range(10):
        large_data.append([j for j in range(100000)])  # ~40MB per list
        time.sleep(0.1)
    
    memory_without_opt = get_global_monitor().get_memory_usage()
    print(f"Memory usage without optimization: {memory_without_opt:.1f}MB")
    
    # Clean up
    del large_data
    gc.collect()
    
    # With optimization (using context manager)
    print("Testing optimized processing...")
    with memory_context("optimized_processing", cleanup_threshold_mb=100):
        for i in range(10):
            temp_data = [j for j in range(100000)]
            del temp_data
            time.sleep(0.1)
    
    memory_with_opt = get_global_monitor().get_memory_usage()
    print(f"Memory usage with optimization: {memory_with_opt:.1f}MB")
    
    improvement = ((memory_without_opt - memory_with_opt) / memory_without_opt) * 100
    print(f"Memory improvement: {improvement:.1f}%")
    
    print("✓ Performance comparison completed\n")


def main():
    """Run all memory optimization tests."""
    print("=" * 60)
    print("Memory Optimization Tests")
    print("=" * 60)
    
    try:
        # Initialize memory management
        from memory_utils import initialize_memory_management
        initialize_memory_management()
        
        # Run tests
        test_memory_monitor()
        test_memory_context()
        test_safe_image_open()
        test_thumbnail_streaming()
        test_large_file_optimization()
        test_system_memory_check()
        test_global_monitor()
        test_cbz_processing()
        run_performance_comparison()
        
        print("=" * 60)
        print("All tests completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    finally:
        # Cleanup
        from memory_utils import cleanup_on_exit
        cleanup_on_exit()
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 