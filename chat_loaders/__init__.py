"""Chat history loader registry.

Each loader parses one source format (Telegram Desktop / QQChatExporter /
WeChat / ChatGPT / Claude / generic JSON) and yields ``ChatSegment``
records. The common abstraction lets the rest of the app stay
format-agnostic.
"""
from __future__ import annotations

from .base import ChatSegment, NormalizedMessage, detect_format, iter_messages
from .telegram import TelegramDesktopLoader
from .qq_chat_exporter import QQChatExporterLoader
from .wechat import WeChatLoader
from .chatgpt import ChatGPTLoader
from .claude import ClaudeLoader
from .generic_json import GenericJSONLoader

# Registry: format key -> loader class
LOADERS = {
    "telegram": TelegramDesktopLoader,
    "qq_chat_exporter": QQChatExporterLoader,
    "wechat": WeChatLoader,
    "chatgpt": ChatGPTLoader,
    "claude": ClaudeLoader,
    "generic": GenericJSONLoader,
}


def get_loader(format_key: str):
    cls = LOADERS.get(format_key)
    if cls is None:
        raise ValueError(f"unknown chat format: {format_key}")
    return cls()


__all__ = [
    "ChatSegment",
    "NormalizedMessage",
    "LOADERS",
    "detect_format",
    "get_loader",
    "iter_messages",
]