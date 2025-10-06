---
description: Quick install using Docker
icon: bullseye-arrow
---

# Quickstart

Docker Hub images are available for quick installation and deploy.

### Install via Docker Compose

{% hint style="info" %}
{% code lineNumbers="true" %}
```yaml
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
```
{% endcode %}
{% endhint %}

### Install via Docker

```
docker run \
  --name clu \
  --restart always \
  -p 5577:5577 \
  -v /Users/phillipduncan/Documents/docker/clu:/config \
  -v /Users/phillipduncan/Documents/GitHub/comic-utils/bak:/data \
  -v /Users/phillipduncan/Documents/GitHub/comic-utils/files:/downloads \
  -e FLASK_ENV=development \
  -e MONITOR=no \
  -e PUID=99 \
  -e PGID=100 \
  -e UMASK=022 \
  allaboutduncan/comic-utils-web:latest
```

### Parameters

text

<table><thead><tr><th>Parameter</th><th>Function</th></tr></thead><tbody><tr><td><pre><code>-p 5577:5577
</code></pre></td><td>The port exposed by the app for the web interface.</td></tr><tr><td><pre><code>-v /docker/clu:/config
</code></pre></td><td>Location for your CLU directory on a local disk. Enables local storage of the <code>config.ini</code> which preservers settings during updates. Must be mapped to <code>/config</code></td></tr><tr><td><pre><code>-v /User/comics:/data
</code></pre></td><td>Location of your library to manage. Must be mapped to <code>/data</code></td></tr><tr><td><pre><code>-v /User/downloads:/downloads
</code></pre></td><td>Optional folder to configure if MONITOR is enabled (see below)</td></tr><tr><td><pre><code>-e FLASK_ENV=development
</code></pre></td><td></td></tr><tr><td><pre><code>-e MONITOR=no
</code></pre></td><td>If set to <code>yes</code> <a href="../features/folder-monitoring/">folder monitoring</a> will be enabled</td></tr><tr><td></td><td></td></tr></tbody></table>



### Paths

