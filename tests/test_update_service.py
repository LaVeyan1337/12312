import hashlib
import json
from pathlib import Path

from enc_takip import update_service as updates
from enc_takip.config import Settings


def _manifest_fixture(tmp_path: Path, version: str = "10.0.6"):
    updates_dir = tmp_path / ".encev_sistem" / "updates"
    updates_dir.mkdir(parents=True)
    exe = updates_dir / f"ENCTakip_{version}.exe"
    exe.write_bytes(b"test executable bytes" * 100)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    settings = Settings(ag_yolu=str(tmp_path))
    manifest_path = updates.manifest_yolu(settings)
    manifest_path.write_text(json.dumps({
        "version": version,
        "path": str(exe),
        "sha256": digest,
    }), encoding="utf-8")
    return settings, manifest_path, exe, digest


def test_valid_new_manifest_is_accepted(tmp_path, monkeypatch):
    settings, _, exe, digest = _manifest_fixture(tmp_path)
    monkeypatch.setattr(updates, "APP_VERSION", "10.0.5")

    manifest, error = updates.kontrol_et(settings)

    assert error is None
    assert manifest is not None
    assert Path(manifest["path"]) == exe.resolve()
    assert manifest["sha256"] == digest


def test_missing_sha256_is_rejected(tmp_path, monkeypatch):
    settings, manifest_path, exe, _ = _manifest_fixture(tmp_path)
    monkeypatch.setattr(updates, "APP_VERSION", "10.0.5")
    manifest_path.write_text(json.dumps({"version": "10.0.6", "path": str(exe)}), encoding="utf-8")

    manifest, error = updates.kontrol_et(settings)

    assert manifest is None
    assert "SHA-256" in error


def test_update_path_outside_updates_directory_is_rejected(tmp_path, monkeypatch):
    settings, manifest_path, exe, digest = _manifest_fixture(tmp_path)
    monkeypatch.setattr(updates, "APP_VERSION", "10.0.5")
    external = tmp_path / "outside.exe"
    external.write_bytes(exe.read_bytes())
    manifest_path.write_text(json.dumps({
        "version": "10.0.6", "path": str(external), "sha256": digest
    }), encoding="utf-8")

    manifest, error = updates.kontrol_et(settings)

    assert manifest is None
    assert "updates klasöründe değil" in error


def test_equal_or_old_version_is_ignored(tmp_path, monkeypatch):
    settings, manifest_path, exe, digest = _manifest_fixture(tmp_path, "10.0.5")
    monkeypatch.setattr(updates, "APP_VERSION", "10.0.5")
    manifest_path.write_text(json.dumps({
        "version": "10.0.5", "path": str(exe), "sha256": digest
    }), encoding="utf-8")

    manifest, error = updates.kontrol_et(settings)

    assert manifest is None
    assert error is None


def test_malformed_version_is_not_treated_as_newer(monkeypatch):
    monkeypatch.setattr(updates, "APP_VERSION", "10.0.5")
    assert not updates.daha_yeni("10.x.6")
    assert not updates.daha_yeni("../11.0.0")
    assert updates.daha_yeni("10.0.6")


def test_missing_manifest_means_no_update_when_share_parent_is_accessible(tmp_path):
    settings = Settings(ag_yolu=str(tmp_path))
    (tmp_path / ".encev_sistem").mkdir()
    manifest, error = updates.kontrol_et(settings)
    assert manifest is None
    assert error is None


def test_too_small_update_executable_is_rejected(tmp_path, monkeypatch):
    updates_dir = tmp_path / ".encev_sistem" / "updates"
    updates_dir.mkdir(parents=True)
    exe = updates_dir / "ENCTakip_10.0.6.exe"
    exe.write_bytes(b"too small")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    settings = Settings(ag_yolu=str(tmp_path))
    updates.manifest_yolu(settings).write_text(json.dumps({
        "version": "10.0.6", "path": str(exe), "sha256": digest
    }), encoding="utf-8")
    monkeypatch.setattr(updates, "APP_VERSION", "10.0.5")

    manifest, error = updates.kontrol_et(settings)

    assert manifest is None
    assert "geçerli bir EXE boyutunda değil" in error
