---
description: >-
  Release v3.4 Supports Connecting to a Local Instance of the GCD Database for
  getting Metadata
---

# GCD Database Support

Getting Metadata and building ComicInfo.xml files for a large collection using the ComicVine API is time consuming to say the least. In an effort to update large collections in short amount of time, support has been added to connect to a local MySQL database running an export of the Grand Comics Database (GCD).

Follow the steps on the next few pages to get a GCD Database dump setup on your Docker - Portainer instances

{% hint style="warning" %}
**Note:** This will be a snapshot in time, so it will not contain data for any new comics released after installation. For our purposes, this is fine as we are looking to bulk update comics prior to the current year.
{% endhint %}

