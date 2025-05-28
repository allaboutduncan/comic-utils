---
description: Clean all filenames in the directory
icon: folder
---

# Clean All Filenames

<figure><img src="../../.gitbook/assets/Screenshot 2025-02-25 at 1.47.42 PM.png" alt=""><figcaption><p>Rename (Clean) Filenames</p></figcaption></figure>

Currently this function does 5 things to all files in a directory and any sub-directories.

1. Removes everything in parentheses with the exception of the 4-digit year (if available)
2. Removes `c2c`
3. Removes anything in brackets `[any text removed]` - along with the brackets
4. Removes any text / characters after "filename issue (year)"
5. Removes any extra spaces before the file extension

The pattern used for renaming is `{Series Name} {Issue Number} ({Year})`

[Mylar3](https://mylarcomics.com/) and [ComicRackCE](https://github.com/maforget/ComicRackCE) should be your first choice for performing these actions, but I wanted something I could easily run on my manual downloads directory or repair in a one-off method.

Oftentimes series archives or torrent files will have numerous naming patterns with information in parenthesis, brackets, before the year, after and all over the place. I continuously update these to handle as many as I encounter.

