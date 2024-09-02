import config
from qbittorrentapi import Client
import sqlite3
import logging
import time
import asyncio
from urllib.parse import urlparse
from Torrent import Torrent
import os

class TorrentManager:
    def __init__(self):
        # 设置上传文件夹
        if not os.path.exists(config.upload_folder):
            os.makedirs(config.upload_folder)

        self.qbittorrent = Client(config.qb_url)
        self.qbittorrent.auth_log_in(config.qb_username, config.qb_password)
        logging.info("Successfully connected to qBittorrent")
        self.qb_torrents_info = {}
        self.fetch_ok = False
        self.daemon_torrents_got_paused = False
        self.over_speed_factor = 0
        self.fasttorrent = None
        self.v2_last_ft_time = time.time()

        # Initialize SQLite database for deployment queue
        self.conn = sqlite3.connect('torrent_manager.db')
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        # Create tables if they don't exist
        self.cursor.executescript('''
            CREATE TABLE IF NOT EXISTS deployment_torrents_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                torrent_name TEXT NOT NULL,
                torrent_md5 TEXT NOT NULL,
                isavailable BOOLEAN NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS daemon_config (
                id INTERGER NOT NULL DEFAULT 1,
                fifo_max INTEGER NOT NULL DEFAULT 0,
                over_speed_factor INTEGER NOT NULL DEFAULT 0,
                v2_fast_torrent_count INTEGER NOT NULL DEFAULT 1
            );
        ''')
        self.conn.commit()

        # Insert default values if no rows exist
        self.cursor.execute('''
            INSERT OR IGNORE INTO daemon_config (id, fifo_max, over_speed_factor, v2_fast_torrent_count)
            VALUES (1, 0, 0, 1);
        ''')
        self.conn.commit()

        # Retrieve configuration values
        self.cursor.execute(
            'SELECT fifo_max, over_speed_factor, v2_fast_torrent_count FROM daemon_config WHERE id = 1 LIMIT 1')
        result = self.cursor.fetchone()
        if result:
            self.fifo_max, self.over_speed_factor, self.v2_fast_torrent_count = result

    async def fetch_torrents_info(self):
        while True:
            try:
                raw_info = self.qbittorrent.torrents_info()
                queue_io_jobs = self.qbittorrent.sync.maindata().server_state.queued_io_jobs
                torrents_hash2torrent = {torrent["hash"]: torrent for torrent in raw_info}
                torrents_name2hash = {}

                for torrent in raw_info:
                    torrent_name = torrent["name"]
                    torrents_name2hash.setdefault(torrent_name, []).append(torrent["hash"])

                next_qb_torrents_info = {
                    "raw_info": raw_info,
                    "queue_io_jobs": queue_io_jobs,
                    "torrents_hash2torrent": torrents_hash2torrent,
                    "torrents_name2hash": torrents_name2hash
                }
                self.fetch_ok = True
                self.qb_torrents_info = next_qb_torrents_info
                await asyncio.sleep(config.sleep_time)
            except Exception as e:
                logging.error(f"Fetch Torrent Info Failed: {e}")
                self.fetch_ok = False
                await asyncio.sleep(config.exception_sleep_time)

    async def torrent_manager(self):
        while True:
            if self.fetch_ok and config.enable_torrent_manager:
                if config.enable_newest_fasttorrent_manager:
                    await self.manage_fast_torrents_v2()
                else:
                    await self.manage_fast_torrents()
                await self.manage_super_seeding()
                await self.available_torrent_marker()
                await self.deploy_torrents()
            await asyncio.sleep(config.sleep_time)

    async def manage_fast_torrents(self):
        found_fast_torrent = False
        fasttorrent = ""

        if config.enable_fasttorrent_manager:
            queue_io_jobs = self.qb_torrents_info["queue_io_jobs"]
            for torrent in self.qb_torrents_info["raw_info"]:
                upspeed = torrent['upspeed'] / (1024 * 1024)
                dlspeed = torrent['dlspeed'] / (1024 * 1024)

                if (((upspeed >= 25 or dlspeed >= 25) and queue_io_jobs >= 15) or
                    ((upspeed >= 40 or dlspeed >= 40) and queue_io_jobs >= 5) or
                    (upspeed >= 55 or dlspeed >= 55)) and torrent['category'] == config.daemon_category:
                    fasttorrent = torrent['hash']
                    if self.fasttorent != fasttorrent:
                        logging.info(
                            f"Fast Torrent {torrent['name']} on Tracker {urlparse(torrent['tracker'].decode('utf-8')).netloc}")
                        self.fasttorent == fasttorrent
                    found_fast_torrent = True
                    break

            if found_fast_torrent:
                if self.over_speed_factor < config.over_speed_max_factor:
                    self.over_speed_factor += 1
                    self.update_over_speed_factor()

                paused_torrent = [
                    torrent['hash'] for torrent in self.qb_torrents_info["raw_info"]
                    if (torrent['hash'] != fasttorrent and torrent['category'] == config.daemon_category
                        and not (time.time() - torrent['added_on'] > config.prevent_seed_time
                                 and any(k in torrent['tracker'] for k in config.prevent_seed_time_tracker_list)))
                ]
                self.qbittorrent.torrents_reannounce(torrent_hashes=paused_torrent)
                await asyncio.sleep(5)
                self.qbittorrent.torrents_pause(torrent_hashes=paused_torrent)

                if not self.daemon_torrents_got_paused:
                    logging.info(f"Fast Torrent Start. hash is {fasttorrent}")

                self.daemon_torrents_got_paused = True
            else:
                if 0 < self.over_speed_factor:
                    self.over_speed_factor -= 1
                    self.update_over_speed_factor()

                if self.daemon_torrents_got_paused:
                    all_torrents = [torrent['hash'] for torrent in self.qb_torrents_info["raw_info"] if
                                    torrent['category'] == config.daemon_category]
                    self.qbittorrent.torrents_resume(torrent_hashes=all_torrents)
                    self.daemon_torrents_got_paused = False
                    logging.info(f"Fast Torrent End, Now return normal Seeding")

    async def manage_fast_torrents_v2(self):
        found_fast_torrent = False
        fasttorrent = []

        if config.enable_fasttorrent_manager:
            queue_io_jobs = self.qb_torrents_info["queue_io_jobs"]
            sorted_torrents = sorted(
                self.qb_torrents_info["raw_info"],
                key=lambda torrent: (torrent['upspeed'] + torrent['dlspeed']),
                reverse=True
            )

            # Adjust max fast torrents count based on queue_io_jobs
            if queue_io_jobs > 20 and self.v2_fast_torrent_count > 1:
                self.v2_fast_torrent_count -= 1
                self.update_v2_fast_torrent_count()
                logging.info(f"V2 Fast Torrent Count decreased to {self.v2_fast_torrent_count}")

            elif queue_io_jobs < 5 and self.v2_fast_torrent_count < config.v2_fast_torrent_count and time.time() - self.v2_last_ft_time >= config.v2_max_torrent_timer:
                self.v2_fast_torrent_count += 1
                logging.info(f"V2 Fast Torrent Count increased to {self.v2_fast_torrent_count}")
                self.update_v2_fast_torrent_count()
                self.v2_last_ft_time = time.time()

            # Determine if the top torrent qualifies as a fast torrent
            if sorted_torrents:
                top_torrent = sorted_torrents[0]
                upspeed = top_torrent['upspeed'] / (1024 * 1024)
                dlspeed = top_torrent['dlspeed'] / (1024 * 1024)
                is_fast_condition_met = (
                        ((upspeed >= 12 or dlspeed >= 12) and queue_io_jobs >= 45) or
                        ((upspeed >= 25 or dlspeed >= 25) and queue_io_jobs >= 15) or
                        ((upspeed >= 40 or dlspeed >= 40) and queue_io_jobs >= 5) or
                        (upspeed >= 55 or dlspeed >= 55)
                )
                if is_fast_condition_met and top_torrent['category'] == config.daemon_category:
                    found_fast_torrent = True

            if found_fast_torrent:
                # Add fast torrents to the list
                fasttorrent = [torrent['hash'] for torrent in sorted_torrents[:self.v2_fast_torrent_count]]

                # Increase over_speed_factor if applicable
                if self.over_speed_factor < config.over_speed_max_factor:
                    self.over_speed_factor += 1
                    self.update_over_speed_factor()

                # Pause torrents that aren't in the fast torrent list
                paused_torrent = [
                    torrent['hash'] for torrent in self.qb_torrents_info["raw_info"]
                    if torrent['hash'] not in fasttorrent and torrent['category'] == config.daemon_category
                       and not (time.time() - torrent['added_on'] > config.prevent_seed_time
                                and any(k in torrent['tracker'] for k in config.prevent_seed_time_tracker_list))
                ]
                self.qbittorrent.torrents_reannounce(torrent_hashes=paused_torrent)
                await asyncio.sleep(5)
                self.qbittorrent.torrents_pause(torrent_hashes=paused_torrent)

                if not self.daemon_torrents_got_paused:
                    logging.info(f"Fast Torrent Start. hash is {fasttorrent[0]} etc")
                self.daemon_torrents_got_paused = True

            else:
                # Decrease over_speed_factor if no fast torrent is found
                if self.over_speed_factor > 0:
                    self.over_speed_factor -= 1
                    self.update_over_speed_factor()

                # Resume all torrents if they were paused before
                if self.daemon_torrents_got_paused:
                    all_torrents = [
                        torrent['hash'] for torrent in self.qb_torrents_info["raw_info"]
                        if torrent['category'] == config.daemon_category
                    ]
                    self.qbittorrent.torrents_resume(torrent_hashes=all_torrents)
                    self.daemon_torrents_got_paused = False
                    logging.info("V2 Fast Torrent End, now returning to normal seeding.")

    def update_over_speed_factor(self):
        self.cursor.execute("UPDATE daemon_config SET over_speed_factor = ?  WHERE id = 1 ", (self.over_speed_factor,))
        self.conn.commit()

    def update_v2_fast_torrent_count(self):
        self.cursor.execute("UPDATE daemon_config SET v2_fast_torrent_count = ?  WHERE id = 1 ",
                            (self.v2_fast_torrent_count,))
        self.conn.commit()

    async def manage_super_seeding(self):
        if config.auto_super_seeding_manager:
            for torrent in self.qb_torrents_info["raw_info"]:
                # Check conditions for enabling super seeding
                if (any(k in torrent['tracker'] for k in config.auto_super_seeding_tracker_list)
                        and not torrent['super_seeding']
                        and torrent['uploaded'] / torrent['size'] >= config.auto_super_seeding_ratio
                        and torrent['downloaded'] == 0):
                    # Enable super seeding
                    self.qbittorrent.torrents_set_super_seeding(torrent_hashes=torrent['hash'])

                    # Log the action
                    tracker_info = torrent['tracker'][0] if isinstance(torrent['tracker'], list) else torrent['tracker']
                    logging.info(f"Torrent {torrent['name']} on Tracker {tracker_info} has been set to super seeding.")

    async def available_torrent_marker(self):
        # Fetch all queue entries with isavailable = False
        self.cursor.execute("SELECT torrent_name FROM deployment_torrents_queue WHERE isavailable = False")
        queue_entries = self.cursor.fetchall()

        # Process each entry to check availability
        for entry in queue_entries:
            torrent_name = entry[0]
            is_available = True

            # Check if the torrent is available in qb_torrents_info

            if torrent_name in self.qb_torrents_info["torrents_name2hash"]:
                for hash in self.qb_torrents_info["torrents_name2hash"][torrent_name]:
                    if self.qb_torrents_info["torrents_hash2torrent"][hash]["progress"] != 1:
                        is_available = False
                        break  # Exit early if any torrent is not fully downloaded

                # Update the availability status in the database
                if is_available:
                    self.cursor.execute(
                        "UPDATE deployment_torrents_queue SET isavailable = True WHERE torrent_name = ?",
                        (torrent_name,))
                    self.conn.commit()  # Commit the change after each update

    async def deploy_torrents(self):
        if self.over_speed_factor == 0 or not config.enable_fasttorrent_manager:
            try:
                # 开始事务
                self.conn.execute('BEGIN')

                # Fetch all available torrents
                self.cursor.execute(
                    "SELECT torrent_name, torrent_md5 FROM deployment_torrents_queue WHERE isavailable = 1")
                queue_entries = self.cursor.fetchall()

                torrent_table_queries = []
                table_name_mapping = {}

                for entry in queue_entries:
                    torrent_md5 = entry[1]
                    table_name = f"torrent_{torrent_md5}"

                    # Check if the table exists before querying
                    self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                    if not self.cursor.fetchone():
                        logging.warning(f"Table {table_name} does not exist. Skipping.")
                        continue

                    torrent_table_queries.append(
                        f"SELECT torrent_byteio, fifoid, '{table_name}' as table_name FROM {table_name} WHERE ispushed = 0")
                    table_name_mapping[table_name] = torrent_md5

                if torrent_table_queries:
                    table_query = ' UNION ALL '.join(torrent_table_queries) + " ORDER BY fifoid ASC LIMIT 1"
                    self.cursor.execute(table_query)
                    result = self.cursor.fetchone()

                    if result:
                        torrent_byteio = result[0]
                        table_name = result[2]
                        torrent_md5 = table_name_mapping[table_name]

                        target_torrent = Torrent(torrent_bytesio=torrent_byteio, qb_torrents_info=self.qb_torrents_info)
                        if not target_torrent.is_in_client:
                            self.qbittorrent.torrents_add(
                                torrent_files=target_torrent.torrent,
                                is_skip_checking=True,
                                upload_limit=target_torrent.maxspeed,
                                category=config.daemon_category
                            )
                            logging.info(
                                f"Torrent {target_torrent.name} on Tracker {target_torrent.announce} has been deployed.")
                        else:
                            logging.info(
                                f"Torrent {target_torrent.name} on Tracker {target_torrent.announce} already deployed.")

                        update_query = f"UPDATE {table_name} SET ispushed = 1 WHERE torrent_hash = ?"
                        self.cursor.execute(update_query, (target_torrent.hash,))

                        self.cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE ispushed = 0")
                        remaining_count = self.cursor.fetchone()[0]
                        if remaining_count == 0:
                            self.cursor.execute(f"DROP TABLE {table_name}")
                            delete_query = "DELETE FROM deployment_torrents_queue WHERE torrent_md5 = ?"
                            self.cursor.execute(delete_query, (torrent_md5,))
                            logging.info(
                                f"Deployment queue entry for {target_torrent.name} with md5 {torrent_md5} has been deleted.")

                        self.cursor.execute("SELECT COUNT(*) FROM deployment_torrents_queue")
                        queue_count = self.cursor.fetchone()[0]

                        if queue_count == 0:
                            self.cursor.execute("UPDATE daemon_config SET fifo_max = 0 WHERE id = 1 ")
                            logging.info("deployment_torrents_queue is empty. fifo_max has been reset to 0.")

                # 提交所有数据库操作
                self.conn.commit()

            except Exception as e:
                self.conn.rollback()  # 出错时回滚
                logging.error(f"Error deploying torrent: {e}")