"""Render result cards to PNG through a resident Chromium instance."""

from __future__ import annotations

import asyncio
import importlib.util
from typing import TYPE_CHECKING
from pathlib import Path

import jinja2

from gsuid_core.logger import logger

if TYPE_CHECKING:
    from playwright.async_api import Page, Browser, Playwright

TEMPLATE_PATH = Path(__file__).parent.parent / "musicuid_card" / "templates"

VIEWPORT_WIDTH = 640
VIEWPORT_HEIGHT = 800
DEVICE_SCALE_FACTOR = 2
CONTENT_TIMEOUT_MS = 15000
IDLE_TIMEOUT_MS = 8000
SETTLE_MS = 50
CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

_HINT = (
    "卡片渲染不可用：请在 GsCore 的 Python 环境执行 pip install playwright 与 "
    "python -m playwright install chromium，然后重载插件；期间指令仍以纯文本返回。"
)

# 卡片数据含用户可控文本（关键词/歌名），autoescape 必须开着
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATE_PATH)),
    autoescape=jinja2.select_autoescape(["html"]),
    undefined=jinja2.ChainableUndefined,
)

_lock = asyncio.Lock()
_playwright: Playwright | None = None
_browser: Browser | None = None
_hint_logged = False


def _log_hint(reason: str) -> None:
    """Report a broken render environment once, then stay quiet.

    Args:
        reason: Short description of why rendering failed.
    """
    global _hint_logged
    if _hint_logged:
        logger.debug(f"[MusicUID] 卡片渲染仍不可用：{reason}")
        return
    _hint_logged = True
    logger.warning(f"[MusicUID] {_HINT}（原因：{reason}）")


async def _get_browser() -> Browser | None:
    """Return the shared Chromium, or ``None`` when the environment is broken.

    Returns:
        A launched browser instance, or ``None`` when unavailable.
    """
    global _playwright, _browser
    if _browser is not None:
        return _browser
    if importlib.util.find_spec("playwright") is None:
        _log_hint("缺少 playwright 依赖")
        return None
    from playwright.async_api import async_playwright

    async with _lock:
        if _browser is not None:
            return _browser
        try:
            runtime = await async_playwright().start()
            browser = await runtime.chromium.launch(args=CHROME_ARGS)
        except Exception as e:
            _log_hint(str(e))
            return None
        _playwright, _browser = runtime, browser
        return browser


async def _discard_browser() -> None:
    """Drop the resident browser so the next render rebuilds it."""
    global _playwright, _browser
    async with _lock:
        browser, runtime = _browser, _playwright
        _browser, _playwright = None, None
    if browser is not None:
        try:
            await browser.close()
        except Exception as e:
            logger.debug(f"[MusicUID] 关闭 Chromium 时出错：{e}")
    if runtime is not None:
        try:
            await runtime.stop()
        except Exception as e:
            logger.debug(f"[MusicUID] 关闭 playwright 运行时出错：{e}")


async def close_browser() -> None:
    """Close the resident browser and its Playwright runtime."""
    await _discard_browser()


async def _open_page() -> Page | None:
    """Open a render page, rebuilding Chromium once when it went stale.

    常驻实例可能因内核崩溃或被回收而失效，那时 new_page 会抛异常；丢弃并重建，
    否则之后每张卡片都会失败，直到插件被重载。

    Returns:
        A ready page, or ``None`` when no browser could be used.
    """
    for attempt in range(2):
        browser = await _get_browser()
        if browser is None:
            return None
        try:
            return await browser.new_page(
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                device_scale_factor=DEVICE_SCALE_FACTOR,
            )
        except Exception as e:
            logger.warning(f"[MusicUID] 创建渲染页失败（第 {attempt + 1} 次）：{e}")
            await _discard_browser()
    return None


async def render_card(template_name: str, data: dict[str, object]) -> bytes | None:
    """Render a Jinja template into a PNG screenshot.

    Args:
        template_name: File name inside ``musicuid_card/templates``.
        data: Template payload, exposed as ``data`` in the template.

    Returns:
        PNG bytes, or ``None`` when the environment or template is unusable.
    """
    browser = await _get_browser()
    if browser is None:
        return None
    try:
        template = _env.get_template(template_name)
    except jinja2.TemplateNotFound:
        logger.error(f"[MusicUID] 卡片模板缺失：{template_name}")
        return None
    html = template.render(data=data)

    page = await _open_page()
    if page is None:
        return None
    try:
        try:
            await page.set_content(html, wait_until="load", timeout=CONTENT_TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
        except Exception as e:
            # 封面 CDN 抖动不应让整张卡片失败：DOM 已写好，继续截图
            logger.debug(f"[MusicUID] 卡片资源未完全加载：{e}")
        rect = await page.evaluate(
            "() => { const el = document.querySelector('.page') || document.body;"
            " const r = el.getBoundingClientRect();"
            " return { w: Math.max(1, Math.ceil(r.right)), h: Math.max(1, Math.ceil(r.bottom)) }; }"
        )
        await page.set_viewport_size({"width": rect["w"], "height": rect["h"]})
        await page.wait_for_timeout(SETTLE_MS)
        return await page.screenshot(full_page=True, type="png")
    except Exception as e:
        logger.error(f"[MusicUID] 卡片渲染失败：{e}")
        # 中途失败多半意味着浏览器实例已经坏了，丢弃以免后续卡片全部失败
        await _discard_browser()
        return None
    finally:
        try:
            await page.close()
        except Exception as e:
            logger.debug(f"[MusicUID] 关闭渲染页出错：{e}")
