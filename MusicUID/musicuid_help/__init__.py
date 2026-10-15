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

【点歌与播放】
点歌 关键词        三平台并发搜索（编号全平台连续）
点歌 QQ 关键词     临时指定平台（网易 / QQ / 酷狗）
播放 关键词        搜索并直接发送第一首
听N                播放列表第 N 首（10 分钟内有效）
歌词 关键词        查看歌词（仅网易云）
音乐帮助           查看这份帮助

【账号登录】
QQ登录             手机 QQ 扫码，凭据后台自动续期
酷狗登录           手机酷狗扫码
网易云登录         手机网易云扫码（官方常在确认环节拒绝，扫不动用下一条）
网易云登录 <手机号> 验证码登录，再回复「网易云验证码 <6位码>」
点歌账号           查看各平台账号池（多号轮换取流）
点歌状态           查看凭据与音源配置状态

【Cookie 快捷配置】
网易云cookie <MUSIC_U值>
QQ音乐cookie <Cookie值>
酷狗cookie <Cookie值>
设置cookie 平台 <Cookie值>
（只发指令名可查看提取指引）

【自建音源（可选）】
设置自建api <URL>   接入自建 / 第三方音源，发「清空」移除
自建api模式 <模式>  fallback 官方优先兜底 / first 自建优先
测试自建api         测试自建服务连通性

【网易云每日任务】
音乐签到           每日签到 + 领取已完成任务的云贝
（控制台可开「网易云每日任务」，每天 23:30 自动执行）

【权限管理（主人专用）】
点歌加白 <用户ID/@用户>   添加登录白名单
点歌删白 <用户ID/@用户>   移除登录白名单
点歌白名单                查看当前白名单

【其他】
• 直接发送各平台分享链接会自动解析并播放
• 所有较长的回复都会渲染成图片，不再刷屏"""


@sv_help.on_command(HELP_COMMANDS, block=True, to_ai="查看点歌插件帮助文档与指令列表")
async def send_help(bot: Bot, ev: Event) -> None:
    """返回 MusicUID 插件使用帮助与功能指令清单。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    await send_help_card(bot, HELP_TEXT)


register_help("MusicUID", HELP_COMMANDS[0])
