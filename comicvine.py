"""
ComicVine API integration for comic metadata retrieval.

This module provides functions to search for and retrieve comic metadata from ComicVine API,
including volume (series) search, issue search, and metadata mapping to ComicInfo.xml format.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

try:
    from simyan.comicvine import Comicvine
    from simyan.sqlite_cache import SQLiteCache
    from simyan.comicvine import ComicvineResource
    SIMYAN_AVAILABLE = True
except ImportError:
    SIMYAN_AVAILABLE = False

logger = logging.getLogger(__name__)


def is_simyan_available() -> bool:
    """Check if the Simyan library is available."""
    return SIMYAN_AVAILABLE


def search_volumes(api_key: str, series_name: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Search for comic volumes (series) on ComicVine.

    Args:
        api_key: ComicVine API key
        series_name: Name of the series to search for
        year: Optional year to filter/rank results

    Returns:
        List of volume dictionaries with id, name, start_year, publisher info

    Raises:
        Exception: If API request fails
    """
    if not SIMYAN_AVAILABLE:
        raise Exception("Simyan library not installed. Install with: pip install simyan")

    try:
        logger.info(f"Searching ComicVine for volume: '{series_name}' (year: {year})")

        # Initialize ComicVine API client
        cv = Comicvine(api_key=api_key)

        # Search for volumes using fuzzy search
        volumes = cv.search(resource=ComicvineResource.VOLUME, query=series_name)

        if not volumes:
            logger.info(f"No volumes found for '{series_name}'")
            return []

        # Convert to simple dict format
        results = []
        for vol in volumes:
            vol_dict = {
                "id": vol.id,
                "name": vol.name,
                "start_year": getattr(vol, 'start_year', None),
                "publisher_name": vol.publisher.name if hasattr(vol, 'publisher') and vol.publisher else None,
                "count_of_issues": getattr(vol, 'count_of_issues', None),
                "image_url": vol.image.thumbnail if hasattr(vol, 'image') and vol.image and hasattr(vol.image, 'thumbnail') else None,
                "description": getattr(vol, 'description', None)
            }
            # Truncate description if present
            if vol_dict["description"] and len(vol_dict["description"]) > 200:
                vol_dict["description"] = vol_dict["description"][:200] + "..."
            results.append(vol_dict)

        logger.info(f"Found {len(results)} volumes")

        # If year is provided, sort by closest year match
        if year:
            results = _rank_volumes_by_year(results, year)

        return results

    except Exception as e:
        logger.error(f"Error searching ComicVine volumes: {str(e)}")
        raise


def _rank_volumes_by_year(volumes: List[Dict[str, Any]], target_year: int) -> List[Dict[str, Any]]:
    """
    Rank volumes by how close their start_year is to the target year.

    Args:
        volumes: List of volume dictionaries
        target_year: Target year to match

    Returns:
        Sorted list of volumes (closest year first)
    """
    def year_distance(vol):
        if not vol.get('start_year'):
            return 9999  # Put volumes without year at the end
        return abs(vol['start_year'] - target_year)

    return sorted(volumes, key=year_distance)


def get_issue_by_number(api_key: str, volume_id: int, issue_number: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Get a specific issue from a volume by issue number.

    Args:
        api_key: ComicVine API key
        volume_id: ComicVine volume ID
        issue_number: Issue number (can be "1", "12.1", etc.)
        year: Optional publication year for filtering

    Returns:
        Issue dictionary with metadata, or None if not found

    Raises:
        Exception: If API request fails
    """
    if not SIMYAN_AVAILABLE:
        raise Exception("Simyan library not installed. Install with: pip install simyan")

    try:
        logger.info(f"Searching for issue #{issue_number} in volume {volume_id} (year: {year})")

        # Initialize ComicVine API client
        cv = Comicvine(api_key=api_key)

        # Get issues from the volume
        # Build filter string
        filter_str = f"volume:{volume_id},issue_number:{issue_number}"

        issues = cv.list_issues(params={"filter": filter_str})

        if not issues:
            logger.info(f"No issues found for volume {volume_id}, issue #{issue_number}")
            return None

        # If year is provided and multiple issues found, filter by year
        if year and len(issues) > 1:
            issues = [issue for issue in issues if _extract_year_from_date(issue.cover_date) == year]

        # If still multiple issues, take the first one
        if not issues:
            logger.info(f"No issues found matching year {year}")
            return None

        basic_issue = issues[0]

        # Fetch full issue details to get all metadata (credits, characters, etc.)
        issue = cv.get_issue(basic_issue.id)

        # Convert to dict format
        issue_dict = _issue_to_dict(issue)

        logger.info(f"Found issue: {issue_dict['name']} (ID: {issue_dict['id']})")

        return issue_dict

    except Exception as e:
        logger.error(f"Error getting ComicVine issue: {str(e)}")
        raise


def _extract_year_from_date(date_str: Optional[str]) -> Optional[int]:
    """
    Extract year from a date string.

    Args:
        date_str: Date string in format "YYYY-MM-DD"

    Returns:
        Year as integer, or None if parsing fails
    """
    if not date_str:
        return None

    try:
        # Try parsing as YYYY-MM-DD
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.year
    except (ValueError, TypeError):
        # Try extracting just the year
        try:
            return int(date_str.split("-")[0])
        except (ValueError, IndexError, AttributeError):
            return None


def _issue_to_dict(issue: Any) -> Dict[str, Any]:
    """
    Convert a Simyan Issue object to a dictionary.

    Args:
        issue: Simyan Issue object

    Returns:
        Dictionary with issue metadata
    """
    # Parse cover date or store date (prefer cover_date, fallback to store_date)
    year = None
    month = None
    day = None

    # Try cover_date first (preferred), then store_date as fallback
    date_str = getattr(issue, 'cover_date', None)
    if not date_str:
        date_str = getattr(issue, 'store_date', None)

    if date_str:
        year = _extract_year_from_date(date_str)
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            month = date_obj.month
            day = date_obj.day
        except (ValueError, TypeError):
            pass

    # Extract person credits (creators)
    writers = []
    pencillers = []
    inkers = []
    colorists = []
    letterers = []
    cover_artists = []

    creators = getattr(issue, 'creators', None)
    if creators:
        for credit in creators:
            name = credit.name if hasattr(credit, 'name') else str(credit)
            role = credit.roles.lower() if hasattr(credit, 'roles') else ""

            if "writer" in role or "script" in role:
                writers.append(name)
            elif "pencil" in role:
                pencillers.append(name)
            elif "ink" in role:
                inkers.append(name)
            elif "color" in role:
                colorists.append(name)
            elif "letter" in role:
                letterers.append(name)
            elif "cover" in role:
                cover_artists.append(name)

    # Extract character names
    character_list = []
    characters = getattr(issue, 'characters', None)
    if characters:
        character_list = [char.name if hasattr(char, 'name') else str(char) for char in characters]

    # Extract teams
    team_list = []
    teams = getattr(issue, 'teams', None)
    if teams:
        team_list = [team.name if hasattr(team, 'name') else str(team) for team in teams]

    # Extract locations
    location_list = []
    locations = getattr(issue, 'locations', None)
    if locations:
        location_list = [loc.name if hasattr(loc, 'name') else str(loc) for loc in locations]

    # Extract story arc
    story_arc = None
    story_arcs = getattr(issue, 'story_arcs', None)
    if story_arcs and len(story_arcs) > 0:
        story_arc = story_arcs[0].name if hasattr(story_arcs[0], 'name') else None

    # Get volume info
    volume = getattr(issue, 'volume', None)
    volume_name = volume.name if volume and hasattr(volume, 'name') else None
    volume_id = volume.id if volume and hasattr(volume, 'id') else None
    publisher = None
    if volume and hasattr(volume, 'publisher') and volume.publisher:
        publisher = volume.publisher.name if hasattr(volume.publisher, 'name') else None

    # Get image URL
    image = getattr(issue, 'image', None)
    image_url = image.thumbnail if image and hasattr(image, 'thumbnail') else None

    return {
        "id": issue.id,
        "name": getattr(issue, 'name', None),  # BasicIssue.name -> Title
        "issue_number": getattr(issue, 'number', None),  # BasicIssue.number -> Number
        "volume_name": volume_name,  # BasicIssue.volume.name -> Series
        "volume_id": volume_id,
        "publisher": publisher,
        "cover_date": date_str,  # BasicIssue.cover_date or store_date
        "year": year,  # Parsed from cover_date or store_date -> Year
        "month": month,  # Parsed from cover_date or store_date -> Month
        "day": day,  # Parsed from cover_date or store_date -> Day
        "description": getattr(issue, 'description', None),  # BasicIssue.description -> Summary
        "image_url": image_url,
        "page_count": None,  # ComicVine doesn't always provide page count
        "writers": writers,
        "pencillers": pencillers,
        "inkers": inkers,
        "colorists": colorists,
        "letterers": letterers,
        "cover_artists": cover_artists,
        "characters": character_list,
        "teams": team_list,
        "locations": location_list,
        "story_arc": story_arc,
    }


def map_to_comicinfo(issue_data: Dict[str, Any], volume_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Map ComicVine issue data to ComicInfo.xml format.

    Args:
        issue_data: Issue data from ComicVine
        volume_data: Optional volume data for additional context

    Returns:
        Dictionary in ComicInfo.xml format
    """
    # Use volume name from volume_data if available, otherwise from issue_data
    series_name = volume_data.get('name') if volume_data else issue_data.get('volume_name')

    # Get publisher - prefer volume_data (from search results), fallback to issue_data
    publisher = None
    if volume_data and volume_data.get('publisher_name'):
        publisher = volume_data.get('publisher_name')
    else:
        publisher = issue_data.get('publisher')

    # Get volume ID for Notes field
    from datetime import datetime
    volume_id = volume_data.get('id') if volume_data else issue_data.get('volume_id')
    current_date = datetime.now().strftime('%Y-%m-%d')
    if volume_id:
        notes = f'Metadata from ComicVine CVDB. Volume ID: {volume_id} — retrieved {current_date}.'
    else:
        notes = f'Metadata from ComicVine CVDB — retrieved {current_date}.'

    comicinfo = {
        'Series': series_name,
        'Number': issue_data.get('issue_number'),
        'Volume': issue_data.get('year'),  # Use publication year as Volume (ComicVine standard)
        'Title': issue_data.get('name'),
        'Publisher': publisher,
        'Summary': issue_data.get('description'),
        'Year': issue_data.get('year'),
        'Month': issue_data.get('month'),
        'Day': issue_data.get('day'),
        'Writer': ', '.join(issue_data.get('writers', [])) if issue_data.get('writers') else None,
        'Penciller': ', '.join(issue_data.get('pencillers', [])) if issue_data.get('pencillers') else None,
        'Inker': ', '.join(issue_data.get('inkers', [])) if issue_data.get('inkers') else None,
        'Colorist': ', '.join(issue_data.get('colorists', [])) if issue_data.get('colorists') else None,
        'Letterer': ', '.join(issue_data.get('letterers', [])) if issue_data.get('letterers') else None,
        'CoverArtist': ', '.join(issue_data.get('cover_artists', [])) if issue_data.get('cover_artists') else None,
        'Characters': ', '.join(issue_data.get('characters', [])) if issue_data.get('characters') else None,
        'Teams': ', '.join(issue_data.get('teams', [])) if issue_data.get('teams') else None,
        'Locations': ', '.join(issue_data.get('locations', [])) if issue_data.get('locations') else None,
        'StoryArc': issue_data.get('story_arc'),
        'PageCount': issue_data.get('page_count'),
        'LanguageISO': 'en',  # ComicVine is primarily English content
        'Notes': notes,
        'Count': None,  # Not needed per requirements
    }

    # Remove None values
    return {k: v for k, v in comicinfo.items() if v is not None}


def search_and_get_metadata(api_key: str, series_name: str, issue_number: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    High-level function to search for a series and get issue metadata.

    Args:
        api_key: ComicVine API key
        series_name: Name of the series
        issue_number: Issue number
        year: Optional year for better matching

    Returns:
        Dictionary with metadata in ComicInfo.xml format, or None if not found

    Raises:
        Exception: If API request fails
    """
    try:
        # Search for volumes
        volumes = search_volumes(api_key, series_name, year)

        if not volumes:
            logger.info(f"No volumes found for '{series_name}'")
            return None

        # Auto-select first volume (already sorted by year if provided)
        selected_volume = volumes[0]
        logger.info(f"Auto-selected volume: {selected_volume['name']} ({selected_volume['start_year']})")

        # Get the issue
        issue_data = get_issue_by_number(api_key, selected_volume['id'], issue_number, year)

        if not issue_data:
            logger.info(f"Issue #{issue_number} not found in volume {selected_volume['name']}")
            return None

        # Map to ComicInfo format
        comicinfo = map_to_comicinfo(issue_data, selected_volume)

        # Add image URL for UI display
        comicinfo['_image_url'] = issue_data.get('image_url')
        comicinfo['_volume_matches'] = volumes  # For showing alternatives if needed

        return comicinfo

    except Exception as e:
        logger.error(f"Error in search_and_get_metadata: {str(e)}")
        raise
