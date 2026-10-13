"""Ağ paylaşımından güvenli, GUI dışı ENCTakip güncelleme işlemleri.

Bu modül Tkinter nesnelerine dokunmaz. Ağ/dosya işleri worker thread'de
çalıştırılır ve sonuçlar uygulamanın mevcut Queue nesnesine bırakılır.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from queue import Queue
from typing import Any

from .version import APP_VERSION

CHECK_INTERVAL_MS = 60 * 1000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$", re.IGNORECASE)
_COPY_LOCK = threading.Lock()


def _version_tuple(v: str) -> tuple[int, ...] | None:
    """Sürüm dizisini güvenli biçimde sayısal tuple'a çevirir."""
    match = _VERSION_RE.fullmatch(str(v or "").strip())
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def daha_yeni(v: str) -> bool:
    yeni = _version_tuple(v)
    mevcut = _version_tuple(APP_VERSION)
    return yeni is not None and mevcut is not None and yeni > mevcut


def manifest_yolu(settings) -> Path:
    return Path(settings.sistem_klasoru) / "updates" / "latest.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().lower()


def _same_directory(a: Path, b: Path) -> bool:
    """Windows ağ yollarında da dosyanın manifest klasöründe kalmasını sağlar."""
    try:
        left = os.path.normcase(str(a.resolve(strict=False))).rstrip("\\/")
        right = os.path.normcase(str(b.resolve(strict=False))).rstrip("\\/")
        return left == right
    except (OSError, RuntimeError):
        return False


def kontrol_et(settings):
    """Manifesti doğrular; (manifest, hata) döndürür.

    Ağ okuması bu fonksiyon çağrıldığı thread üzerinde gerçekleşir. GUI,
    bu fonksiyonu doğrudan değil arka plan thread'i içinden çağırmalıdır.
    Ağ erişilemiyorsa bu bir hata/bilinmeyen durumdur; sahte "güncel" sonucu
    yaratmadan (None, hata) döner.
    """
    p = manifest_yolu(settings)
    try:
        # exists() tek başına erişim sorunlarını gizleyebildiği için stat()
        # kullanıyor, yalnızca gerçekten bulunmayan manifesti normal sayıyoruz.
        try:
            p.stat()
        except FileNotFoundError:
            # Manifest veya updates klasörü henüz oluşturulmamış olabilir.
            # UNC kopukluğunda da FileNotFoundError görülebildiğinden üst
            # dizinlerden en az birinin gerçekten erişilebilir olduğunu doğrula.
            accessible = False
            for candidate in (p.parent, Path(settings.sistem_klasoru), Path(settings.ag_yolu)):
                try:
                    candidate.stat()
                    accessible = True
                    break
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    raise OSError(f"Güncelleme paylaşımına erişilemiyor: {type(exc).__name__}") from exc
            if not accessible:
                raise OSError("Güncelleme paylaşımına erişilemiyor; üst klasörler doğrulanamadı.")
            return None, None

        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Güncelleme manifesti JSON nesnesi değil.")

        version = str(data.get("version", "")).strip()
        parsed = _version_tuple(version)
        if parsed is None:
            raise ValueError("Manifestte geçersiz sürüm numarası var.")
        if not daha_yeni(version):
            return None, None

        raw_path = str(data.get("path", "")).strip()
        if not raw_path:
            raise ValueError("Manifestte güncelleme dosyasının yolu yok.")

        exe = Path(raw_path)
        if not exe.is_absolute():
            exe = p.parent / exe
        exe = exe.resolve(strict=False)

        if not _same_directory(exe.parent, p.parent):
            raise ValueError("Güncelleme dosyası beklenen updates klasöründe değil.")

        expected_name = f"ENCTakip_{version}.exe"
        if exe.name.casefold() != expected_name.casefold():
            raise ValueError("Güncelleme dosyasının adı sürümle eşleşmiyor.")

        digest = str(data.get("sha256", "")).strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("Manifestte geçerli SHA-256 bulunmuyor; güncelleme reddedildi.")

        try:
            info = exe.stat()
        except FileNotFoundError:
            return None, "Güncelleme dosyası bulunamadı."
        if not exe.is_file() or info.st_size < 1024:
            raise ValueError("Güncelleme dosyası eksik veya geçerli bir EXE boyutunda değil.")

        data["path"] = str(exe)
        data["version"] = version
        data["sha256"] = digest
        return data, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _kuyruga_yaz(kuyruk: Queue, tip: str, veri: Any = None) -> None:
    try:
        kuyruk.put_nowait((tip, veri))
    except Exception:
        # GUI kapanırken kuyruk artık tüketilmiyor olabilir. Güncelleme
        # thread'inin bunu uygulama çökmesine çevirmesine izin verme.
        pass


def _dengeli_kopyala(kaynak: Path, hedef: Path) -> None:
    hedef.parent.mkdir(parents=True, exist_ok=True)
    with kaynak.open("rb") as src, hedef.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())


def guncellemeyi_baslat(settings, manifest: dict, kuyruk: Queue) -> None:
    """Güncellemeyi bağımsız worker'da hazırlar ve updater'ı başlatır.

    GUI nesnesi bu fonksiyona verilmez ve worker içinden Tkinter çağrısı
    yapılmaz. Sonuçlar uygulamanın olay kuyruğuna yazılır.
    """
    # Birden fazla aynı çağrı kaynak kopyalarının/yardımcıların çakışmasını
    # önlesin. GUI tarafında da çalışma bayrağı tutulur.
    def worker() -> None:
        staged: Path | None = None
        try:
            with _COPY_LOCK:
                raw_version = str(manifest.get("version", "")).strip()
                if _version_tuple(raw_version) is None or not daha_yeni(raw_version):
                    raise ValueError("Güncelleme sürümü geçersiz veya artık yeni değil.")

                expected_hash = str(manifest.get("sha256", "")).strip().lower()
                if not _SHA256_RE.fullmatch(expected_hash):
                    raise ValueError("SHA-256 yok/geçersiz; güvenlik için güncelleme başlatılmadı.")

                raw_source = str(manifest.get("path", "")).strip()
                if not raw_source:
                    raise ValueError("Güncelleme kaynağının yolu boş.")
                source = Path(raw_source).resolve(strict=True)
                update_dir = manifest_yolu(settings).parent
                if not _same_directory(source.parent, update_dir):
                    raise ValueError("Güncelleme kaynağı beklenen updates klasörünün dışında.")
                expected_name = f"ENCTakip_{raw_version}.exe"
                if source.name.casefold() != expected_name.casefold():
                    raise ValueError("Güncelleme dosyasının adı sürümle eşleşmiyor.")
                if not source.is_file() or source.stat().st_size < 1024:
                    raise ValueError("Güncelleme kaynağı eksik veya geçerli bir EXE boyutunda değil.")

                target = Path(sys.executable).resolve()
                if target.name.casefold() in {"python.exe", "pythonw.exe", "python", "pythonw"}:
                    _kuyruga_yaz(kuyruk, "GUNCELLEME_HATA", "Geliştirme ortamında EXE güncellemesi yapılmadı.")
                    return
                if target.name.casefold() != "enctakip.exe":
                    raise ValueError(f"Beklenmeyen uygulama adı; hedef güncellenmedi: {target.name}")

                updater = target.with_name("ENCTakipUpdater.exe")
                if not updater.is_file():
                    raise FileNotFoundError(f"ENCTakipUpdater.exe bulunamadı: {updater}")

                # Her deneme için benzersiz dosya: eski/yarım kopya ile karışmaz.
                staged = Path(tempfile.gettempdir()) / (
                    f"ENCTakip_update_{raw_version}_{uuid.uuid4().hex}.exe"
                )
                _dengeli_kopyala(source, staged)
                actual_hash = sha256(staged)
                if actual_hash != expected_hash:
                    raise ValueError("Güncelleme SHA-256 doğrulaması başarısız; çalışan EXE korunuyor.")

                flags = 0
                if os.name == "nt":
                    flags = (
                        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    )
                subprocess.Popen(
                    [str(updater), str(os.getpid()), str(staged), str(target), expected_hash],
                    cwd=str(updater.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=flags,
                )
                _kuyruga_yaz(kuyruk, "GUNCELLEME_BASLADI", {"version": raw_version})
                staged = None  # Dosyanın yaşam döngüsü artık updater'a ait.
        except Exception as exc:
            _kuyruga_yaz(kuyruk, "GUNCELLEME_HATA", f"{type(exc).__name__}: {exc}")
        finally:
            if staged is not None:
                try:
                    staged.unlink(missing_ok=True)
                except OSError:
                    pass

    threading.Thread(
        target=worker,
        daemon=True,
        name="ENCTakip-GuncellemeHazirla",
    ).start()
