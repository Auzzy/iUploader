#!/usr/bin/env python

import requests
import json
import glob
import os
import hashlib
import sys
import traceback

sys.tracebacklimit = 0

def get_input(inp):
    if sys.version_info >= (3, 0):
        return input(inp)
    else:
        return raw_input(inp)

class ServerError(Exception):
    pass

class ValueError(Exception):
    pass

class Uploader(object):
    """
    Class for uploading content to iBroadcast.
    """

    VERSION = '0.3'
    CLIENT = 'python 3 uploader script'
    DEVICE_NAME = 'python 3 uploader script'
    USER_AGENT = 'python 3 uploader script 0.3'


    def __init__(self, login_token):
        self.login_token = login_token

        # Initialise our variables that each function will set.
        self.user_id = None
        self.token = None
        self.supported = None
        self.files = None
        self.md5 = None

    def process(self):
        try:
            self.login()
        except ValueError as e:
            print('Login failed: %s' % e)
            return

        try:
            self.get_supported_types()
        except ValueError as e:
            print('Unable to fetch account info: %s' % e)
            return

        self.load_files()

        if self.confirm():
            self.upload()
            self.tag("test-tag")

    def login(self, login_token=None,):
        """
        Login to iBroadcast with the given login_token

        Raises:
            ValueError on invalid login

        """
        # Default to passed in values, but fallback to initial data.
        login_token = login_token or self.login_token

        print('Logging in...')
        # Build a request object.
        post_data = json.dumps({
            'mode' : 'login_token',
            'login_token': login_token,
            'app_id': 1007,
            'type': 'account',
            'version': self.VERSION,
            'client': self.CLIENT,
            'device_name' : self.DEVICE_NAME,
            'user_agent' : self.USER_AGENT
        })
        print(post_data)
        response = requests.post(
            "https://api.ibroadcast.com/s/JSON/",
            data=post_data,
            headers={'Content-Type': 'application/json', 'User-Agent': self.USER_AGENT}
        )
        print({'Content-Type': 'application/json', 'User-Agent': self.USER_AGENT})

        exit()

        if not response.ok:
            raise ServerError('Server returned bad status: ',
                             response.status_code)

        jsoned = response.json()

        if 'user' not in jsoned:
            raise ValueError(jsoned.message)

        print('Login successful - user_id: ', jsoned['user']['id'])
        self.user_id = jsoned['user']['id']
        self.token = jsoned['user']['token']

    def get_supported_types(self):
        """
        Get supported file types

        Raises:
            ValueError on invalid login

        """
        print('Fetching account info...')
        # Build a request object.
        post_data = json.dumps({
            'mode' : 'status',
            'user_id': self.user_id,
            'token': self.token,
            'supported_types': 1,
            'version': self.VERSION,
            'client': self.CLIENT,
            'device_name' : self.DEVICE_NAME,
            'user_agent' : self.USER_AGENT
        })
        response = requests.post(
            "https://api.ibroadcast.com/s/JSON/",
            data=post_data,
            headers={'Content-Type': 'application/json', 'User-Agent': self.USER_AGENT}
        )

        if not response.ok:
            raise ServerError('Server returned bad status: ',
                             response.status_code)

        jsoned = response.json()

        if 'user' not in jsoned:
            raise ValueError(jsoned.message)

        print('Account info fetched')

        self.supported = []
        self.files = []

        for filetype in jsoned['supported']:
             self.supported.append(filetype['extension'])

    def load_files(self, directory=None):
        """
        Load all files in the directory that match the supported extension list.

        directory defaults to present working directory.

        raises:
            ValueError if supported is not yet set.
        """
        if self.supported is None:
            raise ValueError('Supported not yet set - have you logged in yet?')

        if not directory:
            directory = os.getcwd()

        for full_filename in glob.glob(os.path.join(directory, '*')):
            filename = os.path.basename(full_filename)
            # Skip hidden files.
            if filename.startswith('.'):
                continue

            # Make sure it's a supported extension.
            dummy, ext = os.path.splitext(full_filename)
            if ext in self.supported:
                self.files.append(full_filename)

            # Recurse into subdirectories.
            # XXX Symlinks may cause... issues.
            if os.path.isdir(full_filename):
                self.load_files(full_filename)

    def confirm(self):
        """
        Presents a dialog for the user to either list all files, or just upload.
        """
        print("Found %s files.  Press 'L' to list, or 'U' to start the " \
              "upload." % len(self.files))
        response = get_input('--> ')

        print()
        if response == 'L'.upper():
            print('Listing found, supported files')
            for filename in self.files:
                print(' - ', filename)
            print()
            print("Press 'U' to start the upload if this looks reasonable.")
            response = get_input('--> ')
        if response == 'U'.upper():
            print('Starting upload.')
            return True

        print('Aborting')
        return False

    def __load_md5(self):
        """
        Reach out to iBroadcast and get an md5.
        """
        post_data = "user_id=%s&token=%s" % (self.user_id, self.token)

        # Send our request.
        response = requests.post(
            "https://upload.ibroadcast.com",
            data=post_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )

        if not response.ok:
            raise ServerError('Server returned bad status: ',
                             response.status_code)

        jsoned = response.json()

        self.md5 = jsoned['md5']

    def calcmd5(self, filePath="."):
        with open(filePath, 'rb') as fh:
            m = hashlib.md5()
            while True:
                data = fh.read(8192)
                if not data:
                    break
                m.update(data)
        return m.hexdigest()

    def upload(self):
        """
        Go and perform an upload of any files that haven't yet been uploaded
        """
        self.__load_md5()

        for filename in self.files:

            print('Uploading ', filename)

            # Get an md5 of the file contents and compare it to whats up
            # there already
            file_md5 = self.calcmd5(filename)

            if file_md5 in self.md5:
                print('Skipping - already uploaded.')
                continue
            upload_file = open(filename, 'rb')

            file_data = {
                'file': upload_file,
            }

            post_data = {
                'user_id': self.user_id,
                'token': self.token,
                'file_path' : filename,
                'method': self.CLIENT,
            }

            response = requests.post(
                "https://upload.ibroadcast.com",
                post_data,
                files=file_data,

            )

            upload_file.close()

            if not response.ok:
                raise ServerError('Server returned bad status: ',
                    response.status_code)
            jsoned = response.json()
            print(jsoned)
            result = jsoned['result']

            if result is False:
                raise ValueError('File upload failed.')
        print('Done')


    def _get_lib(self):
        requests.post("https://api.ibroadcast.com/s/JSON/", headers={"User-Agent": "python 3 uploader script 0.3"}, json={'app_id': 1007, 'version': 0.3, 'client': 'python 3 uploader script', 'device_name': 'python 3 uploader script', 'user_agent': 'python 3 uploader script 0.3', 'user_id': 2217732, 'token': '6fcbcb45-aa75-11eb-ad2e-1418774e50a6', 'mode': 'library'}).json()
        pass

    def tag(self, *tag_names):
        tag_ids = {}

        post_data = {
            'user_agent' : self.USER_AGENT,
            'device_name' : self.DEVICE_NAME,
            'version': self.VERSION,
            'client': self.CLIENT,
            'mode' : 'tagtracks',
            'tagid': 0,
            'tracks': [0]
        }
        response = requests.post(
            "https://api.ibroadcast.com/s/JSON/",
            json=post_data,
            headers={'User-Agent': self.USER_AGENT}
        )


if __name__ == '__main__':
    # NB: this could use parsearg
    if len(sys.argv) != 2:
        print("Run this script in the parent directory of your music files.\n")
        print("To acquire a login token, enable the \"Simple Uploaders\" app by visiting https://ibroadcast.com, logging in to your account, and clicking the \"Apps\" button in the side menu.\n")
        print("Usage: ibroadcast-uploader.py <login_token>\n")
        sys.exit(1)

    uploader = Uploader(sys.argv[1])

    uploader.process()
