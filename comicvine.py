"""
ComicVine API integration for comic metadata retrieval.

This module provides functions to search for and retrieve comic metadata from ComicVine API,
including volume (series) search, issue search, and metadata mapping to ComicInfo.xml format.
"""

from app_logging import app_logger
from datetime import datetime, date
from typing import Optional, Dict, List, Any
import os
import shutil
import re
from rename import load_custom_rename_config

try:
    from simyan.comicvine import Comicvine
    from simyan.sqlite_cache import SQLiteCache
    from simyan.comicvine import ComicvineResource
    SIMYAN_AVAILABLE = True
except ImportError:
    SIMYAN_AVAILABLE = False

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
        app_logger.info(f"Searching ComicVine for volume: '{series_name}' (year: {year})")

        # Initialize ComicVine API client
        cv = Comicvine(api_key=api_key)

        # Search for volumes using fuzzy search
        volumes = cv.search(resource=ComicvineResource.VOLUME, query=series_name)

        if not volumes:
            app_logger.info(f"No volumes found for '{series_name}'")
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

        app_logger.info(f"Found {len(results)} volumes")

        # If year is provided, sort by closest year match
        if year:
            results = _rank_volumes_by_year(results, year)

        return results

    except Exception as e:
        app_logger.error(f"Error searching ComicVine volumes: {str(e)}")
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
        app_logger.info(f"Searching for issue #{issue_number} in volume {volume_id} (year: {year})")

        # Initialize ComicVine API client
        cv = Comicvine(api_key=api_key)

        # Get issues from the volume
        # Build filter string
        filter_str = f"volume:{volume_id},issue_number:{issue_number}"

        issues = cv.list_issues(params={"filter": filter_str})

        if not issues:
            app_logger.info(f"No issues found for volume {volume_id}, issue #{issue_number}")
            return None

        # If year is provided and multiple issues found, filter by year
        if year and len(issues) > 1:
            issues = [issue for issue in issues if _extract_year_from_date(issue.cover_date) == year]

        # If still multiple issues, take the first one
        if not issues:
            app_logger.info(f"No issues found matching year {year}")
            return None

        basic_issue = issues[0]

        # Fetch full issue details to get all metadata (credits, characters, etc.)
        issue = cv.get_issue(basic_issue.id)

        # Convert to dict format
        issue_dict = _issue_to_dict(issue)

        app_logger.info(f"Found issue: {issue_dict['name']} (ID: {issue_dict['id']})")

        return issue_dict

    except Exception as e:
        app_logger.error(f"Error getting ComicVine issue: {str(e)}")
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
    date_str = None

    # Try cover_date first (preferred), then store_date as fallback
    date_value = getattr(issue, 'cover_date', None)
    if not date_value:
        date_value = getattr(issue, 'store_date', None)

    if date_value:
        # Handle datetime objects, date objects, and strings
        if isinstance(date_value, (datetime, date)):
            # Already a datetime or date object
            year = date_value.year
            month = date_value.month
            day = date_value.day
            date_str = date_value.strftime("%Y-%m-%d")
            app_logger.info(f"DEBUG _issue_to_dict: date/datetime object - year={year}, month={month}, day={day}")
        elif isinstance(date_value, str):
            # String format
            date_str = date_value
            year = _extract_year_from_date(date_str)
            app_logger.info(f"DEBUG _issue_to_dict: date_str={date_str}, extracted year={year}")
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                month = date_obj.month
                day = date_obj.day
                app_logger.info(f"DEBUG _issue_to_dict: parsed month={month}, day={day}")
            except (ValueError, TypeError):
                app_logger.warning(f"DEBUG _issue_to_dict: Failed to parse date_str={date_str}")
                pass
        else:
            # Unknown type - try converting to string
            date_str = str(date_value)
            year = _extract_year_from_date(date_str)
            app_logger.info(f"DEBUG _issue_to_dict: unknown type {type(date_value)}, converted to string={date_str}, year={year}")

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

    # Append cover_date or store_date if available
    if issue_data.get('cover_date'):
        notes += f' Cover/Store Date: {issue_data.get("cover_date")}.'

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

    # Debug logging for date fields
    app_logger.info(f"DEBUG map_to_comicinfo: cover_date={issue_data.get('cover_date')}, year={issue_data.get('year')}, month={issue_data.get('month')}, day={issue_data.get('day')}")

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
            app_logger.info(f"No volumes found for '{series_name}'")
            return None

        # Auto-select first volume (already sorted by year if provided)
        selected_volume = volumes[0]
        app_logger.info(f"Auto-selected volume: {selected_volume['name']} ({selected_volume['start_year']})")

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
        app_logger.error(f"Error in search_and_get_metadata: {str(e)}")
        raise


def auto_move_file(file_path: str, volume_data: Dict[str, Any], config: Dict[str, Any]) -> Optional[str]:
    """
    Automatically move a file to an organized location based on the custom move pattern.

    Args:
        file_path: Current absolute path to the file
        volume_data: Volume data containing 'name', 'start_year', 'publisher_name'
        config: Flask app config with ENABLE_AUTO_MOVE and CUSTOM_MOVE_PATTERN

    Returns:
        New file path if moved successfully, None if not moved

    Raises:
        Exception: If file move fails
    """
    try:
        # Check if auto-move is enabled
        if not config.get("ENABLE_AUTO_MOVE", False):
            app_logger.debug("Auto-move is disabled in config")
            return None

        # Get the custom move pattern
        move_pattern = config.get("CUSTOM_MOVE_PATTERN", "")
        if not move_pattern:
            app_logger.warning("CUSTOM_MOVE_PATTERN is empty, skipping auto-move")
            return None

        # Extract metadata values for pattern replacement
        series_name = volume_data.get('name', '')
        start_year = str(volume_data.get('start_year', '')) if volume_data.get('start_year') else ''
        publisher = volume_data.get('publisher_name', '')

        # Log the metadata values
        app_logger.info(f"📦 Auto-move preparation - series_name: '{series_name}', start_year: '{start_year}', publisher: '{publisher}'")

        # Replace pattern placeholders with actual values
        folder_structure = move_pattern
        folder_structure = folder_structure.replace('{series_name}', series_name)
        folder_structure = folder_structure.replace('{start_year}', start_year)
        folder_structure = folder_structure.replace('{publisher}', publisher)

        # Handle other optional placeholders that might be in the pattern
        # These won't have values from volume data, so we'll just remove them or keep them as-is
        folder_structure = folder_structure.replace('{volume_number}', '')
        folder_structure = folder_structure.replace('{issue_number}', '')

        # Clean up any double slashes or trailing slashes
        folder_structure = folder_structure.replace('//', '/').strip('/')

        app_logger.info(f"📂 Computed folder structure: '{folder_structure}'")

        # Construct the target directory path
        # Base directory is /data
        base_dir = '/data'
        target_dir = os.path.join(base_dir, folder_structure)

        app_logger.info(f"🎯 Target directory: '{target_dir}'")

        # Create target directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)
        app_logger.info(f"✅ Target directory created/verified: '{target_dir}'")

        # Get the filename from the current path
        filename = os.path.basename(file_path)

        # Construct the new file path
        new_file_path = os.path.join(target_dir, filename)

        # Check if source file exists
        if not os.path.exists(file_path):
            app_logger.error(f"❌ Source file does not exist: '{file_path}'")
            return None

        # Check if a file with the same name already exists at the target
        if os.path.exists(new_file_path):
            app_logger.warning(f"⚠️ File already exists at target location: '{new_file_path}', skipping move")
            return None

        # Move the file
        app_logger.info(f"🚚 Moving file from '{file_path}' to '{new_file_path}'")
        shutil.move(file_path, new_file_path)
        app_logger.info(f"✅ File successfully moved to: '{new_file_path}'")

        return new_file_path

    except Exception as e:
        app_logger.error(f"❌ Error during auto-move: {str(e)}")
        import traceback
        app_logger.error(f"Traceback: {traceback.format_exc()}")
        raise


def get_metadata_by_volume_id(api_key: str, volume_id: int, issue_number: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Get issue metadata using a known volume ID (from cvinfo file).

    Args:
        api_key: ComicVine API key
        volume_id: ComicVine volume ID (extracted from cvinfo URL)
        issue_number: Issue number to look up
        year: Optional year for filtering

    Returns:
        Dictionary with metadata in ComicInfo.xml format, or None if not found
    """
    try:
        # Get the issue directly using volume_id
        issue_data = get_issue_by_number(api_key, volume_id, issue_number, year)

        if not issue_data:
            return None

        # Map to ComicInfo format (volume_data=None since we don't have full volume info)
        comicinfo = map_to_comicinfo(issue_data, None)
        comicinfo['_image_url'] = issue_data.get('image_url')

        return comicinfo
    except Exception as e:
        app_logger.error(f"Error in get_metadata_by_volume_id: {str(e)}")
        return None


def parse_cvinfo_volume_id(cvinfo_path: str) -> Optional[int]:
    """
    Parse a cvinfo file and extract the ComicVine volume ID.

    cvinfo file contains a URL like: https://comicvine.gamespot.com/avengers/4050-150431/
    The volume ID is the number after '4050-' (e.g., 150431)

    Args:
        cvinfo_path: Path to the cvinfo file

    Returns:
        Volume ID as integer, or None if not found/parseable
    """
    import re

    try:
        with open(cvinfo_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        # Match pattern: 4050-{volume_id}
        match = re.search(r'/4050-(\d+)', content)
        if match:
            return int(match.group(1))

        return None
    except Exception as e:
        app_logger.error(f"Error parsing cvinfo file {cvinfo_path}: {e}")
        return None


def find_cvinfo_in_folder(folder_path: str) -> Optional[str]:
    """
    Look for a cvinfo file in a folder (case-insensitive).

    Args:
        folder_path: Path to the folder to search

    Returns:
        Full path to cvinfo file if found, None otherwise
    """
    try:
        for item in os.listdir(folder_path):
            if item.lower() == 'cvinfo':
                return os.path.join(folder_path, item)
        return None
    except Exception as e:
        app_logger.error(f"Error searching for cvinfo in {folder_path}: {e}")
        return None


def extract_issue_number(filename: str) -> Optional[str]:
    """
    Extract issue number from a comic filename.

    Handles patterns like:
    - "Amazing Spider-Man 001.cbz" -> "1"
    - "Batman #42.cbz" -> "42"
    - "X-Men v2 012.cbz" -> "12"

    Args:
        filename: Comic filename

    Returns:
        Issue number as string, or None if not found
    """
    import re

    # Remove extension
    name = os.path.splitext(filename)[0]

    # Pattern: look for issue number (often at end, with or without #)
    # Try various patterns
    patterns = [
        r'#(\d+(?:\.\d+)?)',           # #42 or #42.1
        r'\s+(\d{3,})(?:\s|$)',         # Space + 3+ digits (001, 012)
        r'\s+(\d{1,2})(?:\s|$)',        # Space + 1-2 digits at end
        r'[-_](\d+)(?:\s|$)',           # Dash/underscore + digits
    ]

    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            # Remove leading zeros but preserve decimal parts
            num_str = match.group(1)
            if '.' in num_str:
                parts = num_str.split('.')
                return str(int(parts[0])) + '.' + parts[1]
            else:
                return str(int(num_str))

    return None


def add_comicinfo_to_archive(file_path: str, xml_content) -> bool:
    """
    Add or update ComicInfo.xml in a CBZ archive.

    Args:
        file_path: Path to the CBZ file
        xml_content: XML content to add (str or bytes)

    Returns:
        True on success, False on failure
    """
    import zipfile
    import tempfile

    temp_path = None
    try:
        # Create temp file
        temp_fd, temp_path = tempfile.mkstemp(suffix='.cbz')
        os.close(temp_fd)

        with zipfile.ZipFile(file_path, 'r') as zin:
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    # Skip existing ComicInfo.xml
                    if item.filename.lower() == 'comicinfo.xml':
                        continue
                    zout.writestr(item, zin.read(item.filename))

                # Add new ComicInfo.xml - handle both str and bytes
                if isinstance(xml_content, bytes):
                    zout.writestr('ComicInfo.xml', xml_content)
                else:
                    zout.writestr('ComicInfo.xml', xml_content.encode('utf-8'))

        # Replace original with temp
        shutil.move(temp_path, file_path)
        return True

    except Exception as e:
        app_logger.error(f"Error adding ComicInfo.xml to {file_path}: {e}")
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        return False


def generate_comicinfo_xml(issue_data: Dict[str, Any]) -> bytes:
    """
    Generate ComicInfo.xml content from issue metadata.

    Args:
        issue_data: Dictionary with ComicInfo fields

    Returns:
        XML content as bytes
    """
    import xml.etree.ElementTree as ET
    import io

    root = ET.Element("ComicInfo")

    def add(tag, value):
        if value is not None and str(value).strip():
            ET.SubElement(root, tag).text = str(value)

    # Basic fields
    add("Title", issue_data.get("Title"))
    add("Series", issue_data.get("Series"))

    # Number field
    num = issue_data.get("Number")
    if num is not None and str(num).strip():
        try:
            # Try to format as integer if possible
            if str(num).replace(".", "", 1).isdigit():
                add("Number", str(int(float(num))))
            else:
                add("Number", str(num))
        except (ValueError, TypeError):
            add("Number", str(num))

    # Volume
    vol = issue_data.get("Volume")
    if vol is not None and str(vol).strip():
        try:
            add("Volume", str(int(vol)))
        except (ValueError, TypeError):
            add("Volume", str(vol))

    add("Summary", issue_data.get("Summary"))

    # Dates
    if issue_data.get("Year"):
        try:
            add("Year", str(int(issue_data["Year"])))
        except (ValueError, TypeError):
            pass
    if issue_data.get("Month"):
        try:
            m = int(issue_data["Month"])
            if 1 <= m <= 12:
                add("Month", str(m))
        except (ValueError, TypeError):
            pass
    if issue_data.get("Day"):
        try:
            d = int(issue_data["Day"])
            if 1 <= d <= 31:
                add("Day", str(d))
        except (ValueError, TypeError):
            pass

    # Credits
    add("Writer", issue_data.get("Writer"))
    add("Penciller", issue_data.get("Penciller"))
    add("Inker", issue_data.get("Inker"))
    add("Colorist", issue_data.get("Colorist"))
    add("Letterer", issue_data.get("Letterer"))
    add("CoverArtist", issue_data.get("CoverArtist"))

    # Publisher
    add("Publisher", issue_data.get("Publisher"))

    # Characters/Teams/Locations
    add("Characters", issue_data.get("Characters"))
    add("Teams", issue_data.get("Teams"))
    add("Locations", issue_data.get("Locations"))
    add("StoryArc", issue_data.get("StoryArc"))

    # Language
    add("LanguageISO", issue_data.get("LanguageISO") or "en")

    # Page count
    if issue_data.get("PageCount"):
        try:
            add("PageCount", str(int(issue_data["PageCount"])))
        except (ValueError, TypeError):
            pass

    # Notes
    add("Notes", issue_data.get("Notes"))

    # Serialize as UTF-8 bytes
    ET.indent(root)
    tree = ET.ElementTree(root)
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def auto_fetch_metadata_for_folder(folder_path: str, api_key: str, target_file: str = None) -> Dict[str, Any]:
    """
    Auto-fetch ComicVine metadata for comics in a folder using cvinfo.

    Processes all comic files in the folder that don't have meaningful comicinfo.xml.
    Files are processed one at a time consecutively (API rate limiting).

    Args:
        folder_path: Path to the folder containing cvinfo and comic files
        api_key: ComicVine API key
        target_file: Optional specific file to prioritize (just moved)

    Returns:
        Dict with 'processed', 'skipped', 'errors' counts and 'details' list
    """
    from comicinfo import read_comicinfo_from_zip
    import time

    result = {'processed': 0, 'skipped': 0, 'errors': 0, 'details': []}

    # Find cvinfo file
    cvinfo_path = find_cvinfo_in_folder(folder_path)
    if not cvinfo_path:
        app_logger.debug(f"No cvinfo file found in {folder_path}")
        return result

    # Parse volume ID
    volume_id = parse_cvinfo_volume_id(cvinfo_path)
    if not volume_id:
        app_logger.warning(f"Could not extract volume ID from {cvinfo_path}")
        return result

    app_logger.info(f"Found cvinfo with volume ID: {volume_id}")

    # Get list of comic files to process
    comic_files = []
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path) and item.lower().endswith(('.cbz', '.cbr')):
            comic_files.append(item_path)

    # Prioritize target_file if specified
    if target_file and target_file in comic_files:
        comic_files.remove(target_file)
        comic_files.insert(0, target_file)

    for file_path in comic_files:
        try:
            # Check if file already has meaningful metadata
            existing = read_comicinfo_from_zip(file_path)
            existing_notes = existing.get('Notes', '').strip()

            if existing_notes:
                app_logger.debug(f"Skipping {file_path} - already has metadata")
                result['skipped'] += 1
                result['details'].append({'file': file_path, 'status': 'skipped', 'reason': 'has metadata'})
                continue

            # Extract issue number from filename
            issue_number = extract_issue_number(os.path.basename(file_path))
            if not issue_number:
                app_logger.warning(f"Could not extract issue number from {file_path}")
                result['errors'] += 1
                result['details'].append({'file': file_path, 'status': 'error', 'reason': 'no issue number'})
                continue

            # Fetch metadata from ComicVine
            metadata = get_metadata_by_volume_id(api_key, volume_id, issue_number)

            if not metadata:
                app_logger.warning(f"No metadata found for {file_path}, issue #{issue_number}")
                result['errors'] += 1
                result['details'].append({'file': file_path, 'status': 'error', 'reason': 'not found on ComicVine'})
                continue

            # Generate and add ComicInfo.xml to the file
            xml_content = generate_comicinfo_xml(metadata)
            if add_comicinfo_to_archive(file_path, xml_content):
                app_logger.debug(f"Added metadata to {file_path}")
                result['processed'] += 1

                # Auto-rename if enabled - use ComicVine metadata for rename
                try:
                    custom_enabled, custom_pattern = load_custom_rename_config()
                    app_logger.info(f"Auto-rename check: enabled={custom_enabled}, pattern={custom_pattern}")
                    if custom_enabled and custom_pattern:
                        app_logger.debug(f"Attempting auto-rename for: {file_path}")

                        # Get values from ComicVine metadata
                        series = metadata.get('Series', '')
                        # Clean series name for filename
                        series = series.replace(':', ' -')  # Replace colon with dash for Windows
                        series = re.sub(r'[<>"/\\|?*]', '', series)  # Remove invalid chars

                        issue_number = str(metadata.get('Number', '')).zfill(3)
                        year = str(metadata.get('Year', ''))

                        app_logger.debug(f"Rename values: series={series}, issue={issue_number}, year={year}")

                        # Apply pattern
                        new_name = custom_pattern
                        new_name = re.sub(r'\{series_name\}', series, new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\{issue_number\}', issue_number, new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\{year\}|\{YYYY\}', year, new_name, flags=re.IGNORECASE)
                        new_name = re.sub(r'\{volume_number\}', '', new_name, flags=re.IGNORECASE)

                        # Clean up
                        new_name = re.sub(r'\s+', ' ', new_name).strip()
                        new_name = re.sub(r'\s*\(\s*\)', '', new_name).strip()  # Remove empty parentheses

                        # Add extension
                        _, ext = os.path.splitext(file_path)
                        new_name = new_name + ext

                        # Get directory and construct new path
                        directory = os.path.dirname(file_path)
                        old_name = os.path.basename(file_path)
                        new_path = os.path.join(directory, new_name)

                        app_logger.debug(f"Rename: {old_name} -> {new_name}")

                        # Only rename if name changed and target doesn't exist
                        if new_name != old_name:
                            if os.path.exists(new_path):
                                app_logger.warning(f"Target file already exists, skipping rename: {new_path}")
                                result['details'].append({'file': file_path, 'status': 'success'})
                            else:
                                os.rename(file_path, new_path)
                                app_logger.info(f"Auto-renamed: {file_path} -> {new_path}")
                                result['details'].append({'file': file_path, 'status': 'success', 'renamed_to': new_path})
                        else:
                            app_logger.debug(f"No rename needed, name unchanged")
                            result['details'].append({'file': file_path, 'status': 'success'})
                    else:
                        app_logger.debug("Auto-rename disabled or no pattern, skipping")
                        result['details'].append({'file': file_path, 'status': 'success'})
                except Exception as rename_error:
                    app_logger.error(f"Auto-rename error: {rename_error}")
                    import traceback
                    app_logger.error(f"Auto-rename traceback: {traceback.format_exc()}")
                    result['details'].append({'file': file_path, 'status': 'success'})
            else:
                result['errors'] += 1
                result['details'].append({'file': file_path, 'status': 'error', 'reason': 'failed to add XML'})

            # Rate limiting - wait between API calls
            time.sleep(1)

        except Exception as e:
            app_logger.error(f"Error processing {file_path}: {e}")
            result['errors'] += 1
            result['details'].append({'file': file_path, 'status': 'error', 'reason': str(e)})

    return result
