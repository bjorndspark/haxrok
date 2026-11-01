"""POST /api/v1/logs/frontend — browser log ingest: unauthenticated,
best-effort (always succeeds), appends formatted lines to FRONTEND_LOG_FILE."""

from app.core.config import settings
from app.core.logging import setup_logging

_OK = {"success": True, "data": None}


def _payload() -> dict:
    return {
        "session_id": "abcd1234-0000-0000-0000-000000000000",
        "entries": [
            {
                "ts": "2026-09-21T04:56:27.123Z",
                "level": "INFO",
                "source": "console",
                "message": "hello from browser",
            },
            {
                "ts": "2026-09-21T04:56:28.000Z",
                "level": "ERROR",
                "source": "window.onerror",
                "message": "boom",
            },
        ],
    }


async def test_ingest_frontend_logs_writes_file(client, tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "frontend" / "app.log"
    monkeypatch.setattr(settings, "FRONTEND_LOG_FILE", str(log_file))

    resp = await client.post("/api/v1/logs/frontend", json=_payload())

    assert resp.status_code == 200
    assert resp.json() == _OK
    lines = log_file.read_text().splitlines()
    assert len(lines) == 2
    assert "INFO" in lines[0] and "hello from browser" in lines[0]
    assert "ERROR" in lines[1] and "boom" in lines[1]
    assert "abcd1234" in lines[0]
    assert "console" in lines[0] and "window.onerror" in lines[1]


async def test_ingest_frontend_logs_appends_across_batches(client, tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "app.log"
    monkeypatch.setattr(settings, "FRONTEND_LOG_FILE", str(log_file))

    resp = await client.post("/api/v1/logs/frontend", json=_payload())
    assert resp.status_code == 200
    resp = await client.post("/api/v1/logs/frontend", json=_payload())
    assert resp.status_code == 200

    assert len(log_file.read_text().splitlines()) == 4


async def test_ingest_frontend_logs_escapes_newlines(client, tmp_path, monkeypatch) -> None:
    """A newline in an entry must not forge a second log line."""
    log_file = tmp_path / "app.log"
    monkeypatch.setattr(settings, "FRONTEND_LOG_FILE", str(log_file))
    payload = _payload()
    payload["entries"] = [
        {**payload["entries"][0], "message": "real\n2026-01-01 00:00:00 | ERROR | forged"}
    ]

    await client.post("/api/v1/logs/frontend", json=payload)

    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    assert "real\\n2026-01-01" in lines[0]


async def test_ingest_frontend_logs_disabled_still_succeeds(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FRONTEND_LOG_FILE", "")

    resp = await client.post("/api/v1/logs/frontend", json=_payload())

    assert resp.status_code == 200
    assert resp.json() == _OK
    assert not (tmp_path / "app.log").exists()


async def test_ingest_frontend_logs_rejects_oversized_batch(client) -> None:
    payload = _payload()
    payload["entries"] = payload["entries"] * 300
    resp = await client.post("/api/v1/logs/frontend", json=payload)
    assert resp.status_code == 422


def test_frontend_log_rotates_into_archive_on_startup(tmp_path, monkeypatch) -> None:
    """setup_logging() archives a non-empty frontend log so app.log always
    holds only the current session."""
    log_file = tmp_path / "frontend" / "app.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("old session\n")
    monkeypatch.setattr(settings, "LOG_FILE", "")
    monkeypatch.setattr(settings, "FRONTEND_LOG_FILE", str(log_file))

    setup_logging()

    assert not log_file.exists()
    archived = list((tmp_path / "frontend" / "archive").rglob("app.log.*"))
    assert len(archived) == 1
    assert archived[0].read_text() == "old session\n"
