from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from aioamocrm import (
    AuthError,
    JsonFileTokenStorage,
    MemoryTokenStorage,
    TokenSet,
    TokenStorage,
)


def token(access: str = "access-1", refresh: str = "refresh-1", expires_at: float = 1000.0) -> TokenSet:
    return TokenSet(access_token=access, refresh_token=refresh, expires_at=expires_at)


def test_token_set_from_the_token_endpoint_answer() -> None:
    answer = {
        "token_type": "Bearer",
        "expires_in": 86400,
        "access_token": "eyJ0eXAi.access",
        "refresh_token": "def50200.refresh",
    }
    tokens = TokenSet.from_response(answer, now=1_700_000_000.0)
    assert tokens.access_token == "eyJ0eXAi.access"
    assert tokens.refresh_token == "def50200.refresh"
    assert tokens.expires_at == 1_700_086_400.0
    assert tokens.token_type == "Bearer"


@pytest.mark.parametrize(
    "answer",
    [
        {"access_token": "a", "expires_in": 10},
        {"refresh_token": "r", "expires_in": 10},
        {"access_token": "a", "refresh_token": "r"},
        {"access_token": "a", "refresh_token": "r", "expires_in": "soon"},
        {"access_token": "", "refresh_token": "r", "expires_in": 10},
    ],
)
def test_incomplete_token_answers_are_rejected(answer: dict[str, object]) -> None:
    with pytest.raises(AuthError):
        TokenSet.from_response(answer, now=0.0)


def test_token_set_never_shows_its_secrets() -> None:
    tokens = token("very-secret-access", "very-secret-refresh")
    for text in (repr(tokens), str(tokens)):
        assert "very-secret-access" not in text
        assert "very-secret-refresh" not in text


def test_expiry_check_uses_the_margin() -> None:
    tokens = token(expires_at=1000.0)
    assert not tokens.expires_within(60.0, now=900.0)
    assert tokens.expires_within(60.0, now=940.0)
    assert tokens.expires_within(0.0, now=1000.0)


def test_round_trip_through_a_dict() -> None:
    tokens = token()
    assert TokenSet.from_dict(tokens.to_dict()) == tokens
    assert tokens.to_dict() == {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_at": 1000.0,
        "token_type": "Bearer",
    }


async def test_memory_storage_keeps_the_last_saved_tokens() -> None:
    storage = MemoryTokenStorage()
    assert isinstance(storage, TokenStorage)
    assert await storage.load() is None
    await storage.save(token("a1"))
    await storage.save(token("a2"))
    loaded = await storage.load()
    assert loaded is not None
    assert loaded.access_token == "a2"


async def test_memory_storage_can_start_with_tokens() -> None:
    storage = MemoryTokenStorage(token("a0"))
    assert await storage.load() == token("a0")


async def test_json_file_storage_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    storage = JsonFileTokenStorage(path)
    assert isinstance(storage, TokenStorage)
    assert await storage.load() is None

    await storage.save(token("a1", "r1", 123.5))
    assert await storage.load() == token("a1", "r1", 123.5)
    assert json.loads(path.read_text(encoding="utf-8"))["refresh_token"] == "r1"

    # A second instance (another process, a restart) reads the rotated tokens.
    await storage.save(token("a2", "r2", 456.0))
    assert await JsonFileTokenStorage(str(path)).load() == token("a2", "r2", 456.0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
async def test_json_file_storage_is_private_to_the_owner(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    await JsonFileTokenStorage(path).save(token())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_a_failed_replace_keeps_the_old_file_and_leaves_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tokens.json"
    storage = JsonFileTokenStorage(path)
    await storage.save(token("old-access", "old-refresh"))

    def broken_replace(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        await storage.save(token("new-access", "new-refresh"))
    monkeypatch.undo()

    assert await storage.load() == token("old-access", "old-refresh")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tokens.json"]


async def test_a_failed_write_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tokens.json"
    storage = JsonFileTokenStorage(path)
    await storage.save(token("old-access", "old-refresh"))

    def broken_fsync(fd: int) -> None:
        raise OSError("I/O error")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="I/O error"):
        await storage.save(token("new-access", "new-refresh"))
    monkeypatch.undo()

    assert json.loads(path.read_text(encoding="utf-8"))["access_token"] == "old-access"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tokens.json"]


async def test_the_file_is_never_seen_half_written(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    storage = JsonFileTokenStorage(path)
    await storage.save(token("a0"))
    seen: list[str] = []
    real_replace = os.replace

    def watching_replace(src: str, dst: str) -> None:
        # Right before the swap the target still holds the complete previous version.
        seen.append(json.loads(Path(dst).read_text(encoding="utf-8"))["access_token"])
        seen.append(json.loads(Path(src).read_text(encoding="utf-8"))["access_token"])
        real_replace(src, dst)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "replace", watching_replace)
        await storage.save(token("a1"))
    assert seen == ["a0", "a1"]


async def test_json_file_storage_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "state" / "amo" / "tokens.json"
    await JsonFileTokenStorage(path).save(token())
    assert path.exists()


@pytest.mark.parametrize("content", ["{not json", '{"access_token": "a"}', "[]"])
async def test_a_corrupted_file_is_an_auth_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AuthError, match="tokens.json"):
        await JsonFileTokenStorage(path).load()
