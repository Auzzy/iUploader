#!/usr/bin/env python

import argparse
import collections
import glob
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor as PoolExecutor, as_completed

import requests


DEBUG = os.environ.get("DEBUG", "false").lower() in ("true", "yes", "y")
if not DEBUG:
    sys.tracebacklimit = 0


API_URL = "https://api.ibroadcast.com/"
LIBRARY_URL = "https://library.ibroadcast.com/"
UPLOAD_URL = "https://upload.ibroadcast.com/"

NAME = "iUploader"
VERSION = "0.1"
APP_ID = 1014

USER_AGENT = f"{NAME} {VERSION}"
CLIENT = NAME
BASE_API_PAYLOAD = {
    "app_id": APP_ID,
    "version": VERSION,
    "client": CLIENT,
    "device_name": NAME,
    "user_agent": USER_AGENT
}

TRACK_ID_RE = re.compile("File .* \((?P<trackid>\d+)\) uploaded successfully and is being processed.")


class IBroadcastClient:
    def __init__(self, login_token):
        self.login_token = login_token

        self.user_id = None
        self.token = None

    def _request(self, url, data, encode_data=lambda val: val, *, check_result=True, **req_args):
        # provide the auth parameters if they"re set.
        if self.user_id:
            data["user_id"] = self.user_id
        if self.token:
            data["token"] = self.token

        headers = {**req_args.pop("headers", {}), "User-Agent": USER_AGENT}

        response = requests.post(url, data=encode_data(data), headers=headers, **req_args)
        response.raise_for_status()

        response_json = response.json()
        if check_result:
            if "result" not in response_json:
                raise KeyError("\"result\" key not found in the response. Please contact the owner of this script, "
                        "as this may indicate it needs to be updated.")
            elif not response_json["result"]:
                raise ValueError(f"The server failed to perform the desired action. Details: {response_json}.")

        return response_json

    def api_request(self, mode, *, check_result=True, **data):
        if mode == "library":
            return self.library_request(**data)

        post_json = {
            "mode": mode,
            **data,
            **BASE_API_PAYLOAD
        }
        return self._request(API_URL, post_json, encode_data=json.dumps, check_result=check_result)

    def library_request(self, *, check_result=True, **data):
        return self._request(LIBRARY_URL, data, encode_data=json.dumps, check_result=check_result)

    def upload_request(self, *, files={}, check_result=True, **data):
        return self._request(UPLOAD_URL, data, files=files, check_result=check_result)


    def login(self):
        jsoned = self.api_request("login_token", login_token=self.login_token, type="account")

        if "user" not in jsoned:
            raise ValueError(jsoned["message"])

        self.user_id = jsoned["user"]["id"]
        self.token = jsoned["user"]["token"]
    
    def supported_filetypes(self):
        jsoned = self.api_request("status", supported_types=1)
        if "user" not in jsoned:
            raise ValueError(jsoned["message"])

        print("Account info fetched")

        return {filetype["extension"] for filetype in jsoned["supported"]}



class Uploader:
    def __init__(self, login_token):
        self.client = IBroadcastClient(login_token)

    def process(self, parent_dirs=[], tag_names=[], playlist_names=[], skip_duplicates=True, parallel=True):
        try:
            self.client.login()
        except ValueError as e:
            print("Login failed: %s" % e)
            return

        try:
            filetypes = self.client.supported_filetypes()
        except ValueError as e:
            print("Unable to fetch account info: %s" % e)
            return

        files = self.discover_files(parent_dirs, filetypes)
        if self.confirm(files):
            library_info = self.load_library_info(tag_names, playlist_names)
            self.upload(files, library_info, skip_duplicates, parallel)

    def discover_files(self, root_directories, filetypes):
        files = set()
        for root_directory in root_directories:
            for dirpath, _, filenames in os.walk(os.path.abspath(root_directory)):
                for filename in filenames:
                    if os.path.splitext(filename)[1] in filetypes:
                        files.add(os.path.join(dirpath, filename))
        return files

    def confirm(self, files):
        """
        Presents a dialog for the user to either list all files, or just upload.
        """
        print(f"Found {len(files)} files. Press \"L\" to list, or \"U\" to "
            "start the upload.")
        response = input("--> ")

        print()
        if response.lower() == "l":
            print("Listing found, supported files")
            for filename in sorted(files):
                print(f" - {filename}")
            print()
            print("Press \"U\" to start the upload if this looks reasonable.")
            response = input("--> ")
        if response.lower() == "u":
            print("Starting upload.")
            return True

        print("Aborting")
        return False

    def load_library_info(self, tag_names, playlist_names):
        library = self.client.library_request()["library"]
        return {
            "tags": self.load_tags(library, tag_names),
            "playlists": self.load_playlists(library, playlist_names)
        }

    def load_tags(self, library, names):
        # Tags have their ID as the key, and the name inside. So we need to
        # iterate over all of them, checking whose names are in the requested
        # list, and collection those IDs.
        tags = {}
        missing_tags = set(names)
        for tag_id, info in library["tags"].items():
            if info["name"] in names:
                tags[info["name"]] = tag_id
                missing_tags.remove(info["name"])

        # If any of the requested tag names were not found, we create them, and
        # add their ID to the list.
        for tag_name in missing_tags:
            tags[tag_name] = self.client.api_request("createtag", tagname=tag_name)["id"]

        return tags

    def load_playlists(self, library, names):
        # Playlists have their ID as the key, and the rest of their info
        # presented as a list. The keys for this list come from another field.
        # So we need to iterate over all of them, perform this mapping, then
        # check whose names are in the requested list, and collect those IDs.
        playlists_dict = library["playlists"].copy()
        field_map = playlists_dict.pop("map")
        fields = sorted(field_map.keys(), key=lambda key: field_map[key])

        playlists = {}
        missing_playlists = set(names)
        for playlist_id, info_list in playlists_dict.items():
            info = dict(zip(fields, info_list))
            if info["name"] in names:
                playlists[info["name"]] = playlist_id
                missing_playlists.remove(info["name"])

        # If any of the requested playlist names were not found, we create them, and
        # add their ID to the list.
        for name in missing_playlists:
            playlists[name] = self.client.api_request("createplaylist", name=name)["playlist_id"]

        return playlists

    def calc_md5(self, filepath):
        # Read the file in chunks, to avoid loading it into memory all at once.
        md5 = hashlib.md5()
        with open(filepath, "rb") as fileobj:
            while True:
                data = fileobj.read(8192)
                if not data:
                    break
                md5.update(data)
        return md5.hexdigest()

    def upload(self, files, library_info, skip_duplicates=True, parallel=True):
        """
        Go and perform an upload of any files that haven"t yet been uploaded
        """
        if skip_duplicates:
            print("Any duplicates will be skipped and listed at the end.")

        library = self.client.upload_request()["md5"] if skip_duplicates else None

        # For now at least, parallel uploads are all or nothing: either the
        # default max workers are used, or one is used.
        max_workers = None if parallel else 1
        with PoolExecutor(max_workers=max_workers) as executor:
            promises = [executor.submit(self._upload_worker, filepath, library_info, library) for filepath in sorted(files)]

            start = time.time()
            results = collections.defaultdict(list)
            for promise in as_completed(promises):
                retval = promise.result()
                results[retval["result"]].append(retval["info"])
            end = time.time()

        if results["skipped"]:
            sorted_skipped = sorted(results["skipped"], key=lambda val: val["path"])
            skipped_lines = [f"- {info['path']}" for info in sorted_skipped]
            print("\nSkipped tracks:", *skipped_lines, sep="\n")

        if results["error"]:
            error_lines = []
            for info in sorted(results["error"], key=lambda val: val["path"]):
                error_lines.append(f"- {info['path']}")
                error_lines.append(f"  Error: {info['summary']}")
                if DEBUG:
                    error_lines.append(f"  Debug info: {info['debug']}")
            print("\nFailed to upload:", *error_lines, sep="\n")

        print(f"\nTotal uploaded: {len(results['uploaded'])}")
        if skip_duplicates:
            print(f"Total skipped: {len(results['skipped'])}")
        if results["error"]:
            print(f"Total failed: {len(results['error'])}")
        print(f"Total time: {int(end - start)} seconds")

        return results

    def _upload_worker(self, filepath, library_info, library):
        def _err_result(summary, **extra):
            exc_info = sys.exc_info()

            if all(exc_info):
                debug_details = {
                    "traceback": "".join(traceback.format_exception(*exc_info)),
                    "message": str(exc_info[1])
                }
            else:
                debug_details = {
                    "traceback": "".join(traceback.format_stack())
                }

            return {"result": "error", "info": {
                "path": filepath, "summary": summary, "debug": {**debug_details, **extra}}}

        # library is None if we shouldn't check for duplicates.
        if library is not None and self.calc_md5(filepath) in library:
            return {"result": "skipped", "info": {"path": filepath}}

        print(f"[{int(time.time())}] Uploading {filepath}...")
        try:
            with open(filepath, "rb") as upload_file:
                jsoned = self.client.upload_request(
                    file_path=filepath,
                    method=CLIENT,
                    files={"file": upload_file},
                    check_result=False)
        except Exception:
            return _err_result("File upload request error.")

        result = jsoned["result"]
        if not result:
            return _err_result("File upload failed.", response=jsoned)

        # Extracting the ID of the uploaded track.
        match = TRACK_ID_RE.match(jsoned["message"])
        if not match:
            return _err_result("Unexpected message format. Maybe it's changed?",
                    regex=TRACK_ID_RE.pattern, response=jsoned)

        track_id = int(match.group("trackid"))

        # Tagging the track. Immediately tagging ensures a script failure
        # will minimize the untagged tracks.
        # The tradeoff is it takes a LOT more requests, so more time and
        # server load. Best would be for the API to support tagging as part
        # of the upload request.
        # Note: Looks like it does, but only a single tag. So not useful here.
        for name, id_ in library_info["tags"].items():
            try:
                jsoned = self.client.api_request("tagtracks", tagid=id_, tracks=[track_id], check_result=False)
                if not jsoned["result"]:
                    return _err_result("Failed to apply tag.", tag=name, tag_id=id_)
            except Exception:
                return _err_result("Tag track request error.", tag=name, tag_id=id_)

        # Adding the track to playlist(s). The same caveats apply as above,
        # including that the upload endpoint accepts a single playlist name.
        for name, id_ in library_info["playlists"].items():
            try:
                jsoned = self.client.api_request("appendplaylist", playlist=id_, tracks=[track_id], check_result=False)
                if not jsoned["result"]:
                    return _err_result("Failed to add to playlist.", playlist=name, playlist_id=id_)
            except Exception:
                return _err_result("Add to playlist request error.", playlist=name, playlist_id=id_)

        print(f"[{int(time.time())}] Finished {filepath} ({track_id})")

        return {"result": "uploaded", "info": {"path": filepath, "id": track_id}}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("login_token",
            help=("Your login token. If you don't already have one, visit "
            "https://ibroadcast.com, log in, click the \"Apps\" button in the "
            "side menu, and enable this app."))
    parser.add_argument("-d", "--directory", action="append", type=pathlib.Path, default=[os.getcwd()], dest="dirs",
            help=("Directory in which to search for music files. Repeat to "
            "search in multiple directories. Default: %(default)s"))
    parser.add_argument("-t", "--tag", action="append", dest="tags", default=[],
            help=("Apply this tag all discovered files after uploading, and "
            "create it if needed. Repeat this argument for multiple tags."))
    parser.add_argument("-p", "--playlist", action="append", dest="playlists", default=[],
            help=("Add all all discovered files to this playlist, and create it "
            "if needed. Repeat this argument for multiple playlists."))
    parser.add_argument("--no-parallel", action="store_false", dest="parallel",
            help="Disable parallel uploads.")
    parser.add_argument("--no-skip-duplicates", action="store_false", dest="skip_duplicates",
            help=("Upload a file even when iBroadcast thinks it's already "
            "been uploaded."))

    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    uploader = Uploader(args.login_token)

    uploader.process(args.dirs, args.tags, args.playlists, args.skip_duplicates, args.parallel)
