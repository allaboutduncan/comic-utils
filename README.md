# Comic Library Utilities
This is a set of utilities that I developed while moving my 70,000+ comic library to Komga.

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

### Rebuild Directory
Text
 
### Rename - All Files in Diretory
Text

### Single File - Rebuild/Convert
Running this will rebuild a CBZ file in an attempt to fix issues/errors with it not displaying correctly.
Additionally, this will also convert a single CBR file to a CBZ

| Before    | After |
| -------- | ------- |
|  ![Rebuild - Before](/images/rebuild01.png)  |  ![Rebuild - After](/images/rebuild02.png)    |

### Crop Cover
Text

| Before    | After |
| -------- | ------- |
|  ![Rebuild - Before](/images/rebuild01.png)  |  ![Rebuild - After](/images/rebuild02.png)    |

### Remove First Image
Many older files will have a cover image that is not the cover of the comic.

Use this utility to remove the first image in the file.

| Before    | After |
| -------- | ------- |
|  ![Remove - Before](/images/remove01.png)  |  ![Remove - After](/images/remove02.png)    |