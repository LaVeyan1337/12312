"""
Otomatik yedekleme (versiyon geçmişi).

Ağ paylaşımındaki tek bir .docx dosyası tek hata noktasıdır: bozuk bir
kaydetme, yanlışlıkla silinen bir bölüm, ya da dosyanın kendisinin
bozulması durumunda geri dönecek bir şey olmayabilir. Bu modül, dosya
her kaydedildiğinde (mtime değiştiğinde) otomatik olarak zaman damgalı
bir kopyasını saklar. Kimse bir şey yapmasa bile arka planda çalışır.
"""
from __future__ import annotations

import contextlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from .config import Settings


class YedekYoneticisi:
    def __init__(self, settings: Settings, azami_adet: int = 30):
        self.s = settings
        self.klasor = settings.sistem_klasoru / "yedekler"
        self.azami_adet = azami_adet

    def yedekle(self) -> bool:
        if not self.s.dosya_yolu.exists():
            return False
        try:
            self.klasor.mkdir(parents=True, exist_ok=True)
            damga = datetime.now().strftime("%Y%m%d_%H%M%S")
            hedef = self.klasor / f"{self.s.dosya_yolu.stem}__{damga}{self.s.dosya_yolu.suffix}"
            shutil.copy2(self.s.dosya_yolu, hedef)
            self._eskileri_temizle()
            return True
        except OSError:
            return False

    def _eskileri_temizle(self) -> None:
        dosyalar = self.liste()
        for eski in dosyalar[self.azami_adet:]:
            with contextlib.suppress(OSError):
                eski.unlink()

    def liste(self) -> List[Path]:
        if not self.klasor.exists():
            return []
        desen = f"{self.s.dosya_yolu.stem}__*{self.s.dosya_yolu.suffix}"
        try:
            return sorted(self.klasor.glob(desen), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []

    def son_yedek_zamani(self):
        liste = self.liste()
        if not liste:
            return None
        try:
            return datetime.fromtimestamp(liste[0].stat().st_mtime)
        except OSError:
            return None
