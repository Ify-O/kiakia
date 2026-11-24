"""Unit tests for the Telegram parser. Fake data only (tests/fixtures)."""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kiakia.adapters.telegram.parser import parse_updates
from kiakia.domain.models import Channel, Direction, Message, MessageKind, Sender

FIXTURE = Path(__file__).parent.parent / "fixtures" / "telegram_updates.json"


@pytest.fixture
def result():
    updates = json.loads(FIXTURE.read_text())["result"]
    return parse_updates(updates)


def by_id(result, chat_id, message_id):
    matches = [m for m in result.messages
               if m.external_chat_id == chat_id and m.external_message_id == message_id]
    return matches[-1]  # the edited version comes last


def test_counts(result):
    # 5 usable messages (incl. one edit), 2 ignored (group + callback), 1 rejected (no date)
    assert len(result.messages) == 5
    assert result.ignored == 2
    assert len(result.rejects) == 1
    assert result.rejects[0].ref == "500007"
    assert "date" in result.rejects[0].reason


def test_cursor_moves_past_everything_seen(result):
    # Even the rejected/ignored updates must be confirmed, or they'd block the queue.
    assert result.next_cursor == "500009"


def test_text_message_is_cleaned_and_in_utc(result):
    m = result.messages[0]
    assert m.channel is Channel.TELEGRAM
    assert m.direction is Direction.IN
    assert m.kind is MessageKind.TEXT
    assert m.body == "Hello, how much is the blue bag?"
    assert m.sent_at == datetime.fromtimestamp(1795600000, tz=UTC)
    assert m.sender.display_name == "Ada Test"
    assert m.sender.username == "ada_test"


def test_voice_note_keeps_file_id_for_transcription_later(result):
    m = by_id(result, "9001", "12")
    assert m.kind is MessageKind.VOICE
    assert m.media_ref == "VOICE-FILE-1"
    assert m.body is None


def test_photo_uses_largest_size_and_caption(result):
    m = by_id(result, "9002", "3")
    assert m.kind is MessageKind.IMAGE
    assert m.media_ref == "PHOTO-LARGE"
    assert m.body == "Do you have this in red?"
    assert m.sender.display_name == "@buyer_two"  # no first name -> username


def test_edited_message_has_same_key_and_new_text(result):
    m = by_id(result, "9001", "11")
    assert m.body == "Hello, how much is the blue bag in size L?"
    assert m.sent_at == datetime.fromtimestamp(1795600000, tz=UTC)  # original time


def test_sender_without_any_name_gets_a_fallback(result):
    m = by_id(result, "9005", "4")
    assert m.kind is MessageKind.STICKER
    assert m.sender.display_name == "Telegram user 9005"


def test_garbage_never_crashes_the_run():
    r = parse_updates([None, "text", {}, {"update_id": "x"}, {"update_id": 7, "message": {"chat": 5}}])
    assert r.messages == []
    assert len(r.rejects) == 5
    assert r.next_cursor == "8"


def test_empty_batch_has_no_cursor():
    r = parse_updates([])
    assert r.next_cursor is None and r.messages == []


def test_message_refuses_naive_datetime():
    with pytest.raises(ValueError):
        Message(Channel.TELEGRAM, "1", "1", Direction.IN, MessageKind.TEXT,
                datetime(2026, 1, 1), Sender("1", "x"))
