import os, re, time, zipfile, shutil
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright
from app_logging import app_logger

BASE = "https://readcomiconline.li"
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
    t = t.replace(" - Read Comic Online", "")
    t = re.sub(r' - Read .+ comic online.*$', '', t, flags=re.I)
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
    anchors = soup.find_all("a", href=True)
    links = []
    series_path = urlparse(series_url).path.rstrip("/")
    for a in anchors:
        href = a["href"]
        full = urljoin(series_url, href)
        # Match both /Issue-X and /Full formats
        if series_path in full and ("/Issue-" in full or "/Full" in full):
            if "readType=" not in full:
                full += ("&" if "?" in full else "?") + "readType=1"
            links.append(full)

    log(f"Found {len(links)} issue links")
    return sorted(set(links))

def create_cbz(folder: str, log_callback=None):
    """Create CBZ file from folder"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    cbz = f"{folder}.cbz"
    with zipfile.ZipFile(cbz, "w", zipfile.ZIP_DEFLATED) as z:
        for fname in sorted(os.listdir(folder)):
            z.write(os.path.join(folder, fname), arcname=fname)
    shutil.rmtree(folder, ignore_errors=True)
    log(f"Created {cbz}")
    return cbz

def download_image_via_requests(url, output_path, referer, log_callback=None):
    """Download image using requests with proper headers"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    headers = {
        "Referer": referer,
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with session.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(32768):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as e:
        log(f"    Request failed: {str(e)[:60]}")
        return False

def scrape_issue_with_browser(pw, issue_url: str, output_dir: str = None, log_callback=None, progress_callback=None):
    """Scrape a single issue using Playwright browser"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    def update_progress(data):
        if progress_callback:
            progress_callback(data)

    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, java_script_enabled=True)
    page = ctx.new_page()

    # Force readType=0 for single-page mode
    if "readType=" in issue_url:
        issue_url = re.sub(r'readType=\d+', 'readType=0', issue_url)
    else:
        issue_url += ("&" if "?" in issue_url else "?") + "readType=0"

    # Remove any existing fragment
    base_url = issue_url.split('#')[0]

    img_urls = []
    title_text = "comic"

    try:
        log(f"  -> Opening {base_url}")
        update_progress({"status": "Loading page", "current": base_url})
        page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)

        # Get title
        title_text = page.title()

        # Find number of pages from select dropdown
        num_pages = page.evaluate("""
            () => {
                const sel = document.querySelector('select.selectEpisode') || document.querySelector('#selectPage');
                return sel ? sel.options.length : 0;
            }
        """)

        if not num_pages:
            log("  !! Could not find page selector")
            page.close()
            ctx.close()
            browser.close()
            return None

        log(f"  -> Found {num_pages} pages")
        update_progress({"status": "Extracting pages", "current": f"{num_pages} pages found"})

        # Navigate through each page and extract the main image from #divImage
        for page_num in range(1, num_pages + 1):
            log(f"  -> Page {page_num}/{num_pages}")
            update_progress({
                "status": "Extracting images",
                "current": f"Page {page_num}/{num_pages}",
                "progress": (page_num / num_pages) * 50  # 0-50% for extraction
            })

            # Wait for images to be present and loaded
            try:
                page.wait_for_selector("#divImage img", timeout=5000)
                page.wait_for_timeout(800)
            except Exception:
                pass

            # Extract only the main comic image from #divImage (the middle/2nd image)
            try:
                img_src = page.evaluate("""
                    () => {
                        const div = document.querySelector('#divImage');
                        if (!div) return null;
                        const imgs = Array.from(div.querySelectorAll('img')).filter(img => {
                            const src = img.src;
                            return src && src.includes('blogspot.com') && !src.includes('loading.gif');
                        });
                        // Return the middle image (usually index 1 in 0-indexed array)
                        if (imgs.length >= 2) {
                            return imgs[1].src;
                        } else if (imgs.length === 1) {
                            return imgs[0].src;
                        }
                        return null;
                    }
                """)

                if img_src:
                    img_urls.append(img_src)
                else:
                    log(f"    ! No image found on page {page_num}")
            except Exception as e:
                log(f"    ! Failed to extract image on page {page_num}: {str(e)[:60]}")

            # Click next button for the next page (except on last page)
            if page_num < num_pages:
                try:
                    # Store current URL hash before click
                    current_hash = page.evaluate("() => window.location.hash")

                    # Click the next button
                    page.evaluate("() => document.querySelector('#btnNext').click()")

                    # Wait for hash to change
                    page.wait_for_function(
                        f"() => window.location.hash !== '{current_hash}'",
                        timeout=3000
                    )
                except Exception as e:
                    log(f"    ! Navigation error: {str(e)[:60]}")

    except Exception as e:
        log(f"  !! Error: {e}")

    if not img_urls:
        log("  !! No images collected")
        try:
            page.close()
            ctx.close()
            browser.close()
        except Exception:
            pass
        return None

    folder = safe_title(title_text)
    if output_dir:
        folder = os.path.join(output_dir, folder)

    log(f"  -> Collected {len(img_urls)} images. Title: {os.path.basename(folder)}")
    update_progress({"status": "Downloading images", "current": os.path.basename(folder)})
    os.makedirs(folder, exist_ok=True)

    # Download images directly using requests with proper referer
    for i, url in enumerate(img_urls, 1):
        ext = os.path.splitext(url.split("?")[0])[-1].lower()
        if not ext or len(ext) > 5:
            ext = ".jpg"
        out = os.path.join(folder, f"{i:03d}{ext}")

        # Try downloading with requests
        if download_image_via_requests(url, out, base_url, log_callback):
            log(f"  ✓ Downloaded {i}/{len(img_urls)}")
            update_progress({
                "status": "Downloading images",
                "current": f"{i}/{len(img_urls)}",
                "progress": 50 + (i / len(img_urls)) * 50  # 50-100% for downloads
            })
        else:
            log(f"  ! Failed {i}/{len(img_urls)}")

        time.sleep(0.2)

    try:
        page.close()
        ctx.close()
        browser.close()
    except Exception:
        pass

    update_progress({"status": "Creating CBZ", "current": os.path.basename(folder), "progress": 95})
    cbz_path = create_cbz(folder, log_callback)
    update_progress({"status": "Completed", "current": os.path.basename(cbz_path), "progress": 100})
    return cbz_path

def scrape_series(series_url: str, output_dir: str = None, log_callback=None, progress_callback=None):
    """Scrape all issues from a series"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        app_logger.info(msg)

    def update_progress(data):
        if progress_callback:
            progress_callback(data)

    # Check if URL is a single issue or series
    if "/Issue-" in series_url or "/Full" in series_url:
        # Single issue or full comic
        log(f"Scraping single issue: {series_url}")
        with sync_playwright() as pw:
            return [scrape_issue_with_browser(pw, series_url, output_dir, log_callback, progress_callback)]
    else:
        # Series - get all issues
        issues = get_issue_links(series_url, log_callback)
        if not issues:
            log("No issues found")
            return []

        results = []
        with sync_playwright() as pw:
            for idx, u in enumerate(issues, 1):
                try:
                    log(f"\n{'='*60}")
                    log(f"Scraping issue {idx}/{len(issues)}: {u}")
                    log('='*60)
                    update_progress({
                        "status": f"Processing issue {idx}/{len(issues)}",
                        "current": u,
                        "progress": 0
                    })
                    result = scrape_issue_with_browser(pw, u, output_dir, log_callback, progress_callback)
                    if result:
                        results.append(result)
                    time.sleep(0.8)
                except Exception as e:
                    log(f"Error on {u}: {e}")

        return results
