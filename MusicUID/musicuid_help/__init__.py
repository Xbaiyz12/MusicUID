"""帮助指令与全局帮助一览注册。"""

from __future__ import annotations

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

sv_help = SV("点歌帮助", priority=3)

HELP_COMMANDS = ("音乐帮助", "听歌帮助", "音乐菜单")

HELP_TEXT = """🎵 MusicUID · 多平台点歌

【点歌】
点歌 关键词        搜索并列出结果
点歌 QQ 关键词     临时切换平台
播放 关键词        搜索并直接播放第一首
听N                播放当前列表第 N 首
歌词 关键词        查看歌词

【平台】
可用平台词：网易 / QQ / 酷狗
网易云音乐支持搜索、歌词与音频下发
QQ音乐、酷狗音乐当前仅支持搜索与信息展示

【链接解析】
发送网易云单曲分享链接会自动播放
发送歌单 / 专辑链接会列出歌曲，可用「听N」选播

【配置】
在 GsCore 网页控制台 - 插件配置中修改默认平台、列表条数、
语音/文件发送、卡片渲染与链接解析开关。"""


@sv_help.on_command(HELP_COMMANDS, block=True)
async def send_help(bot: Bot, ev: Event) -> None:
    """返回插件帮助文本。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    await bot.send(HELP_TEXT)


register_help("MusicUID", HELP_COMMANDS[0])
