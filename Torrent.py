import config
import requests
import yabencode
import hashlib
import base64
from urllib.parse import urlparse


class Torrent:
    def __init__(self, torrent_link=None, torrent_bytesio=None, qb_torrents_info=None, encode_base64=False):
        if (torrent_link and torrent_bytesio) or (not torrent_link and not torrent_bytesio):
            raise ValueError("Exactly one of torrent_link or torrent_bytesio must be provided.")

        self.maxspeed = config.torrent_overspeed * 1024 * 1024
        self.qb_torrents_info = qb_torrents_info

        if torrent_link:
            self.torrent = requests.get(url=torrent_link).content
        else:
            self.torrent = base64.b64decode(torrent_bytesio) if encode_base64 else torrent_bytesio

        torrentinfo = yabencode.decode(self.torrent)
        self.hash = self.calculate_v1_hash(torrentinfo)
        self.is_in_client = self.hash in qb_torrents_info["torrents_hash2torrent"]
        self.announce, announcelist = self.extract_announce_info(torrentinfo)
        self.name = torrentinfo["info"]["name"].decode('utf-8')
        self.set_maxspeed(announcelist)
        self.md5 = hashlib.md5(str(torrentinfo["info"]["pieces"]).encode('utf8')).hexdigest()

    def calculate_v1_hash(self, torrentinfo):
        info = torrentinfo["info"]
        bencoded_info = yabencode.encode(info)
        sha1_hash = hashlib.sha1(bencoded_info).hexdigest()
        return sha1_hash

    def extract_announce_info(self, torrentinfo):
        announce = urlparse(torrentinfo["announce"].decode('utf-8')).netloc
        announcelist = [urlparse(tracker[0].decode('utf-8')).netloc for tracker in
                        torrentinfo.get("announce-list", [])] or [announce]
        return announce, announcelist

    def set_maxspeed(self, announcelist):
        if any(tracker in config.torrent_overspeed_whitelist for tracker in announcelist):
            self.maxspeed = 0
        else:
            for tracker in announcelist:
                if tracker in config.custom_torrent_speed_list:
                    self.maxspeed = 1024 * 1024 * config.custom_torrent_speed_list[tracker]
                    self.announce = tracker
                    break