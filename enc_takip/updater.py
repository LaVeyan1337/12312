"""Windows için güvenli ENCTakip EXE güncelleme yardımcısı.

Argümanlar: <uygulama_pid> <hazır_exe> <hedef_exe> <beklenen_sha256>
Bu dosya GUI'den bağımsız bir süreçte çalışır.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from ctypes import wintypes

LOG_FILE = Path(os.environ.get("TEMP") or Path.home()) / "ENCTakip_updater.log"
MIN_EXE_BYTES = 1024
PROCESS_GRACE_SECONDS = 12
APP_EXIT_TIMEOUT_SECONDS = 30


def log(message: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n")
    except Exception:
        pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _norm_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path.resolve(strict=False))))


def _wait_for_original_process(pid: int, target: Path, timeout_seconds: int) -> bool:
    """PID'nin gerçekten hedef ENCTakip.exe olduğunu doğrulayarak kapanmasını bekler.

    OpenProcess erişim hatası durumunda "kapandı" varsayılmaz; yanlış pozitif
    bir sonuç çalışan EXE'nin üzerine yazılmasını engellemelidir.
    """
    if os.name != "nt":
        log("HATA: Güncelleme yardımcısı yalnızca Windows'ta çalışabilir.")
        return False
    if pid <= 0:
        log("HATA: Geçersiz uygulama PID'si.")
        return False

    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_INVALID_PARAMETER = 87
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == ERROR_INVALID_PARAMETER:
            log("Ana uygulama PID'si artık bulunmuyor; kapanmış kabul edildi.")
            return True
        log(f"HATA: OpenProcess başarısız; WinError={error}. Güvenlik için güncelleme durduruldu.")
        return False

    try:
        # Süreç bu arada bitmiş olabilir. Wait sonucu bunu güvenli biçimde söyler.
        wait_now = kernel32.WaitForSingleObject(handle, 0)
        if wait_now == WAIT_OBJECT_0:
            return True
        if wait_now not in (WAIT_TIMEOUT,):
            log(f"HATA: PID kontrolü başarısız; wait={wait_now}.")
            return False

        buffer = ctypes.create_unicode_buffer(32768)
        chars = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(chars)):
            error = ctypes.get_last_error()
            log(f"HATA: Süreç yolu doğrulanamadı; WinError={error}.")
            return False
        process_path = Path(buffer.value)
        if _norm_path(process_path) != _norm_path(target):
            log("HATA: Verilen PID artık hedef ENCTakip.exe değil; olası PID yeniden kullanımı.")
            return False

        wait_ms = max(0, int(timeout_seconds * 1000))
        result = kernel32.WaitForSingleObject(handle, wait_ms)
        if result == WAIT_OBJECT_0:
            log("Ana uygulama tamamen kapandı.")
            return True
        if result == WAIT_TIMEOUT:
            log(f"HATA: Ana uygulama {timeout_seconds} saniyede kapanmadı.")
            return False
        log(f"HATA: Süreç bekleme işlemi başarısız; wait={result}.")
        return False
    finally:
        kernel32.CloseHandle(handle)


def _safe_restore(target: Path, backup: Path, original_hash: str) -> bool:
    """İyi olduğu doğrulanmış eski sürümü atomik kopya üzerinden geri yükler."""
    try:
        if not backup.is_file() or sha256(backup) != original_hash:
            log("KRİTİK: Geri dönüş kopyası eksik veya doğrulama başarısız; kopyaya dokunulmadı.")
            return False
        restore_tmp = target.with_name(f".{target.name}.restore.tmp")
        restore_tmp.unlink(missing_ok=True)
        shutil.copy2(backup, restore_tmp)
        if sha256(restore_tmp) != original_hash:
            restore_tmp.unlink(missing_ok=True)
            log("KRİTİK: Geri yükleme geçici kopyası doğrulanamadı.")
            return False
        os.replace(restore_tmp, target)
        log("Önceki çalışan sürüm geri yüklendi.")
        return True
    except Exception as exc:
        log(f"KRİTİK: Otomatik geri yükleme başarısız: {type(exc).__name__}: {exc}")
        return False


def _launch_application(target: Path, note: str) -> bool:
    """Launch the EXE currently at target, typically after rollback."""
    try:
        subprocess.Popen(
            [str(target), "--updated"],
            cwd=str(target.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log(note)
        return True
    except Exception as exc:
        log(f"UYARI: {note} başarısız: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    log("=" * 56)
    log("ENCTakip güncelleme yardımcısı başladı.")
    if os.name != "nt":
        log("HATA: Bu yardımcı yalnızca Windows içindir.")
        return 10
    if len(sys.argv) < 5:
        log("HATA: PID, kaynak EXE, hedef EXE ve beklenen SHA-256 zorunlu.")
        return 2

    try:
        pid = int(sys.argv[1])
        source = Path(sys.argv[2]).resolve(strict=True)
        target = Path(sys.argv[3]).resolve(strict=False)
        expected_hash = str(sys.argv[4]).strip().lower()
    except Exception as exc:
        log(f"HATA: Argümanlar okunamadı: {type(exc).__name__}: {exc}")
        return 2

    if target.name.casefold() != "enctakip.exe":
        log(f"HATA: Hedef dosya ENCTakip.exe değil: {target.name}")
        return 2
    if source.suffix.casefold() != ".exe" or not source.is_file():
        log("HATA: Güncelleme kaynağı geçerli bir EXE değil.")
        return 4
    if len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
        log("HATA: SHA-256 argümanı geçersiz.")
        return 2

    new_target = target.with_name(f".{target.name}.{pid}.new")
    backup = target.with_name(f"{target.name}.rollback")
    backup_tmp = target.with_name(f".{target.name}.{pid}.backup-new")
    replaced = False
    original_hash = ""
    original_closed = False
    wait_attempted = False

    try:
        if not target.is_file() or target.stat().st_size < MIN_EXE_BYTES:
            raise ValueError("Mevcut hedef EXE bulunamadı veya boyutu geçersiz.")
        if source.stat().st_size < MIN_EXE_BYTES:
            raise ValueError("Güncelleme EXE'si beklenenden küçük.")
        if sha256(source) != expected_hash:
            raise ValueError("Hazır güncelleme EXE'sinin SHA-256 değeri uyuşmuyor.")

        original_hash = sha256(target)
        wait_attempted = True
        original_closed = _wait_for_original_process(pid, target, APP_EXIT_TIMEOUT_SECONDS)
        if not original_closed:
            raise TimeoutError("Ana uygulama güvenli biçimde kapanmadı; hedef dosya değiştirilmedi.")

        # Yeni EXE hedefin yanına kopyalanır; hedefle aynı diskte olduğu için
        # son os.replace işlemi atomik dosya değişimi olarak kullanılabilir.
        new_target.unlink(missing_ok=True)
        shutil.copy2(source, new_target)
        if new_target.stat().st_size < MIN_EXE_BYTES or sha256(new_target) != expected_hash:
            raise ValueError("Hedef yanındaki geçici EXE doğrulamadan geçmedi.")

        # Mevcut EXE'nin doğrulanmış geri dönüş kopyası, hedef değiştirilmeden
        # önce hazırlanıp atomik olarak adlandırılır.
        backup_tmp.unlink(missing_ok=True)
        shutil.copy2(target, backup_tmp)
        if sha256(backup_tmp) != original_hash:
            raise ValueError("Önceki sürümün yedeği doğrulanamadı.")
        os.replace(backup_tmp, backup)

        os.replace(new_target, target)
        replaced = True
        if sha256(target) != expected_hash:
            raise ValueError("Yeni hedef EXE'nin SHA-256 doğrulaması başarısız.")

        log("Yeni EXE yerleştirildi; açılış kontrolü başlıyor.")
        process = subprocess.Popen(
            [str(target), "--updated"],
            cwd=str(target.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        try:
            exit_code = process.wait(timeout=PROCESS_GRACE_SECONDS)
            log(f"Yeni uygulama ilk {PROCESS_GRACE_SECONDS} saniyede kapandı; exit={exit_code}.")
            if not _safe_restore(target, backup, original_hash):
                log("KRİTİK: Yeni sürüm başarısız; otomatik geri dönüş tamamlanamadı.")
                return 8
            _launch_application(target, "Önceki sürüm geri yüklendi ve yeniden başlatıldı.")
            return 7
        except subprocess.TimeoutExpired:
            log("Yeni uygulama başlangıç kontrolü sırasında çalışır durumda kaldı; güncelleme başarılı kabul edildi.")

        try:
            source.unlink(missing_ok=True)
        except OSError as exc:
            log(f"UYARI: Geçici güncelleme dosyası silinemedi: {exc}")
        log(f"GÜNCELLEME BAŞARILI. Geri dönüş kopyası korundu: {backup}")
        return 0

    except Exception as exc:
        log(f"HATA: {type(exc).__name__}: {exc}")
        if replaced and original_hash and backup.exists():
            if _safe_restore(target, backup, original_hash):
                _launch_application(target, "Hata sonrası önceki sürüm yeniden başlatıldı.")
        elif target.is_file() and (original_closed or not wait_attempted):
            # Hata dosya değişiminden önce geldiyse de uygulama olay kuyruğu
            # yüzünden kapanmış olabilir. Yalnızca eski süreç gerçekten yoksa
            # mevcut (değiştirilmemiş) EXE yeniden açılır.
            try:
                kapandi = original_closed or _wait_for_original_process(pid, target, 3)
                if kapandi:
                    _launch_application(target, "Güncelleme uygulanmadı; eski sürüm yeniden başlatıldı.")
            except Exception as launch_exc:
                log(f"UYARI: Eski sürümün yeniden başlatma kontrolü başarısız: {launch_exc}")
        return 5
    finally:
        for transient in (new_target, backup_tmp):
            try:
                transient.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
