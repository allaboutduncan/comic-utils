---
description: What can folder monitoring do?
icon: folders
---

# Features

Once enabled and configured, here are a list of features that are available via folder monitoring.

#### Ignored Extensions

This setting is used to ignore file types in your **WATCH** folder. Any extension configured here will be ignored. This is used in 2 ways:

1. Ignore temp files associated with downloads. The app monitors filesize and attempts to determine when a file download (or move) is complete. Certain file extensions are associated with temp files and this setting allows us to easily ignore them.
2. Some files could contain multiple files or a single file (RAR) or we may not want to move and rename PDF files or images placed in the WATCH folder. Just add the file extension and they will be ignored.

Default file extensions are:&#x20;

```ini
.crdownload,.torrent,.tmp,.mega,.rar,.bak
```

#### Renaming

This feature is enabled by default and will be running when monitoring is enabled. This applies the same renaming logic outlined in the [editor.md](../directory-features/editor.md "mention") section to any file added to the directory.

Files will be moved to the **TARGET** directory when they are renamed.

#### Process Sub-Directories

When enabled, this feature will apply all other configured options to you **WATCH** folder.

For example, sub-directory files will not be renamed and moved by default. If enabled, all files on the root of your **WATCH** folder will be renamed as well as any sub-directories.

#### Move Sub-Directories

If you have multiple issues of a series in a sub-directory of your **WATCH** folder, you may want to keep them in a separate folder as opposed to the root of the **TARGET** folder.&#x20;

Simply enable this feature and the sub-directory will be moved along with the files.

**Note:** The sub-directory name will be renamed/cleaned using similar logic to the filenames.&#x20;

#### Auto ZIP Extraction

If you download a ZIP file with multiple files, when this feature is enabled, all files will be extracted once the download is complete.

Files are extracted to the **WATCH** folder and will maintain the structure within the ZIP file.

#### Convert to CBZ

When enabled, any CBR file will be auto-converted to a CBZ when processed.
