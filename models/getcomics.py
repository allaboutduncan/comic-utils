"""
GetComics.org search and download functionality.
"""
import requests
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


def search_getcomics(query: str, max_pages: int = 3) -> list:
    """
    Search getcomics.org and return list of results.

    Args:
        query: Search query string
        max_pages: Maximum number of pages to search (default 3)

    Returns:
        List of dicts with keys: title, link, image
    """
    results = []
    base_url = "https://getcomics.org"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    for page in range(1, max_pages + 1):
        try:
            url = f"{base_url}/page/{page}/" if page > 1 else base_url
            params = {"s": query}

            logger.info(f"Searching getcomics.org page {page}: {query}")
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Find all article posts
            articles = soup.find_all("article", class_="post")
            if not articles:
                logger.info(f"No more results on page {page}")
                break

            for article in articles:
                title_el = article.find("h1", class_="post-title")
                if not title_el:
                    continue

                link_el = title_el.find("a")
                if not link_el:
                    continue

                # Get thumbnail image
                img_el = article.find("img")
                image = ""
                if img_el:
                    # Try data-src first (lazy loading), then src
                    image = img_el.get("data-lazy-src") or img_el.get("data-src") or img_el.get("src", "")

                results.append({
                    "title": title_el.get_text(strip=True),
                    "link": link_el.get("href", ""),
                    "image": image
                })

            logger.info(f"Found {len(articles)} results on page {page}")

        except requests.RequestException as e:
            logger.error(f"Error fetching page {page}: {e}")
            break
        except Exception as e:
            logger.error(f"Error parsing page {page}: {e}")
            break

    logger.info(f"Total results found: {len(results)}")
    return results


def get_download_links(page_url: str) -> dict:
    """
    Fetch a getcomics page and extract download links.

    Args:
        page_url: URL of the getcomics page

    Returns:
        Dict with keys: pixeldrain, download_now (values are URLs or None)
        Priority: PIXELDRAIN first, then DOWNLOAD NOW
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    try:
        logger.info(f"Fetching download links from: {page_url}")
        resp = requests.get(page_url, headers=headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, 'html.parser')

        links = {"pixeldrain": None, "download_now": None}

        # Search for download links by title attribute
        for a in soup.find_all("a"):
            title = (a.get("title") or "").upper()
            href = a.get("href", "")

            if not href:
                continue

            if "PIXELDRAIN" in title and not links["pixeldrain"]:
                links["pixeldrain"] = href
                logger.info(f"Found PIXELDRAIN link: {href}")
            elif "DOWNLOAD NOW" in title and not links["download_now"]:
                links["download_now"] = href
                logger.info(f"Found DOWNLOAD NOW link: {href}")

        # If no links found by title, try button text content
        if not links["pixeldrain"] and not links["download_now"]:
            for a in soup.find_all("a", class_="aio-red"):
                text = a.get_text(strip=True).upper()
                href = a.get("href", "")

                if not href:
                    continue

                if "PIXELDRAIN" in text and not links["pixeldrain"]:
                    links["pixeldrain"] = href
                    logger.info(f"Found PIXELDRAIN link (by text): {href}")
                elif "DOWNLOAD" in text and not links["download_now"]:
                    links["download_now"] = href
                    logger.info(f"Found DOWNLOAD link (by text): {href}")

        return links

    except requests.RequestException as e:
        logger.error(f"Error fetching page: {e}")
        return {"pixeldrain": None, "download_now": None}
    except Exception as e:
        logger.error(f"Error parsing page: {e}")
        return {"pixeldrain": None, "download_now": None}


def score_getcomics_result(result_title: str, series_name: str, issue_number: str, year: int) -> int:
    """
    Score a GetComics result against wanted issue criteria.

    Scoring:
    - Series name found (fuzzy - all words present): +40
    - Issue number matches: +40
    - Year matches: +20

    Args:
        result_title: Title from GetComics search result
        series_name: Expected series name
        issue_number: Expected issue number (as string)
        year: Expected year (series year_began or store_date year)

    Returns:
        Score from 0-100
    """
    import re

    score = 0
    title_lower = result_title.lower()
    series_lower = series_name.lower()

    # Series name check (fuzzy - all words present)
    series_words = series_lower.split()
    if all(word in title_lower for word in series_words):
        score += 40
        logger.debug(f"Series name match: +40")

    # Issue number check (exact)
    # Normalize issue number (remove leading zeros for comparison)
    issue_num = str(issue_number).lstrip('0') or '0'

    # Look for patterns: #15, #015, Issue 15, etc.
    issue_patterns = [
        rf'#0*{re.escape(issue_num)}\b',           # #15, #015
        rf'issue\s*0*{re.escape(issue_num)}\b',    # Issue 15, Issue15
        rf'\b0*{re.escape(issue_num)}\b'           # Standalone number
    ]

    for pattern in issue_patterns:
        if re.search(pattern, title_lower, re.IGNORECASE):
            score += 40
            logger.debug(f"Issue number match ({pattern}): +40")
            break

    # Year check
    if year and str(year) in result_title:
        score += 20
        logger.debug(f"Year match ({year}): +20")

    logger.debug(f"Score for '{result_title}' vs '{series_name} #{issue_number} ({year})': {score}")
    return score
