"""
Uygulama ayarları.

Eskiden sabitler kodun içine gömülüydü (AG, DOSYA_KLS, YONETICILER...).
Artık bunlar `settings.json` dosyasından okunuyor. Böylece:
  - Ağ yolu değiştiğinde kodu değil, sadece settings.json'ı güncellersin.
  - Her makineye farklı bir kopya dağıtmak zorunda kalmazsın.
  - settings.json bozuksa/eksikse uygulama çökmez, varsayılanlara düşer.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = APP_DIR / "settings.json"


@dataclass(frozen=True)
class Settings:
    sirket_adi: str = "ENÇEV"
    sirket_alt: str = "Çevre Danışmanlık"
    ag_yolu: str = r"\\192.168.1.55\Genel Evrak"
    dosya_klasoru: str = r"______DİLEKÇELERİMİZ\2026 DİLEKÇELER"
    dosya_adi: str = "DİLEKÇELERİMİZ 2026.docx"

    # Yönetici isimleri dağıtım paketine gömülmez.
    # Mevcut şirket ayarındaki liste varsa onu kullanır.
    yoneticiler: Tuple[str, ...] = ("burak")

    tarama_araligi_sn: int = 4
    heartbeat_araligi_sn: int = 20
    kilit_olu_esigi_sn: int = 90  # bu süre heartbeat gelmezse kilit "ölü" sayılır
    hareketsizlik_uyari_esigi_sn: int = 240

    @property
    def versiyon(self) -> str:
        # GUI sürüm bilgisini settings.json'dan bağımsız olarak paket içindeki
        # tekil sürüm kaynağından alır. Böylece ilk çalıştırmada oluşturulan
        # settings.json eksik olsa bile GUI çökmez.
        try:
            from .version import APP_VERSION
            return APP_VERSION
        except Exception:
            return "8.4"

    @property
    def dosya_yolu(self) -> Path:
        return Path(self.ag_yolu) / self.dosya_klasoru / self.dosya_adi

    @property
    def sistem_klasoru(self) -> Path:
        # Ağ paylaşımında uygulamanın kendi verilerini tuttuğu gizli klasör.
        return Path(self.ag_yolu) / ".encev_sistem"


def yukle(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "yoneticiler" in data:
            data["yoneticiler"] = tuple(data["yoneticiler"])
        # Burak her kurulumda yönetici olarak bulunmalı.
        if "burak" not in {x.lower() for x in data["yoneticiler"]}:
            data["yoneticiler"] = tuple(data["yoneticiler"]) + ("burak",)
        return Settings(**data)
    except Exception:
        # Bozuk ayar dosyası tüm şirketin programını çökertmesin.
        return Settings()


def _serilestir(s: Settings) -> dict:
    return {
        "sirket_adi": s.sirket_adi,
        "sirket_alt": s.sirket_alt,
        "ag_yolu": s.ag_yolu,
        "dosya_klasoru": s.dosya_klasoru,
        "dosya_adi": s.dosya_adi,
        "yoneticiler": list(s.yoneticiler),
        "tarama_araligi_sn": s.tarama_araligi_sn,
        "heartbeat_araligi_sn": s.heartbeat_araligi_sn,
        "kilit_olu_esigi_sn": s.kilit_olu_esigi_sn,
        "hareketsizlik_uyari_esigi_sn": s.hareketsizlik_uyari_esigi_sn,
    }


def ornek_settings_yaz(path: Path = DEFAULT_SETTINGS_PATH) -> None:
    """İlk kurulumda örnek settings.json üretmek için yardımcı."""
    path.write_text(json.dumps(_serilestir(Settings()), ensure_ascii=False, indent=2), encoding="utf-8")


def kaydet(settings: Settings, path: Path = DEFAULT_SETTINGS_PATH) -> bool:
    """Ayarlar penceresinden gelen değişiklikleri diske yazar."""
    try:
        path.write_text(json.dumps(_serilestir(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


KULLANICI = os.environ.get("USERNAME", "bilinmiyor")
BILGISAYAR = socket.gethostname()


def yonetici_mi(settings) -> bool:
    """
    ENCTakip yönetici kontrolü.

    Yönetici hesabı artık yalnızca Windows kullanıcı adına değil;
    kullanıcı adı + ad soyad gibi bilinen kimliklere de göre tanınır.
    """

    import unicodedata

    def normalize(metin: str) -> str:
        metin = str(metin or "").strip().casefold()
        metin = unicodedata.normalize("NFKD", metin)
        metin = "".join(
            ch for ch in metin
            if not unicodedata.combining(ch)
        )
        return metin

    yoneticiler = {
        "burak",
        "burak can erdağ",
        "burak can erdag",
    }

    kimlikler = {
        normalize(KULLANICI),
    }

    # Güvenlik / uyumluluk için ayarlardan gelen kullanıcı adını da değerlendir.
    for alan in (
        "kullanici_adi",
        "kullanıcı_adi",
        "kullanici",
        "kullanıcı",
        "ad_soyad",
    ):
        try:
            deger = getattr(settings, alan, "")
        except Exception:
            deger = ""

        if deger:
            kimlikler.add(normalize(deger))

    return any(
        normalize(y) in kimlikler
        for y in yoneticiler
    )
