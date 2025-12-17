---
description: How to get metadata and create ComicInfo.xml files
---

# Generate ComicInfo.xml

If you have enabled [app-settings-1](../app-settings-1/ "mention") you'll see an additional icon in the File Manager for searching your local GCD Database for Metadata.

{% hint style="warning" %}
While not as thorough as ComicVine, the GCD database offers a quick way to get metadata for a large collection.
{% endhint %}

### Get Metadata for all comics in a folder

To get metadata and generate a ComicInfo.xml file for all issues in a folder, simply click the ![](<../../.gitbook/assets/Screenshot 2025-10-07 090845.png>) GCD Download icon.

Depending on your folder structure, CLU will then search for the best match using:

* Series Name Year - using the year from the folder name, we attempt to match the series and year
* Series Name - if no exact match, we then search for the exact series name
* % Series Name % - if no match, we then search for the words in the series name

Here are two specific examples:

#### Series Name (Year)

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 091007.png" alt=""><figcaption></figcaption></figure>

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 091355.png" alt=""><figcaption></figcaption></figure>

Searching for _'H.A.R.D. Corps (1992)'_ finds an exact match and applies metadata. Any issues with existing metadata will be skipped.

#### Series Name \ v(Year)

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 090950.png" alt=""><figcaption></figcaption></figure>

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 091418.png" alt=""><figcaption></figcaption></figure>

Similarly, when multiple volumes are present, CLU will grab the 'YEAR' from the folder and append it to the parent folder, so this search was for _'Archer & Armstrong (1992)'_ and we found an exact match.

Searching for _'Archer & Armstrong (2012)'_ however, does not find an exact match, so we're presented with a list of possible matches. From that we can see a series from (2013-2015) with 10 Issues, so we select this for matching.

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 091938.png" alt=""><figcaption></figcaption></figure>

### Get metadata for a single issue

Getting details for single issues works similar to the directory search. The search order is:

* Series Name Year Issue Number - we attempt to find an example match an apply the results
* Series Name Year - If no exact match, we search for Series Name and year and llok for the issue
* % Series Name % - If that fails, we search for series words, return the results and then match the issue from the user selection

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 093407.png" alt=""><figcaption></figcaption></figure>

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 093426.png" alt=""><figcaption></figcaption></figure>

Selecting the (2016-2017) series will match and generate the data.

Issue count currently counts alternate covers and reprints, so some issues counts (like this) are exaggerated.&#x20;

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 093337.png" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}
Usage of this feature requires a copy of the GCD database running on a local mySQL server. You can use your own setup and follow the [integrations-1-1.md](../app-settings-1/integrations-1-1.md "mention") or follow the guide on how to setup [app-settings-1](../app-settings-1/ "mention")
{% endhint %}
