# Comic Library Utilities
This is a set of utilities that I developed while moving my 70,000+ comic library to Komga.

## Installation via Docker Compose (Portainer)

Copy the following and edit the environment variables

    version: '3.9'
    services:
        notion-books:
            image: allaboutduncan/notion-isbn:latest
            container_name: notion-books
            logging:
                options:
                    max-size: 1g
            restart: always
            volumes:
                - '/var/run/docker.sock:/tmp/docker.sock:ro'
            ports:
                - '3331:3331'
            environment:
                - AWS_ACCESS_KEY_ID=ENTER-YOUR-ACCESS-KEY-HERE
                - AWS_SECRET_ACCESS_KEY=ENTER-YOUR-SECRET-KEY-HERE
                - AWS_BUCKET=bucket-name
                - NOTION_TOKEN=notion_secret
                - NOTION_DATABASE_ID=notion-database-id
                - GoogleAPIKey=Google-Books-API-Key
                - USE_PUSHOVER=yes/no
                - PO_TOKEN=pushover-app-API-key
                - PO_USER=pushover_user_key
                - USE_PUSHBULLET=yes/no
                - PB_TOKEN=pushbullet_access_token

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