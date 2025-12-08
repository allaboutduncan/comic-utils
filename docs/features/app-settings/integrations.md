---
description: Installation Steps for Config
---

# Config Installation

When installed, the following values are set by default in the config file

```ini
[SETTINGS]
WATCH=/downloads/temp
TARGET=/downloads/processed
IGNORED_TERMS=Annual
IGNORED_EXTENSIONS=.crdownload,.torrent,.tmp,.mega,.rar,.bak
IGNORED_FILES=cover.jpg,cvinfo,.DS_Store
READ_SUBDIRECTORIES=True
CONVERT_SUBDIRECTORIES=True
AUTOCONVERT=True
XML_YEAR=False
XML_MARKDOWN=False
XML_LIST=True
MOVE_DIRECTORY=True
AUTO_UNPACK=True
```

During installation (see [quickstart.md](../../getting-started/quickstart.md "mention")), you will need to map the `/config` directory to a local folder ensure that config settings are persisted on updates.

```yaml
- '/path/to/local/config:/config' # Maps local folder to container
```

**First Install:** On the first install with new config settings, visit the config page, ensure everything is configured as desired.

* Save your Config settings
* Click the Restart App button

### Explanation of Values

<table><thead><tr><th>Parameter</th><th width="439">Function</th></tr></thead><tbody><tr><td><pre><code>WATCH
</code></pre></td><td>Path/Folder to watch if folder monitoring enabled</td></tr><tr><td><pre><code>TARGET
</code></pre></td><td>Path/Folder to where watched files are moved after processing</td></tr><tr><td><pre><code>INGNORED_TERMS
</code></pre></td><td>Option for <a data-mention href="../directory-features/markdown-3.md">markdown-3.md</a> to not look for issues.</td></tr><tr><td><pre><code>IGNORED_EXTENSIONS
</code></pre></td><td>File types that will be ignored by <a data-mention href="../directory-features/markdown-3.md">markdown-3.md</a></td></tr><tr><td><pre><code>IGNORED_FILES
</code></pre></td><td>Files here will not show when browsing file structure in the app.</td></tr><tr><td><pre><code>READ_SUBDIRECTORIES
</code></pre></td><td>Read sub-directories when <a data-mention href="../folder-monitoring/">folder-monitoring</a>enabled</td></tr><tr><td><pre><code>CONVERT_SUBDIRECTORIES
</code></pre></td><td>Enable traversing sub-directories when converting CBR to CBZ - see <a data-mention href="../directory-features/markdown.md">markdown.md</a></td></tr><tr><td><pre><code>AUTOCONVERT
</code></pre></td><td>Auto-convert files to CBZ as they are downloaded with <a data-mention href="../folder-monitoring/">folder-monitoring</a></td></tr><tr><td><pre><code>XML_YEAR
</code></pre></td><td>Setting for <a data-mention href="../directory-features/markdown-5.md">markdown-5.md</a></td></tr><tr><td><pre><code>XML_MARKDOWN
</code></pre></td><td>Setting for <a data-mention href="../directory-features/markdown-5.md">markdown-5.md</a></td></tr><tr><td><pre><code>XML_LIST
</code></pre></td><td>Setting for <a data-mention href="../directory-features/markdown-5.md">markdown-5.md</a></td></tr><tr><td><pre><code>MOVE_DIRECTORY
</code></pre></td><td>Move sub-directories when moving files in <a data-mention href="../folder-monitoring/">folder-monitoring</a></td></tr><tr><td><pre><code>AUTO_UNPACK
</code></pre></td><td>When using <a data-mention href="../folder-monitoring/">folder-monitoring</a>, this will enable auto-extraction of ZIP archives</td></tr><tr><td><pre><code>GCD_METADATA_LANGUAGES
</code></pre></td><td>When using <a data-mention href="../app-settings-1/">app-settings-1</a>, CLU will search for metadata on these languages</td></tr></tbody></table>
