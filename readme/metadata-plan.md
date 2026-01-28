# Metadata Provider Architecture Plan

## Overview

This plan outlines the architecture for supporting multiple metadata providers that can be enabled per library. Each library can have its own set of configured providers with priority ordering.

**Example Configuration:**
| Library | Path | Providers (in priority order) |
|---------|------|-------------------------------|
| Comics | `/data` | Metron, ComicVine, GCD |
| Manga | `/manga` | AniList, MangaUpdates |
| Dutch | `/dutch` | Bedetheque |

---

## Current State

### Existing Providers
| Provider | Implementation | Auth Method | API Type |
|----------|---------------|-------------|----------|
| Metron | `models/metron.py` via `mokkari` library | Username/Password | REST |
| ComicVine | `comicvine.py` via `simyan` library | API Key | REST |
| GCD | `models/gcd.py` | MySQL connection | Database |

### Current Issues
1. **No abstraction layer** - Each provider has different method signatures and return types
2. **Hardcoded priority** - Metron → ComicVine → GCD fallback is hardcoded in templates
3. **Tight coupling** - UI buttons directly call provider-specific endpoints
4. **Global credentials** - API keys stored in config.ini, not per-library
5. **cvinfo files** - Retained for cross-app compatibility; need standardized key naming per provider

### Current Database Schema (Libraries)
```sql
CREATE TABLE libraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

---

## Proposed Architecture

### 1. Provider Abstraction Layer (Adapter Pattern)

Create a unified interface that all providers must implement:

```
models/
├── providers/
│   ├── __init__.py           # Provider registry and factory
│   ├── base.py               # Abstract base class
│   ├── metron_provider.py    # Metron adapter
│   ├── comicvine_provider.py # ComicVine adapter
│   ├── gcd_provider.py       # GCD adapter
│   ├── anilist_provider.py   # AniList adapter (NEW)
│   ├── mangaupdates_provider.py  # MangaUpdates adapter (NEW)
│   └── bedetheque_provider.py    # Bedetheque adapter (NEW)
```

#### Base Provider Interface (`base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum

class ProviderType(Enum):
    METRON = "metron"
    COMICVINE = "comicvine"
    GCD = "gcd"
    ANILIST = "anilist"
    MANGAUPDATES = "mangaupdates"
    BEDETHEQUE = "bedetheque"

@dataclass
class SearchResult:
    """Unified search result across all providers"""
    provider: ProviderType
    id: str                    # Provider-specific ID
    title: str
    year: Optional[int] = None
    publisher: Optional[str] = None
    issue_count: Optional[int] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None

@dataclass
class IssueResult:
    """Unified issue data"""
    provider: ProviderType
    id: str
    series_id: str
    issue_number: str
    title: Optional[str] = None
    cover_date: Optional[str] = None
    store_date: Optional[str] = None
    cover_url: Optional[str] = None
    summary: Optional[str] = None

@dataclass
class ProviderCredentials:
    """Credentials for a provider"""
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    connection_string: Optional[str] = None

class BaseProvider(ABC):
    """Abstract base class for all metadata providers"""

    provider_type: ProviderType
    display_name: str
    requires_auth: bool = True
    auth_fields: List[str] = []  # e.g., ["api_key"] or ["username", "password"]

    def __init__(self, credentials: ProviderCredentials):
        self.credentials = credentials
        self._client = None

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify credentials and connectivity"""
        pass

    @abstractmethod
    def search_series(self, query: str, year: Optional[int] = None) -> List[SearchResult]:
        """Search for series/volumes"""
        pass

    @abstractmethod
    def get_series(self, series_id: str) -> Optional[SearchResult]:
        """Get series details by ID"""
        pass

    @abstractmethod
    def search_issues(self, series_id: str, issue_number: Optional[str] = None) -> List[IssueResult]:
        """Get issues for a series"""
        pass

    @abstractmethod
    def get_issue(self, issue_id: str) -> Optional[IssueResult]:
        """Get issue details by ID"""
        pass

    @abstractmethod
    def to_comicinfo(self, issue: IssueResult, series: SearchResult) -> dict:
        """Convert provider data to ComicInfo.xml fields"""
        pass
```

#### Provider Registry (`__init__.py`)

```python
from typing import Dict, Type, Optional
from .base import BaseProvider, ProviderType, ProviderCredentials

_PROVIDER_REGISTRY: Dict[ProviderType, Type[BaseProvider]] = {}

def register_provider(provider_class: Type[BaseProvider]):
    """Decorator to register a provider"""
    _PROVIDER_REGISTRY[provider_class.provider_type] = provider_class
    return provider_class

def get_provider(provider_type: ProviderType, credentials: ProviderCredentials) -> BaseProvider:
    """Factory function to create provider instances"""
    if provider_type not in _PROVIDER_REGISTRY:
        raise ValueError(f"Unknown provider: {provider_type}")
    return _PROVIDER_REGISTRY[provider_type](credentials)

def get_available_providers() -> List[Dict]:
    """Return list of available providers with their auth requirements"""
    return [
        {
            "type": p.provider_type.value,
            "name": p.display_name,
            "requires_auth": p.requires_auth,
            "auth_fields": p.auth_fields
        }
        for p in _PROVIDER_REGISTRY.values()
    ]
```

---

### 2. Database Schema Changes

#### New Tables

```sql
-- Provider credentials (global, not per-library)
-- IMPORTANT: credentials field is AES-256 encrypted, NOT plain JSON
CREATE TABLE provider_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_type TEXT NOT NULL UNIQUE,  -- 'metron', 'comicvine', etc.
    credentials_encrypted BLOB NOT NULL, -- AES-256-GCM encrypted JSON
    credentials_nonce BLOB NOT NULL,     -- Unique nonce for each encryption
    is_valid INTEGER DEFAULT 0,          -- Last connection test result
    last_tested TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Library-provider mappings (which providers each library uses)
CREATE TABLE library_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL,
    provider_type TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,  -- Lower = higher priority (0 = first choice)
    enabled INTEGER DEFAULT 1,
    FOREIGN KEY (library_id) REFERENCES libraries(id) ON DELETE CASCADE,
    UNIQUE(library_id, provider_type)
);

-- Provider cache (unified cache for all providers)
CREATE TABLE provider_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_type TEXT NOT NULL,
    cache_type TEXT NOT NULL,        -- 'series' or 'issue'
    provider_id TEXT NOT NULL,       -- Provider-specific ID
    data TEXT NOT NULL,              -- JSON serialized data
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    UNIQUE(provider_type, cache_type, provider_id)
);

```

#### cvinfo Files (Retained)

cvinfo files are a standard convention used by other comic management apps (e.g., Mylar, ComicTagger). CLU will **continue reading and writing cvinfo files** in each series folder to maintain cross-application compatibility.

- CLU writes cvinfo when a series is matched to a provider
- CLU reads cvinfo when opening a folder to pre-populate provider links
- cvinfo format remains JSON with provider-specific IDs (e.g., `cv_volume_id`, `metron_series_id`)
- New providers add their own keys (e.g., `anilist_id`, `mangaupdates_id`)

#### Migration Strategy

1. Migrate config.ini credentials to `provider_credentials` (encrypted)
2. Create default `library_providers` entries based on current hardcoded behavior
3. cvinfo files remain in place - no migration needed

---

### 3. New Provider Implementations

#### AniList Provider (GraphQL)

```python
@register_provider
class AniListProvider(BaseProvider):
    provider_type = ProviderType.ANILIST
    display_name = "AniList"
    requires_auth = False  # Public API, auth optional for higher rate limits
    auth_fields = ["api_key"]  # Optional OAuth token

    GRAPHQL_URL = "https://graphql.anilist.co"

    def search_series(self, query: str, year: Optional[int] = None) -> List[SearchResult]:
        graphql_query = """
        query ($search: String, $year: Int) {
            Page(perPage: 20) {
                media(search: $search, startDate_year: $year, type: MANGA) {
                    id
                    title { romaji english native }
                    startDate { year }
                    coverImage { large }
                    description
                    chapters
                }
            }
        }
        """
        # Implementation...
```

#### MangaUpdates Provider (REST)

```python
@register_provider
class MangaUpdatesProvider(BaseProvider):
    provider_type = ProviderType.MANGAUPDATES
    display_name = "MangaUpdates"
    requires_auth = True
    auth_fields = ["api_key"]

    BASE_URL = "https://api.mangaupdates.com/v1"

    def search_series(self, query: str, year: Optional[int] = None) -> List[SearchResult]:
        response = requests.post(
            f"{self.BASE_URL}/series/search",
            headers={"Authorization": f"Bearer {self.credentials.api_key}"},
            json={"search": query}
        )
        # Implementation...
```

#### Bedetheque Provider (Web Scraping)

```python
@register_provider
class BedethequeProvider(BaseProvider):
    provider_type = ProviderType.BEDETHEQUE
    display_name = "Bedetheque"
    requires_auth = False  # Web scraping, no auth needed
    auth_fields = []

    BASE_URL = "https://www.bedetheque.com"

    def search_series(self, query: str, year: Optional[int] = None) -> List[SearchResult]:
        # Use requests + BeautifulSoup or Playwright for scraping
        # Note: Respect robots.txt and rate limits
        pass
```

---

### 4. ComicInfo.xml Field Mapping

All providers map to the same ComicInfo.xml schema:

| ComicInfo Field | Metron | ComicVine | GCD | AniList | MangaUpdates |
|-----------------|--------|-----------|-----|---------|--------------|
| Series | series.name | volume.name | series.name | title.english/romaji | title |
| Number | issue.number | issue.issue_number | issue.number | chapter | chapter |
| Title | issue.name | issue.name | issue.title | - | - |
| Year | cover_date.year | cover_date.year | key_date.year | startDate.year | year |
| Month | cover_date.month | cover_date.month | key_date.month | startDate.month | - |
| Writer | credits[writer] | person_credits[writer] | story_credits | - | author |
| Penciller | credits[penciller] | person_credits[penciller] | - | - | artist |
| Publisher | publisher.name | publisher.name | publisher.name | - | publisher |
| Genre | genres[] | - | - | genres[] | genres[] |
| Summary | issue.desc | issue.description | - | description | description |
| Web | issue.resource_url | issue.site_detail_url | - | siteUrl | url |
| PageCount | issue.page_count | issue.page_count | - | chapters | - |
| AgeRating | issue.rating | - | - | isAdult | - |

---

### 5. API Endpoints

#### Provider Management

```python
# List all available providers
GET /api/providers
Response: [
    {"type": "metron", "name": "Metron", "requires_auth": true, "auth_fields": ["username", "password"]},
    {"type": "comicvine", "name": "ComicVine", "requires_auth": true, "auth_fields": ["api_key"]},
    ...
]

# Get/Set provider credentials
GET /api/providers/<provider_type>/credentials
POST /api/providers/<provider_type>/credentials
Body: {"username": "...", "password": "..."} or {"api_key": "..."}

# Test provider connection
POST /api/providers/<provider_type>/test
Response: {"success": true} or {"success": false, "error": "Invalid credentials"}

# Get library provider configuration
GET /api/libraries/<library_id>/providers
Response: [
    {"provider_type": "metron", "priority": 0, "enabled": true},
    {"provider_type": "comicvine", "priority": 1, "enabled": true}
]

# Update library provider configuration
PUT /api/libraries/<library_id>/providers
Body: [
    {"provider_type": "metron", "priority": 0, "enabled": true},
    {"provider_type": "comicvine", "priority": 1, "enabled": false}
]
```

#### Metadata Search (Unified)

```python
# Search across enabled providers for a library
GET /api/metadata/search?library_id=1&query=batman&year=2020
Response: {
    "results": [
        {"provider": "metron", "id": "123", "title": "Batman", ...},
        {"provider": "comicvine", "id": "456", "title": "Batman", ...}
    ]
}

# Get series details from specific provider
GET /api/metadata/series/<provider>/<series_id>

# Get issues for a series
GET /api/metadata/series/<provider>/<series_id>/issues

# Apply metadata to file
POST /api/metadata/apply
Body: {
    "file_path": "/data/Batman/Batman 001.cbz",
    "provider": "metron",
    "series_id": "123",
    "issue_id": "456"
}
```

---

### 6. UI Changes

#### Settings Page (`/settings`)

Add new section for provider management:

```html
<div class="card mb-4">
    <div class="card-header">
        <h5>Metadata Providers</h5>
    </div>
    <div class="card-body">
        <!-- Provider credential forms -->
        <div class="accordion" id="providerAccordion">
            <!-- Each provider gets a collapsible section -->
            <div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="accordion-button">
                        <span class="badge bg-success me-2">Connected</span>
                        Metron
                    </button>
                </h2>
                <div class="accordion-collapse">
                    <div class="accordion-body">
                        <input type="text" placeholder="Username">
                        <input type="password" placeholder="Password">
                        <button class="btn btn-primary">Test Connection</button>
                    </div>
                </div>
            </div>
            <!-- Repeat for each provider -->
        </div>
    </div>
</div>
```

#### Library Settings

Add provider configuration per library:

```html
<div class="card mb-4">
    <div class="card-header">
        <h5>Library: Comics</h5>
    </div>
    <div class="card-body">
        <h6>Enabled Metadata Providers</h6>
        <p class="text-muted small">Drag to reorder priority. First enabled provider is tried first.</p>
        <ul class="list-group" id="providerSortable">
            <li class="list-group-item d-flex justify-content-between">
                <span><i class="bi bi-grip-vertical me-2"></i> Metron</span>
                <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" checked>
                </div>
            </li>
            <li class="list-group-item d-flex justify-content-between">
                <span><i class="bi bi-grip-vertical me-2"></i> ComicVine</span>
                <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" checked>
                </div>
            </li>
        </ul>
    </div>
</div>
```

#### Metadata Buttons (files.html, collection.html)

Replace hardcoded provider buttons with dynamic buttons based on library:

```javascript
// Current (hardcoded)
<button onclick="searchMetron()">Metron</button>
<button onclick="searchComicVine()">ComicVine</button>

// New (dynamic based on library)
async function loadMetadataButtons(libraryId) {
    const response = await fetch(`/api/libraries/${libraryId}/providers`);
    const providers = await response.json();

    const container = document.getElementById('metadataProviders');
    container.innerHTML = providers
        .filter(p => p.enabled)
        .sort((a, b) => a.priority - b.priority)
        .map(p => `<button class="btn btn-outline-primary" onclick="searchProvider('${p.provider_type}')">${p.name}</button>`)
        .join('');
}
```

---

### 7. Implementation Phases

#### Phase 1: Foundation (Core Infrastructure)
1. Create `models/providers/` directory structure
2. Implement `BaseProvider` abstract class and data classes
3. Implement provider registry and factory
4. Create database migration for new tables
5. Add provider credentials API endpoints

#### Phase 2: Migrate Existing Providers
1. Wrap Metron (`mokkari`) in `MetronProvider` adapter
2. Wrap ComicVine (`simyan`) in `ComicVineProvider` adapter
3. Wrap GCD in `GCDProvider` adapter
4. Migrate existing credentials from config.ini
5. Update existing endpoints to use provider abstraction

#### Phase 3: Library Configuration
1. Add library provider configuration API endpoints
2. Create UI for managing library providers in settings
3. Update metadata buttons to be dynamic per library
4. Standardize cvinfo key naming for new providers (e.g., `anilist_id`, `mangaupdates_id`)

#### Phase 4: New Providers
1. Implement `AniListProvider` (GraphQL)
2. Implement `MangaUpdatesProvider` (REST)
3. Implement `BedethequeProvider` (Web scraping)
4. Add comprehensive tests for each provider

#### Phase 5: Enhanced Features
1. Add provider fallback logic (auto-try next provider on failure)
2. Add batch metadata lookup across folders
3. Add provider match confidence scoring
4. Add manual provider override per folder

---

### 8. Credential Encryption

All provider credentials (API keys, usernames, passwords, connection strings) are encrypted at rest using AES-256-GCM before being stored in the database. They are **never** stored as plain text or plain JSON.

#### Encryption Module (`models/providers/crypto.py`)

```python
import os
import json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Key file location (outside the database, generated once)
KEY_FILE = os.path.join(os.environ.get("CONFIG_DIR", "/config"), ".provider_key")

def _get_or_create_key() -> bytes:
    """Load or generate the 256-bit encryption key."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    key = AESGCM.generate_key(bit_length=256)
    # Restrict file permissions (owner read/write only)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key

def encrypt_credentials(credentials: dict) -> tuple[bytes, bytes]:
    """Encrypt a credentials dict. Returns (ciphertext, nonce)."""
    key = _get_or_create_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    plaintext = json.dumps(credentials).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return ciphertext, nonce

def decrypt_credentials(ciphertext: bytes, nonce: bytes) -> dict:
    """Decrypt credentials back to a dict."""
    key = _get_or_create_key()
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))
```

#### Key Management
- A 256-bit AES key is generated once and stored at `/config/.provider_key`
- File permissions are set to `0600` (owner read/write only)
- The key file lives in the config volume, persisted across container restarts
- If the key file is lost, credentials must be re-entered (they cannot be recovered)
- The key file should be included in backup strategies alongside `config.ini`

#### Dependency
- Requires the `cryptography` package (`pip install cryptography`)
- Already commonly available in Python environments; add to `requirements.txt`

#### Configuration Migration (from config.ini)

```python
def migrate_provider_credentials():
    """One-time migration from config.ini to encrypted database storage."""
    from config import config
    from models.providers.crypto import encrypt_credentials

    migrations = [
        ("metron", {
            "username": config.get("METRON_USERNAME"),
            "password": config.get("METRON_PASSWORD")
        }),
        ("comicvine", {
            "api_key": config.get("COMICVINE_API_KEY")
        }),
        ("gcd", {
            "connection_string": config.get("GCD_CONNECTION")
        })
    ]

    conn = get_db_connection()
    for provider_type, creds in migrations:
        if any(v for v in creds.values() if v):
            ciphertext, nonce = encrypt_credentials(creds)
            conn.execute("""
                INSERT OR REPLACE INTO provider_credentials
                (provider_type, credentials_encrypted, credentials_nonce)
                VALUES (?, ?, ?)
            """, (provider_type, ciphertext, nonce))
    conn.commit()

    # After successful migration, the original config.ini keys can
    # optionally be removed to avoid storing credentials in two places.
```

#### API Credential Endpoints (Encryption Flow)

```python
# Saving credentials (POST /api/providers/<type>/credentials)
creds = request.json  # {"api_key": "abc123"}
ciphertext, nonce = encrypt_credentials(creds)
db.execute("""
    INSERT OR REPLACE INTO provider_credentials
    (provider_type, credentials_encrypted, credentials_nonce)
    VALUES (?, ?, ?)
""", (provider_type, ciphertext, nonce))

# Reading credentials (only decrypted in memory, never sent to frontend)
row = db.execute("""
    SELECT credentials_encrypted, credentials_nonce
    FROM provider_credentials WHERE provider_type = ?
""", (provider_type,)).fetchone()
creds = decrypt_credentials(row["credentials_encrypted"], row["credentials_nonce"])

# GET endpoint returns masked values only, never raw credentials
# e.g., {"api_key": "abc...123", "has_credentials": true}
```

---

### 9. Testing Strategy

#### Unit Tests
- Each provider adapter has isolated tests with mocked API responses
- Test `to_comicinfo()` mapping for each provider
- Test credential validation

#### Integration Tests
- Test provider registry and factory
- Test database operations for credentials and library mappings
- Test API endpoints

#### E2E Tests
- Test full flow: configure provider → search → apply metadata
- Test provider fallback behavior
- Test UI provider selection

---

### 10. Open Questions / Decisions Needed

1. ~~**Credential Storage Security**~~: **Resolved** - AES-256-GCM encryption at rest (see Section 8).

2. **Rate Limiting**: Should we implement per-provider rate limiting to avoid API bans?

3. **Caching Strategy**: How long should provider responses be cached? Current plan: 24 hours for series, 1 week for issues.

4. **Fallback Behavior**: When first provider fails, should we automatically try next provider or prompt user?

5. **Match Confidence**: Should we implement fuzzy matching scores to help users choose between provider results?

6. ~~**cvinfo Migration**~~: **Resolved** - cvinfo files are retained for cross-app compatibility (see Section 2).

---

## Summary

This architecture provides:
- **Unified interface** for all metadata providers via Adapter Pattern
- **Per-library configuration** of which providers to use and in what order
- **Extensibility** for adding new providers with minimal code changes
- **Encrypted credential management** with AES-256-GCM encryption at rest and connection testing
- **cvinfo compatibility** - cvinfo files retained for cross-app interoperability (Mylar, ComicTagger, etc.)
- **Flexible UI** that adapts to available providers per library
- **Migration path** from current hardcoded implementation

The phased implementation allows incremental delivery while maintaining backward compatibility throughout the transition.
