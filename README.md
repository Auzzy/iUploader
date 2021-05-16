A script for uploading music to [iBroadcast](https://www.ibroadcast.com/). This intends to augment the official uploader script with some features I found myself wanting.

## Usage
> ```iuploader.py <login token> [-d DIRECTORY]... [-t TAG]... [-p PLAYLIST]... [--no-parallel] [--no-skip-duplicates]```

**login_token**

Your app login token. Get it by enabling this app on the Apps page in iBroadcast.

**-d**, **--directory=DIRECTORY**

Where to search for files to upload. Repeat this argument to search in multiple directories. Defaults to the current directory.

**-t**, **--tag=TAG**

Tag all discovered tracks. Creates the tag if needed. Repeat this argument to apply multiple tags.

**-p**, **--playlist=PLAYLIST**

Add all discovered tracks to a playlist. Repeat this argument for multiple playlists.

**--no-parallel**

Disable simultaneous uploads. Default count depends on how many cores your machine has.

**--no-skip-duplicates**

Disables skipping duplicate files. Duplicates are deterined by checking the file contents; name and location don't matter. By default, a duplicate file isn't uploaded again.
