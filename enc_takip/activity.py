"""
Hareketsizlik takibi.

Amaç: dosyayı sizin üzerinizden açtınız ama uzun süredir Word'de hiçbir
işlem yapmıyorsanız (unutulmuş, başka işe geçilmiş vb.), kullanıcıya
"hâlâ orada mısın?" diye sormak ve yanıt yoksa kilidi otomatik bırakmak.
Bu, orijinal programdaki en değerli özelliklerden biriydi ve ilk
yeniden yazımda gözden kaçmıştı — asıl şikayetin de (dosyanın günlerce
'kullanımda' görünmesi) bununla doğrudan ilişkili.

Word'ün ~$ dosyasının İÇERİĞİNİ okumuyoruz (bkz. lock_service.py'deki
gerekçe); sadece dosyanın mtime'ının değişip değişmediğine bakıyoruz.
Word, belge üzerinde otomatik kaydetme/checkpoint işlemleri sırasında bu
dosyaya dokunduğu için mtime pratikte makul bir "hâlâ aktif" sinyalidir.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .config import Settings


class HareketsizlikTakipcisi:
    def __init__(self, settings: Settings):
        self.s = settings
        self._son_mtime: Optional[float] = None
        self._son_hareket_ts: Optional[float] = None

    def sifirla(self) -> None:
        self._son_mtime = None
        self._son_hareket_ts = None

    def _word_kilit_yolu(self) -> Optional[Path]:
        klasor = self.s.dosya_yolu.parent
        for aday in (
            klasor / f"~${self.s.dosya_adi}",
            klasor / f"~${self.s.dosya_adi[2:]}" if len(self.s.dosya_adi) > 2 else None,
        ):
            if aday is not None and aday.exists():
                return aday
        return None

    def hareketsiz_saniye(self) -> int:
        """Word ~$ dosyası mtime'ına göre hareketsiz kalınan saniyeyi döndürür."""
        yol = self._word_kilit_yolu()
        if yol is None:
            self.sifirla()
            return 0

        try:
            mtime = yol.stat().st_mtime
        except OSError:
            return 0

        simdi = time.time()
        if self._son_mtime is None or mtime != self._son_mtime:
            self._son_mtime = mtime
            self._son_hareket_ts = simdi
            return 0

        return int(simdi - (self._son_hareket_ts or simdi))
