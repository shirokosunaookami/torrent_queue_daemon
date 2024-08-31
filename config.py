#qBittorrent配置区域
qb_url = "" #qb地址，目前也只支持qb
qb_username = ""  #qb账号
qb_password = "" #qb密码
#api_key = "aimwarenet"
api_key = None
httpapi_ip = "0.0.0.0"
httpapi_port = 8888
###########################################################################################################
#种子速度区

torrent_overspeed_whitelist = [#超速白名单,在此列表中的种子将不会限速,可自由填写
    "tracker.pterclub.com",
    "tracker.leaves.red",
    "tracker2.leaves.red",
    "tracker3.leaves.red",
    "tracker.hdsky.me",
    "tracker.ubits.club",
    "dajiao.cyou",
    "landof.tv",
    "greatposterwall.com",
    "tracker.animebytes.tv",
    "tracker.yemapt.org",
    "tracker.qingwa.pro",
    "tracker.greatposterwall.com",
    "tracker.hdbits.org",
    "flacsfor.me"
    ] 
torrent_overspeed = 95 #普通限速速度，以mb/s为单位

custom_torrent_speed_list = {
    "pt.keepfrds.com": 12,
    "zhuque.in": 49,
    "pt.btschool.club": 24,
    "ourbits.club": 49
} #可自定义种子速度


###########################################################################################################
#自动种子管理区
enable_torrent_manager = True #种子管理器总开关
daemon_category = "daemon" #自动种子管理所属分类，可取别名

enable_fasttorrent_manager = True #快速种子开关，开启后，当有种子快速出种时，会自动暂停其他种子确保IO效率
over_speed_max_factor = 3 #快速种子因素值，越大其他种子暂停时间越长。

enable_newest_fasttorrent_manager = True #新版快速种子开关，开启后，当有种子快速出种时，会自动暂停其他种子确保IO效率,这是新版算法，依旧需要启用"enable_fasttorrent_manager"才能被执行，如关闭则使用老算法。
v2_max_torrent_timer = 300 #新版快速种子计时器，到时候再写desc吧
v2_fast_torrent_count = 3 #新版快速种子最大数量，到时候再写desc吧

auto_super_seeding_manager = True #自动超级做种
auto_super_seeding_ratio = 3.02 #当分享率达到多少开启超级做种
auto_super_seeding_tracker_list = [
    "tracker.hhan.club",
    "hhanclub.top",
    "t.audiences.me",
    "tracker.cinefiles.info"
] #属于此tracker的种子开启超级做种

prevent_seed_time_tracker_list = [
    "tracker.hhan.club",
    "hhanclub.top",
    "t.audiences.me",
    "tracker.cinefiles.info",
    "on.springsunday.net",
    "tracker.pterclub.com",
] #一些站点会考核发种人的做种时间，属于此tracker的种子的活动时间在超过prevent_seed_time之后将不会被脚本暂停
prevent_seed_time = 3600 #对在做种时间超过x秒之后并在上述列表的种子起效

sleep_time = 60 # 脚本运行间隔时间（秒）
exception_sleep_time = 120 # 异常时脚本运行间隔时间（秒）
is_backup_torrent = True #是否备份种子,Ture时，发送种子的接口会备份到服务器目录
upload_folder = 'uploads' #种子备份文件夹
