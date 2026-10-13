"""
Varlık (online) takibi.

Eski kodda tüm kullanıcıların "kimler online" listesi TEK bir JSON
dosyasında tutuluyor ve her güncellemede "oku -> kendi kaydını
güncelle -> tüm dosyayı geri yaz" yapılıyordu — burada da klasik bir
yarış durumu potansiyeli vardı.

Burada her kullanıcı SADECE KENDİ dosyasına yazar (varlik/<kullanici>.json).
Okuma tarafı, o klasördeki tüm dosyaları tarayıp son N saniye içinde
güncellenenleri "online" sayar. İki kullanıcı asla aynı dosyaya aynı anda
yazmadığı için çakışma imkansızdır.
"""
from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from .config import BILGISAYAR, KULLANICI, Settings

ONLINE_ESIGI_SN = 90


@dataclass
class Varlik:
    kullanici: str
    bilgisayar: str
    son_gorulme: str


def _guvenli_ad(ad: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", ad)


class VarlikServisi:
    def __init__(self, settings: Settings):
        self._klasor = settings.sistem_klasoru / "varlik"

    def bildir(self) -> None:
        with contextlib.suppress(OSError):
            self._klasor.mkdir(parents=True, exist_ok=True)
            dosya = self._klasor / f"{_guvenli_ad(KULLANICI)}.json"
            veri = {
                "kullanici": KULLANICI,
                "bilgisayar": BILGISAYAR,
                "son_gorulme": datetime.now().isoformat(timespec="seconds"),
            }
            dosya.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")

    def aktifler(self) -> List[Varlik]:
        sonuc: List[Varlik] = []
        if not self._klasor.exists():
            return sonuc

        simdi = datetime.now()
        with contextlib.suppress(OSError):
            for f in self._klasor.glob("*.json"):
                try:
                    veri = json.loads(f.read_text(encoding="utf-8"))
                    son = datetime.fromisoformat(veri["son_gorulme"])
                    if (simdi - son).total_seconds() <= ONLINE_ESIGI_SN:
                        sonuc.append(Varlik(**veri))
                except (OSError, json.JSONDecodeError, KeyError, ValueError):
                    continue

        sonuc.sort(key=lambda v: (v.kullanici.lower() != KULLANICI.lower(), v.kullanici.lower()))
        return sonuc
