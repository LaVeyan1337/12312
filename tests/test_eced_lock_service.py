import json
import time
from pathlib import Path

from enc_takip.config import Settings
from enc_takip.eced_lock_service import EcedDurum, EcedKilitServisi


def _settings(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Settings(ag_yolu=str(tmp_path), kilit_olu_esigi_sn=60)


def test_initial_status_is_free_when_shared_parent_is_accessible(tmp_path):
    service = EcedKilitServisi(_settings(tmp_path), "kullanici-a", "PC-A")
    assert service.durum().durum == EcedDurum.BOS


def test_only_one_instance_can_acquire_lock(tmp_path):
    settings = _settings(tmp_path)
    first = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    second = EcedKilitServisi(settings, "kullanici-b", "PC-B")

    assert first.al()[0] is True
    assert second.al()[0] is False
    status = second.durum()
    assert status.durum == EcedDurum.KULLANIMDA
    assert status.kullanici == "kullanici-a"
    assert first.birak()[0] is True
    assert second.durum().durum == EcedDurum.BOS


def test_heartbeat_and_release_are_owner_only(tmp_path):
    settings = _settings(tmp_path)
    first = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    second = EcedKilitServisi(settings, "kullanici-b", "PC-B")
    assert first.al()[0]
    assert second.heartbeat()[0] is False
    assert first.heartbeat()[0] is True
    assert second.birak()[0] is False
    assert first.durum().durum == EcedDurum.KULLANIMDA
    assert first.birak()[0] is True


def test_stale_lock_is_not_automatically_deleted(tmp_path):
    settings = _settings(tmp_path)
    first = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    assert first.al()[0]
    record = json.loads(first.kilit_yolu.read_text(encoding="utf-8"))
    record["heartbeat"] = time.time() - 600
    first.kilit_yolu.write_text(json.dumps(record), encoding="utf-8")

    status = EcedKilitServisi(settings, "kullanici-b", "PC-B").durum()
    assert status.durum == EcedDurum.ESKI_KILIT
    assert first.kilit_yolu.exists()


def test_stale_lock_clear_requires_explicit_confirmation(tmp_path):
    settings = _settings(tmp_path)
    first = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    assert first.al()[0]
    record = json.loads(first.kilit_yolu.read_text(encoding="utf-8"))
    record["heartbeat"] = time.time() - 600
    first.kilit_yolu.write_text(json.dumps(record), encoding="utf-8")

    admin_tool = EcedKilitServisi(settings, "admin", "PC-ADMIN")
    assert admin_tool.eski_kilidi_temizle(onay=False)[0] is False
    assert first.kilit_yolu.exists()
    assert admin_tool.eski_kilidi_temizle(onay=True)[0] is True
    assert not first.kilit_yolu.exists()


def test_corrupt_lock_is_unknown_not_free(tmp_path):
    settings = _settings(tmp_path)
    service = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    service.klasor.mkdir(parents=True, exist_ok=True)
    service.kilit_yolu.write_text("{bozuk json", encoding="utf-8")
    assert service.durum().durum == EcedDurum.BELIRSIZ


def test_non_finite_heartbeat_is_unknown_not_free(tmp_path):
    settings = _settings(tmp_path)
    service = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    service.klasor.mkdir(parents=True, exist_ok=True)
    service.kilit_yolu.write_text(
        json.dumps({
            "token": "opaque-token",
            "kullanici": "kullanici-a",
            "bilgisayar": "PC-A",
            "baslangic": time.time(),
            "heartbeat": float("nan"),
        }),
        encoding="utf-8",
    )
    assert service.durum().durum == EcedDurum.BELIRSIZ


def test_unreachable_shared_root_does_not_report_free(tmp_path):
    settings = Settings(ag_yolu=str(tmp_path / "missing-share"), kilit_olu_esigi_sn=60)
    service = EcedKilitServisi(settings, "kullanici-a", "PC-A")
    assert service.durum().durum == EcedDurum.BELIRSIZ


def test_parallel_acquisition_allows_only_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    settings = _settings(tmp_path)
    services = [
        EcedKilitServisi(settings, "kullanici-a", "PC-A"),
        EcedKilitServisi(settings, "kullanici-b", "PC-B"),
    ]
    start = Barrier(3)

    def attempt(service):
        start.wait(timeout=3)
        return service.al()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, service) for service in services]
        start.wait(timeout=3)
        results = [future.result(timeout=5) for future in futures]

    assert sum(1 for ok, _ in results if ok) == 1
