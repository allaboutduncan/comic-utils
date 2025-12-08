---
description: Perform edits to a CBZ File
---

# Edit CBZ File

<figure><img src="../../.gitbook/assets/Screenshot 2025-05-28 121745.png" alt=""><figcaption><p>Edit a CBZ File</p></figcaption></figure>

This feature, new in v3.0, allows you to open a CBZ file in the browser and rename/rearrange files, crop images, and delete images.

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 102235.png" alt=""><figcaption></figcaption></figure>

Once you select a CBZ file and click "Edit CBZ"  you will see a loading icon while the CBZ is extracted to a temp directory. Once this is complete, all of the image files will be displayed in the UI.

In the example above we can see the first 6 files in the CBZ. From this UI you can:

### Rename / Reorder Files

Clicking a file name will allow you to edit the file name. Press `ENTER` to save the changes. Once a file name has been edited, it will re-order in the UI based on alpha/number ordering.

In the image below, you can see that updating the filename to `00b.jpg` has re-ordered the file to appear in the CBZ after `00a.jpg`

<figure><img src="../../.gitbook/assets/Screenshot 2025-05-28 122227.png" alt=""><figcaption><p>Renamed Example</p></figcaption></figure>

### Crop Images

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 101721.png" alt=""><figcaption></figcaption></figure>

This is a more precise implementation of the [Crop Cover](editor-2.md) feature that allows you to select which piece of the cover you wish to save as the main cover image. If you had a double-cover, clicking the `<-| Left` button would crop and save the back as the new cover. The original image is never deleted. It is renamed and saved in the CBZ.

Clicking the `Right |->` button will crop the image to the right half and save that as the cover image.

Clicking the `Middle` button is used for Tri-fold covers where the main image is in the middle.

`Free Crop` ![](<../../.gitbook/assets/Screenshot 2025-10-07 102418.png>) lets you click and drag to free-form crop an image in the UI. Click to draw your area, SPACE to move the area and SHIFT to maintain typical comic page proportions.

<figure><img src="/broken/files/Y7Sk1ON4QDgF6ENfVLPH" alt=""><figcaption></figcaption></figure>

<figure><img src="../../.gitbook/assets/Screenshot 2025-10-07 101954.png" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}
All Crop actions backup/preserve the original file in the CBZ and it should be instantly viewable. If you mistakenly crop something - just delete the file and start over.
{% endhint %}

### Delete Files

Clicking the Trash Can icon will delete the file from the CBZ. This is useful for removing extra images not related to the comic.

<figure><img src="../../.gitbook/assets/Screenshot 2025-05-28 122248.png" alt=""><figcaption><p>Deleting this file</p></figcaption></figure>

{% hint style="danger" %}
**Warning:** The file/image is deleted immediately and this is not reversible.
{% endhint %}

### Save Changes

Once you have completed all actions on the file, click `Save` to re-pack the file and remove all temp files.

There is no cancel button - as cancelling would still need to re-pack the files.

### Skipped & Deleted File Types

In [Settings](../app-settings/integrations-1.md), you can configure certain file types to be automatically deleted whenever a CBZ is unpacked. For example, I have my setup configured to auto-delete the following types of files when they are found `.nfo,.sfv,.db,.DS_Store`

Additionally, you can configure file types to be skipped as well. This ensures that files like `.xml` are re-packed when the editing is completed.
