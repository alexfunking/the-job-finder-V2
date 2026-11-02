"""The Telegram Bot API, behind the `Poster` protocol so tests never reach the network.

The token is part of every URL, so URLs are never logged or put in an error message. Link previews stay
off: with a preview, Telegram's servers would fetch each job link, and many of those are LinkedIn pages.
Button presses are pulled at each run, so no process has to stay up; Telegram keeps them for 24 hours.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from jobfinder.http import FetchError, Poster

API_BASE: Final = "https://api.telegram.org"


@dataclass(frozen=True, slots=True)
class Button:
    text: str
    data: str  # what Telegram hands back when the button is pressed, at most 64 bytes


@dataclass(frozen=True, slots=True)
class ButtonPress:
    user_id: str
    chat_id: str
    message_id: int
    data: str


def _keyboard(buttons: Sequence[Button]) -> dict[str, object]:
    return {"inline_keyboard": [[{"text": button.text, "callback_data": button.data} for button in buttons]]}


def _press(update: dict[object, object]) -> ButtonPress | None:
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return None
    sender, message, data = query.get("from"), query.get("message"), query.get("data")
    chat = message.get("chat") if isinstance(message, dict) else None
    if not isinstance(sender, dict) or not isinstance(message, dict) or not isinstance(chat, dict):
        return None
    message_id = message.get("message_id")
    if not isinstance(data, str) or not isinstance(message_id, int) or "id" not in sender or "id" not in chat:
        return None
    return ButtonPress(str(sender["id"]), str(chat["id"]), message_id, data)


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

    def send_message(
        self, chat_id: str, text: str, buttons: Sequence[Button] = (), quiet: bool = False
    ) -> None:
        """Send `text`, written in Telegram's HTML subset, to one chat; `quiet` means no notification sound."""
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "disable_notification": quiet,
        }
        if buttons:
            payload["reply_markup"] = _keyboard(buttons)
        self._call("sendMessage", payload)

    def replace_buttons(self, chat_id: str, message_id: int, buttons: Sequence[Button]) -> None:
        self._call("editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id, "reply_markup": _keyboard(buttons),
        })

    def read_presses(self) -> tuple[list[ButtonPress], int | None]:
        """The waiting button presses, and the offset to confirm them with (None when nothing is waiting)."""
        updates = self._call("getUpdates", {"allowed_updates": ["callback_query"]})
        presses: list[ButtonPress] = []
        offset: int | None = None
        for update in updates if isinstance(updates, list) else []:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                continue
            offset = update["update_id"] + 1
            if (press := _press(update)) is not None:
                presses.append(press)
        return presses, offset

    def confirm_presses(self, offset: int) -> None:
        """Tell Telegram everything before `offset` was handled, so it is not delivered again."""
        self._call("getUpdates", {"offset": offset, "limit": 1, "allowed_updates": ["callback_query"]})

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
