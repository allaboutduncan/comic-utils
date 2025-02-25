# Comic Library Utilities (CLU)

![Docker Pulls](https://img.shields.io/docker/pulls/allaboutduncan/comic-utils-web)
![GitHub Release](https://img.shields.io/github/v/release/allaboutduncan/comic-utils)
![GitHub commits since latest release](https://img.shields.io/github/commits-since/allaboutduncan/comic-utils/latest)

![Comic Library Utilities (CLU)](images/clu-logo-360.png "Comic Library Utilities")

## What is CLU & Why Does it Exist

This is a set of utilities that I developed while moving my 70,000+ comic library to [Komga](https://komga.org/).

As I've continued to work on it, add features and discuss with other users, I wanted to pivot away from usage as a side-load to Komga and focus on it as a stand-alone app.

With the v1.1 Update, you can now browse your dictories and files directly from that app. This enables you to easily maintain and update your Comic Library no matter the app you use for browsing, organizing and reading.

![Comic Library Utilities (CLU)](/images/home_v1.png "Comic Library Utilities Homepage")

## Features
Below are examples and explanations of each feature available

### 1. Rename - All Files in Diretory
Currently this function does 5 things to all files in a directory and any sub-directories.

1. Removes everything in parentheses with the exception of the 4-digit year (if available)
2. Removes `c2c`
3. Removes anything in brackets [any text removed] - along with the brackets
4. Removes any text / characters after "filename issue (year)"
5. Removes any extra spaces before the file extension

Mylar and ComicRack should be your first choice for performing these actions, but I wanted something I could easliy run on my manual downloads directory. Oftentimes series archives or torrent files will have numerous naming patterns with information in parenthesis, brackets, before the year, after and all over the place. I continuously update these to handle as many as I encounter.

### 2. Convert Directory (CBR / RAR Only)
Converts all CBR / RAR files in a directory to ZIP. This will skip any existing CBZ files.

This utility does 2 things:
1. Convert all RAR / CBR files to CBZ

The simply converts all CBR/RAR files to CBZ and skips any existing files. If you need to rebuild a directory to fix corrupted files as well, use the next function.
 
### 3. Rebuild Directory - All Files in Diretory
Rebuilds all files in a directory. 

This utility does 2 things:
1. Convert all RAR / CBR files to CBZ
2. Rebuild all ZIP / CBZ files

I often see, in both newer and older files, that Komga doesn't recognize or scan them correctly. More often than not, rebuilding them corrects the error. This option will rebuild all files in a directory (RAR/CBR/ZIP/CBZ --> CBZ)

### 4. Convert PDF to CBZ
Converts all PDF files in a directory to CBZ files

This seems to be an edge case, but I had several comic magazines in PDF format. With the art and full-color pages, I wanted them available as CBZ for reading on my iPad via Komga OPDS.

*Note:* I've only tested this on a few edge cases. If you see an issue using this, please [create an Issue here](https://github.com/allaboutduncan/comic-utils/issues).

### 5. Missing File Check
Generates a Text file of "Missing" Issues

Having various folders from various sources or many years, I wanted to be able to check and see if any issues were "missing" from a series.

![Missing Issue Check](/images/missing.png) 

Running this feature on my `/data/Valiant` directory, generated the following `missing.txt` file

    Directory: /data/Valiant/Eternal Warrior/v1992
    Eternal Warrior 008 (1992).cbz

    Directory: /data/Valiant/Harbinger Wars 2 (2018)
    Harbinger Wars 001 (2018).cbz

    Directory: /data/Valiant/Harbinger/v1992
    Harbinger 011 (1992).cbz

    Directory: /data/Valiant/The Valiant/v2014
    The Valiant 001 (2015).cbz

    Directory: /data/Valiant/X-O Manowar/v1992
    X-O Manowar 050 (1992).cbz

    Directory: /data/Valiant/X-O Manowar/v2012
    X-O Manowar 005 (2012).cbz
    X-O Manowar 006 (2012).cbz
    X-O Manowar 007 (2012).cbz

    Directory: /data/Valiant/X-O Manowar/v2017
    X-O Manowar 001 (2018).cbz
    X-O Manowar 002 (2018).cbz
    X-O Manowar 003 (2018).cbz
    X-O Manowar 004 (2018).cbz
    X-O Manowar 005 (2018).cbz
    X-O Manowar 006 (2018).cbz
    X-O Manowar 007 (2018).cbz
    X-O Manowar 008 (2018).cbz
    X-O Manowar 009 (2018).cbz
    X-O Manowar 010 (2018).cbz
    X-O Manowar 011 (2018).cbz
    X-O Manowar 012 (2018).cbz
    X-O Manowar 013 (2018).cbz
    X-O Manowar 014 (2018).cbz
    X-O Manowar 015 (2018).cbz
    X-O Manowar 016 (2018).cbz
    X-O Manowar 017 (2018).cbz

From this, I can assume that in `Directory: /data/Valiant/X-O Manowar/v2012` I am missing issues 001 - 004 and so on.

There is a threshold of 50 issues currently configured, so if more than 50 issues are missing of a series, the results will be truncated like so:

    Series Name 071-499 (1998) [Total missing: 429]

This is useful when publsihers revert to the original number of a long-running series and you have issues like `001 - 070, 500-542`

*Note:* This is not a "smart" feature and simply assumes each folder should have files starting with (#01, 01, 001) and the "last" file is the last alpha-numeric file in the folder.

### 6. Clean / Update ComicInfo.xml

Requested by a user on Discord, this feature will let you bulk update specific items in the `ComicInfo.xml` of each *CBZ* file in a directory. Currently (Fed 2025) it supports two fields and will only work on the selected directory (no traversing sub-directories). Both options need to be enabled in the CONFIG section of the app. 

1. Update Volume: This will update `Volume` all files in the selected directory using the (YEAR) obtained from the first file in the directory. If a 4-digit YEAR in parentheses is not available, no updates will be made.
2. Clean Comments: This feature will REMOVE content from the `Comments` field each file of the selected sub-directory. There are two options available for cleaning comments.
    1. Remove any content that starts with '*List' until the next paragraph/blank line. This specifically targets *List of covers and their creators:* and the table data that follows
    2. Remove all content that is '## Header', '__Bold__' or in a '| Table |'

### 7. Single File - Rebuild/Convert
Running this will rebuild a CBZ file in an attempt to fix issues/errors with it not displaying correctly.
Additionally, this will also convert a single CBR file to a CBZ

| Before    | After |
| -------- | ------- |
|  ![Rebuild - Before](/images/rebuild01.png)  |  ![Rebuild - After](/images/rebuild02.png)    |

### 8. Crop Cover
Use this tool to crop a cover that is front & back, to front only.

The wraparound cover is not deleted, but re-ordered in the CBZ to be the 2nd image.

| Before    | After |
| -------- | ------- |
|  ![Crop - Before](/images/crop01.png)  |  ![Crop - After](/images/crop02.png)    |
| Page Count: 63 | Page Count: 64 |

### 9. Remove First Image
Many older files will have a cover image that is not the cover of the comic.

Use this utility to remove the first image in the file.

| Before    | After |
| -------- | ------- |
|  ![Remove - Before](/images/remove01.png)  |  ![Remove - After](/images/remove02.png)    |
| Page Count: 23 | Page Count: 22 |

### 10. Add blank Image at End
Requested feature from Komga Discord. Corrects display issues with Manga files.

Adds a blank / empty PNG file (zzzz9999.png) to the archive.

### 11. Delete File
Utility to delete a single file and requires confirmation before performing the delete action.

![Delete File](/images/delete01.png)
![Delete Confirmation](/images/delete02.png)

## Folder Monitoring

![Folder Monitoring Enabled](/images/monitoring.png)

During installation, you can enable foldering monitoring. If enabled, you will see a dismissable notice on the home page, informing you that folder monitoring is enabled and it will list the folder being monitored.

The following options are available for Folder Monitoring:

1. __Auto-Renaming:__ Based on the manually triggered renaming, this option will monitor the configured folder.
    * Renaming pattern is `{Series} {Issue} ({YEAR})`. If a *Volume* exists, this will be added between `{Series} v1 {Issue}`
    * The *default monitoring location* is `/downloads/temp`. This can be updated to any folder you map during installation.
    * Renaming moves the files to a new folder. The default location is `/downloads/processed`. This can be updated in the config menu
2. __Auto-Convert to CBZ:__ If this is enabled, files that are not CBZ will be converted to CBZ when they are moved to the `/downloads/processed` location
3. __Processing Sub-Directories:__ If this is enabled, the app will monitor and perform all functions on any sub-directory within the *default monitoring location*. 
    * Sub-directory structure IS NOT maintained when files are moved

## Installation via Docker Compose

Copy the following and edit the environment variables

    version: '3.9'
    services:
        comic-utils:
            image: allaboutduncan/comic-utils-web:latest

            container_name: comic-utils
            logging:
                options:
                max-size: '20m'  # Reduce log size to 20MB
                max-file: '3'     # Keep only 3 rotated files
            restart: always
            ports:
                - '5577:5577'
            volumes:
                - '/var/run/docker.sock:/tmp/docker.sock:ro' # do not change this line
                - '/path/to/local/config:/config' # Maps local folder to container
                ## update the line below to map to your library.
                ## Your library MUST be mapped to '/data' for the app to work
                - 'D:/Comics:/data'
                ## Additional folder is you want to use Folder Monitoring.
                - 'F:/downloads:/temp'
            environment:
                - FLASK_ENV=development
                ## Set to 'yes' if you want to use folder monitoring.
                - MONITOR=yes/no 

__Update your Docker Compose:__ Mapping the `/config` directory is required now to ensure that config settings are persisted on updates.
__First Install:__ On the first install with new config settings, visit the config page, ensure everything is configured as desired.
* Save your Config settings
* Click the Restart App button

### More About Volumes Mapping for Your Library
For the utility to work, you need to map your Library to `/data`

I am running [Komga](https://komga.org/) on my Windows home server, via Docker.

My comics are located in `D:/Comics` therefore, my mapping is: `- "D:/Comics:/data"`

## Using the Utilities

In your browser, navigate to http://localhost:5577

You'll be presented with the main screen, where you can select which option you'd like to perform. The app will default to "Directory" operations.

![Directory Main Menu](/images/home_v1.png)

### Browsing for directories / files

1. CLick the Browse button to get started

![Browse](/images/browse01.png)

2. You should see the contents of your Library

![Browse Directory](/images/browse02.png)

3. As you navigate through your folders, clicking in the __<-- Parent__ option will take you back up the tree.

![Browse](/images/browse03.png)

4. If you want to perform actions on a __Directory__ click the __Choose__ button

![Browse - Select Folder](/images/browse06.png)

5. The app will allow you run any of the Directory options on the path selected

![Browse - Select Folder](/images/browse07.png)

### Single File Options

When a path to a single file is selected - the app will switch to the "Single File" options

![Single File Main Menu](/images/home-single.png)

You may continue navigating to select a single file

![Browse - Select File](/images/browse05.png)

Additionally, you may populate the __Directory__ path. In the example below, I've entered my file path provided by [Komga](https://komga.org/)

![Path Example](/images/path.png)

## Configuration

Configuration options are available in the Web UI. The app will install with the recommended options. All options will be updated in the App once you click __Save__

See the explanation for each feature for more information on the config options.

![Config Page](/images/configure.png)

## Folder Monitoring

Folder Monitoring will "watch" a configured folder for new files. Once it detects a new file, it will rename it using the naming conventions of the app and them move it to a configured "processed" folder.

This must be enabled via an Environment Variable during install. You can update your Docker container without re-pulling the image to turn this feature on or off.

Additionally, you can enable via the Config menu, the ability to convert files to CBZ after they are renamed and moved. This will only convery CBR files.

In the Config menu, you'll file an __Ignored Extensions__ setting that comes pre-configured to ignore popular *temp* file types. This ensures these files are not converted until the download is completed. 

## Trouble-Shooting

Info and error logging is now visible in the app, via the *App Logs* and *Monitor Logs* (if enabled) links in the header navigation. As processes are performed, details are logged here for reivew and trouble-shooting.

### Example App Log

![App Log Screen](/images/app-log.png)

### Example Monitor Log

![Monitor Log Screen](/images/mon-log.png)
