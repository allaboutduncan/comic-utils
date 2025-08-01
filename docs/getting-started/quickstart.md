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

        restart: always
        ports:
            - '5577:5577'
        volumes:
            - '/path/to/local/config:/config' # Maps local folder to container
            ## update the line below to map to your library.
            ## Your library MUST be mapped to '/data' for the app to work
            - 'D:/Comics:/data'
            ## Additional folder if you want to use Folder Monitoring.
            - 'F:/downloads:/temp'
        environment:
            - FLASK_ENV=development
            ## Set to 'yes' if you want to use folder monitoring.
            - MONITOR=yes/no 
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

