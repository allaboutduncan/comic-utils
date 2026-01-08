"""
Metron API integration for comic metadata retrieval using Mokkari library.
"""
from app_logging import app_logger
from typing import Optional, Dict, Any, List
import re
from datetime import datetime, timedelta

# Check if mokkari is available
try:
    import mokkari
    MOKKARI_AVAILABLE = True
except ImportError:
    MOKKARI_AVAILABLE = False


def is_mokkari_available() -> bool:
    """Check if the Mokkari library is available."""
    return MOKKARI_AVAILABLE


def get_api(username: str, password: str):
    """
    Initialize and return a Metron API client.

    Args:
        username: Metron username
        password: Metron password

    Returns:
        Mokkari API client or None if unavailable
    """
    if not MOKKARI_AVAILABLE:
        app_logger.warning("Mokkari library not available. Install with: pip install mokkari")
        return None
    if not username or not password:
        app_logger.warning("Metron credentials not configured")
        return None
    try:
        return mokkari.api(username, password)
    except Exception as e:
        app_logger.error(f"Failed to initialize Metron API: {e}")
        return None


def parse_cvinfo_for_metron_id(cvinfo_path: str) -> Optional[int]:
    """
    Parse a cvinfo file for series_id.

    cvinfo format:
        https://comicvine.gamespot.com/series-name/4050-123456/
        series_id: 10354

    Args:
        cvinfo_path: Path to the cvinfo file

    Returns:
        Metron series ID as integer, or None if not found
    """
    try:
        with open(cvinfo_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Look for series_id: <number>
        match = re.search(r'series_id:\s*(\d+)', content, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
    except Exception as e:
        app_logger.error(f"Error parsing cvinfo for Metron ID: {e}")
        return None


def parse_cvinfo_for_comicvine_id(cvinfo_path: str) -> Optional[int]:
    """
    Parse a cvinfo file for ComicVine series ID.

    URL format: https://comicvine.gamespot.com/series-name/4050-123456/
    The CV series ID is 123456 (after 4050-)

    Args:
        cvinfo_path: Path to the cvinfo file

    Returns:
        ComicVine series ID as integer, or None if not found
    """
    try:
        with open(cvinfo_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Match pattern: 4050-{volume_id}
        match = re.search(r'/4050-(\d+)', content)
        if match:
            return int(match.group(1))
        return None
    except Exception as e:
        app_logger.error(f"Error parsing cvinfo for ComicVine ID: {e}")
        return None


def get_series_id_by_comicvine_id(api, cv_series_id: int) -> Optional[int]:
    """
    Look up Metron series ID using ComicVine series ID.

    Searches Metron for series with matching cv_id.

    Args:
        api: Mokkari API client
        cv_series_id: ComicVine series/volume ID

    Returns:
        Metron series ID, or None if not found
    """
    try:
        # Search for series by cv_id
        params = {"cv_id": cv_series_id}
        results = api.series_list(params)

        if results:
            series_id = results[0].id
            app_logger.info(f"Found Metron series {series_id} for CV ID {cv_series_id}")
            return series_id

        app_logger.warning(f"No Metron series found for ComicVine ID {cv_series_id}")
        return None
    except Exception as e:
        app_logger.error(f"Error looking up Metron series by CV ID {cv_series_id}: {e}")
        return None


def update_cvinfo_with_metron_id(cvinfo_path: str, series_id: int) -> bool:
    """
    Update cvinfo file to include series_id.

    Args:
        cvinfo_path: Path to the cvinfo file
        series_id: Metron series ID to add

    Returns:
        True if successful, False otherwise
    """
    try:
        with open(cvinfo_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if series_id already exists
        if re.search(r'series_id:', content, re.IGNORECASE):
            # Update existing
            content = re.sub(
                r'series_id:\s*\d+',
                f'series_id: {series_id}',
                content,
                flags=re.IGNORECASE
            )
        else:
            # Append new line
            content = content.rstrip() + f'\nseries_id: {series_id}\n'

        with open(cvinfo_path, 'w', encoding='utf-8') as f:
            f.write(content)

        app_logger.info(f"Updated cvinfo with series_id: {series_id}")
        return True
    except Exception as e:
        app_logger.error(f"Error updating cvinfo with Metron ID: {e}")
        return False


def get_issue_metadata(api, series_id: int, issue_number: str) -> Optional[Dict[str, Any]]:
    """
    Fetch issue metadata from Metron.

    Uses the "double fetch" pattern: first search for issue, then get full details.

    Args:
        api: Mokkari API client
        series_id: Metron series ID
        issue_number: Issue number (string to handle "10.1", "Annual 1", etc.)

    Returns:
        Full issue data dict, or None if not found
    """
    try:
        # Search for the issue within the series
        params = {
            "series_id": series_id,
            "number": issue_number
        }
        issues = api.issues_list(params)

        if not issues:
            app_logger.warning(f"Issue {issue_number} not found in Metron series {series_id}")
            return None

        # Get the full detailed metadata
        metron_issue_id = issues[0].id
        app_logger.info(f"Found Metron issue ID {metron_issue_id}, fetching full details...")
        details = api.issue(metron_issue_id)

        # Convert schema object to dict - try multiple methods
        # Pydantic v2 uses model_dump(), v1 uses dict()
        result = None
        if hasattr(details, 'model_dump'):
            app_logger.debug("Converting Metron response using model_dump()")
            result = details.model_dump()
        elif hasattr(details, 'dict'):
            app_logger.debug("Converting Metron response using dict()")
            result = details.dict()
        elif hasattr(details, 'json'):
            import json
            app_logger.debug("Converting Metron response using json()")
            result = json.loads(details.json())
        elif hasattr(details, '__dict__'):
            app_logger.debug("Converting Metron response using vars()")
            result = vars(details)
        else:
            app_logger.debug(f"Metron response type: {type(details)}")
            result = details

        # Log key fields to verify conversion
        if result and isinstance(result, dict):
            app_logger.debug(f"Metron data keys: {list(result.keys())}")
            app_logger.debug(f"Series: {result.get('series')}, Number: {result.get('number')}")

        return result

    except Exception as e:
        app_logger.error(f"Error fetching issue metadata from Metron: {e}")
        return None


def _get_attr(obj, key, default=None):
    """Helper to get attribute from dict or object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def extract_credits_by_role(credits: List, role_names: List[str]) -> str:
    """
    Extract creator names for specific roles from credits list.

    Args:
        credits: List of credit dicts or objects with 'creator' and 'role' fields
        role_names: List of role names to match (e.g., ['Writer'])

    Returns:
        Comma-separated string of creator names
    """
    creators = []
    for credit in credits:
        roles = _get_attr(credit, 'role', [])
        if roles is None:
            roles = []
        for role in roles:
            role_name = _get_attr(role, 'name', '')
            if role_name is None:
                role_name = str(role)
            if role_name in role_names:
                creator_name = _get_attr(credit, 'creator', '')
                if creator_name and creator_name not in creators:
                    creators.append(creator_name)
    return ', '.join(creators)


def map_to_comicinfo(issue_data) -> Dict[str, Any]:
    """
    Map Metron issue data to ComicInfo.xml format.

    Args:
        issue_data: Issue data from Metron API (dict or object)

    Returns:
        Dictionary in ComicInfo.xml format
    """
    from datetime import datetime

    # Debug: log what we received
    app_logger.info(f"map_to_comicinfo received type: {type(issue_data)}")
    if isinstance(issue_data, dict):
        app_logger.info(f"map_to_comicinfo keys: {list(issue_data.keys())[:10]}...")

    # Parse cover_date for Year/Month/Day
    cover_date = _get_attr(issue_data, 'cover_date', '')
    year = None
    month = None
    day = None
    if cover_date:
        try:
            dt = datetime.strptime(str(cover_date), '%Y-%m-%d')
            year = dt.year
            month = dt.month
            day = dt.day
        except ValueError:
            # Try parsing just year
            try:
                year = int(str(cover_date)[:4])
            except (ValueError, TypeError):
                pass

    # Extract series info
    series = _get_attr(issue_data, 'series', {}) or {}
    series_name = _get_attr(series, 'name', '') or ''
    volume = _get_attr(series, 'volume', None)

    # Extract genres from series
    genres = _get_attr(series, 'genres', []) or []
    genre_names = []
    for g in genres:
        name = _get_attr(g, 'name', '')
        if name:
            genre_names.append(name)
    genre_str = ', '.join(genre_names) if genre_names else None

    # Extract publisher
    publisher = _get_attr(issue_data, 'publisher', {}) or {}
    publisher_name = _get_attr(publisher, 'name', '') or ''

    # Extract credits
    credits = _get_attr(issue_data, 'credits', []) or []
    writer = extract_credits_by_role(credits, ['Writer'])
    penciller = extract_credits_by_role(credits, ['Penciller', 'Artist'])
    inker = extract_credits_by_role(credits, ['Inker'])
    colorist = extract_credits_by_role(credits, ['Colorist'])
    letterer = extract_credits_by_role(credits, ['Letterer'])
    cover_artist = extract_credits_by_role(credits, ['Cover'])

    # Extract characters
    characters = _get_attr(issue_data, 'characters', []) or []
    char_names = []
    for c in characters:
        name = _get_attr(c, 'name', '')
        if name:
            char_names.append(name)
    characters_str = ', '.join(char_names) if char_names else None

    # Extract teams
    teams = _get_attr(issue_data, 'teams', []) or []
    team_names = []
    for t in teams:
        name = _get_attr(t, 'name', '')
        if name:
            team_names.append(name)
    teams_str = ', '.join(team_names) if team_names else None

    # Get title from 'name' array (first element)
    names = _get_attr(issue_data, 'name', [])
    if isinstance(names, list) and names:
        title = names[0]
    elif isinstance(names, str):
        title = names
    else:
        title = None

    # Rating
    rating = _get_attr(issue_data, 'rating', {})
    age_rating = _get_attr(rating, 'name', None) if rating else None

    # Build notes
    resource_url = _get_attr(issue_data, 'resource_url', 'Unknown')
    modified = _get_attr(issue_data, 'modified', 'Unknown')
    notes = f"Metadata from Metron. Resource URL: {resource_url} — modified {modified}."

    comicinfo = {
        'Series': series_name,
        'Number': _get_attr(issue_data, 'number', None),
        'Volume': volume,
        'Title': title,
        'Summary': _get_attr(issue_data, 'desc', None),
        'Publisher': publisher_name,
        'Year': year,
        'Month': month,
        'Day': day,
        'Writer': writer or None,
        'Penciller': penciller or None,
        'Inker': inker or None,
        'Colorist': colorist or None,
        'Letterer': letterer or None,
        'CoverArtist': cover_artist or None,
        'Characters': characters_str,
        'Teams': teams_str,
        'Genre': genre_str,
        'AgeRating': age_rating,
        'LanguageISO': 'en',
        'Manga': 'No',
        'Notes': notes,
        'PageCount': _get_attr(issue_data, 'page', None),
    }

    # Remove None values
    result = {k: v for k, v in comicinfo.items() if v is not None}
    app_logger.info(f"map_to_comicinfo returning {len(result)} fields: {list(result.keys())}")
    return result


def get_series_id(cvinfo_path: str, api) -> Optional[int]:
    """
    Get Metron series ID from cvinfo, looking up by CV ID if needed.

    This is a convenience function that:
    1. Checks cvinfo for existing series_id
    2. If not found, extracts CV ID and looks up Metron series
    3. Updates cvinfo with the found Metron series ID

    Args:
        cvinfo_path: Path to cvinfo file
        api: Mokkari API client

    Returns:
        Metron series ID, or None if not found
    """
    # First, check if series_id already exists
    metron_id = parse_cvinfo_for_metron_id(cvinfo_path)
    if metron_id:
        app_logger.debug(f"Found existing series_id: {metron_id}")
        return metron_id

    # Not found, try to look up by ComicVine ID
    cv_id = parse_cvinfo_for_comicvine_id(cvinfo_path)
    if not cv_id:
        app_logger.warning("No ComicVine ID found in cvinfo")
        return None

    app_logger.info(f"Looking up Metron series by ComicVine ID: {cv_id}")
    metron_id = get_series_id_by_comicvine_id(api, cv_id)

    if metron_id:
        # Save to cvinfo for future use
        update_cvinfo_with_metron_id(cvinfo_path, metron_id)
        return metron_id

    return None


def fetch_and_map_issue(api, cvinfo_path: str, issue_number: str) -> Optional[Dict[str, Any]]:
    """
    Convenience function to fetch issue metadata and map to ComicInfo format.

    This combines get_series_id, get_issue_metadata, and map_to_comicinfo.

    Args:
        api: Mokkari API client
        cvinfo_path: Path to cvinfo file
        issue_number: Issue number to fetch

    Returns:
        ComicInfo-formatted dict, or None if not found
    """
    # Get the Metron series ID
    series_id = get_series_id(cvinfo_path, api)
    if not series_id:
        app_logger.warning("Could not determine Metron series ID")
        return None

    # Fetch issue metadata
    issue_data = get_issue_metadata(api, series_id, issue_number)
    if not issue_data:
        return None

    # Map to ComicInfo format
    return map_to_comicinfo(issue_data)


def calculate_comic_week(date_obj=None):
    """
    Calculate the comic week (Sunday to Saturday) for a given date.

    Args:
        date_obj: datetime object (defaults to now)

    Returns:
        tuple of (start_date_obj, end_date_obj)
    """
    if date_obj is None:
        date_obj = datetime.now()

    # If date_obj is a string, parse it
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, '%Y-%m-%d')
        except ValueError:
            app_logger.error(f"Invalid date string format: {date_obj}")
            date_obj = datetime.now()

    # Calculate start of week (Sunday)
    # Weekday: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    # To get Sunday: (weekday + 1) % 7 gives days since Sunday
    days_since_sunday = (date_obj.weekday() + 1) % 7
    start_of_week = date_obj - timedelta(days=days_since_sunday)

    # End of week is Saturday (6 days later)
    end_of_week = start_of_week + timedelta(days=6)

    return start_of_week, end_of_week


def get_releases(api, date_after: str, date_before: Optional[str] = None) -> List[Any]:
    """
    Fetch releases from Metron API within a date range.

    Args:
        api: Mokkari API client
        date_after: Start date (YYYY-MM-DD)
        date_before: End date (YYYY-MM-DD), optional. If None, fetches everything after start date.

    Returns:
        List of issue objects
    """
    try:
        if not api:
            return []

        params = {
            "store_date_range_after": date_after
        }
        if date_before:
            params["store_date_range_before"] = date_before
            
        app_logger.info(f"Fetching releases with params: {params}")
        
        # Note: Using issues_list matching existing patterns in this file
        results = api.issues_list(params)
        return results
        
    except Exception as e:
        app_logger.error(f"Error getting releases: {e}")
        return []

def get_all_issues_for_series(api, series_id):
    """
    Retrieves all issues associated with a specific series ID.
    """
    try:
        # Pass the series ID as a filter in the params dictionary
        params = {
            "series_id": series_id
        }

        app_logger.info(f"Fetching issues for series_id: {series_id} with params: {params}")
        series_issues = api.issues_list(params)

        return series_issues

    except Exception as e:
        app_logger.error(f"Error retrieving issues for series {series_id}: {e}")
        return []

# Example usage:
# series_id = 12345
# issues = get_all_issues_for_series(api, series_id)