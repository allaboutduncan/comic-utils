# Comic Library Utilities (CLU)

![Docker Pulls](https://img.shields.io/docker/pulls/allaboutduncan/comic-utils-web)
![GitHub Release](https://img.shields.io/github/v/release/allaboutduncan/comic-utils)
![GitHub commits since latest release](https://img.shields.io/github/commits-since/allaboutduncan/comic-utils/latest)

[![Join our Discord](https://img.shields.io/discord/678794935368941569?label=CLU%20Discord&logo=discord&style=for-the-badge)](https://discord.com/channels/678794935368941569/1349564970609938553)


![Comic Library Utilities (CLU)](images/clu-logo-360.png "Comic Library Utilities")

## What is CLU & Why Does it Exist

This is a set of utilities I developed while moving my 70,000+ comic library to [Komga](https://komga.org/).

As I've continued to work on it, add features and discuss with other users, I wanted to pivot away from usage as an accessory to Komga and focus on it as a stand-alone app.

The app is intended to allow users to manage their remote comic collections, performing many actions in bulk, without having direct access to the server. You can convert, rename, move, enhance CBZ files within the app.

![Comic Library Utilities (CLU)](/images/home_v1.png "Comic Library Utilities Homepage")

### Full Documentation
With the 2.0 release, full documention and install steps have [moved to Gitbook.io](https://phillips-organization-6.gitbook.io/clu-comic-library-utilities/)

## Features
Here's a quick list of features

### Directory Options
1. Rename - All Files in Diretory
2. Convert Directory (CBR / RAR Only)
3. Rebuild Directory - Rebuild All Files in Diretory
4. Convert PDF to CBZ
5. Missing File Check
6. Enhance Images (__New in 2.0__)
7. Clean / Update ComicInfo.xml

### Single File Options
1. Single File - Rebuild/Convert
2. Crop Cover
3. Remove First Image
4. Add blank Image at End
5. Enhance Images (__New in 2.0__)
6. Delete File

### File Management (New in 2.0)
1. _Source_ and _Destination_ file browsing
2. Drag and drop to move directories and files
3. Rename directories and files
4. Delete directories or files

### Folder Monitoring

1. __Auto-Renaming:__ Based on the manually triggered renaming, this option will monitor the configured folder.
2. __Auto-Convert to CBZ:__ If this is enabled, files that are not CBZ will be converted to CBZ when they are moved to the `/downloads/processed` location
3. __Processing Sub-Directories:__ If this is enabled, the app will monitor and perform all functions on any sub-directory within the *default monitoring location*. 
4. __Auto-Upack:__ If enabled, app will extract contents of ZIP files when download complete (__New in 2.0__)
5. __Move Sub-Directories:__ If enabled, when processing files in sub-directories, the sub-directory name will be cleaned and moved (__New in 2.0__)

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

## Say Thanks
If you enjoyed this, want to say thanks or want to encourage updates and enhancements, feel free to [!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/allaboutduncan)

### Full Documentation
With the 2.0 release, full documention and install steps have [moved to Gitbook.io](https://phillips-organization-6.gitbook.io/clu-comic-library-utilities/)