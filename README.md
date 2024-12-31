# Comic Library Utilities
This is a set of utilities that I developed while moving my 70,000+ comic library to <img src="https://komga.org/img/logo.svg" alt="Komga Logo" width="20"/> [Komga](https://komga.org/).

## Installation via Docker Compose (Portainer)

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
                - "D:/Comics:/data" #update this line to map to your library. If you are running Komga via Docker - this should be the same setting
            ports:
                - '5577:5577'
            environment:
                - FLASK_ENV=development

## Using the Utilities

In your browser, navigate to http://localhost:5577

You'll be presented with the main screen, where you can select which option you'd like to perform

![Main Menu](/images/example.png)

### 1. Rebuild Directory
Rebuilds all files in a directory. 

This utility does 2 things:
1. Convert all RAR / CBR files to CBZ
2. Rebuild all ZIP / CBZ files

I often see, in both newer and older files, that Komga doesn't recognize or scan them correctly. More often than not, rebuilding them corrects the error.
 
### 2. Rename - All Files in Diretory
Currently this functiuon does 3 things to all files in a directory and any sub-directories.

1. Removes everything in parentheses with the exepction of the 4-digit year (if available)
2. Removes `c2c`
3. Removes any extra spaces before the file entenstion

Mylar and ComicRack should be your first choice for performing these actions, but I wanted something I could easliy run on my manual downloads directory.

### 3. Single File - Rebuild/Convert
Running this will rebuild a CBZ file in an attempt to fix issues/errors with it not displaying correctly.
Additionally, this will also convert a single CBR file to a CBZ

| Before    | After |
| -------- | ------- |
|  ![Rebuild - Before](/images/rebuild01.png)  |  ![Rebuild - After](/images/rebuild02.png)    |

### 4. Crop Cover
Use this tool to crop a cover that is front & back, to front only.

The wraparound cover is not deleted, but re-ordered in the CBZ to be the 2nd image.

| Before    | After |
| -------- | ------- |
|  ![Crop - Before](/images/crop01.png)  |  ![Crop - After](/images/crop02.png)    |
| Page Count: 63 | Page Count: 64 |

### 5. Remove First Image
Many older files will have a cover image that is not the cover of the comic.

Use this utility to remove the first image in the file.

| Before    | After |
| -------- | ------- |
|  ![Remove - Before](/images/remove01.png)  |  ![Remove - After](/images/remove02.png)    |
| Page Count: 23 | Page Count: 22 |