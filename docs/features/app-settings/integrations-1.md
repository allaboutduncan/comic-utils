---
description: All of the Options available in Settings
icon: square-sliders
---

# Settings Available

With the exception on enabling [folder-monitoring](../folder-monitoring/ "mention"), all options can be updated in the Settings page.

<figure><img src="../../.gitbook/assets/Screenshot 2025-08-20 125115.png" alt=""><figcaption></figcaption></figure>

### Missing Issue Configuration

The two options here will vary greatly on how much you use [markdown-3.md](../directory-features/markdown-3.md "mention") and how your library is structured.

**IGNORED TERMS:** Add a comma-separated list of words/terms to ignore while checking for missing issues. Update these terms and re-run the missing issue check to better parse your library.

**IGNORED FILES:** Add a comma-separated list of files to ignore when checking for missing issues. Your collection may be a mix of CBZ/CBR/PDF or other files but if you have other files in your directories you want excluded, just add them here.

### Directory & File Processing Settings

**Enable Subdirectories for Conversion:** This specifically allows [markdown.md](../directory-features/markdown.md "mention")to traverse subdirectories and convert all CBR/RAR files to CBZ. This is not enabled by default - as running this on a high level folder AND a large collection could take quite a bit of time.

**SKIPPED TYPES:** Add a comma-separated list of extensions to skip while performing actions on files. When any operation unpacks a RAR/ZIP File, files with these extensions will be skipped. They will be re-added to the archive. Examples are `.xml`

**DELETED TYPES:** Add a comma-separated list of extensions to delete while performing actions on files. When any operation unpacks a RAR/ZIP File, files with these extensions will deleted before the file is re-packed. Examples are: `.nfo,.sfv,.db,.DS_Store`

### Folder Monitoring

This is the most extensive set of features and will only be applicable if [folder-monitoring](../folder-monitoring/ "mention") is enabled. Most of these feature flags enhance the previous feature flag.

**WATCH:** The folder that will be monitored for files being added. This setting is dependent on the optional location mapped during [quickstart.md](../../getting-started/quickstart.md "mention")guide.

**TARGET:** The folder where files will be after they are processed. This setting is dependent on the optional location mapped during [quickstart.md](../../getting-started/quickstart.md "mention")guide.

**IGNORED EXTENSIONS:** File types listed here will be ignored by the file monitoring process. Many of these file types are `temp`file types and should be ignore. However, if you want to have others files in the WATCH folder and not have them processed with your enabled options - add those extenison types here.

**Auto CBZ Conversion:** If enabled, when CBR files are downloaded, this will auto-convert them to CBZ

**Auto ZIP Extraction:** If enabled, when ZIP files are added to your WATCH folder, this will automatically extract them. This does not create folders. It uses the structure within the ZIP file.&#x20;

For ZIP only, this specifically bypasses the IGNORED EXTENSIONS.

**Processing Sub-Directories:** If enabled, this will perform monitoring functions on sub-directories within your WATCH folder. For example, if you have `/WATCH/archive01.zip` and it is auto-extracted to `/WATCH/archive`each file will be processed and moved to `/TARGET`.

**Moving Sub-Directories:** If enabled, this will preserve any sub-directories in your `/WATCH` folder when they are moved to your `TARGET` folder. For example, if you have `/WATCH/archive01.zip` and it is auto-extracted to `/WATCH/archive`each file will be processed and moved to `/TARGET/archive`.

{% hint style="info" %}
To Do: Hide these in settings if folder monitoring is not enabled or inform user that folder monitoring is not enabled.
{% endhint %}

**Auto Cleanup Orphan Files:** If you are using the monitoring and [editor.md](../file-downloads/editor.md "mention") for downloads, failed downloads will be removed at regular intervals.

**Cleanup Interval (hours):** Set the timing for removing orphaned files.

## App Settings Continued

<figure><img src="../../.gitbook/assets/Screenshot 2025-08-20 125142.png" alt=""><figcaption></figcaption></figure>

### API Download Configuration <a href="#api-configuration" id="api-configuration"></a>

#### Custom Headers for Auth

Depending on your authentication/VPN/etc to protect your site, this setting will allow you to pass custom auth variables or anything else in the header. Simply enter the content you need to pass as JSON.&#x20;

The example provided shows you how to pass Client ID and Client Secret to authenticate to your site.

```json
{
    "CF-Access-Client-Id":"you-client-id",
    "CF-Access-Client-Secret":"your-secret"
}
```

#### PixelDrain API Key

For the [file-downloads](../file-downloads/ "mention") feature, you can bypass daily limits by entering your API key in this field.

### Performance and Timeout Settings

Allows you to better manage large files depending on your system. Adjusting these values will enable/disable additional timing checks when processing large files (converting or rebuilding files). The default settings on average systems should easily handle converting a 2GB CBR file to CBZ.

### Cache Management

v4 Introduced searches and improved cache management. To allow you to search your library quickly, a cached search index is built when the app starts and rebuilt every 6 hours.

From this area in admin, you can manually trigger a cache rebuild or clear the current cache.

Settings are show, but currently hard coded, for:

#### Cache Rebuild Interval (hours)

Timing for automatically rebuilding the cache. Default is every 6 hours

#### Cache Duration (seconds)

When browsing in file manager, directory listings are cached (default 5-seconds) to speed up moving back and forth between views.

#### Maximum Cache Size

Determines the number of directories that can be cached in memory while browsing. Current default value is set to 100 directories.

{% hint style="info" %}
Download directories configured in the app are not indexed.&#x20;

Moving files in these directories does not invalidate the cache.
{% endhint %}

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 094321.png" alt=""><figcaption></figcaption></figure>

### Custom Rename Pattern Settings

Use a custom naming scheme for renaming issues when downloads are processed or files are renamed.

Enter your naming pattern using the syntax provided and see a real-time preview of the result.

{% hint style="danger" %}
Renaming applies only to issues. Entering directory paths or folder structures is not yet supported.
{% endhint %}

### Logging & Debugging

If you are experiencing issues or odd behavior, enable this to add more detailed logging.

### ComicInfo.XML Updates

All of these features related to updating/cleaning the `ComicInfo.xml` file in archives. I would consider these experimental or beta features - in that they have been tested the least.

**Update Volume to First Issue Year:** If there is not a **volume year** in the `ComicInfo.xml` this will read the (YEAR) from the first issue in the folder and update the **volume year** for each file to match.

**Remove All Markdown Content:** When enabled, if there are _tables_, _bold text_, or _headers_ in the Comments field of the `ComicInfo.xml` file, they will be removed.

**Remove 'Covers & Creators' Table:** When enabled, if there is a _Covers & Creators table_ in the Comments field of the `ComicInfo.xml` file, it will be removed.

### Save & Restart

**SAVE:** Click the Save button to save any changes you have made to the app.

**RESTART APP:** Only require on the initial install and a quick way to force restart to reload the config/settings changes.
