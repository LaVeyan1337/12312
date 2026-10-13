"""Depolama katmanı."""
from __future__ import annotations

import json
import logging
import logging.handlers
import socket
from pathlib import Path
from typing import List, Optional, Iterable, Tuple

from .config import Settings
from .models import LogKaydi, Mesaj


class JsonlDeposu:
    """Ortak append-only JSON-Lines deposu."""

    def __init__(self, dosya: Path):
        self.dosya = dosya

    def ekle(self, veri: dict) -> bool:
        try:
            self.dosya.parent.mkdir(parents=True, exist_ok=True)
            with open(self.dosya, "a", encoding="utf-8") as f:
                f.write(json.dumps(veri, ensure_ascii=False, separators=(",", ":")) + "\n")
                f.flush()
            return True
        except OSError:
            return False

    def oku(self, son_n: int = 500) -> List[dict]:
        if not self.dosya.exists():
            return []
        sonuc: List[dict] = []
        try:
            with open(self.dosya, "r", encoding="utf-8") as f:
                for satir in f:
                    satir = satir.strip()
                    if not satir:
                        continue
                    try:
                        sonuc.append(json.loads(satir))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return sonuc[-max(0, int(son_n)):]


class MesajDeposu(JsonlDeposu):
    """Mesajlar için append-only depo + artımlı/tarih tabanlı okuma API'si.

    Önemli: ortak ağdaki JSONL dosyasını her mesaj kontrolünde baştan okumaz.
    Yeni kayıtlar dosya sonundan okunabilir; geçmiş ise gerektiğinde sondan
    geriye doğru sayfalı olarak alınabilir.
    """

    GEREKLI = {"id", "zaman", "gonderen", "hedef", "metin"}

    def gonder(self, mesaj: Mesaj) -> bool:
        return self.ekle(mesaj.__dict__)

    @staticmethod
    def _mesaj(d: dict) -> Optional[Mesaj]:
        if not MesajDeposu.GEREKLI <= d.keys():
            return None
        try:
            return Mesaj(**d)
        except Exception:
            return None

    def tumunu_oku(self, son_n: int = 300) -> List[Mesaj]:
        return [m for d in self.oku(son_n) if (m := self._mesaj(d)) is not None]

    def dosya_bilgisi(self) -> Tuple[int, int]:
        """(size, mtime_ns); değişiklik kontrolü için ucuz metadata sorgusu."""
        try:
            st = self.dosya.stat()
            return int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except OSError:
            return 0, 0

    def sondan_offsete_kadar(self, baslangic_offset: int, max_bytes: int = 8 * 1024 * 1024) -> Tuple[List[Mesaj], int]:
        """Verilen byte offset'inden EOF'a kadar yeni JSONL kayıtlarını okur.

        Yarım kalan son satır geri bırakılır; bir sonraki kontrolde tamamlanınca
        tekrar okunur. Dönüş: (mesajlar, güvenli_yeni_offset).
        """
        if not self.dosya.exists():
            return [], 0
        try:
            size = self.dosya.stat().st_size
            offset = max(0, int(baslangic_offset))
            if offset > size:
                offset = 0
            # Tek turda aşırı büyük ağ okumasını önle. Offset korunur; sonraki
            # tur devam eder.
            son = min(size, offset + max_bytes)
            with open(self.dosya, "rb") as f:
                f.seek(offset)
                data = f.read(son - offset)
            if not data:
                return [], offset

            last_nl = data.rfind(b"\n")
            if last_nl < 0:
                return [], offset
            complete = data[:last_nl + 1]
            safe_offset = offset + last_nl + 1
            sonuc: List[Mesaj] = []
            for raw in complete.splitlines():
                if not raw.strip():
                    continue
                try:
                    m = self._mesaj(json.loads(raw.decode("utf-8")))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if m is not None:
                    sonuc.append(m)
            return sonuc, safe_offset
        except OSError:
            return [], baslangic_offset

    def son_mesajlar(self, limit: int = 1500) -> List[Mesaj]:
        """Dosyanın sonundan geriye doğru limit kadar mesaj okur.

        Dosyanın tamamını RAM'e almaz; bloklar halinde geriye doğru gider.
        """
        limit = max(1, int(limit))
        if not self.dosya.exists():
            return []
        try:
            size = self.dosya.stat().st_size
            pos = size
            parcalar: List[bytes] = []
            bulunan = 0
            chunk = 128 * 1024
            with open(self.dosya, "rb") as f:
                while pos > 0 and bulunan <= limit:
                    read_size = min(chunk, pos)
                    pos -= read_size
                    f.seek(pos)
                    data = f.read(read_size)
                    parcalar.append(data)
                    bulunan += data.count(b"\n")
                    if bulunan >= limit + 1:
                        break
            data = b"".join(reversed(parcalar))
            sonuc: List[Mesaj] = []
            for raw in data.splitlines()[-limit:]:
                try:
                    m = self._mesaj(json.loads(raw.decode("utf-8")))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if m is not None:
                    sonuc.append(m)
            return sonuc[-limit:]
        except OSError:
            return []

    @staticmethod
    def _sohbet_uygun(m: Mesaj, hedef: Optional[str], kullanici: Optional[str]) -> bool:
        if not hedef:
            return True
        if hedef in ("GENEL", "ODA_ECED", "ODA_DILEK"):
            return m.hedef == hedef
        kullanici = kullanici or ""
        return (
            (m.gonderen == kullanici and m.hedef == hedef)
            or (m.gonderen == hedef and m.hedef == kullanici)
        )

    def oncekileri_getir(
        self,
        before_zaman: Optional[str],
        limit: int = 300,
        hedef: Optional[str] = None,
        kullanici: Optional[str] = None,
    ) -> List[Mesaj]:
        """before_zaman'dan eski kayıtları sondan geriye doğru getirir.

        Dosyanın tamamını RAM'e almaz; 128 KB bloklarla geriye gider ve
        satır sınırlarını carry ile güvenli biçimde birleştirir.
        """
        limit = max(1, int(limit))
        if not before_zaman or not self.dosya.exists():
            return []
        try:
            size = self.dosya.stat().st_size
            pos = size
            chunk = 128 * 1024
            carry = b""
            sonuc: List[Mesaj] = []
            with open(self.dosya, "rb") as f:
                while pos > 0 and len(sonuc) < limit:
                    read_size = min(chunk, pos)
                    pos -= read_size
                    f.seek(pos)
                    data = f.read(read_size) + carry
                    lines = data.split(b"\n")
                    carry = lines[0]
                    for raw in reversed(lines[1:]):
                        if not raw.strip():
                            continue
                        try:
                            m = self._mesaj(json.loads(raw.decode("utf-8")))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if m is None or m.zaman >= before_zaman:
                            continue
                        if not self._sohbet_uygun(m, hedef, kullanici):
                            continue
                        sonuc.append(m)
                        if len(sonuc) >= limit:
                            break

                if pos == 0 and carry and len(sonuc) < limit:
                    try:
                        m = self._mesaj(json.loads(carry.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        m = None
                    if m is not None and m.zaman < before_zaman and self._sohbet_uygun(m, hedef, kullanici):
                        sonuc.append(m)

            sonuc.reverse()
            return sonuc
        except OSError:
            return []

    def ara_sohbet(self, sorgu: str, hedef: str, limit: int = 5000, kullanici: str = "") -> List[Mesaj]:
        """Yalnızca belirtilen sohbetin tamamında metin arar."""
        q = str(sorgu or "").strip().casefold()
        hedef = str(hedef or "")
        if not q or not self.dosya.exists():
            return []
        sonuc: List[Mesaj] = []
        try:
            with open(self.dosya, "r", encoding="utf-8", errors="replace") as f:
                for satir in f:
                    try:
                        m = self._mesaj(json.loads(satir))
                    except json.JSONDecodeError:
                        continue
                    if m is None or not self._sohbet_uygun(m, hedef, kullanici):
                        continue
                    kind_text = str(m.metin or "")
                    # Receipt/edit/file markerları kullanıcı aramasında sonuç değildir.
                    if kind_text.startswith(("__ENCTAKIP_DELIVERED__", "__ENCTAKIP_READ__", "__ENCTAKIP_EDIT__", "__ENCTAKIP_DELETE__")):
                        continue
                    if q not in kind_text.casefold():
                        continue
                    sonuc.append(m)
                    if len(sonuc) >= limit:
                        break
        except OSError:
            return []
        return sonuc

    def receiptleri(self, mesaj_id: str, hedef: str = "", kullanici: str = "") -> List[Mesaj]:
        """Bir mesajın teslim/okundu markerlarını server geçmişinden bulur.

        Bu metod yalnızca kullanıcı mesaj bilgilerini açtığında çağrılır; normal
        watcher/render akışında kullanılmaz.
        """
        mid = str(mesaj_id or "")
        if not mid or not self.dosya.exists():
            return []
        sonuc: List[Mesaj] = []
        try:
            with open(self.dosya, "r", encoding="utf-8", errors="replace") as f:
                for satir in f:
                    try:
                        m = self._mesaj(json.loads(satir))
                    except json.JSONDecodeError:
                        continue
                    if m is None or not str(m.metin or "").startswith(("__ENCTAKIP_DELIVERED__", "__ENCTAKIP_READ__")):
                        continue
                    try:
                        data = json.loads(str(m.metin).split("__", 2)[-1]) if False else json.loads(str(m.metin).split("}", 1)[0] + "}")
                    except Exception:
                        try:
                            raw = str(m.metin)
                            brace = raw.find("{")
                            data = json.loads(raw[brace:]) if brace >= 0 else {}
                        except Exception:
                            continue
                    if str(data.get("id", "")) != mid:
                        continue
                    if hedef and not self._sohbet_uygun(m, hedef, kullanici):
                        continue
                    sonuc.append(m)
        except OSError:
            return []
        return sonuc

    def ara(self, sorgu: str, limit: int = 5000) -> List[Mesaj]:
        """Server geçmişinin tamamını tarar; yalnızca açıkça arama yapılınca kullanılır."""
        q = str(sorgu or "").strip().casefold()
        if not q or not self.dosya.exists():
            return []
        sonuc: List[Mesaj] = []
        try:
            with open(self.dosya, "r", encoding="utf-8", errors="replace") as f:
                for satir in f:
                    try:
                        m = self._mesaj(json.loads(satir))
                    except json.JSONDecodeError:
                        continue
                    if m is None:
                        continue
                    marker_text = str(m.metin or "")
                    if marker_text.startswith((
                        "__ENCTAKIP_DELIVERED__",
                        "__ENCTAKIP_READ__",
                        "__ENCTAKIP_EDIT__",
                        "__ENCTAKIP_FILE__",
                        "__ENCTAKIP_DELETE__",
                    )):
                        continue
                    aranacak = f"{m.metin} {m.gonderen} {m.hedef}".casefold()
                    if q not in aranacak:
                        continue
                    sonuc.append(m)
                    if len(sonuc) >= limit:
                        break
        except OSError:
            return []
        return sonuc


class OlayGunlugu(JsonlDeposu):
    def yaz(self, kayit: LogKaydi) -> bool:
        return self.ekle(kayit.__dict__)

    def tumunu_oku(self, son_n: int = 1000) -> List[LogKaydi]:
        gerekli = {"zaman", "kullanici", "bilgisayar", "olay"}
        sonuc = []
        for d in self.oku(son_n):
            if not gerekli <= d.keys():
                continue
            sonuc.append(LogKaydi(
                zaman=d["zaman"], kullanici=d["kullanici"], bilgisayar=d["bilgisayar"],
                olay=d["olay"], detay=d.get("detay", ""), sure_sn=d.get("sure_sn", 0),
            ))
        return sonuc


def teknik_logger(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("enctakip.debug")
    if logger.handlers:
        return logger
    settings.sistem_klasoru.mkdir(parents=True, exist_ok=True)
    dosya = settings.sistem_klasoru / f"debug_{socket.gethostname()}.log"
    handler = logging.handlers.RotatingFileHandler(
        dosya, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    return logger
