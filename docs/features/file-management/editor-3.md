---
description: >-
  As you browse and manage your library, there are additional features that will
  help you manage your collection
---

# Additional Features

### Directory Filtering

For any directory that has more than more than 25 folders, you can now start typing to filter the list. This does not search within the directories, this just quickly narrows down your results.

![](<../../.gitbook/assets/Screenshot 2025-08-01 at 11.05.25 AM.png>)![](<../../.gitbook/assets/Screenshot 2025-08-01 at 11.05.37 AM.png>)

### Search

Search your Comic folders to see if you have a particular issue or to see if you want to look for a better file. Search performance will be dependent on your library size until the cache is built.

<figure><img src="/broken/files/mImjNrmDMjGAXw9MSQHn" alt=""><figcaption></figcaption></figure>

When you start the app, a Search Cache will be built. Timing on this will be dependent on your library. During testing, a library with 100,000 files indexed in about 6-minutes. This will speed up search results.\
\
Search will also run in real-time, but be warned, if you have a large library, it will likely time out before returning any valid results. Additionally, anytime you move files to this directory, the cache will be invalidated.

See the Cache Management section on [app-settings](../app-settings/ "mention") for more details

### New Files

Clicking the "New Files" button will attempt to show any new files added within the last 7 days. This is useful for seeing new downloads. This searches you entire '/data' library, but ignores the 'TARGET' folder if you are using [folder-monitoring](../folder-monitoring/ "mention")

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-21 092519.png" alt=""><figcaption></figcaption></figure>

{% hint style="warning" %}
The "New Files" feature is limited to 500 files or 30-seconds. Large libraries will timeout attempting to search all files.
{% endhint %}

### File & Folder Sizes

When viewing folders, you can click the _**info icon**_ and CLU will scan the folder and return the size and file count. This is done on request, as providing that data in the list would increase load time significantly.&#x20;

<figure><img src="../../.gitbook/assets/Screenshot 2025-08-01 at 11.05.50 AM.png" alt=""><figcaption></figcaption></figure>

When browsing files, you'll also see file size next to each file. This allows you compare two files, allowing you to see if a newer file was larger / better quality than an existing file.

<figure><img src="../../.gitbook/assets/Screenshot 2025-08-01 at 11.31.34 AM.png" alt=""><figcaption></figcaption></figure>

### View Cover, Meta-Data and ComicInfo.xml

Additionally, you'll see a new **info icon** next to all comics as well. Clicking this will open a modal window and the cover, meta-data and ComicInfo.xml data (if available) will be displayed.

Using the `<— Prev` and `Next -->` buttons, you can quickly view details for multiple issues.

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 103714.png" alt=""><figcaption></figcaption></figure>

<figure><img src="../../.gitbook/assets/Screenshot 2025-08-01 at 11.16.42 AM.png" alt=""><figcaption></figcaption></figure>

For any comic that has a valid 'ComicInfo.xml' file, you'll also see a 'red eraser' icon that will let you clear the comic info for that file. This is useful if the metadata is incorrect or incomplete.

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-21 093056.png" alt=""><figcaption></figcaption></figure>

After clicking the icon, you'll be asked to confirm you want to delete the metadata. Clicking Yes, will unpack the file, delete the XML and re-pack the file.
