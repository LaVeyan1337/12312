"""
Bekleme sırası.

Senaryo: dosya kullanımdayken birisi "boşalınca haber ver" der. Dosya
serbest kaldığında, sırada ilk bekleyen kişiye otomatik bir bildirim
gider — sürekli "boş mu acaba" diye kontrol etmek zorunda kalmaz.

JSONL (event-sourced) yaklaşımı: "katıldı" / "ayrıldı" / "bildirildi"
olayları sadece dosyanın SONUNA eklenir, hiçbir zaman var olan içerik
okunup değiştirilmez. Güncel sıra, bu olaylar üzerinden hesaplanır.
Bu, storage.py'deki JsonlDeposu ile aynı prensip — okuma/yazma yarışı
yok.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .storage import JsonlDeposu


class BeklemeSirasi:
    def __init__(self, dosya: Path):
        self._depo = JsonlDeposu(dosya)

    def katil(self, kullanici: str) -> None:
        if kullanici in self.guncel_sira():
            return
        self._depo.ekle({"tip": "katil", "kullanici": kullanici, "zaman": datetime.now().isoformat(timespec="seconds")})

    def ayril(self, kullanici: str) -> None:
        self._depo.ekle({"tip": "ayril", "kullanici": kullanici, "zaman": datetime.now().isoformat(timespec="seconds")})

    def guncel_sira(self) -> List[str]:
        aktif: dict[str, bool] = {}
        sira: List[str] = []
        for olay in self._depo.oku(2000):
            k = olay.get("kullanici")
            tip = olay.get("tip")
            if not k or not tip:
                continue
            if tip == "katil":
                if k not in aktif:
                    aktif[k] = True
                    sira.append(k)
            elif tip in ("ayril", "bildirildi"):
                aktif.pop(k, None)
                if k in sira:
                    sira.remove(k)
        return sira

    def detayli_sira(self) -> List[str]:
        return list(self.guncel_sira())


    def bendemi(self, kullanici: str) -> bool:
        return kullanici in self.guncel_sira()

    def ilkini_bildir_ve_cikar(self) -> Optional[str]:
        """Dosya serbest kaldığında çağrılır: sıradaki ilk kişiyi döndürür ve sıradan çıkarır."""
        sira = self.guncel_sira()
        if not sira:
            return None
        ilk = sira[0]
        self._depo.ekle({"tip": "bildirildi", "kullanici": ilk, "zaman": datetime.now().isoformat(timespec="seconds")})
        return ilk
