"""帮助指令与全局帮助一览注册。"""

from __future__ import annotations

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

from ..musicuid_card import send_help_card

# 帮助服务优先级设为 1，确保不会被通用搜索指令抢占
sv_help = SV("点歌帮助", priority=1)

HELP_COMMANDS = (
    "点歌帮助",
    "音乐帮助",
    "听歌帮助",
    "点歌菜单",
    "音乐菜单",
    "点歌help",
    "音乐help",
)

HELP_TEXT = """🎵 MusicUID · 多平台点歌

【点歌】
点歌 关键词        搜索并列出结果（支持并发搜索网易/QQ/酷狗）
点歌 酷狗 关键词   临时切换指定平台（网易/QQ/酷狗）
播放 关键词        搜索并直接发送第一首歌曲语音/音频
听N                播放当前搜索列表第 N 首（如听1、听2）
歌词 关键词        查看歌曲完整歌词

【手机扫码登录（酷狗音乐）】
酷狗登录 / 点歌登录 酷狗        手机扫码一键登录酷狗音乐（解锁完整播放与音质）
点歌登录 / 音乐登录            查看登录与凭据配置菜单
点歌状态 / 点歌登录状态        查看三平台凭证配置状态

【Cookie 快捷配置（网易云 / QQ音乐）】
网易云cookie <MUSIC_U值>       快捷导入网易云音乐 Cookie
QQ音乐cookie <Cookie值>        直接设置 QQ 音乐 Cookie
设置cookie 平台 <Cookie值>     手动绑定任意平台 Cookie
（发送「网易云cookie」或「QQ音乐cookie」可查看极简提取指引）

【权限管理（主人专用）】
点歌加白 <用户ID/@用户>        添加用户至登录白名单
点歌删白 <用户ID/@用户>        从白名单移除用户
点歌白名单                     查看当前已授权用户列表

【平台与解析】
• 支持平台：网易云音乐、QQ 音乐、酷狗音乐
• 链接解析：直接发送各平台分享链接，自动解析并播放"""


@sv_help.on_command(HELP_COMMANDS, block=True, to_ai="查看点歌插件帮助文档与指令列表")
async def send_help(bot: Bot, ev: Event) -> None:
    """返回 MusicUID 插件使用帮助与功能指令清单。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    await send_help_card(bot, HELP_TEXT)


register_help("MusicUID", HELP_COMMANDS[0])
