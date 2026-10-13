"""
Veri modelleri.

Eski kodda kilit/mesaj/log bilgisi hep gevşek `dict` olarak taşınıyordu
(`{"kul": ..., "pc": ..., "ac": ...}`). Bu, bir alanı yanlış yazdığında
(typo) hiçbir uyarı almadan sessizce yanlış çalışmasına yol açar.
Dataclass kullanmak IDE'nin ve okuyan insanın alanları görmesini sağlar.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum


class DosyaDurumu(str, Enum):
    BAGLANTI_YOK = "baglanti_yok"
    DOSYA_YOK = "dosya_yok"
    MUSAIT = "musait"
    KULLANIMDA_BENDE = "kullanimda_bende"
    KULLANIMDA_BASKASI = "kullanimda_baskasi"
    BELIRSIZ = "belirsiz"  # Word muhtemelen uygulama dışından açılmış


@dataclass
class KilitKaydi:
    kullanici: str
    bilgisayar: str
    acilis: str  # ISO 8601 zaman
    son_heartbeat: str  # ISO 8601 zaman

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "KilitKaydi":
        return KilitKaydi(
            kullanici=d["kullanici"],
            bilgisayar=d["bilgisayar"],
            acilis=d["acilis"],
            son_heartbeat=d["son_heartbeat"],
        )


@dataclass
class Mesaj:
    id: str
    zaman: str
    gonderen: str
    hedef: str  # "GENEL" ya da bir kullanıcı adı (özel mesaj)
    metin: str

    @staticmethod
    def yeni(gonderen: str, hedef: str, metin: str) -> "Mesaj":
        return Mesaj(
            id=uuid.uuid4().hex[:12],
            zaman=datetime.now().isoformat(timespec="seconds"),
            gonderen=gonderen,
            hedef=hedef,
            metin=metin,
        )


@dataclass
class LogKaydi:
    zaman: str
    kullanici: str
    bilgisayar: str
    olay: str
    detay: str = ""
    sure_sn: int = 0
