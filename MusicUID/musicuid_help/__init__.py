"""帮助指令与全局帮助一览注册。

帮助内容只在 ``HELP_SECTIONS`` 里维护一份：帮助图与纯文本兜底都由它生成，
避免图和文本各写一套。配色按分组给 ``theme``（紫 / 橙 / 绿 / 蓝），条目角标
文案逐条给，这样图里颜色分明、一眼能扫出每条指令属于哪类。
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
        "点歌搜索与播放",
        (
            HelpItem("点歌 关键词", "搜索并在列表中展示多平台匹配歌曲", "搜索"),
            HelpItem("点歌 QQ 关键词", "临时切换至指定平台（网易/QQ/酷狗）搜索", "平台"),
            HelpItem("播放 关键词", "搜索并直接发送第一首匹配歌曲的音频", "播放"),
            HelpItem("听N", "播放当前搜索列表第 N 首（支持听1、听2等）", "选播"),
            HelpItem("歌词 关键词", "获取并展示指定歌曲的歌词内容", "歌词"),
        ),
        theme="search",
    ),
    HelpSection(
        "扫码登录与账号池",
        (
            HelpItem("QQ登录", "手机 QQ 扫码，凭据后台自动续期", "扫码登录"),
            HelpItem("酷狗登录", "手机酷狗扫码一键登录", "扫码登录"),
            HelpItem("网易云登录", "扫码；官方常在确认环节拒绝，扫不动用下一条", "扫码登录"),
            HelpItem("网易云登录 <手机号>", "验证码登录，再回复「网易云验证码 <数字>」", "验证码"),
            HelpItem("点歌账号", "查看三家账号池，多号轮换取流", "账号池"),
            HelpItem("点歌删号 <平台> <序号>", "删除账号池里的账号（序号见上一条）", "删号"),
            HelpItem("点歌状态", "查看凭据与音源配置状态", "状态查询"),
        ),
        theme="login",
    ),
    HelpSection(
        "Cookie 快捷配置",
        (
            HelpItem("网易云cookie <MUSIC_U值>", "手动导入网易云凭据", "快捷导入"),
            HelpItem("QQ音乐cookie <Cookie值>", "手动导入 QQ 音乐凭据", "快捷导入"),
            HelpItem("酷狗cookie <Cookie值>", "手动导入酷狗凭据", "快捷导入"),
            HelpItem("设置cookie 平台 <值>", "通用写法（如 qq / 网易云 / 酷狗）", "通用配置"),
        ),
        theme="config",
    ),
    HelpSection(
        "自建音源（可选）",
        (
            HelpItem("设置自建api <URL>", "接入自建 / 第三方音源，发「清空」移除", "配置"),
            HelpItem("自建api模式 <模式>", "fallback 官方优先兜底 / first 自建优先", "模式"),
            HelpItem("测试自建api", "测试自建服务连通性", "测试"),
        ),
        theme="link",
    ),
    HelpSection(
        "网易云每日任务",
        (
            HelpItem("音乐签到", "每日签到 + 领取已完成任务的云贝", "任务"),
            HelpItem("控制台开关", "开「网易云每日任务」后每天 23:30 自动执行", "后台配置"),
        ),
        theme="login",
    ),
    HelpSection(
        "权限管理（主人专用）",
        (
            HelpItem("点歌加白 <用户ID/@用户>", "授权指定用户使用平台登录与凭据配置权限", "主人权限"),
            HelpItem("点歌删白 <用户ID/@用户>", "从登录白名单中移除用户", "主人权限"),
            HelpItem("点歌白名单", "查看当前已获得登录授权的白名单用户列表", "白名单"),
        ),
        theme="search",
        tag_type="perm",
    ),
    HelpSection(
        "链接解析与配置",
        (HelpItem("发送音乐分享链接", "自动解析网易云 / QQ / 酷狗单曲或歌单并播放", "自动解析"),),
        theme="link",
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
