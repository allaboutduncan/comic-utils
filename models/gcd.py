"""
GCD (Grand Comics Database) integration for comic metadata retrieval.
"""
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from app_logging import app_logger

# Check if mysql.connector is available
try:
    import mysql.connector
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False

# =============================================================================
# Constants
# =============================================================================

STOPWORDS = {"the", "a", "an", "of", "and", "vol", "volume", "season", "series"}

# =============================================================================
# Helper Functions
# =============================================================================

def normalize_title(s: str) -> str:
    """Normalize a title string for better matching."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)   # remove punctuation/hyphens
    s = " ".join(s.split())              # collapse spaces
    return s


def tokens_for_all_match(s: str):
    """Normalize and drop stopwords for 'all tokens present' matching."""
    norm = normalize_title(s)
    toks = [t for t in norm.split() if t not in STOPWORDS]
    return norm, toks


def lookahead_regex(toks):
    """Build ^(?=.*\\bsuperman\\b)(?=.*\\bsecret\\b)(?=.*\\byears\\b).*$
    Works with MySQL REGEXP and is case-insensitive when we pass 'i' or pre-lowercase."""
    if not toks:
        return r".*"          # match-all fallback
    parts = [rf"(?=.*\\b{re.escape(t)}\\b)" for t in toks]
    return "^" + "".join(parts) + ".*$"


def generate_search_variations(series_name: str, year: str = None):
    """Generate progressive search variations for a comic title."""
    variations = []

    # Original exact search (current behavior)
    variations.append(("exact", f"%{series_name}%"))

    # Remove issue number pattern from title for broader search
    clean_title = re.sub(r'\s+\d{3}\s*$', '', series_name)  # Remove trailing issue numbers like "001"
    clean_title = re.sub(r'\s+#\d+\s*$', '', clean_title)   # Remove trailing issue numbers like "#1"

    if clean_title != series_name:
        variations.append(("no_issue", f"%{clean_title}%"))

    # Remove year from title if present
    title_no_year = re.sub(r'\s*\(\d{4}\)\s*', '', clean_title)
    title_no_year = re.sub(r'\s+\d{4}\s*$', '', title_no_year)

    if title_no_year != clean_title:
        variations.append(("no_year", f"%{title_no_year}%"))

    # Normalize and tokenize for advanced matching
    norm, tokens = tokens_for_all_match(title_no_year)

    # Remove hyphens/dashes for matching (Superman - The Secret Years -> Superman The Secret Years)
    no_dash_title = re.sub(r'\s*-+\s*', ' ', title_no_year).strip()
    if no_dash_title != title_no_year:
        variations.append(("no_dash", f"%{no_dash_title}%"))

    # Remove articles and common words for broader matching
    if len(tokens) > 1:
        regex_pattern = lookahead_regex(tokens)
        variations.append(("tokenized", regex_pattern))

    # Just the main character/franchise name (first significant word)
    if len(tokens) > 0:
        main_word = tokens[0]
        if year:
            variations.append(("main_with_year", f"%{main_word}%"))
        else:
            variations.append(("main_only", f"%{main_word}%"))

    return variations


# =============================================================================
# Database Connection
# =============================================================================

def is_mysql_available() -> bool:
    """Check if MySQL connector is available."""
    return MYSQL_AVAILABLE


def check_mysql_status() -> Dict[str, Any]:
    """Check if GCD MySQL database is configured."""
    try:
        gcd_host = os.environ.get('GCD_MYSQL_HOST')
        gcd_available = bool(gcd_host and gcd_host.strip())

        return {
            "gcd_mysql_available": gcd_available,
            "gcd_host_configured": gcd_available
        }
    except Exception as e:
        return {
            "gcd_mysql_available": False,
            "gcd_host_configured": False,
            "error": str(e)
        }


def get_connection():
    """
    Create and return a MySQL connection to the GCD database.

    Returns:
        MySQL connection object or None if connection fails
    """
    if not MYSQL_AVAILABLE:
        app_logger.error("MySQL connector not available")
        return None

    try:
        gcd_host = os.environ.get('GCD_MYSQL_HOST')
        gcd_port = int(os.environ.get('GCD_MYSQL_PORT', 3306))
        gcd_database = os.environ.get('GCD_MYSQL_DATABASE')
        gcd_user = os.environ.get('GCD_MYSQL_USER')
        gcd_password = os.environ.get('GCD_MYSQL_PASSWORD')

        if not all([gcd_host, gcd_database, gcd_user]):
            app_logger.error("GCD MySQL environment variables not fully configured")
            return None

        conn = mysql.connector.connect(
            host=gcd_host,
            port=gcd_port,
            database=gcd_database,
            user=gcd_user,
            password=gcd_password,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        return conn
    except Exception as e:
        app_logger.error(f"Failed to connect to GCD MySQL database: {e}")
        return None


# =============================================================================
# Issue Validation
# =============================================================================

def validate_issue(series_id: int, issue_number: str) -> Dict[str, Any]:
    """
    Validate if an issue exists in a series.

    Args:
        series_id: GCD series ID
        issue_number: Issue number to validate

    Returns:
        Dict with success status and issue data or error
    """
    if not series_id or not issue_number:
        return {
            "success": False,
            "error": "Missing series_id or issue_number"
        }

    if not MYSQL_AVAILABLE:
        return {
            "success": False,
            "error": "MySQL connector not available"
        }

    try:
        conn = get_connection()
        if not conn:
            return {
                "success": False,
                "error": "Failed to connect to GCD database"
            }

        cursor = conn.cursor(dictionary=True)

        # Query to find the issue
        validation_query = """
            SELECT id, title, number
            FROM gcd_issue
            WHERE series_id = %s
            AND (number = %s OR number = CONCAT('[', %s, ']') OR number LIKE CONCAT(%s, ' (%'))
            AND deleted = 0
            LIMIT 1
        """
        cursor.execute(validation_query, (series_id, issue_number, issue_number, issue_number))
        issue = cursor.fetchone()

        cursor.close()
        conn.close()

        if issue:
            return {
                "success": True,
                "valid": True,
                "issue": {
                    "id": issue['id'],
                    "title": issue['title'],
                    "number": issue['number']
                }
            }
        else:
            return {
                "success": True,
                "valid": False,
                "message": f"Issue #{issue_number} not found in series {series_id}"
            }

    except mysql.connector.Error as db_error:
        app_logger.error(f"Database error in validate_issue: {db_error}")
        return {
            "success": False,
            "error": f"Database error: {str(db_error)}"
        }
    except Exception as e:
        app_logger.error(f"Exception in validate_issue: {e}")
        return {
            "success": False,
            "error": str(e)
        }
