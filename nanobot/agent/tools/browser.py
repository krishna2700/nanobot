"""Browser control tool using Playwright."""

import asyncio
import base64
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool

# Lazy-loaded browser state shared across tool invocations.
_browser_state: dict[str, Any] = {
    "playwright": None,
    "browser": None,
    "page": None,
}


async def _ensure_browser() -> Any:
    """Lazily launch a Chromium browser and return the active page."""
    if _browser_state["page"] is not None:
        return _browser_state["page"]

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. "
            "Install it with: pip install playwright && playwright install chromium"
        )

    pw = await async_playwright().start()
    _browser_state["playwright"] = pw

    try:
        browser = await pw.chromium.launch(headless=True)
    except Exception:
        # Mutually-exclusive fallback: browser binaries may not be installed yet.
        raise RuntimeError(
            "Chromium browser not found. "
            "Run: playwright install chromium"
        )

    _browser_state["browser"] = browser
    page = await browser.new_page()
    _browser_state["page"] = page
    return page


async def _close_browser() -> str:
    """Close the browser and clean up resources."""
    page = _browser_state.get("page")
    browser = _browser_state.get("browser")
    pw = _browser_state.get("playwright")

    if page:
        try:
            await page.close()
        except Exception:
            pass
    if browser:
        try:
            await browser.close()
        except Exception:
            pass
    if pw:
        try:
            await pw.stop()
        except Exception:
            pass

    _browser_state["page"] = None
    _browser_state["browser"] = None
    _browser_state["playwright"] = None
    return "Browser closed."


class BrowserTool(Tool):
    """Control a headless browser: navigate, click, type, screenshot, extract text."""

    name = "browser"
    description = (
        "Control a headless browser. Actions: "
        "navigate (go to URL), click (CSS selector), type (CSS selector + text), "
        "screenshot (capture page), get_text (extract visible text), close (shut down browser)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "click", "type", "screenshot", "get_text", "close"],
                "description": "The browser action to perform.",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (required for 'navigate').",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector for the target element (required for 'click' and 'type').",
            },
            "text": {
                "type": "string",
                "description": "Text to type into the element (required for 'type').",
            },
            "wait": {
                "type": "integer",
                "description": "Extra milliseconds to wait after the action (optional, default 0).",
                "minimum": 0,
                "maximum": 30000,
            },
        },
        "required": ["action"],
    }

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace or Path.cwd()

    async def execute(self, action: str, **kwargs: Any) -> str:
        handler = {
            "navigate": self._navigate,
            "click": self._click,
            "type": self._type,
            "screenshot": self._screenshot,
            "get_text": self._get_text,
            "close": self._close,
        }.get(action)

        if handler is None:
            return f"Error: Unknown action '{action}'. Use: navigate, click, type, screenshot, get_text, close."

        try:
            result = await handler(**kwargs)
            # Optional post-action wait
            wait_ms = kwargs.get("wait", 0)
            if wait_ms and wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000)
            return result
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("Browser tool error ({}): {}", action, e)
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _navigate(self, url: str | None = None, **_: Any) -> str:
        if not url:
            return "Error: 'url' is required for navigate action."
        page = await _ensure_browser()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else "unknown"
        title = await page.title()
        return f"Navigated to {url} (status {status}, title: \"{title}\")."

    async def _click(self, selector: str | None = None, **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for click action."
        page = await _ensure_browser()
        await page.click(selector, timeout=10000)
        return f"Clicked element: {selector}"

    async def _type(self, selector: str | None = None, text: str | None = None, **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for type action."
        if text is None:
            return "Error: 'text' is required for type action."
        page = await _ensure_browser()
        await page.fill(selector, text, timeout=10000)
        return f"Typed into {selector}: \"{text[:80]}{'…' if len(text) > 80 else ''}\""

    async def _screenshot(self, **_: Any) -> str:
        page = await _ensure_browser()
        screenshot_dir = self._workspace / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        import time
        filename = f"screenshot_{int(time.time() * 1000)}.png"
        filepath = screenshot_dir / filename

        await page.screenshot(path=str(filepath), full_page=False)
        return f"Screenshot saved to {filepath}"

    async def _get_text(self, selector: str | None = None, **_: Any) -> str:
        page = await _ensure_browser()
        if selector:
            element = await page.query_selector(selector)
            if not element:
                return f"Error: Element not found: {selector}"
            text = await element.inner_text()
        else:
            text = await page.inner_text("body")

        # Truncate very long text
        max_len = 8000
        if len(text) > max_len:
            text = text[:max_len] + f"\n... (truncated, {len(text) - max_len} more chars)"
        return text

    async def _close(self, **_: Any) -> str:
        return await _close_browser()
