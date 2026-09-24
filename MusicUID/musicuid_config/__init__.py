"""Plugin configuration schema and the shared config instance."""

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
    GsListStrConfig,
)
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from ..utils.resource.RESOURCE_PATH import CONFIG_PATH

CONFIG_DEFAULT: Dict[str, GSC] = {
    "default_platform": GsStrConfig(
        "默认平台",
        "点歌未指定平台时使用的音乐平台",
        "netease",
        options=["netease", "qq", "kugou"],
    ),
    "login_whitelist": GsListStrConfig(
        "登录权限白名单",
        "允许扫码登录与配置凭据的用户 ID 列表（主人默认拥有全部权限，无需在此额外添加）",
        [],
    ),
    "netease_cookie": GsStrConfig(
        "网易云 Cookie",
        "填入 MUSIC_U 的值后 VIP 歌曲与高音质才生效；留空只能播放免费曲目。"
        "获取方式：浏览器登录 music.163.com → F12 → Application → Cookies → 复制 MUSIC_U 的 Value",
        "",
        secret=True,
    ),
    "qqmusic_cookie": GsStrConfig(
        "QQ音乐 Cookie",
        "填入浏览器 y.qq.com 的完整 Cookie 后，会员曲目与 320kbps 档位才可播放；留空只能播放免费曲目。"
        "Cookie 必须含 uin 与 qm_keyst 两个字段。"
        "获取方式：浏览器登录 y.qq.com → F12 → Application → Cookies → 复制整条 Cookie",
        "",
        secret=True,
    ),
    "kugou_cookie": GsStrConfig(
        "酷狗 Cookie",
        "填入浏览器酷狗网页版的完整 Cookie 后，需要单独购买专辑的曲目（周杰伦等原唱）可播放 60 秒试听片段，"
        "完整版仍需在酷狗购买该专辑；免费曲目始终是完整版，不受影响。"
        "获取方式：浏览器登录 kugou.com → F12 → Application → Cookies → 复制整条 Cookie",
        "",
        secret=True,
    ),
    "netease_level": GsStrConfig(
        "网易云音质",
        "weapi 取流时优先尝试的档位，拿不到会自动降到标准档；lossless 体积很大（单曲可达 100MB+），群里发歌建议 exhigh",
        "exhigh",
        options=["standard", "exhigh", "lossless"],
    ),
    "max_list": GsIntConfig(
        "每平台条数",
        "每个平台最多列出多少首；未指定平台时会三平台同时搜索，卡片总数 = 每平台条数 × 平台数",
        5,
        10,
    ),
    "render_card": GsBoolConfig(
        "图片卡片",
        "用图片卡片展示结果；关闭或渲染环境缺失时自动回退纯文本",
        True,
    ),
    "auto_install_render": GsBoolConfig(
        "自动安装渲染依赖",
        "启动时检测卡片所需的 playwright 与 Chromium 内核，缺失则自动下载安装"
        "（Chromium 约 150MB，视网络需数分钟，期间不影响其他指令）；"
        "关闭后缺失依赖时只会回退纯文本，需自行执行 python -m playwright install chromium",
        True,
    ),
    "send_voice": GsBoolConfig("语音发送", "以语音消息发送音频（歌曲较长时体验更好）", True),
    "send_file": GsBoolConfig("文件发送", "额外再发一份音频文件；语音能正常收听时无需开启", False),
    "local_file_ref": GsBoolConfig(
        "强制本机路径引用",
        "默认按渠道自动选择：QQ 官方发 file:// 本机路径（它的适配器认不出无后缀的 base64 音频），"
        "其余渠道发 base64。开启此项则所有渠道都用 file://（要求 core 与适配器同机）",
        False,
    ),
    "enable_resolve": GsBoolConfig("链接自动解析", "自动识别聊天中的网易云分享链接并播放", True),
    "download_timeout": GsIntConfig("下载超时", "音频下载超时时间（秒）", 60, 300),
    "voice_max_mb": GsIntConfig(
        "语音体积上限",
        "音频超过该体积时会先用 ffmpeg 压成单声道低码率再当语音发送（QQ 对语音体积限制很严，"
        "整首 320kbps 会被静默丢弃）",
        2,
        20,
    ),
    "voice_bitrate": GsIntConfig("语音压缩码率", "压缩时使用的 mp3 码率（kbps），越低体积越小", 64, 320),
    "keep_temp_sec": GsIntConfig("临时文件保留", "音频与卡片临时文件保留秒数", 60, 600),
}

music_config = StringConfig("MusicUID", CONFIG_PATH, CONFIG_DEFAULT)
