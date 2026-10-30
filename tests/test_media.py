from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import Chat, Message, PhotoSize, Voice

from tg_logging_bot.media import extract_media, is_one_time, send_media

CHAT = Chat(id=100, type="private", first_name="Полина")
NOW = datetime.now(tz=timezone.utc)


class FakeBot:
    """Just enough of aiogram's Bot for send_media to drive: records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, dict]] = []

    async def send_video_note(self, chat_id: int, file_id: str, **kwargs) -> Message:
        self.calls.append(("send_video_note", chat_id, file_id, kwargs))
        return Message(message_id=99, date=NOW, chat=CHAT)

    async def send_photo(self, chat_id: int, file_id: str, **kwargs) -> Message:
        self.calls.append(("send_photo", chat_id, file_id, kwargs))
        return Message(message_id=99, date=NOW, chat=CHAT)

    async def send_message(self, chat_id: int, text: str, **kwargs) -> Message:
        self.calls.append(("send_message", chat_id, text, kwargs))
        return Message(message_id=100, date=NOW, chat=CHAT)

    def __getattr__(self, name: str):
        # media.py builds its senders dict eagerly, so every send_* method
        # must resolve even for kinds a given test doesn't exercise.
        async def _unused(chat_id: int, file_id: str, **kwargs) -> Message:
            self.calls.append((name, chat_id, file_id, kwargs))
            return Message(message_id=99, date=NOW, chat=CHAT)

        return _unused


def photo_message(**extra) -> Message:
    return Message(
        message_id=1,
        date=NOW,
        chat=CHAT,
        photo=[
            PhotoSize(file_id="small", file_unique_id="u-small", width=90, height=90),
            PhotoSize(
                file_id="big", file_unique_id="u-big", width=1280, height=1280, file_size=4096
            ),
        ],
        **extra,
    )


def test_extract_media_picks_the_largest_photo() -> None:
    media = extract_media(photo_message())
    assert media is not None
    assert media.kind == "photo"
    assert media.file_id == "big"
    assert media.file_size == 4096


def test_extract_media_handles_voice() -> None:
    message = Message(
        message_id=2,
        date=NOW,
        chat=CHAT,
        voice=Voice(file_id="v", file_unique_id="u-v", duration=3),
    )
    media = extract_media(message)
    assert media is not None
    assert media.kind == "voice"


def test_extract_media_returns_none_for_text() -> None:
    assert extract_media(Message(message_id=3, date=NOW, chat=CHAT, text="привет")) is None


def test_plain_media_is_not_one_time() -> None:
    assert is_one_time(photo_message()) is False


def test_one_time_flag_on_message_is_detected() -> None:
    assert is_one_time(photo_message(view_once=True)) is True


def test_ttl_seconds_marks_one_time() -> None:
    assert is_one_time(photo_message(ttl_seconds=5)) is True


def test_zero_ttl_is_not_one_time() -> None:
    assert is_one_time(photo_message(ttl_seconds=0)) is False


async def test_send_media_follows_up_caption_for_video_note() -> None:
    # send_video_note has no caption parameter on Telegram's side — the
    # caption must still reach the owner, as a separate message.
    bot = FakeBot()
    sent = await send_media(
        bot, 100, kind="video_note", file_id="vn1", file_path=None, caption="<b>card</b>"
    )
    assert sent is not None
    kinds = [call[0] for call in bot.calls]
    assert kinds == ["send_video_note", "send_message"]
    assert "caption" not in bot.calls[0][3]
    assert bot.calls[1][2] == "<b>card</b>"


async def test_send_media_keeps_native_caption_for_photo() -> None:
    bot = FakeBot()
    sent = await send_media(
        bot, 100, kind="photo", file_id="p1", file_path=None, caption="<b>card</b>"
    )
    assert sent is not None
    assert [call[0] for call in bot.calls] == ["send_photo"]
    assert bot.calls[0][3]["caption"] == "<b>card</b>"
