"""Heartbeat service - periodic agent wake-up to check for tasks."""

import asyncio
import re
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

# Default interval: 30 minutes
DEFAULT_HEARTBEAT_INTERVAL_S = 30 * 60

# Token the agent replies with when there is nothing to report
HEARTBEAT_OK_TOKEN = "HEARTBEAT_OK"

# The prompt sent to agent during heartbeat
HEARTBEAT_PROMPT = (
    "Let me check if `HEARTBEAT.md` exists in the workspace and read its contents.\n\n"
    "IMPORTANT: If the file does not exist, is empty, or contains no actionable tasks, "
    f"you MUST reply with ONLY the following token and nothing else:\n{HEARTBEAT_OK_TOKEN}\n\n"
    "Do NOT describe what you are doing. Do NOT narrate your actions. "
    f"If there is nothing to do, just reply: {HEARTBEAT_OK_TOKEN}\n"
    "Only if there are real, actionable tasks in HEARTBEAT.md should you execute them "
    "and report the results."
)

# Patterns that indicate the agent found nothing to do but didn't use the OK token.
# These catch common LLM narration responses like "Let me check if HEARTBEAT.md exists..."
_NOOP_PATTERNS = [
    re.compile(r"no\s+(actionable\s+)?tasks?\b", re.IGNORECASE),
    re.compile(r"nothing\s+(needs|requires|to)\s+(attention|action|do|report)", re.IGNORECASE),
    re.compile(r"no\s+(instructions|items|entries)\s+(listed|found|to)", re.IGNORECASE),
    re.compile(r"file\s+(does\s+not|doesn'?t)\s+exist", re.IGNORECASE),
    re.compile(r"HEARTBEAT\.md\s+(is\s+empty|does\s+not|doesn'?t|was\s+not|wasn'?t)", re.IGNORECASE),
    re.compile(r"(empty|no\s+content|nothing)\s+in\s+HEARTBEAT", re.IGNORECASE),
    re.compile(r"(?:could|can)\s*(?:not|n'?t)\s+(?:find|read|open|access)\s+.*HEARTBEAT", re.IGNORECASE),
]


def _is_heartbeat_empty(content: str | None) -> bool:
    """Check if HEARTBEAT.md has no actionable content."""
    if not content:
        return True
    
    # Lines to skip: empty, headers, HTML comments, empty checkboxes
    skip_patterns = {"- [ ]", "* [ ]", "- [x]", "* [x]"}
    
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("<!--") or line in skip_patterns:
            continue
        return False  # Found actionable content
    
    return True


def _is_noop_response(response: str) -> bool:
    """Return True if the agent response indicates nothing actionable was found.

    The LLM sometimes narrates its actions (e.g. "Let me check if HEARTBEAT.md
    exists…") instead of replying with the exact HEARTBEAT_OK token.  This
    helper catches those cases so the narration is not forwarded to the user.
    """
    if not response:
        return True

    text = response.strip()

    # Exact or embedded token
    if HEARTBEAT_OK_TOKEN in text.upper():
        return True

    # Short responses that match known "nothing to do" patterns
    for pat in _NOOP_PATTERNS:
        if pat.search(text):
            return True

    return False


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    The agent reads HEARTBEAT.md from the workspace and executes any tasks
    listed there. If it has something to report, the response is forwarded
    to the user via on_notify. If nothing needs attention, the agent replies
    HEARTBEAT_OK and the response is silently dropped.
    """

    def __init__(
        self,
        workspace: Path,
        on_heartbeat: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S,
        enabled: bool = True,
    ):
        self.workspace = workspace
        self.on_heartbeat = on_heartbeat
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None
    
    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"
    
    def _read_heartbeat_file(self) -> str | None:
        """Read HEARTBEAT.md content."""
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None
    
    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)
    
    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
    
    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)
    
    async def _tick(self) -> None:
        """Execute a single heartbeat tick."""
        content = self._read_heartbeat_file()
        
        # Skip if HEARTBEAT.md is empty or doesn't exist
        if _is_heartbeat_empty(content):
            logger.debug("Heartbeat: no tasks (HEARTBEAT.md empty)")
            return
        
        logger.info("Heartbeat: checking for tasks...")
        
        if self.on_heartbeat:
            try:
                response = await self.on_heartbeat(HEARTBEAT_PROMPT)
                if _is_noop_response(response):
                    logger.info("Heartbeat: OK (nothing to report)")
                else:
                    logger.info("Heartbeat: completed, delivering response")
                    if self.on_notify:
                        await self.on_notify(response)
            except Exception:
                logger.exception("Heartbeat execution failed")
    
    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat."""
        if self.on_heartbeat:
            return await self.on_heartbeat(HEARTBEAT_PROMPT)
        return None
