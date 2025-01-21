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
            volumes:
                - '/var/run/docker.sock:/tmp/docker.sock:ro' # do not change this line
                ## update the line below to map to your library.
                ## If you are running Komga via Docker - this should match
                - "D:/Comics:/data"
            ports:
                - '5577:5577'
            environment:
                - FLASK_ENV=development

### More About Volumes Mapping for Your Library
For the utility to work, you'll want to mimic your [Komga](https://komga.org/) settings. 

I am running [Komga](https://komga.org/) on my Windows home server, via Docker.

My comics are located on `D:/Comics` and when installing [Komga](https://komga.org/) I didn't change the default mapping of `target: /data`

Mirroring this setup in the Docker Compose install looks like this: `- "D:/Comics:/data"`


---

## Using the Utilities

In your browser, navigate to http://localhost:5577

You'll be presented with the main screen, where you can select which option you'd like to perform

![Main Menu](/images/home.png)

When popularing the *Directory* path, you should enter what you see in [Komga](https://komga.org/)

![Path Example](/images/path.png)

To regenerate this file, before clicking run, it should look like this:

![Single Example](/images/single.png)

Validation has been added and the app checks to see if directory / single file path has been entered. Based on entry, you can perform the related options.

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

Mylar and ComicRack should be your first choice for performing these actions, but I wanted something I could easliy run on my manual downloads directory.

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

### 4. Single File - Rebuild/Convert
Running this will rebuild a CBZ file in an attempt to fix issues/errors with it not displaying correctly.
Additionally, this will also convert a single CBR file to a CBZ

| Before    | After |
| -------- | ------- |
|  ![Rebuild - Before](/images/rebuild01.png)  |  ![Rebuild - After](/images/rebuild02.png)    |

### 5. Crop Cover
Use this tool to crop a cover that is front & back, to front only.

The wraparound cover is not deleted, but re-ordered in the CBZ to be the 2nd image.

| Before    | After |
| -------- | ------- |
|  ![Crop - Before](/images/crop01.png)  |  ![Crop - After](/images/crop02.png)    |
| Page Count: 63 | Page Count: 64 |

### 6. Remove First Image
Many older files will have a cover image that is not the cover of the comic.

Use this utility to remove the first image in the file.

| Before    | After |
| -------- | ------- |
|  ![Remove - Before](/images/remove01.png)  |  ![Remove - After](/images/remove02.png)    |
| Page Count: 23 | Page Count: 22 |

### 7. Add blank Image at End
Requested feature from Komga Discord. Corrects display issues with Manga files.

Adds a blank / empty PNG file (zzzz9999.png) to the archive.

### 8. Delete File
Utility to delete a single file and requires confirmation before performing the delete action.

![Delete File](/images/delete01.png)
![Delete Confirmation](/images/delete02.png)
