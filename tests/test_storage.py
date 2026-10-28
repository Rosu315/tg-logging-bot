from __future__ import annotations

import time

import pytest

from tg_logging_bot.storage import Connection, Snapshot, Storage


def make_snapshot(message_id: int = 1, **overrides) -> Snapshot:
    payload = {
        "connection_id": "conn-1",
        "chat_id": 100,
        "message_id": message_id,
        "owner_id": 42,
        "sent_at": int(time.time()),
        "sender_name": "Полина",
        "text": "любое сообщение",
    }
    payload.update(overrides)
    return Snapshot(**payload)


async def test_connection_roundtrip(storage: Storage, connection: Connection) -> None:
    await storage.save_connection(connection)

    stored = await storage.get_connection("conn-1")
    assert stored is not None
    assert stored.owner_id == 42
    assert stored.can_read is True

    await storage.disable_connection("conn-1")
    stored = await storage.get_connection("conn-1")
    assert stored is not None and stored.is_enabled is False


async def test_prefs_default_and_toggle(storage: Storage) -> None:
    prefs = await storage.get_prefs(42)
    assert prefs.notify_deleted is True
    assert prefs.include_own is False

    prefs = await storage.toggle_pref(42, "notify_deleted")
    assert prefs.notify_deleted is False

    prefs = await storage.toggle_pref(42, "notify_deleted")
    assert prefs.notify_deleted is True


async def test_toggle_unknown_pref_is_rejected(storage: Storage) -> None:
    with pytest.raises(ValueError, match="unknown preference"):
        await storage.toggle_pref(42, "drop table")


async def test_snapshot_upsert_keeps_cached_file(storage: Storage) -> None:
    await storage.put_snapshot(make_snapshot(file_path="/tmp/a.jpg", media_type="photo"))
    # A later edit arrives without a freshly cached copy.
    await storage.put_snapshot(make_snapshot(text="изменено", media_type="photo"))

    stored = await storage.get_snapshot("conn-1", 100, 1)
    assert stored is not None
    assert stored.text == "изменено"
    assert stored.file_path == "/tmp/a.jpg"


async def test_mark_deleted_returns_predeletion_content_and_flags_rows(storage: Storage) -> None:
    await storage.put_snapshot(make_snapshot(1))
    await storage.put_snapshot(make_snapshot(2))

    marked = await storage.mark_deleted("conn-1", 100, [1, 2, 3])
    assert [s.message_id for s in marked] == [1, 2]
    assert all(s.deleted_at is None for s in marked)  # pre-deletion snapshot, as before

    stored = await storage.get_snapshot("conn-1", 100, 1)
    assert stored is not None
    assert stored.deleted_at is not None
    assert stored.text == "любое сообщение"  # content stays, just flagged


async def test_mark_deleted_without_ids(storage: Storage) -> None:
    assert await storage.mark_deleted("conn-1", 100, []) == []


async def test_set_snapshot_file_path_patches_existing_row(storage: Storage) -> None:
    await storage.put_snapshot(make_snapshot(1, media_type="photo"))
    affected = await storage.set_snapshot_file_path("conn-1", 100, 1, "/tmp/rescued.jpg")
    assert affected == 1

    stored = await storage.get_snapshot("conn-1", 100, 1)
    assert stored is not None
    assert stored.file_path == "/tmp/rescued.jpg"


async def test_set_snapshot_file_path_mismatched_id_is_a_silent_no_op(storage: Storage) -> None:
    await storage.put_snapshot(make_snapshot(1, media_type="photo"))
    affected = await storage.set_snapshot_file_path("conn-1", 100, 999, "/tmp/rescued.jpg")
    assert affected == 0

    stored = await storage.get_snapshot("conn-1", 100, 1)
    assert stored is not None
    assert stored.file_path is None


async def test_set_file_path_by_unique_id_ignores_mismatched_message_id(storage: Storage) -> None:
    # Simulates the view-once quirk: the real snapshot sits under message_id
    # 1, but the rescue only has a (correct) file_unique_id to go on.
    await storage.put_snapshot(
        make_snapshot(1, media_type="photo", file_unique_id="uniq-1")
    )
    affected = await storage.set_file_path_by_unique_id(
        "conn-1", 100, "uniq-1", "/tmp/rescued.jpg"
    )
    assert affected == 1

    stored = await storage.get_snapshot("conn-1", 100, 1)
    assert stored is not None
    assert stored.file_path == "/tmp/rescued.jpg"


async def test_peer_title_prefers_peers_table_over_legacy_fallback(storage: Storage) -> None:
    # No peers row yet, and the chat has only outgoing messages: falls back
    # to the generic label rather than the owner's own name.
    await storage.put_snapshot(make_snapshot(1, is_outgoing=True, sender_name="Me"))
    assert await storage.peer_title(42, "conn-1", 100) == "Chat 100"

    await storage.upsert_peer("conn-1", 100, "Полина (@polina)")
    assert await storage.peer_title(42, "conn-1", 100) == "Полина (@polina)"


async def test_purge_older_than_returns_cached_paths(storage: Storage) -> None:
    old = make_snapshot(1, file_path="/tmp/old.jpg")
    old.saved_at = int(time.time()) - 10_000
    await storage.put_snapshot(old)
    await storage.put_snapshot(make_snapshot(2))

    paths = await storage.purge_older_than(5_000)
    assert paths == ["/tmp/old.jpg"]
    assert await storage.get_snapshot("conn-1", 100, 1) is None
    assert await storage.get_snapshot("conn-1", 100, 2) is not None


async def test_forget_owner_and_stats(storage: Storage, connection: Connection) -> None:
    await storage.save_connection(connection)
    await storage.put_snapshot(make_snapshot(1))
    await storage.put_snapshot(make_snapshot(2))

    assert await storage.stats(42) == {"snapshots": 2, "connections": 1}
    assert await storage.forget_owner(42) == 2
    assert await storage.stats(42) == {"snapshots": 0, "connections": 1}
