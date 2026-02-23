"""DingTalk/DingDing channel implementation using Stream Mode."""

import asyncio
import json
import time
from typing import Any

from loguru import logger
import httpx

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import DingTalkConfig

try:
    from dingtalk_stream import (
        DingTalkStreamClient,
        Credential,
        CallbackHandler,
        CallbackMessage,
        AckMessage,
    )
    from dingtalk_stream.chatbot import ChatbotMessage

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    # Fallback so class definitions don't crash at module level
    CallbackHandler = object  # type: ignore[assignment,misc]
    CallbackMessage = None  # type: ignore[assignment,misc]
    AckMessage = None  # type: ignore[assignment,misc]
    ChatbotMessage = None  # type: ignore[assignment,misc]


class NanobotDingTalkHandler(CallbackHandler):
    """
    Standard DingTalk Stream SDK Callback Handler.
    Parses incoming messages and forwards them to the Nanobot channel.
    """

    def __init__(self, channel: "DingTalkChannel"):
        super().__init__()
        self.channel = channel

    async def process(self, message: CallbackMessage):
        """Process incoming stream message."""
        try:
            # Parse using SDK's ChatbotMessage for robust handling
            chatbot_msg = ChatbotMessage.from_dict(message.data)

            # Extract text content; fall back to raw dict if SDK object is empty
            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            if not content:
                content = message.data.get("text", {}).get("content", "").strip()

            if not content:
                logger.warning(
                    "Received empty or unsupported message type: {}",
                    chatbot_msg.message_type,
                )
                return AckMessage.STATUS_OK, "OK"

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
            sender_name = chatbot_msg.sender_nick or "Unknown"

            # Detect group chat vs private chat.
            # conversationType "1" = private, "2" = group
            conversation_type = getattr(
                chatbot_msg, "conversation_type", None
            ) or message.data.get("conversationType")
            conversation_id = getattr(
                chatbot_msg, "conversation_id", None
            ) or message.data.get("conversationId")

            is_group = str(conversation_type) == "2"

            logger.info(
                "Received DingTalk message from {} ({}) [group={}]: {}",
                sender_name, sender_id, is_group, content,
            )

            # Forward to Nanobot via _on_message (non-blocking).
            # Store reference to prevent GC before task completes.
            task = asyncio.create_task(
                self.channel._on_message(
                    content,
                    sender_id,
                    sender_name,
                    is_group=is_group,
                    conversation_id=conversation_id,
                )
            )
            self.channel._background_tasks.add(task)
            task.add_done_callback(self.channel._background_tasks.discard)

            return AckMessage.STATUS_OK, "OK"

        except Exception as e:
            logger.error("Error processing DingTalk message: {}", e)
            # Return OK to avoid retry loop from DingTalk server
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannel(BaseChannel):
    """
    DingTalk channel using Stream Mode.

    Uses WebSocket to receive events via `dingtalk-stream` SDK.
    Uses direct HTTP API to send messages (SDK is mainly for receiving).

    Supports both private (1:1) and group chat. When the bot is @mentioned
    in a group conversation the reply is sent back to the same group via the
    ``groupMessages/send`` API. Private messages continue to use the
    ``oToMessages/batchSend`` API.
    """

    name = "dingtalk"

    def __init__(self, config: DingTalkConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: DingTalkConfig = config
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None

        # Access Token management for sending messages
        self._access_token: str | None = None
        self._token_expiry: float = 0

        # Hold references to background tasks to prevent GC
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Start the DingTalk bot with Stream Mode."""
        try:
            if not DINGTALK_AVAILABLE:
                logger.error(
                    "DingTalk Stream SDK not installed. Run: pip install dingtalk-stream"
                )
                return

            if not self.config.client_id or not self.config.client_secret:
                logger.error("DingTalk client_id and client_secret not configured")
                return

            self._running = True
            self._http = httpx.AsyncClient()

            logger.info(
                "Initializing DingTalk Stream Client with Client ID: {}...",
                self.config.client_id,
            )
            credential = Credential(self.config.client_id, self.config.client_secret)
            self._client = DingTalkStreamClient(credential)

            # Register standard handler
            handler = NanobotDingTalkHandler(self)
            self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

            logger.info("DingTalk bot started with Stream Mode")

            # Reconnect loop: restart stream if SDK exits or crashes
            while self._running:
                try:
                    await self._client.start()
                except Exception as e:
                    logger.warning("DingTalk stream error: {}", e)
                if self._running:
                    logger.info("Reconnecting DingTalk stream in 5 seconds...")
                    await asyncio.sleep(5)

        except Exception as e:
            logger.exception("Failed to start DingTalk channel: {}", e)

    async def stop(self) -> None:
        """Stop the DingTalk bot."""
        self._running = False
        # Close the shared HTTP client
        if self._http:
            await self._http.aclose()
            self._http = None
        # Cancel outstanding background tasks
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

    async def _get_access_token(self) -> str | None:
        """Get or refresh Access Token."""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        data = {
            "appKey": self.config.client_id,
            "appSecret": self.config.client_secret,
        }

        if not self._http:
            logger.warning("DingTalk HTTP client not initialized, cannot refresh token")
            return None

        try:
            resp = await self._http.post(url, json=data)
            resp.raise_for_status()
            res_data = resp.json()
            self._access_token = res_data.get("accessToken")
            # Expire 60s early to be safe
            self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60
            return self._access_token
        except Exception as e:
            logger.error("Failed to get DingTalk access token: {}", e)
            return None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through DingTalk.

        Automatically selects the correct API based on message metadata:
        - Group chat  → ``/v1.0/robot/groupMessages/send``
        - Private chat → ``/v1.0/robot/oToMessages/batchSend``
        """
        token = await self._get_access_token()
        if not token:
            return

        if not self._http:
            logger.warning("DingTalk HTTP client not initialized, cannot send")
            return

        headers = {"x-acs-dingtalk-access-token": token}

        is_group = msg.metadata.get("is_group", False)

        msg_param = json.dumps(
            {"text": msg.content, "title": "Nanobot Reply"},
            ensure_ascii=False,
        )

        if is_group:
            # groupMessages/send: sends to a group conversation
            # https://open.dingtalk.com/document/orgapp/robot-send-group-messages
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            data = {
                "robotCode": self.config.client_id,
                "openConversationId": msg.chat_id,  # chat_id is conversationId for groups
                "msgKey": "sampleMarkdown",
                "msgParam": msg_param,
            }
        else:
            # oToMessages/batchSend: sends to individual users (private chat)
            # https://open.dingtalk.com/document/orgapp/robot-batch-send-messages
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            data = {
                "robotCode": self.config.client_id,
                "userIds": [msg.chat_id],  # chat_id is the user's staffId
                "msgKey": "sampleMarkdown",
                "msgParam": msg_param,
            }

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            if resp.status_code != 200:
                logger.error("DingTalk send failed: {}", resp.text)
            else:
                logger.debug("DingTalk message sent to {} [group={}]", msg.chat_id, is_group)
        except Exception as e:
            logger.error("Error sending DingTalk message: {}", e)

    async def _on_message(
        self,
        content: str,
        sender_id: str,
        sender_name: str,
        *,
        is_group: bool = False,
        conversation_id: str | None = None,
    ) -> None:
        """Handle incoming message (called by NanobotDingTalkHandler).

        Delegates to BaseChannel._handle_message() which enforces allow_from
        permission checks before publishing to the bus.

        For group chats, ``chat_id`` is set to the ``conversationId`` so that
        the reply is routed back to the group instead of being sent as a
        private message to the sender.
        """
        try:
            # For group chats use the conversation id; for private chats fall
            # back to sender_id (1:1 conversation).
            chat_id = conversation_id if is_group and conversation_id else sender_id

            logger.info(
                "DingTalk inbound: {} from {} [chat_id={}]",
                content, sender_name, chat_id,
            )
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=str(content),
                metadata={
                    "sender_name": sender_name,
                    "platform": "dingtalk",
                    "is_group": is_group,
                    "conversation_id": conversation_id or "",
                },
            )
        except Exception as e:
            logger.error("Error publishing DingTalk message: {}", e)
