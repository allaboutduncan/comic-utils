---
description: Update your compose file as detailed below to use the local GCD data
icon: square-sliders
---

# Updated CLU Settings for GCD

Once your GCD database has completed the import, you'll need to update your CLU Docker Compose to use the same network in Docker and connect to your MySQL server.

```
version: '3.9'
services:
    comic-utils:
        image: allaboutduncan/comic-utils-web:latest

        container_name: comic-utils
        logging:
            driver: "json-file"
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
            ## Additional folder if you want to use Folder Monitoring.
            - 'F:/downloads:/downloads'
        environment:
            - FLASK_ENV=development
            ## Set to 'yes' if you want to use folder monitoring.
            - MONITOR=yes/no
            ## Set the User ID (PUID) and Group ID (PGID) for the container.
            ## This is often needed to resolve permission issues, especially on systems like Unraid
            ## where a specific user/group owns the files.
            ## For Unraid, PUID is typically 99 (user 'nobody') and PGID is typically 100 (group 'users').
            ## For Windows/WSL, you need to set these to match your Windows user ID (see WINDOWS_WSL_SETUP.md)
            # - PUID=99
            # - PGID=100
            ## Set the file creation mask (UMASK). 022 is a common value.
            # - UMASK=022
            
            # GCD Database Additions: Uncomment everything below and update the
            # GCD_MYSQL_PASSWORD if you're using a local GCD database for Metadata
            #- GCD_MYSQL_HOST=mysql-gcd
            #- GCD_MYSQL_PORT=3306
            #- GCD_MYSQL_DATABASE=gcd_data
            #- GCD_MYSQL_USER=clu
            #- GCD_MYSQL_PASSWORD=my-password-setup-previous-step
        #networks:
        #    - gcd-network

#volumes:
#  config-volume:

#networks:
#  gcd-network:
#    external: true            
```

Once you restart your CLU container, you should see something like this in the logs

```
172.21.0.1 - - [07/Oct/2025 08:41:20] "GET /gcd-mysql-status HTTP/1.1" 200 -
```

and in the UI when browsing files, you should see a Cloud Download icon like this

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 085747.png" alt=""><figcaption></figcaption></figure>

For additional details on usage, see the [editor-2-1.md](../file-management/editor-2-1.md "mention") section
