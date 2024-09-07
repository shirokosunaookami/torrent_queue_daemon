from aiohttp import web
import os
# import uuid
import json
import config
import time
import hashlib
import logging
import base64
from Torrent import Torrent
import asyncio

async def add_torrent(self, request):
    try:
        data = await request.json()
        torrent_link = data.get('torrent_link')
        torrent_bytesio = data.get('torrent_bytesio')

        if config.api_key:
            received_uuid = data.get('uuid')
            received_timestamp = data.get('timestamp')
            received_signature = data.get('signature')

            # 重新生成签名
            server_sign_string = f"{config.api_key}{received_uuid}{received_timestamp}"
            server_signature = hashlib.sha256(server_sign_string.encode()).hexdigest()

            # 验证签名是否匹配
            if server_signature == received_signature:
                # 进一步验证时间戳是否在允许范围内
                current_timestamp = int(time.time())
                request_timestamp = int(received_timestamp)

                # 假设允许的时间差为60秒（1分钟）
                if abs(current_timestamp - request_timestamp) >= 60:
                    return web.json_response({"code": 500, "msg": "Outdated Signature", "data": ""},
                                                headers={'Access-Control-Allow-Origin': '*'})
            else:
                return web.json_response({"code": 500, "msg": "Wrong Signature", "data": ""},
                                            headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        logging.error(f"add_torrent Internal server error: {e}")
        return web.json_response({"code": 500, "msg": "Please Contract Administrator", "data": ""},
                                    headers={'Access-Control-Allow-Origin': '*'})
    try:
        if not torrent_link and not torrent_bytesio:
            return web.json_response(
                {"code": 500, "msg": "torrent_link or torrent_bytesio is required", "data": "ADDTORRENT"}
                , headers={'Access-Control-Allow-Origin': '*'})

        current_torrent = Torrent(torrent_link, torrent_bytesio, self.qb_torrents_info, encode_base64=True)
        if not self.qb_torrents_info["torrents_name2hash"].get(current_torrent.name) and not data.get('forceadd'):
            return web.json_response({"code": 500,
                                        "msg": 'Provided cross-seed torrent does not exist in qBittorrent. Use "forceadd" to skip this check.',
                                        "data": "ADDTORRENT"}
                                        , headers={'Access-Control-Allow-Origin': '*'})
        if current_torrent.is_in_client:
            return web.json_response({'code': 500, 'msg': 'Torrent Already in qBittorrent.', 'data': 'ADDTORRENT'}
                                        , headers={'Access-Control-Allow-Origin': '*'})
        table_name = f"torrent_{current_torrent.md5}"

        # Check if the torrent is already in the queue
        self.cursor.execute(
            "SELECT count(*) FROM deployment_torrents_queue WHERE torrent_name = ? AND torrent_md5 = ?",
            (current_torrent.name, current_torrent.md5)
        )
        result = self.cursor.fetchone()[0]

        if result > 1:
            raise ValueError(f"Unexpected Database Error: Multiple records found for this torrent. Count: {result}")

        if result == 0:
            # Insert into deployment_torrents_queue and create the table
            self.cursor.execute(
                "INSERT INTO deployment_torrents_queue (torrent_name, torrent_md5, isavailable) VALUES (?, ?, ?)",
                (current_torrent.name, current_torrent.md5, False)
            )

            self.cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    torrent_hash TEXT NOT NULL,
                    torrent_byteio BLOB NOT NULL,
                    torrent_tracker TEXT NOT NULL,
                    ispushed BOOLEAN NOT NULL DEFAULT 0,
                    fifoid INTEGER NOT NULL
                )
            """)
            is_new_torrent = True
        else:
            # Check if the torrent data is already in the specific table
            self.cursor.execute(
                f"SELECT count(*) FROM {table_name} WHERE torrent_hash = ?",
                (current_torrent.hash,)
            )
            is_new_torrent = self.cursor.fetchone()[0] == 0

        if is_new_torrent:
            self.cursor.execute("SELECT fifo_max FROM daemon_config WHERE id = 1 LIMIT 1")
            result = self.cursor.fetchone()
            if result:
                thisfifoid = result[0] + 1  # 自增1

                self.cursor.execute("UPDATE daemon_config SET fifo_max = ? WHERE fifo_max = ? and id = 1",
                                    (thisfifoid, result[0]))
            else:
                thisfifoid = 1  # 如果没有找到记录，默认从1开始

            self.cursor.execute(
                f"INSERT INTO {table_name} (torrent_hash, torrent_byteio, torrent_tracker, ispushed, fifoid) VALUES (?, ?, ?, ?, ?)",
                (current_torrent.hash, current_torrent.torrent, current_torrent.announce, False, thisfifoid)
            )

            self.conn.commit()
            return web.json_response({'code': 200, 'msg': '成功', 'data': {
                'action': 'ADDTORRENT',
                'torrent_name': current_torrent.name,
                'tracker': current_torrent.announce
            }}, headers={'Access-Control-Allow-Origin': '*'})
        else:
            return web.json_response({'code': 500, 'msg': 'Torrent Already in Queue.', 'data': {
                'action': 'ADDTORRENT',
                'torrent_name': current_torrent.name,
                'tracker': current_torrent.announce,
            }}, headers={'Access-Control-Allow-Origin': '*'})

    except Exception as e:
        logging.error(f"Error adding torrent: {str(e)}")
        return web.json_response({'code': 500, 'msg': str(e), 'data': ''},
                                    headers={'Access-Control-Allow-Origin': '*'})

async def upload(self, request):
    reader = await request.multipart()
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == 'file':
            # 获取文件名
            filename = part.filename
            if not filename.endswith('.torrent'):
                return web.json_response({'code': 500, 'msg': 'Invalid file format. Only.torrent files are allowed.'},
                                            headers={'Access-Control-Allow-Origin': '*'})

            # 读取文件内容
            contents = await part.read(decode=True)

            # 备份文件
            if config.is_backup_torrent:
                with open(os.path.join('uploads', filename), 'wb') as f:
                    f.write(contents)

            with open(os.path.join('uploads', filename), 'rb') as f:
                try:
                    torrent_bytes = f.read()
                    torrent_bytesio = base64.b64encode(torrent_bytes).decode('utf-8')

                    current_torrent = Torrent(None, torrent_bytesio, self.qb_torrents_info, encode_base64=True)
                    if not self.qb_torrents_info["torrents_name2hash"].get(current_torrent.name) :
                        return web.json_response({"code": 500,
                                                    "msg": 'Provided cross-seed torrent does not exist in qBittorrent. Use "forceadd" to skip this check.',
                                                    "data": "ADDTORRENT"}
                                                    , headers={'Access-Control-Allow-Origin': '*'})
                    if current_torrent.is_in_client:
                        return web.json_response(
                            {'code': 500, 'msg': 'Torrent Already in qBittorrent.', 'data': 'ADDTORRENT'}
                            , headers={'Access-Control-Allow-Origin': '*'})
                    table_name = f"torrent_{current_torrent.md5}"

                    # Check if the torrent is already in the queue
                    self.cursor.execute(
                        "SELECT count(*) FROM deployment_torrents_queue WHERE torrent_name = ? AND torrent_md5 = ?",
                        (current_torrent.name, current_torrent.md5)
                    )
                    result = self.cursor.fetchone()[0]

                    if result > 1:
                        raise ValueError(
                            f"Unexpected Database Error: Multiple records found for this torrent. Count: {result}")

                    if result == 0:
                        # Insert into deployment_torrents_queue and create the table
                        self.cursor.execute(
                            "INSERT INTO deployment_torrents_queue (torrent_name, torrent_md5, isavailable) VALUES (?, ?, ?)",
                            (current_torrent.name, current_torrent.md5, False)
                        )

                        self.cursor.execute(f"""
                            CREATE TABLE IF NOT EXISTS {table_name} (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                torrent_hash TEXT NOT NULL,
                                torrent_byteio BLOB NOT NULL,
                                torrent_tracker TEXT NOT NULL,
                                ispushed BOOLEAN NOT NULL DEFAULT 0,
                                fifoid INTEGER NOT NULL
                            )
                        """)
                        is_new_torrent = True
                    else:
                        # Check if the torrent data is already in the specific table
                        self.cursor.execute(
                            f"SELECT count(*) FROM {table_name} WHERE torrent_hash = ?",
                            (current_torrent.hash,)
                        )
                        is_new_torrent = self.cursor.fetchone()[0] == 0

                    if is_new_torrent:
                        self.cursor.execute("SELECT fifo_max FROM daemon_config WHERE id = 1 LIMIT 1")
                        result = self.cursor.fetchone()
                        if result:
                            thisfifoid = result[0] + 1  # 自增1

                            self.cursor.execute(
                                "UPDATE daemon_config SET fifo_max = ? WHERE fifo_max = ? and id = 1",
                                (thisfifoid, result[0]))
                        else:
                            thisfifoid = 1  # 如果没有找到记录，默认从1开始

                        self.cursor.execute(
                            f"INSERT INTO {table_name} (torrent_hash, torrent_byteio, torrent_tracker, ispushed, fifoid) VALUES (?, ?, ?, ?, ?)",
                            (current_torrent.hash, current_torrent.torrent, current_torrent.announce, False,
                                thisfifoid)
                        )

                        self.conn.commit()
                        return web.json_response({'code': 200, 'msg': '成功', 'data': {
                            'action': 'ADDTORRENT',
                            'torrent_name': current_torrent.name,
                            'tracker': current_torrent.announce
                        }}, headers={'Access-Control-Allow-Origin': '*'})
                    else:
                        return web.json_response({'code': 500, 'msg': 'Torrent Already in Queue.', 'data': {
                            'action': 'ADDTORRENT',
                            'torrent_name': current_torrent.name,
                            'tracker': current_torrent.announce,
                        }}, headers={'Access-Control-Allow-Origin': '*'})

                except Exception as e:
                    logging.error(f"Error adding torrent: {str(e)}")
                    return web.json_response({'code': 500, 'msg': str(e), 'data': ''},
                                                headers={'Access-Control-Allow-Origin': '*'})


async def login(self, request):
    data = await request.json()
    username = data.get('username')
    password = data.get('password')

    if username == config.qb_username and password == config.qb_password:
        return web.json_response({"code": 200, "msg": "登陆成功", "token": "accessToken"},
                                 headers={'Access-Control-Allow-Origin': '*'})
    else:
        return web.json_response({"code": 500, "msg": "用户名或密码错误"},
                                 headers={'Access-Control-Allow-Origin': '*'})

# 用于后面菜单权限控制，暂时用不上，直接空实现
async def getRouters(self, request):
    return web.json_response({"code": 200, "msg": "", "data": []},
                                headers={'Access-Control-Allow-Origin': '*'})

# 用于后面菜单权限控制，暂时用不上，直接空实现
async def getInfo(self, request):
    return web.json_response({
"msg": "操作成功",
"code": 200,
"permissions": [
    "*:*:*"
],
"roles": [
    "admin"
],
"user": {
    "createBy": "admin",
    "createTime": "2024-06-30 11:27:11",
    "remark": "管理员",
    "userId": 1,
    "deptId": 103,
    "userName": "admin",
    "nickName": "若依",
    "email": "ry@163.com",
    "phonenumber": "15888888888",
    "sex": "1",
    "avatar": "",
    "password": "$2a$10$7JB720yubVSZvUI0rEqK/.VqGOZTH.ulu33dHOiBE8ByOhJIrdAu2",
    "status": "0",
    "delFlag": "0",
    "loginIp": "58.209.66.10",
    "loginDate": "2024-08-31T13:46:41.000+08:00",
    "dept": {
        "deptId": 103,
        "parentId": 101,
        "ancestors": "0,100,101",
        "deptName": "研发部门",
        "orderNum": 1,
        "leader": "若依",
        "status": "0",
        "children": []
    },
    "roles": [
        {
            "roleId": 1,
            "roleName": "超级管理员",
            "roleKey": "admin",
            "roleSort": 1,
            "dataScope": "1",
            "menuCheckStrictly": False,
            "deptCheckStrictly": False,
            "status": "0",
            "flag": False,
            "admin": True
        }
    ],
    "admin": True
}
},
                                headers={'Access-Control-Allow-Origin': '*'})


# 获取待deploy种子列表
async def getTorrentList(self, request):
    torrents_list = []
    table_query = ""
    torrent_table_queries = []
    rows = []

    try:
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name like 'torrent_%'")
        tables = self.cursor.fetchall()

        if tables:
            for table in tables:
                torrent_table_queries.append(
                    f"SELECT torrent_byteio, fifoid, '{table[0]}' as table_name, torrent_tracker,ispushed FROM {table[0]}")
                if torrent_table_queries:
                    table_query = ' UNION ALL '.join(torrent_table_queries)

            table_query = "select torrent_name, torrent_tracker, fifoid, ispushed, isavailable, torrent_md5 , torrent_byteio, table_name from (" + table_query + ") a left join deployment_torrents_queue b on a.table_name = 'torrent_'||b.torrent_md5 ORDER BY ispushed,fifoid ASC"
            # Fetch all available torrents
            self.cursor.execute(table_query)
            queue_entries = self.cursor.fetchall()

            # 将查询结果转换为字典列表:
            for row in queue_entries:
                rows.append({'torrent_name': row[0], 'torrent_tracker': row[1], 'fifoid': row[2], 'ispushed': row[3],
                             'isavailable': row[4], 'torrent_md5': row[5], 'table_name': row[7]})

    except Exception as e:
        logging.error(f"Error deploying torrent: {e}")
        return web.json_response({
            "code": 500,
            "msg": "查询种子列表失败"
        }, headers={'Access-Control-Allow-Origin': '*'})
    return web.json_response({
        "code": 200,
        "msg": "",
        "data": json.dumps(rows)
    }, headers={'Access-Control-Allow-Origin': '*'})

async def del_torrent(self, request):
    try:
        data = await request.json()
        torrent_link = data.get('torrent_link')
        torrent_bytesio = data.get('torrent_bytesio')
        torrent_name = data.get('torrent_name')
        torrent_hash = data.get('torrent_hash')

        if config.api_key:
            received_uuid = data.get('uuid')
            received_timestamp = data.get('timestamp')
            received_signature = data.get('signature')

            # 重新生成签名
            server_sign_string = f"{config.api_key}{received_uuid}{received_timestamp}"
            server_signature = hashlib.sha256(server_sign_string.encode()).hexdigest()

            # 验证签名是否匹配
            if server_signature == received_signature:
                # 进一步验证时间戳是否在允许范围内
                current_timestamp = int(time.time())
                request_timestamp = int(received_timestamp)

                # 假设允许的时间差为60秒（1分钟）
                if abs(current_timestamp - request_timestamp) >= 60:
                    web.json_response({'status': 'error', 'message': 'Outdated Signature'},
                                        headers={'Access-Control-Allow-Origin': '*'})
                    return
            else:
                web.json_response({'status': 'error', 'message': 'Wrong Signature'},
                                    headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        logging.error(f"del_torrent Internal server error: {e}")
        return web.json_response({
            'status': 'error',
            'message': "Please Contract Administrator"
        }, headers={'Access-Control-Allow-Origin': '*'})
    try:
        if not any([torrent_link, torrent_bytesio, torrent_name, torrent_hash]):
            return web.json_response(
                {'status': 'error', 'action': 'DELTORRENT', 'message': 'No option provided, Bad Request'},
                headers={'Access-Control-Allow-Origin': '*'})
        torrent_hash = None
        if torrent_link or torrent_bytesio:
            current_torrent = Torrent(torrent_link=torrent_link, torrent_bytesio=torrent_bytesio,
                                        qb_torrents_info=self.qb_torrents_info, encode_base64=True)
            torrent_hash = self.qb_torrents_info["torrents_name2hash"].get(current_torrent.name)
            table_name = f"torrent_{current_torrent.md5}"
            self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            if not self.cursor.fetchone():
                logging.info(f"{current_torrent.name} does not in Queue. Skipping.")
            else:
                self.cursor.execute(f"DROP TABLE {table_name}")
                delete_query = "DELETE FROM deployment_torrents_queue WHERE torrent_md5 = ?"
                self.cursor.execute(delete_query, (current_torrent.md5,))
                self.conn.commit()
        elif torrent_name:
            torrent_hash = self.qb_torrents_info["torrents_name2hash"].get(torrent_name)
            self.cursor.execute("SELECT torrent_md5 FROM deployment_torrents_queue WHERE torrent_name = ? ",
                                (torrent_name,))
            result = self.cursor.fetchone()
            if result:
                logging.info(f"{torrent_name} does not in Queue. Skipping.")
            else:
                table_name = f"torrent_{current_torrent.md5}"
                self.cursor.execute(f"DROP TABLE {table_name}")
                delete_query = "DELETE FROM deployment_torrents_queue WHERE torrent_md5 = ?"
                self.cursor.execute(delete_query, (result[0],))
                self.conn.commit()

        if torrent_hash:
            if isinstance(torrent_hash, str):
                torrent_hashes = [torrent_hash] if torrent_hash in self.qb_torrents_info[
                    "torrents_hash2torrent"] else []
            elif isinstance(torrent_hash, list):
                torrent_hashes = torrent_hash
            else:
                return web.json_response(
                    {'status': 'error', 'action': 'DELTORRENT', 'message': 'Torrent not found'},
                    headers={'Access-Control-Allow-Origin': '*'})

            if torrent_hashes:
                self.qbittorrent.torrents_reannounce(torrent_hashes=torrent_hashes)
                await asyncio.sleep(5)
                self.qbittorrent.torrents_delete(delete_files=True, torrent_hashes=torrent_hashes)

                return web.json_response(
                    {'status': 'success', 'message': f'del torrent {torrent_name or torrent_hash} success.'},
                    headers={'Access-Control-Allow-Origin': '*'})
        else:
            return web.json_response(
                {'status': 'error', 'action': 'DELTORRENT', 'message': 'No torrent hash found'},
                headers={'Access-Control-Allow-Origin': '*'})

    except Exception as e:
        logging.error(f"Error deleting torrent: {str(e)}")
        return web.json_response({'status': 'error', 'action': 'DELTORRENT', 'message': str(e)},
                                    headers={'Access-Control-Allow-Origin': '*'})





async def options_handler(request):
    return web.Response(headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization'
    })
