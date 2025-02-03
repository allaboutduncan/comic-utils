# Comic Library Utilities
This is a set of utilities that I developed while moving my 70,000+ comic library to <img src="https://komga.org/img/logo.svg" alt="Komga Logo" width="20"/> [Komga](https://komga.org/).

## Installation via Docker Compose

Copy the following and edit the environment variables

    version: '3.9'
    services:
        comic-utils:
            image: allaboutduncan/comic-utils-web:latest

            container_name: comic-utils
            logging:
                options:
                    max-size: 1g
            restart: always
            ports:
                - '5577:5577'
            volumes:
                - '/var/run/docker.sock:/tmp/docker.sock:ro' # do not change this line
                ## update the line below to map to your library.
                ## If you are running Komga via Docker - this should match
                - "D:/Comics:/data"
                ## Required if Folder Monitoring is set to "yes". Map this to the folder you want watched for renaming
                - "F:/downloads:/temp"
                ## Required if Folder Monitoring is set to "yes". Map this to the folder where renamed files will be moved
                - "F:/downloads/processed:/processed"
            environment:
                - FLASK_ENV=development
                ## For 'Missing File Check' files with these names will be ignored
                - IGNORE="Annual","(Director's Cut)"
                ## Set to 'yes' if you want to use folder monitoring.
                - MONITOR=yes/no 
                # Update to path for the "Watched" folder set above. Defaults to "/temp"
                - WATCH=/temp
                # Update to path for the "Processed" folder set above. Defaults to "/processed"
                - TARGET=/processed

### More About Volumes Mapping for Your Library
For the utility to work, you'll want to mimic your [Komga](https://komga.org/) settings. 

I am running [Komga](https://komga.org/) on my Windows home server, via Docker.

My comics are located on `D:/Comics` and when installing [Komga](https://komga.org/) I didn't change the default mapping of `target: /data`

Mirroring this setup in the Docker Compose install looks like this: `- "D:/Comics:/data"`


---

## Using the Utilities

In your browser, navigate to http://localhost:5577

You'll be presented with the main screen, where you can select which option you'd like to perform. The app will default to "Directory" operations.

![Directory Main Menu](/images/home.png)

When a path to a single file is entered - the app will switch to the "Single File" options

![Single File Main Menu](/images/home-single.png)

When popularing the *Directory* path, you should enter what you see in [Komga](https://komga.org/)

![Path Example](/images/path.png)

To regenerate this file, before clicking run, it should look like this:

![Single Example](/images/single.png)

---
## Features
Below are examples and explanations of each feature available

### 1. Rename - All Files in Diretory
Currently this functiuon does 5 things to all files in a directory and any sub-directories.

1. Removes everything in parentheses with the exepction of the 4-digit year (if available)
2. Removes `c2c`
3. Removes anything in brackets [any text removed] - along with the brackets
4. Removes any text / characters after "filename issue (year)"
5. Removes any extra spaces before the file entenstion

Mylar and ComicRack should be your first choice for performing these actions, but I wanted something I could easliy run on my manual downloads directory. Oftentimes series archives or torrent files will have numerous naming patterns with information in parentesis, brackets, before the year, after and all over the place. I continuously update these to handle as many as I encounter.

#### Folder Monitor Renaming
During installation, you can setup a "/temp" folder to be monitored. All CBR/CBZ files added to this folder will be renamed and moved to the configured "/processed" folder. 

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

### 6. Single File - Rebuild/Convert
Running this will rebuild a CBZ file in an attempt to fix issues/errors with it not displaying correctly.
Additionally, this will also convert a single CBR file to a CBZ

| Before    | After |
| -------- | ------- |
|  ![Rebuild - Before](/images/rebuild01.png)  |  ![Rebuild - After](/images/rebuild02.png)    |

### 7. Crop Cover
Use this tool to crop a cover that is front & back, to front only.

The wraparound cover is not deleted, but re-ordered in the CBZ to be the 2nd image.

| Before    | After |
| -------- | ------- |
|  ![Crop - Before](/images/crop01.png)  |  ![Crop - After](/images/crop02.png)    |
| Page Count: 63 | Page Count: 64 |

### 8. Remove First Image
Many older files will have a cover image that is not the cover of the comic.

Use this utility to remove the first image in the file.

| Before    | After |
| -------- | ------- |
|  ![Remove - Before](/images/remove01.png)  |  ![Remove - After](/images/remove02.png)    |
| Page Count: 23 | Page Count: 22 |

### 9. Add blank Image at End
Requested feature from Komga Discord. Corrects display issues with Manga files.

Adds a blank / empty PNG file (zzzz9999.png) to the archive.

### 10. Delete File
Utility to delete a single file and requires confirmation before performing the delete action.

![Delete File](/images/delete01.png)
![Delete Confirmation](/images/delete02.png)
