"""插件自带字体的注册入口。

pytakumi 只渲染已注册的字体，系统装了字体也不会自动生效；Docker / Linux
镜像通常没装韩文字体，歌名里的谚文会变成方框。这里把插件自带的
Nanum Gothic（OFL-1.1）注册成框架的谚文回退脸，装插件即不缺字形。
"""

from pathlib import Path

from gsuid_core.logger import logger

FONTS_DIR = Path(__file__).resolve().parent.parent / "resource" / "fonts"
KOREAN_FONT_PATH = FONTS_DIR / "NanumGothic-Regular.ttf"


def register_bundled_fonts() -> bool:
    """注册插件自带字体；返回是否真的注册成功。

    失败只记日志，不影响其它渲染路径：框架自己的系统字体探测仍然生效。
    """
    # 老 core 没有这个入口，缺它只影响自带字体，不该拖垮插件加载。
    try:
        from gsuid_core.utils.html_render import KOREAN_FONT_NAME, register_extra_font
    except ImportError:
        logger.debug("[MusicUID] 当前 core 未提供字体注册入口，跳过自带字体")
        return False

    if not KOREAN_FONT_PATH.is_file():
        logger.warning(f"[MusicUID] 自带韩文字体缺失：{KOREAN_FONT_PATH}")
        return False

    data = KOREAN_FONT_PATH.read_bytes()
    if not register_extra_font(data, name=KOREAN_FONT_NAME):
        logger.warning("[MusicUID] 自带韩文字体注册失败，韩文可能显示为方框")
        return False
    logger.info(f"[MusicUID] 已注册自带韩文字体：{KOREAN_FONT_NAME}")
    return True
