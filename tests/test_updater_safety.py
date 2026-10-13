import hashlib
from pathlib import Path

from enc_takip.updater import _safe_restore, sha256


def test_sha256_returns_lowercase_digest(tmp_path):
    path = tmp_path / "app.exe"
    path.write_bytes(b"known test bytes")
    assert sha256(path) == hashlib.sha256(b"known test bytes").hexdigest()


def test_safe_restore_recovers_the_verified_previous_executable(tmp_path):
    target = tmp_path / "ENCTakip.exe"
    backup = tmp_path / "ENCTakip.exe.rollback"
    old_bytes = b"old working executable" * 100
    target.write_bytes(b"new but failing executable" * 100)
    backup.write_bytes(old_bytes)

    assert _safe_restore(target, backup, hashlib.sha256(old_bytes).hexdigest()) is True
    assert target.read_bytes() == old_bytes


def test_safe_restore_refuses_a_corrupt_backup(tmp_path):
    target = tmp_path / "ENCTakip.exe"
    backup = tmp_path / "ENCTakip.exe.rollback"
    target_bytes = b"current executable" * 100
    target.write_bytes(target_bytes)
    backup.write_bytes(b"corrupt backup")

    assert _safe_restore(target, backup, "0" * 64) is False
    assert target.read_bytes() == target_bytes
