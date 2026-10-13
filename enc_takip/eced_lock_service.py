"""ENCTakip için e-ÇED ortak kullanım kilidi.

Bu servis yalnızca ENCTakip içindeki ortak kullanımı koordine eder. e-ÇED
portalının dışarıdan açılmış oturumlarını göremez; bu bilgi ancak portalın
kendi desteklediği bir durum kontrolüyle belirlenebilir.

Güvenlik ilkeleri:
- Ağ/izin/JSON hatasında "boş" sonucu üretmez; DURUM_BELIRSIZ döner.
- Eski heartbeat kilidini otomatik silmez; yönetici incelemesi gerekir.
- Aynı anda kilit almak için O_CREAT|O_EXCL kullanır.
- Parola, çerez, SMS kodu veya portal oturum anahtarı saklamaz.
"""
from __future__ import annotations

import json
import math
import os
import socket
from contextlib import contextmanager
from typing import Iterator
import time
import uuid
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class EcedDurum(str, Enum):
    BOS = "bos"
    KULLANIMDA = "kullanimda"
    ESKI_KILIT = "eski_kilit"
    BELIRSIZ = "belirsiz"


@dataclass(frozen=True)
class EcedKilitBilgisi:
    durum: EcedDurum
    kullanici: str = ""
    bilgisayar: str = ""
    baslangic: float | None = None
    heartbeat: float | None = None
    gecen_saniye: float | None = None
    aciklama: str = ""


class EcedKilitServisi:
    def __init__(self, settings, kullanici: str, bilgisayar: str | None = None):
        self.settings = settings
        self.kullanici = str(kullanici or "").strip() or "bilinmiyor"
        self.bilgisayar = str(bilgisayar or socket.gethostname()).strip() or "bilinmiyor"
        self.klasor = Path(settings.sistem_klasoru) / "eced"
        self.kilit_yolu = self.klasor / "hesap_kilidi.json"
        self.olay_yolu = self.klasor / "olaylar.jsonl"
        self._token: str | None = None
        self._operation_lock = threading.RLock()
        self._esik = max(60, int(getattr(settings, "kilit_olu_esigi_sn", 90) or 90))

    def _ag_erisimini_dogrula(self) -> bool:
        """En yakın paylaşımlı üst dizinin gerçekten erişilebilir olduğunu doğrula."""
        for aday in (Path(self.settings.sistem_klasoru), Path(self.settings.ag_yolu)):
            try:
                aday.stat()
                return True
            except FileNotFoundError:
                continue
            except OSError:
                return False
        return False

    @staticmethod
    def _json_oku(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("Kilit kaydı JSON nesnesi değil.")
        if not value.get("token") or not value.get("kullanici") or not value.get("bilgisayar"):
            raise ValueError("Kilit kaydında zorunlu alan eksik.")
        heartbeat = float(value["heartbeat"])
        baslangic = float(value["baslangic"])
        if not math.isfinite(heartbeat) or heartbeat <= 0:
            raise ValueError("Kilit heartbeat zamanı geçersiz.")
        if not math.isfinite(baslangic) or baslangic <= 0:
            raise ValueError("Kilit başlangıç zamanı geçersiz.")
        return value

    def _olay_yaz(self, olay: str, ayrinti: str = "") -> None:
        """Gizli kimlik doğrulama verileri yazılmadan kısa bir olay kaydı ekle."""
        try:
            self.klasor.mkdir(parents=True, exist_ok=True)
            row = {
                "zaman": time.time(),
                "olay": str(olay)[:60],
                "kullanici": self.kullanici[:120],
                "bilgisayar": self.bilgisayar[:120],
                "ayrinti": str(ayrinti)[:240],
            }
            line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            fd = os.open(str(self.olay_yolu), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        except OSError:
            # Olay kaydının başarısız olması kilit dosyasını bozmasın.
            pass

    @contextmanager
    def _islem_kilidi(self, timeout_sn: float = 1.0) -> Iterator[None]:
        """Kilit kaydını güncelleme/silme işlemlerini makineler arasında sırala.

        Dosyanın kendisini kilitlemek yerine sabit bir yardımcı dosyanın ilk
        baytında byte-range lock kullanılır; böylece heartbeat sırasında JSON
        dosyasının atomik değiştirilmesi, başka bir istemcinin bırakma/kurtarma
        işlemiyle yarışamaz. Süreç çökerse işletim sistemi kilidi bırakır.
        """
        self.klasor.mkdir(parents=True, exist_ok=True)
        guard = self.klasor / "islem_kilidi.lck"
        fd = os.open(str(guard), os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        deadline = time.monotonic() + max(0.0, timeout_sn)
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Ortak e-ÇED kilit kaydı başka bir işlem tarafından güncelleniyor.")
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

    def durum(self) -> EcedKilitBilgisi:
        try:
            record = self._json_oku(self.kilit_yolu)
        except FileNotFoundError:
            if self._ag_erisimini_dogrula():
                return EcedKilitBilgisi(EcedDurum.BOS, aciklama="Ortak klasörde aktif kilit yok.")
            return EcedKilitBilgisi(EcedDurum.BELIRSIZ, aciklama="Ortak ağ klasörüne erişilemiyor; boş olduğu doğrulanamadı.")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return EcedKilitBilgisi(EcedDurum.BELIRSIZ, aciklama=f"Kilit okunamadı veya bozuk: {type(exc).__name__}.")

        now = time.time()
        heartbeat = float(record["heartbeat"])
        start = float(record["baslangic"])
        age = max(0.0, now - heartbeat)
        common = {
            "kullanici": str(record.get("kullanici", "")),
            "bilgisayar": str(record.get("bilgisayar", "")),
            "baslangic": start,
            "heartbeat": heartbeat,
            "gecen_saniye": age,
        }
        if age > self._esik:
            return EcedKilitBilgisi(
                EcedDurum.ESKI_KILIT,
                **common,
                aciklama=f"Son sinyal {int(age)} saniye önce. Otomatik serbest bırakılmadı; kontrol gerekli.",
            )
        return EcedKilitBilgisi(
            EcedDurum.KULLANIMDA,
            **common,
            aciklama="ENCTakip içinde ortak e-ÇED hesabı kullanımda olarak işaretli.",
        )

    def al(self) -> tuple[bool, str]:
        """Acquire işlemini aynı süreçte ve makineler arasında sıraya koy."""
        with self._operation_lock:
            try:
                with self._islem_kilidi():
                    return self._al_impl()
            except Exception as exc:
                self._olay_yaz("kilit_alinamadi", type(exc).__name__)
                return False, f"Ortak kilit güvenle alınamadı ({type(exc).__name__}); yeniden denemek gerekiyor."

    def _al_impl(self) -> tuple[bool, str]:
        """Kilidi atomik al. Başarısızlık sebebi boş/başka kullanıcı/erişim hatasıdır."""
        if self._token:
            current = self.durum()
            if current.durum == EcedDurum.KULLANIMDA and current.kullanici == self.kullanici and current.bilgisayar == self.bilgisayar:
                return True, "Bu oturum zaten kilit sahibi."
            return False, "Önceki kilidin sahipliği doğrulanamadı; yeniden kilit alınmadı."

        try:
            self.klasor.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            now = time.time()
            record = {
                "schema": 1,
                "token": token,
                "kullanici": self.kullanici,
                "bilgisayar": self.bilgisayar,
                "baslangic": now,
                "heartbeat": now,
            }
            payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            fd = os.open(str(self.kilit_yolu), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                written = os.write(fd, payload)
                if written != len(payload):
                    raise OSError("Kilit kaydının tamamı yazılamadı.")
                os.fsync(fd)
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    self.kilit_yolu.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            else:
                os.close(fd)

            self._token = token
            self._olay_yaz("kilit_alindi")
            return True, "Ortak kullanım kilidi alındı."
        except FileExistsError:
            existing = self.durum()
            if existing.durum == EcedDurum.ESKI_KILIT:
                return False, "Süresi geçmiş kilit var. Otomatik silinmedi; yetkili kontrolü gerekiyor."
            if existing.durum == EcedDurum.KULLANIMDA:
                return False, f"Hesap {existing.kullanici} / {existing.bilgisayar} tarafından kullanımda."
            return False, "Kilit kaydı mevcut ancak güvenle okunamıyor; ağ/izin kontrolü gerekiyor."
        except OSError as exc:
            self._olay_yaz("kilit_alinamadi", type(exc).__name__)
            return False, f"Kilit alınamadı; ağ veya yazma izni kontrol edilmeli ({type(exc).__name__})."

    def heartbeat(self) -> tuple[bool, str]:
        """Heartbeat'i aynı süreçte ve makineler arasında sıraya koy."""
        with self._operation_lock:
            try:
                with self._islem_kilidi():
                    return self._heartbeat_impl()
            except Exception as exc:
                self._olay_yaz("heartbeat_hatasi", type(exc).__name__)
                return False, f"Ortak kilit durumu doğrulanamadı ({type(exc).__name__})."

    def _heartbeat_impl(self) -> tuple[bool, str]:
        """Yalnızca token hâlâ bu örneğe aitse heartbeat günceller."""
        token = self._token
        if not token:
            return False, "Bu oturum kilit sahibi değil."
        temp = self.kilit_yolu.with_name(f".{self.kilit_yolu.name}.{token}.tmp")
        try:
            record = self._json_oku(self.kilit_yolu)
            if record.get("token") != token:
                self._token = None
                self._olay_yaz("heartbeat_sahiplik_kaybi")
                return False, "Kilit sahipliği değişmiş; heartbeat durduruldu."
            record["heartbeat"] = time.time()
            data = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with temp.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Sadece sahibi token'ı doğruladıktan sonra kayıt değişir.
            os.replace(temp, self.kilit_yolu)
            return True, "Heartbeat güncellendi."
        except Exception as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            self._olay_yaz("heartbeat_hatasi", type(exc).__name__)
            return False, f"Heartbeat doğrulanamadı ({type(exc).__name__}); kilit yeniden alınmadı."

    def birak(self) -> tuple[bool, str]:
        with self._operation_lock:
            try:
                with self._islem_kilidi():
                    return self._birak_impl()
            except Exception as exc:
                self._olay_yaz("kilit_birakma_hatasi", type(exc).__name__)
                return False, f"Ortak kilit güvenle bırakılamadı ({type(exc).__name__})."

    def _birak_impl(self) -> tuple[bool, str]:
        token = self._token
        if not token:
            return False, "Bu oturumun bırakacağı kilit yok."
        try:
            record = self._json_oku(self.kilit_yolu)
            if record.get("token") != token:
                self._token = None
                return False, "Kilit başka bir oturuma ait; silinmedi."
            self.kilit_yolu.unlink()
            self._token = None
            self._olay_yaz("kilit_birakildi")
            return True, "Ortak kullanım kilidi bırakıldı."
        except FileNotFoundError:
            self._token = None
            return False, "Kilit dosyası zaten mevcut değil."
        except Exception as exc:
            self._olay_yaz("kilit_birakma_hatasi", type(exc).__name__)
            return False, f"Kilit güvenle bırakılamadı ({type(exc).__name__}); yetkili kontrolü gerekiyor."

    def eski_kilidi_temizle(self, onay: bool = False) -> tuple[bool, str]:
        with self._operation_lock:
            try:
                with self._islem_kilidi():
                    return self._eski_kilidi_temizle_impl(onay=onay)
            except Exception as exc:
                return False, f"Kilit kurtarma işlemi güvenle tamamlanamadı ({type(exc).__name__})."

    def _eski_kilidi_temizle_impl(self, onay: bool = False) -> tuple[bool, str]:
        """Açık kullanıcı onayı gerektiren kurtarma; otomatik çağrılmamalıdır.

        GUI bu yöntemi yalnızca yetkili kontrolü ve ek onaydan sonra sunmalıdır.
        """
        if not onay:
            return False, "Eski kilidi kaldırmak için açık yönetici onayı gerekiyor."
        try:
            record = self._json_oku(self.kilit_yolu)
            state = self.durum()
            if state.durum != EcedDurum.ESKI_KILIT:
                return False, "Kilit süresi geçmiş olarak doğrulanmadı; silinmedi."
            if record.get("token") != self._token and self._token:
                return False, "Kilit başka bir oturuma ait; silinmedi."
            self.kilit_yolu.unlink()
            self._token = None
            self._olay_yaz("eski_kilit_yetkili_onayiyla_kaldirildi")
            return True, "Eski kilit kaldırıldı. Portal oturumunun ayrıca kontrol edilmesi gerekiyor."
        except Exception as exc:
            return False, f"Eski kilit kaldırılamadı ({type(exc).__name__})."
