"""The Telegram Bot API, behind the `Poster` protocol so tests never reach the network.

The token is part of every URL, so URLs are never logged or put in an error message. Link previews stay
off: with a preview, Telegram's servers would fetch each job link, and many of those are LinkedIn pages.
"""

from __future__ import annotations

from typing import Final

from jobfinder.http import FetchError, Poster

API_BASE: Final = "https://api.telegram.org"
MESSAGE_LIMIT: Final = 4096


class TelegramBot:
    def __init__(self, token: str, poster: Poster) -> None:
        self._token = token
        self._poster = poster

    def _call(self, method: str, payload: dict[str, object]) -> object:
        answer = self._poster.post_json(f"{API_BASE}/bot{self._token}/{method}", payload, {})
        if not isinstance(answer, dict) or answer.get("ok") is not True:
            raise FetchError(f"Telegram refused {method}")
        result: object = answer.get("result")
        return result

    def send_message(self, chat_id: str, text: str) -> None:
        """Send `text`, written in Telegram's HTML subset, to one chat."""
        self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })

    def recent_chat_ids(self) -> list[str]:
        """The chats that wrote to the bot lately, so the user can find the id to put in `.env`."""
        updates = self._call("getUpdates", {"allowed_updates": ["message"]})
        chats: dict[str, None] = {}
        for update in updates if isinstance(updates, list) else []:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            if isinstance(chat, dict) and "id" in chat:
                chats[str(chat["id"])] = None
        return list(chats)
