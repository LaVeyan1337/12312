"""
Kişisel tercihler.

`config.Settings` şirket çapında PAYLAŞILAN ayarları tutar (ağ yolu,
dosya adı, yönetici listesi) — settings.json ağ sürücüsünde durur ve
herkesi etkiler.

Ses açık/kapalı, görünüm modu (açık/koyu/sistem), "Windows açılışında
çalıştır" ya da "kapat tuşu tepsiye küçültsün" gibi tercihler ise
KİŞİSELDİR — bir kullanıcının bu tercihi değiştirmesi diğer 10
kullanıcıyı etkilememelidir. Bu yüzden ayrı, yerel bir dosyada
(kullanıcının kendi ev dizininde) tutulur.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_YOL = Path.home() / ".enc_takip_tercihler.json"


@dataclass
class YerelTercihler:
    ses_bildirimleri: bool = True
    baslangicta_calistir: bool = True
    tepsiye_kucult: bool = True
    gorunum_modu: str = "System"  # "Light" | "Dark" | "System"


def yukle() -> YerelTercihler:
    if not _YOL.exists():
        return YerelTercihler()
    try:
        data = json.loads(_YOL.read_text(encoding="utf-8"))
        varsayilan = YerelTercihler()
        return YerelTercihler(
            ses_bildirimleri=bool(data.get("ses_bildirimleri", varsayilan.ses_bildirimleri)),
            baslangicta_calistir=bool(data.get("baslangicta_calistir", varsayilan.baslangicta_calistir)),
            tepsiye_kucult=bool(data.get("tepsiye_kucult", varsayilan.tepsiye_kucult)),
            gorunum_modu=str(data.get("gorunum_modu", varsayilan.gorunum_modu)),
        )
    except Exception:
        return YerelTercihler()


def kaydet(t: YerelTercihler) -> bool:
    try:
        _YOL.write_text(json.dumps(asdict(t), ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False
