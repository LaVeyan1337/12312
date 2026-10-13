"""
Tek örnek (single instance) koruması.

Aynı kişi aynı bilgisayarda programı yanlışlıkla iki kez açarsa (ör.
masaüstü kısayoluna iki kez tıklama), iki ayrı süreç aynı anda heartbeat
atmaya, aynı varlık dosyasını güncellemeye başlar — kafa karıştırıcı ve
gereksiz. Bu, YEREL bir kilit dosyasıyla (sistem geçici klasörü, AĞ
PAYLAŞIMI DEĞİL) ikinci başlatmayı nazikçe engeller.

Aynı atomik O_CREAT|O_EXCL prensibi burada da geçerli (bkz.
lock_service.py) — ama bu sefer sadece bu bilgisayarı ilgilendiriyor.
Çökme sonrası kalan "ölü" kilitler, kayıtlı PID'in hâlâ çalışıp
çalışmadığı kontrol edilerek otomatik temizlenir.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path


class TekOrnekKilidi:
    def __init__(self, ad: str = "enc_takip"):
        self._yol = Path(tempfile.gettempdir()) / f"{ad}.lock"

    def al(self) -> bool:
        try:
            fd = os.open(str(self._yol), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            if self._pid_canli_mi():
                return False
            with contextlib.suppress(OSError):
                self._yol.unlink()
            return self.al()
        except OSError:
            # Kilit dosyası oluşturulamıyorsa (ör. izin sorunu), tek
            # örnek korumasını uygulayamayız ama bu, programın hiç
            # açılamamasından daha iyidir -> izin ver.
            return True

    def _pid_canli_mi(self) -> bool:
        try:
            pid = int(self._yol.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False

        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def birak(self) -> None:
        with contextlib.suppress(OSError):
            self._yol.unlink()
