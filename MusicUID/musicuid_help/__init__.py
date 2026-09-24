"""帮助指令与全局帮助一览注册。"""

from __future__ import annotations

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

from ..musicuid_card import send_help_card

sv_help = SV("点歌帮助", priority=3)

HELP_COMMANDS = ("音乐帮助", "听歌帮助", "音乐菜单")

HELP_TEXT = """🎵 MusicUID · 多平台点歌

【点歌】
点歌 关键词        搜索并列出结果
点歌 QQ 关键词     临时切换平台
播放 关键词        搜索并直接播放第一首
听N                播放当前列表第 N 首
歌词 关键词        查看歌词

【扫码登录与凭证（主人/白名单）】
点歌登录 网易云    扫码登录网易云（免手动填 Cookie，支持 VIP 与无损）
点歌登录 酷狗      扫码登录酷狗（免手动填 Cookie）
点歌登录状态       查看当前三平台的 Cookie 绑定状态
点歌导入cookie 平台 Cookie值   手动导入指定平台 Cookie
点歌添加白名单 用户ID   添加用户至登录白名单（主人专用）
点歌删除白名单 用户ID   从登录白名单移除用户（主人专用）
点歌白名单         查看当前登录白名单列表

【平台】
可用平台词：网易 / QQ / 酷狗
三个平台都支持搜索与音频下发：网易云与酷狗支持扫码一键登录；
QQ 音乐在网页控制台填入 Cookie 后可播放会员与高音质曲目。

【链接解析】
发送网易云 / QQ音乐 / 酷狗的分享链接会自动播放
网易云的歌单 / 专辑链接会列出歌曲，可用「听N」选播

【配置】
在 GsCore 网页控制台 - 插件配置中修改默认平台、列表条数、
语音/文件发送、卡片渲染与链接解析开关。"""


@sv_help.on_command(HELP_COMMANDS, block=True, to_ai="查看点歌插件帮助文档与指令列表")
async def send_help(bot: Bot, ev: Event) -> None:
    """返回 MusicUID 插件使用帮助与功能指令清单。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    await send_help_card(bot, HELP_TEXT)


register_help("MusicUID", HELP_COMMANDS[0])
