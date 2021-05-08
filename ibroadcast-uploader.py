#!/usr/bin/env python

import argparse
import glob
import hashlib
import json
import os
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor as PoolExecutor, as_completed

import requests


if not os.environ.get("DEBUG", False):
    sys.tracebacklimit = 0


API_URL = "https://api.ibroadcast.com/s/JSON/"
UPLOAD_URL = "https://upload.ibroadcast.com/"

USER_AGENT = "python 3 uploader script 0.3"
CLIENT = "python 3 uploader script"
BASE_API_PAYLOAD = {
    "app_id": 1007,
    "version": "0.3",
    "client": CLIENT,
    "device_name": "python 3 uploader script",
    "user_agent": USER_AGENT
}

TRACK_ID_RE_FORMAT = "File {} \((?P<trackid>\d+)\) uploaded successfully and is being processed."


class ServerError(Exception):
    pass

class Uploader(object):
    """
    Class for uploading content to iBroadcast.
    """

    def __init__(self, login_token):
        self.login_token = login_token

        # Initialise our variables that each function will set.
        self.user_id = None
        self.token = None

    def process(self, parent_dirs=[], tag_names=[], skip_duplicates=True, parallel=True):
        try:
            self.login()
        except ValueError as e:
            print("Login failed: %s" % e)
            return

        try:
            filetypes = self.get_supported_filetypes()
        except ValueError as e:
            print("Unable to fetch account info: %s" % e)
            return

        files = set()
        for parent_dir in parent_dirs:
            files.update(self.load_files(parent_dir, filetypes))

        if self.confirm(files):
            tag_ids = self.load_tag_ids(*tag_names)
            self.upload(files, tag_ids, skip_duplicates, parallel)

    def _request(self, url, data, encode_data=lambda val: val, **req_args):
        # provide the auth parameters if they"re set.
        if self.user_id:
            data["user_id"] = self.user_id
        if self.token:
            data["token"] = self.token

        headers = {**req_args.pop("headers", {}), "User-Agent": USER_AGENT}

        response = requests.post(url, data=encode_data(data), headers=headers, **req_args)

        if not response.ok:
            raise ServerError("Server returned bad status: ", response.status_code)

        return response.json()

    def _api_request(self, mode, **data):
        post_json = {
            "mode": mode,
            **data,
            **BASE_API_PAYLOAD
        }
        return self._request(API_URL, post_json, json.dumps)

    def _upload_request(self, *, files={}, **data):
        return self._request(UPLOAD_URL, data, files=files)

    def login(self, login_token=None):
        # Default to passed in values, but fallback to initial data.
        login_token = login_token or self.login_token

        print("Logging in...")
        jsoned = self._api_request("login_token", login_token=login_token, type="account")

        if "user" not in jsoned:
            raise ValueError(jsoned.message)

        print("Login successful - user_id: ", jsoned["user"]["id"])
        self.user_id = jsoned["user"]["id"]
        self.token = jsoned["user"]["token"]

    def get_supported_filetypes(self):
        jsoned = self._api_request("status", supported_types=1)
        if "user" not in jsoned:
            raise ValueError(jsoned.message)

        print("Account info fetched")

        return {filetype["extension"] for filetype in jsoned["supported"]}

    def load_files(self, directory, filetypes):
        if filetypes is None:
            raise ValueError("Supported not yet set - have you logged in yet?")

        files = set()
        for full_filename in glob.glob(os.path.join(directory, "*")):
            filename = os.path.basename(full_filename)
            # Skip hidden files.
            if filename.startswith("."):
                continue

            # Make sure it"s a supported extension.
            dummy, ext = os.path.splitext(full_filename)
            if ext in filetypes:
                files.add(full_filename)

            # Recurse into subdirectories.
            # XXX Symlinks may cause... issues.
            if os.path.isdir(full_filename):
                files.update(self.load_files(full_filename, filetypes))

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
            print("Press "U" to start the upload if this looks reasonable.")
            response = input("--> ")
        if response.lower() == "u":
            print("Starting upload.")
            return True

        print("Aborting")
        return False

    def load_tag_ids(self, *tag_names):
        tags_info = self._api_request("library")["library"]["tags"]

        # Tags have their ID as the key, and the name inside. So we need to
        # iterate over all of them, checking whose names are in the requested
        # list, and collection those IDs.
        tag_ids = set()
        missing_tags = set(tag_names)
        for tag_id, info in tags_info.items():
            if info["name"] in tag_names:
                tag_ids.add(tag_id)
                missing_tags.remove(info["name"])

        # If any of the requested tag names were not found, we create them, and
        # add their ID to the list.
        for tag_name in missing_tags:
            tag_ids.add(self._api_request("createtag", tagname=tag_name)["id"])

        return tag_ids

    def calcmd5(self, filePath="."):
        with open(filePath, "rb") as fh:
            m = hashlib.md5()
            while True:
                data = fh.read(8192)
                if not data:
                    break
                m.update(data)
        return m.hexdigest()

    def upload(self, files, tag_ids=[], skip_duplicates=True, parallel=True):
        """
        Go and perform an upload of any files that haven"t yet been uploaded
        """
        def _upload_worker(filepath):
            print(f"Uploading {filepath}...")

            with open(filepath, "rb") as upload_file:
                jsoned = self._upload_request(
                    file_path=filepath,
                    method=CLIENT,
                    files={"file": upload_file})

            result = jsoned["result"]

            if result is False:
                raise ValueError("File upload failed.")

            # Extracting the ID of the uploaded track.
            track_id_re = TRACK_ID_RE_FORMAT.format(os.path.basename(filepath))
            match = re.match(track_id_re, jsoned["message"])
            if not match:
                raise ValueError(f"Unexpected message format. Maybe it's changed? '{jsoned['message']}'")

            track_id = int(match.group("trackid"))

            # Tagging the track. Immediately tagging ensures a script failure
            # will leave at most one untagged track.
            # The tradeoff is it takes a LOT more requests, so more time and
            # server load. Best would be for the API to support tagging as part
            # of the upload request.
            for tag_id in tag_ids:
                self._api_request("tagtracks", tagid=tag_id, tracks=[track_id])

            print(f"Finished {filepath} ({track_id})")

            return track_id

        if skip_duplicates:
            library_md5s = self._upload_request()["md5"]

        # For now at least, parallel uploads are all or nothing: either the
        # default max workers are used, or one is used.
        max_workers = None if parallel else 1
        with PoolExecutor(max_workers=max_workers) as executor:
            promises = []
            uploaded_track_ids = set()
            for filepath in sorted(files):
                if skip_duplicates:
                    # Get an md5 of the file contents and compare it to whats up
                    # there already
                    file_md5 = self.calcmd5(filepath)
                    if file_md5 in library_md5s:
                        print(f"Skipping {filepath} - already uploaded.")
                        continue

                promises.append(executor.submit(_upload_worker, filepath))

            for promise in as_completed(promises):
                track_id = promise.result()
                uploaded_track_ids.add(track_id)

        print("Done")

        return uploaded_track_ids


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("login_token",
            help=("Your login token. If you don't already have one, visit "
            "https://ibroadcast.com, log into your account, click the \"Apps\" "
            "button in the side menu, and enable the app."))
    parser.add_argument("-d", "--directory", action="append", type=pathlib.Path,
            default=[os.getcwd()], dest="directories",
            help=("Directory in which to search for music files. Repeat to "
            "search in multiple directories. Default: %(default)s"))
    parser.add_argument("-t", "--tag", action="append", dest="tags")
    parser.add_argument("--no-parallel", action="store_false", dest="parallel",
            help="Disable parallel uploads.")
    parser.add_argument("--no-skip-duplicates", action="store_false",
            dest="skip_duplicates",
            help=("Upload a file even when iBroadcast thinks it's already "
            "been uploaded."))

    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    uploader = Uploader(args.login_token)

    uploader.process(args.directories, args.tags, args.skip_duplicates, args.parallel)
