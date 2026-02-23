"""Browser automation tool using Playwright."""

import asyncio
import base64
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class BrowserTool(Tool):
    """
    Tool for browser automation using Playwright.

    Supports opening a browser, navigating to URLs, clicking elements,
    typing text, taking screenshots, extracting text, and closing the browser.
    The browser instance is lazily created and reused across calls.
    """

    name = "browser"
    description = (
        "Automate a headless Chromium browser. Actions: "
        "open (launch browser), navigate (go to URL), click (click element by CSS selector), "
        "type (type text into element), screenshot (capture page as PNG), "
        "get_text (extract visible text from page or element), close (shut down browser). "
        "The browser persists across calls until explicitly closed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "navigate", "click", "type", "screenshot", "get_text", "close"],
                "description": "The browser action to perform",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (required for 'navigate')",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector for target element (required for 'click' and 'type', optional for 'get_text')",
            },
            "text": {
                "type": "string",
                "description": "Text to type (required for 'type')",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in milliseconds (default: 30000)",
                "minimum": 1000,
                "maximum": 120000,
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    async def _ensure_browser(self) -> None:
        """Launch browser and page if not already running."""
        if self._page is not None:
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. "
                "Install it with: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        logger.info("Browser launched (headless Chromium)")

    async def _close_browser(self) -> None:
        """Close browser and clean up resources."""
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.info("Browser closed")

    async def execute(
        self,
        action: str,
        url: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        timeout_ms = timeout or 30000

        try:
            match action:
                case "open":
                    return await self._action_open()
                case "navigate":
                    return await self._action_navigate(url, timeout_ms)
                case "click":
                    return await self._action_click(selector, timeout_ms)
                case "type":
                    return await self._action_type(selector, text, timeout_ms)
                case "screenshot":
                    return await self._action_screenshot()
                case "get_text":
                    return await self._action_get_text(selector, timeout_ms)
                case "close":
                    return await self._action_close()
                case _:
                    return f"Error: Unknown action '{action}'"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("Browser tool error (action={}): {}", action, e)
            return f"Error: {e}"

    async def _action_open(self) -> str:
        """Launch the browser."""
        await self._ensure_browser()
        return "Browser opened (headless Chromium). Use 'navigate' to go to a URL."

    async def _action_navigate(self, url: str | None, timeout_ms: int) -> str:
        """Navigate to a URL."""
        if not url:
            return "Error: 'url' parameter is required for 'navigate' action"
        await self._ensure_browser()
        response = await self._page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        status = response.status if response else "unknown"
        title = await self._page.title()
        return f"Navigated to {url} (status: {status}, title: \"{title}\")"

    async def _action_click(self, selector: str | None, timeout_ms: int) -> str:
        """Click an element by CSS selector."""
        if not selector:
            return "Error: 'selector' parameter is required for 'click' action"
        if self._page is None:
            return "Error: Browser not open. Use 'open' or 'navigate' first."
        await self._page.click(selector, timeout=timeout_ms)
        return f"Clicked element: {selector}"

    async def _action_type(self, selector: str | None, text: str | None, timeout_ms: int) -> str:
        """Type text into an element."""
        if not selector:
            return "Error: 'selector' parameter is required for 'type' action"
        if not text:
            return "Error: 'text' parameter is required for 'type' action"
        if self._page is None:
            return "Error: Browser not open. Use 'open' or 'navigate' first."
        await self._page.fill(selector, text, timeout=timeout_ms)
        return f"Typed text into element: {selector}"

    async def _action_screenshot(self) -> str:
        """Take a screenshot of the current page."""
        if self._page is None:
            return "Error: Browser not open. Use 'open' or 'navigate' first."
        png_bytes = await self._page.screenshot(full_page=True)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        size_kb = len(png_bytes) / 1024
        url = self._page.url
        return (
            f"Screenshot captured ({size_kb:.1f} KB) of {url}\n"
            f"[base64_png:{b64[:200]}... ({len(b64)} chars total)]"
        )

    async def _action_get_text(self, selector: str | None, timeout_ms: int) -> str:
        """Extract visible text from the page or a specific element."""
        if self._page is None:
            return "Error: Browser not open. Use 'open' or 'navigate' first."
        if selector:
            element = await self._page.wait_for_selector(selector, timeout=timeout_ms)
            if not element:
                return f"Error: Element not found: {selector}"
            content = await element.inner_text()
        else:
            content = await self._page.inner_text("body")
        # Truncate very long text
        max_len = 10000
        if len(content) > max_len:
            content = content[:max_len] + f"\n... (truncated, {len(content) - max_len} more chars)"
        return content

    async def _action_close(self) -> str:
        """Close the browser."""
        if self._page is None:
            return "Browser is not open."
        await self._close_browser()
        return "Browser closed."

    async def cleanup(self) -> None:
        """Clean up browser resources. Called when the agent loop shuts down."""
        await self._close_browser()
