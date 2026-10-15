"""帮助指令与全局帮助一览注册。

帮助内容只在 ``HELP_SECTIONS`` 里维护一份：帮助图与纯文本兜底都由它生成，
避免图和文本各写一套、改了一处另一处不同步。
"""

from __future__ import annotations

from typing import Final

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

from ..musicuid_card import HelpItem, HelpSection, send_help_card

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

HELP_SECTIONS: Final[tuple[HelpSection, ...]] = (
    HelpSection(
        "点歌与播放",
        (
            HelpItem("点歌 关键词", "三平台并发搜索，编号全平台连续"),
            HelpItem("点歌 QQ 关键词", "临时指定平台（网易 / QQ / 酷狗）"),
            HelpItem("播放 关键词", "搜索并直接发送第一首"),
            HelpItem("听N", "播放列表第 N 首，10 分钟内有效"),
            HelpItem("歌词 关键词", "查看歌词（仅网易云）"),
        ),
    ),
    HelpSection(
        "账号登录",
        (
            HelpItem("QQ登录", "手机 QQ 扫码，凭据后台自动续期"),
            HelpItem("酷狗登录", "手机酷狗扫码"),
            HelpItem("网易云登录", "扫码；官方常在确认环节拒绝，扫不动用下一条"),
            HelpItem("网易云登录 <手机号>", "验证码登录，再回复「网易云验证码 <数字>」"),
            HelpItem("点歌账号", "查看各平台账号池，多个号轮换取流"),
            HelpItem("点歌状态", "查看凭据与音源配置状态"),
        ),
    ),
    HelpSection(
        "Cookie 快捷配置",
        (
            HelpItem("网易云cookie <MUSIC_U值>", "手动导入网易云凭据"),
            HelpItem("QQ音乐cookie <Cookie值>", "手动导入 QQ 音乐凭据"),
            HelpItem("酷狗cookie <Cookie值>", "手动导入酷狗凭据"),
            HelpItem("设置cookie 平台 <值>", "通用写法，平台可写 网易云 / qq / 酷狗"),
        ),
    ),
    HelpSection(
        "自建音源（可选）",
        (
            HelpItem("设置自建api <URL>", "接入自建 / 第三方音源，发「清空」移除"),
            HelpItem("自建api模式 <模式>", "fallback 官方优先兜底 / first 自建优先"),
            HelpItem("测试自建api", "测试自建服务连通性"),
        ),
    ),
    HelpSection(
        "网易云每日任务",
        (
            HelpItem("音乐签到", "每日签到 + 领取已完成任务的云贝"),
            HelpItem("控制台开关", "开「网易云每日任务」后每天 23:30 自动执行"),
        ),
    ),
    HelpSection(
        "权限管理（主人专用）",
        (
            HelpItem("点歌加白 <ID/@用户>", "添加登录白名单"),
            HelpItem("点歌删白 <ID/@用户>", "移除登录白名单"),
            HelpItem("点歌白名单", "查看当前白名单"),
        ),
    ),
    HelpSection(
        "其他",
        (
            HelpItem("发送音乐分享链接", "自动解析网易云 / QQ / 酷狗单曲或歌单"),
            HelpItem("长回复自动出图", "超过 50 字的回复渲染成图片，不再刷屏"),
        ),
    ),
)


def render_help_text() -> str:
    """把结构化帮助渲染成纯文本，供卡片渲染不可用时兜底。

    Returns:
        多行文本。
    """
    lines = ["🎵 MusicUID · 多平台点歌", ""]
    for section in HELP_SECTIONS:
        lines.append(f"【{section.title}】")
        lines.extend(f"{item.cmd}    {item.desc}" for item in section.items)
        lines.append("")
    return "\n".join(lines).rstrip()


HELP_TEXT = render_help_text()


@sv_help.on_command(HELP_COMMANDS, block=True, to_ai="查看点歌插件帮助文档与指令列表")
async def send_help(bot: Bot, ev: Event) -> None:
    """返回 MusicUID 插件使用帮助与功能指令清单。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    await send_help_card(bot, HELP_SECTIONS, HELP_TEXT)


register_help("MusicUID", HELP_COMMANDS[0])
