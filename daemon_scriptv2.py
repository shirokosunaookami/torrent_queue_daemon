# GNU GENERAL PUBLIC LICENSE
# Version 2, June 1991

# Copyright (C) 1989, 1991 Free Software Foundation, Inc.  
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA

# Everyone is permitted to copy and distribute verbatim copies
# of this license document, but changing it is not allowed.

# More Infomation :https://www.gnu.org/licenses/old-licenses/gpl-2.0.zh-cn.html

# daemon_scriptv2 1.0.8

import asyncio
import logging
import config
from aiohttp import web
from TorrentManager import TorrentManager
import httpapi

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize and run the application

async def safe_task(task):
    try:
        await task
    except Exception as e:
        # Handle or log the exception
        logging.error(f"Task {task} encountered an error: {e}")


async def main():
    torrent_manager = TorrentManager()
    app = web.Application()
    app.router.add_post('/add_torrent', httpapi.add_torrent)
    app.router.add_post('/del_torrent', httpapi.del_torrent)
    app.router.add_post('/login', httpapi.login)
    app.router.add_get('/getRouters', httpapi.getRouters)
    app.router.add_get('/getInfo', httpapi.getInfo)
    app.router.add_post('/upload', httpapi.upload)

    app.router.add_get('/getTorrentList', httpapi.getTorrentList)


    app.router.add_options('/', httpapi.options_handler)
    app.router.add_options('/{tail:.*}', httpapi.options_handler)

    # Start the tasks with error handling
    fetch_torrents_info_task = asyncio.create_task(safe_task(torrent_manager.fetch_torrents_info()))
    torrent_manager_task = asyncio.create_task(safe_task(torrent_manager.torrent_manager()))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.httpapi_ip, config.httpapi_port)
    await site.start()

    # Use asyncio.gather to run the tasks concurrently
    await asyncio.gather(fetch_torrents_info_task, torrent_manager_task)


asyncio.run(main())
