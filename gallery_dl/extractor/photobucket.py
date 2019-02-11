# -*- coding: utf-8 -*-

# Copyright 2019 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extract images from http://photobucket.com/"""

from .common import Extractor, Message
from .. import text, exception
import base64
import json


class PhotobucketAlbumExtractor(Extractor):
    """Extractor for albums on photobucket.com"""
    category = "photobucket"
    subcategory = "album"
    directory_fmt = ("{category}", "{username}", "{location}")
    filename_fmt = "{offset:>03}{pictureId:?_//}_{titleOrFilename}.{extension}"
    archive_fmt = "{id}"
    pattern = (r"(?:https?://)?((?:[^.]+\.)?photobucket\.com)"
               r"/user/[^/?&#]+/library/[^?&#]*")
    test = (
        ("http://s258.photobucket.com/user/focolandia/library/", {
            "pattern": r"http://i\d+.photobucket.com/albums/hh280/focolandia",
            "count": ">= 39"
        }),
        # subalbums of main "directory"
        ("http://s271.photobucket.com/user/lakerfanryan/library/", {
            "options": (("image-filter", "False"),),
            "pattern": pattern,
            "count": 1,
        }),
        # subalbums of subalbum without images
        ("http://s271.photobucket.com/user/lakerfanryan/library/Basketball", {
            "pattern": pattern,
            "count": ">= 9",
        }),
        # private (missing JSON data)
        ("http://s1277.photobucket.com/user/sinisterkat44/library/", {
            "count": 0,
        }),
        ("http://s1110.photobucket.com/user/chndrmhn100/library/"
         "Chandu%20is%20the%20King?sort=3&page=1"),
    )

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.album_path = ""
        self.url = match.group(0)
        self.root = "http://" + match.group(1)
        self.session.headers["Referer"] = self.url

    def items(self):
        yield Message.Version, 1
        for image in self.images():
            image["titleOrFilename"] = text.unescape(image["titleOrFilename"])
            image["title"] = text.unescape(image["title"])
            image["extension"] = image["ext"]
            yield Message.Directory, image
            yield Message.Url, image["fullsizeUrl"], image

        if self.config("subalbums", True):
            for album in self.subalbums():
                yield Message.Queue, album["url"], album

    def images(self):
        """Yield all images of the current album"""
        url = self.url
        params = {"sort": "3", "page": 1}

        while True:
            page = self.request(url, params=params).text
            json_data = text.extract(page, "collectionData:", ",\n")[0]
            if not json_data:
                msg = text.extract(page, 'libraryPrivacyBlock">', "</div>")[0]
                msg = ' ("{}")'.format(text.remove_html(msg)) if msg else ""
                self.log.error("Unable to get JSON data%s", msg)
                return
            data = json.loads(json_data)

            yield from data["items"]["objects"]

            if data["total"] <= data["offset"] + data["pageSize"]:
                self.album_path = data["currentAlbumPath"]
                return
            params["page"] += 1

    def subalbums(self):
        """Return all subalbum objects"""
        url = self.root + "/component/Albums-SubalbumList"
        params = {
            "albumPath": self.album_path,
            "fetchSubAlbumsOnly": "true",
            "deferCollapsed": "true",
            "json": "1",
        }

        data = self.request(url, params=params).json()
        return data["body"].get("subAlbums", ())


class PhotobucketImageExtractor(Extractor):
    """Extractor for individual images from photobucket.com"""
    category = "photobucket"
    subcategory = "image"
    directory_fmt = ("{category}", "{username}")
    filename_fmt = "{pictureId:?/_/}{titleOrFilename}.{extension}"
    archive_fmt = "{username}_{id}"
    pattern = (r"(?:https?://)?(?:[^.]+\.)?photobucket\.com"
               r"(?:/gallery/user/([^/?&#]+)/media/([^/?&#]+)"
               r"|/user/([^/?&#]+)/media/[^?&#]+\.html)")
    test = (
        (("http://s271.photobucket.com/user/lakerfanryan"
          "/media/Untitled-3-1.jpg.html"), {
            "url": "3b647deeaffc184cc48c89945f67574559c9051f",
            "keyword": "a2de4e60d584912537b8025c01bdd1d20bdea735",
        }),
        (("http://s271.photobucket.com/user/lakerfanryan"
          "/media/IsotopeswBros.jpg.html?sort=3&o=2"), {
            "url": "12c1890c09c9cdb8a88fba7eec13f324796a8d7b",
            "keyword": "61200a223df6c06f45ac3d30c88b3f5b048ce9a8",
        }),
    )

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.url = match.group(0)
        self.user = match.group(1) or match.group(3)
        self.media_id = match.group(2)
        self.session.headers["Referer"] = self.url

    def items(self):
        url = "http://photobucket.com/galleryd/search.php"
        params = {"userName": self.user, "searchTerm": "", "ref": ""}

        if self.media_id:
            params["mediaId"] = self.media_id
        else:
            params["url"] = self.url

        # retry API call up to 5 times, since it can randomly fail
        tries = 0
        while tries < 5:
            data = self.request(url, method="POST", params=params).json()
            image = data["mediaDocuments"]
            if "message" not in image:
                break  # success
            tries += 1
            self.log.debug("'%s'", image["message"])
        else:
            self.log.error("photobucket says: '%s'", image["message"])
            raise exception.StopExtraction()

        # adjust metadata entries to be at least somewhat similar
        # to the 'album' extractor
        if "media" in image:
            image = image["media"][image["mediaIndex"]]
            image["albumView"] = data["mediaDocuments"]["albumView"]
            image["username"] = image["ownerId"]
        else:
            image["fileUrl"] = image.pop("imageUrl")

        image.setdefault("title", "")
        image.setdefault("description", "")
        name, _, ext = image["fileUrl"].rpartition("/")[2].rpartition(".")
        image["ext"] = image["extension"] = ext
        image["titleOrFilename"] = image["title"] or name
        image["tags"] = image.pop("clarifaiTagList", [])

        mtype, _, mid = base64.b64decode(image["id"]).partition(b":")
        image["pictureId"] = mid.decode() if mtype == b"mediaId" else ""

        yield Message.Version, 1
        yield Message.Directory, image
        yield Message.Url, image["fileUrl"], image
