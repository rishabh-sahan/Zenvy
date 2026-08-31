from app.services.session_store import RedisSessionStore


def test_save_and_get_session(fake_redis_store: RedisSessionStore):
    fake_redis_store.save_session(
        {
            "session_id": "sess-1",
            "user_id": "user_001",
            "uhid": None,
            "channel": "phone",
            "language": "en",
            "started_at": "2026-08-23T10:00:00",
            "ended_at": None,
            "status": "active",
            "session_metadata": None,
        }
    )

    stored = fake_redis_store.get_session("sess-1")
    assert stored is not None
    assert stored["user_id"] == "user_001"
    assert stored["channel"] == "phone"
    assert fake_redis_store.exists("sess-1")


def test_append_and_get_turns(fake_redis_store: RedisSessionStore):
    fake_redis_store.save_session(
        {
            "session_id": "sess-2",
            "user_id": "user_002",
            "uhid": None,
            "channel": "web",
            "language": "en",
            "started_at": "2026-08-23T10:00:00",
            "ended_at": None,
            "status": "active",
            "session_metadata": None,
        }
    )
    fake_redis_store.append_turn(
        "sess-2",
        {
            "turn_id": "turn-1",
            "session_id": "sess-2",
            "speaker": "user",
            "content": "hello",
            "language": "en",
            "sequence_number": 1,
            "created_at": "2026-08-23T10:01:00",
            "turn_metadata": None,
        },
    )

    turns = fake_redis_store.get_turns("sess-2")
    assert turns is not None
    assert len(turns) == 1
    assert turns[0]["content"] == "hello"


def test_get_turns_cache_miss(fake_redis_store: RedisSessionStore):
    assert fake_redis_store.get_turns("missing") is None


def test_session_keys_have_ttl(fake_redis_store: RedisSessionStore):
    fake_redis_store.save_session(
        {
            "session_id": "sess-ttl",
            "user_id": "user_003",
            "uhid": None,
            "channel": "sms",
            "language": "en",
            "started_at": "2026-08-23T10:00:00",
            "ended_at": None,
            "status": "active",
            "session_metadata": None,
        }
    )
    ttl = fake_redis_store.client.ttl("zenvy:session:sess-ttl")
    assert 0 < ttl <= 3600


def test_delete_session(fake_redis_store: RedisSessionStore):
    fake_redis_store.save_session(
        {
            "session_id": "sess-del",
            "user_id": "user_004",
            "uhid": None,
            "channel": "web",
            "language": "en",
            "started_at": "2026-08-23T10:00:00",
            "ended_at": None,
            "status": "active",
            "session_metadata": None,
        }
    )
    fake_redis_store.delete_session("sess-del")
    assert fake_redis_store.get_session("sess-del") is None
