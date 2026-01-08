# Module: GetComics Integration for Comic-Utils

## Mirroring the download functions from Kapowarr
### Key Files in the Repository:
Repository: https://github.com/Casvt/Kapowarr/tree/development/backend
* backend/download.py: The "workhorse" that contains the extraction functions and the logic for processing GetComics links.
* backend/comicvine.py: Provides the metadata used to verify that the search results from GetComics are correct.
* backend/search.py (or search-related modules): Orchestrates the search across configured providers.

## 1. Configuration & Dependencies
* **Dependencies:** `beautifulsoup4`, `lxml`, `requests`, `urllib.parse`.
* **Optional:** Support for `FlareSolverr` (Docker) to handle Cloudflare challenges on GetComics.
* **Constants:** `GETCOMICS_BASE_URL = "https://getcomics.org/"`

## 2. Search Logic (`search_getcomics`)
* **Input:** Metadata object (from Antigravity/ComicVine or Metron).
    * *Required Fields:* Series Title, Issue Number, Year.
* **Query Construction:**
    * Sanitize title: Remove special characters.
    * Format: `https://getcomics.org/?s={title}+{issue_number}+{year}`.
* **HTML Parsing:**
    * Fetch search result page.
    * Extract all `<article>` or `post` elements.
    * Store `post_url` and `post_title`.

## 3. Metadata Matching (`match_results`)
* **String Normalization:** Convert both ComicVine title and GetComics title to lowercase, remove "The", "A", and punctuation.
* **Filtering:**
    * **Strict Mode:** Post title must contain both the Issue Number and the Year.
    * **Fuzzy Mode:** Use `levenshtein` distance or simple keyword matching to score results.
* **Priority:** Prioritize "Direct Download" posts over "Pack" or "Mega" posts if looking for single issues.

## 4. Link Extraction Logic (`extract_download_links`)
* **Action:** Visit the matched `post_url`.
* **Button Identification:** * Look for `<a>` tags with classes containing "button".
    * Filter by text content: "Download Now", "Main Server", "MediaFire", "Zippyshare", "Mega".
* **Redirect Handling:** * Follow intermediate "Wait" pages if applicable.
    * Capture the final `href` target.

## 5. Download Management (`download_handler`)
* **Stream Download:** Use `requests.get(url, stream=True)` to handle large CBR/CBZ files without memory spikes.
* **Validation:**
    * Check `Content-Type` (should be `application/x-cbr` or `application/zip`).
    * Verify `Content-Length` matches expected file size (usually > 20MB).
* **Naming Convention:** Rename downloaded file using standard format: `{Series} {Issue} ({Year}).cbz`.

## 6. Error Handling & Edge Cases
* **Cloudflare:** Detect "403 Forbidden" or "Cloudflare" keywords in HTML; prompt user to check FlareSolverr settings.
* **Missing Links:** Handle posts that only contain "Read Online" or broken external links.
* **Rate Limiting:** Implement a small delay (1-2 seconds) between page crawls to avoid IP blocking.