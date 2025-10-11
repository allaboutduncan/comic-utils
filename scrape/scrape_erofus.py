import os
import re
import time
import zipfile
import shutil
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app_logging import app_logger

BASE = "https://www.erofus.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# HTTP session for downloads
session = requests.Session()
retries = Retry(total=5, backoff_factor=0.6, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.headers.update({"User-Agent": UA})

def safe_title(text: str) -> str:
    """Sanitize title for filesystem"""
    t = ' '.join((text or 'comic').split())
    return re.sub(r'[<>:"/\\|?*\r\n]', '', t).strip() or "comic"

def get_issue_links(series_url: str, log_callback=None) -> list[str]:
    """Get all issue links from a series page"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    r = session.get(series_url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Extract base path from series URL (e.g., /eros-comix/elizabeth-bathory)
    parsed = urlparse(series_url)
    series_path = parsed.path.rstrip("/")

    # Find all issue links that contain the series path
    issue_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Check if this is an issue link (contains series path + /issue-)
        if series_path in href and "/issue-" in href.lower():
            full_url = urljoin(BASE, href)
            issue_links.append(full_url)

    # Remove duplicates and sort
    issue_links = sorted(set(issue_links))
    log(f"Found {len(issue_links)} issue links")
    return issue_links

def get_page_links(issue_url: str, log_callback=None) -> list[str]:
    """Get all page links from an issue page"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    r = session.get(issue_url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Extract series and issue names from URL
    # e.g., /comics/eurotica-comics/nina/issue-1 -> eurotica-comics/nina/issue-1
    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip("/").split("/")

    # Remove 'comics' prefix if present to get the series/issue identifier
    if path_parts[0] == "comics":
        path_parts = path_parts[1:]

    # Create identifier pattern (e.g., "eurotica-comics/nina/issue-1")
    identifier = "/".join(path_parts)

    # Find all page links that start with /pic/ and contain the identifier
    page_links = []
    for a in soup.find_all("a", href=True, class_="a-click"):
        href = a["href"]
        # Check if this is a page link (/pic/.../series/issue/page-number)
        if href.startswith("/pic/") and identifier in href and re.search(r'/\d+$', href):
            full_url = urljoin(BASE, href)
            page_links.append(full_url)

    # Remove duplicates and sort by page number
    page_links = sorted(set(page_links), key=lambda x: int(x.split('/')[-1]))
    log(f"Found {len(page_links)} page links")
    return page_links

def get_image_url(page_url: str, log_callback=None) -> str:
    """Extract the medium image URL from a page"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    r = session.get(page_url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Find the medium image in .picture-full-container
    container = soup.find("div", class_="picture-full-container")
    if container:
        img = container.find("img", src=re.compile(r'/medium/'))
        if img and img.get("src"):
            return urljoin(BASE, img["src"])

    # Fallback: search anywhere for medium image
    img = soup.find("img", src=re.compile(r'/medium/'))
    if img and img.get("src"):
        return urljoin(BASE, img["src"])

    log(f"Warning: No medium image found on {page_url}")
    return None

def download_image(url: str, output_path: str, log_callback=None) -> bool:
    """Download image using requests"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    try:
        with session.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(32768):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as e:
        log(f"Download failed: {str(e)[:60]}")
        return False

def create_cbz(folder: str, log_callback=None):
    """Create CBZ file from folder with unique naming to prevent overwrites"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    # Generate base CBZ path
    cbz = f"{folder}.cbz"

    # Check if file already exists and add counter if needed
    if os.path.exists(cbz):
        base_path = folder
        counter = 1
        while os.path.exists(cbz):
            cbz = f"{base_path}_({counter}).cbz"
            counter += 1
        log(f"File already exists, using unique name: {os.path.basename(cbz)}")

    with zipfile.ZipFile(cbz, "w", zipfile.ZIP_DEFLATED) as z:
        for fname in sorted(os.listdir(folder)):
            z.write(os.path.join(folder, fname), arcname=fname)
    shutil.rmtree(folder, ignore_errors=True)
    log(f"Created {cbz}")
    return cbz

def cleanup_empty_folder(folder_path, log_callback=None):
    """Remove folder if it exists and is empty or only contains temp files"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    try:
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            files = [f for f in os.listdir(folder_path) if not f.startswith('.')]
            if len(files) == 0:
                shutil.rmtree(folder_path, ignore_errors=True)
                log(f"Cleaned up empty folder: {os.path.basename(folder_path)}")
    except Exception as e:
        app_logger.warning(f"Could not cleanup folder {folder_path}: {e}")

def scrape_issue(issue_url: str, output_dir: str = None, log_callback=None, progress_callback=None):
    """Scrape a single issue"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    def update_progress(data):
        if progress_callback:
            progress_callback(data)

    log(f"Scraping issue: {issue_url}")
    update_progress({"status": "Getting page links", "current": issue_url})

    # Get all page links
    page_links = get_page_links(issue_url, log_callback)
    if not page_links:
        log("No pages found")
        return None

    # Extract issue title from URL
    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip("/").split("/")
    issue_name = safe_title(path_parts[-1])  # e.g., "issue-1"
    series_name = safe_title(path_parts[-2]) # e.g., "elizabeth-bathory"
    title = f"{series_name}_{issue_name}"

    # Create folder
    folder = title
    if output_dir:
        folder = os.path.join(output_dir, folder)

    # Ensure unique folder name
    if os.path.exists(folder):
        base_folder = folder
        counter = 1
        while os.path.exists(folder):
            folder = f"{base_folder}_({counter})"
            counter += 1
        log(f"Folder exists, using unique name: {os.path.basename(folder)}")

    os.makedirs(folder, exist_ok=True)
    log(f"Saving to: {os.path.basename(folder)}")

    # Download all pages
    success_count = 0
    for i, page_url in enumerate(page_links, 1):
        log(f"Processing page {i}/{len(page_links)}")
        update_progress({
            "status": "Downloading images",
            "current": f"{i}/{len(page_links)}",
            "progress": (i / len(page_links)) * 90
        })

        # Get image URL
        img_url = get_image_url(page_url, log_callback)
        if not img_url:
            continue

        # Determine extension
        ext = os.path.splitext(img_url.split("?")[0])[-1].lower()
        if not ext or len(ext) > 5:
            ext = ".jpg"

        # Download image
        output_path = os.path.join(folder, f"{i:03d}{ext}")
        if download_image(img_url, output_path, log_callback):
            success_count += 1
            log(f"Downloaded {i}/{len(page_links)}")
        else:
            log(f"Failed {i}/{len(page_links)}")

        time.sleep(0.3)  # Be nice to the server

    # Check if we downloaded anything
    if success_count == 0:
        log("No images downloaded successfully")
        cleanup_empty_folder(folder, log_callback)
        return None

    # Create CBZ
    update_progress({"status": "Creating CBZ", "current": title, "progress": 95})
    cbz_path = create_cbz(folder, log_callback)
    update_progress({"status": "Completed", "current": os.path.basename(cbz_path), "progress": 100})
    return cbz_path

def is_issue_url(url: str, log_callback=None) -> bool:
    """Check if URL points directly to issue pages (vs a series page with multiple issues)"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    try:
        r = session.get(url, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Look for page links (links starting with /pic/)
        page_links = soup.find_all("a", href=re.compile(r'^/pic/'), class_="a-click")

        # If we find page links, this is an issue URL
        if len(page_links) > 0:
            log(f"Detected direct issue URL (found {len(page_links)} page links)")
            return True

        # Otherwise, it's a series page
        log("Detected series page URL")
        return False

    except Exception as e:
        log(f"Error checking URL type: {e}")
        # Default to treating as series page
        return False

def scrape_series(series_url: str, output_dir: str = None, log_callback=None, progress_callback=None):
    """Scrape all issues from a series or a single issue"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    def update_progress(data):
        if progress_callback:
            progress_callback(data)

    # Check if URL is a direct issue or series page
    if is_issue_url(series_url, log_callback):
        # Single issue - scrape directly
        log(f"Detected direct issue link: {series_url}")
        result = scrape_issue(series_url, output_dir, log_callback, progress_callback)
        return [result] if result else []
    else:
        # Series - get all issues
        log(f"Detected series link, fetching all issues...")

        # Get all issue links
        issue_links = get_issue_links(series_url, log_callback)
        if not issue_links:
            log("No issues found")
            return []

        results = []
        for idx, issue_url in enumerate(issue_links, 1):
            try:
                log(f"\n{'='*60}")
                log(f"Scraping issue {idx}/{len(issue_links)}: {issue_url}")
                log('='*60)
                update_progress({
                    "status": f"Processing issue {idx}/{len(issue_links)}",
                    "current": issue_url,
                    "progress": 0
                })
                result = scrape_issue(issue_url, output_dir, log_callback, progress_callback)
                if result:
                    results.append(result)
                time.sleep(0.5)
            except Exception as e:
                log(f"Error on {issue_url}: {e}")

        return results
