"""
Word/ENÇEV dosya kilidi servisi.

Dosyanın gerçekten başka bir Windows/SMB süreci tarafından kilitli olup
olmadığını işletim sisteminin dosya paylaşım mekanizması üzerinden kontrol eder.
Word'ün ~$ owner dosyası yalnızca kullanıcı adını göstermek için yardımcı
bilgidir; tek başına dosyanın açık olduğuna kanıt sayılmaz.
"""
from __future__ import annotations
import subprocess
import io
import csv

import re
import contextlib
import ctypes
from ctypes import wintypes
import json
import locale
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import BILGISAYAR, KULLANICI, Settings
from .models import DosyaDurumu, KilitKaydi


class KilitServisi:
    def __init__(self, settings: Settings):
        self.s = settings
        self._lock_path = self.s.sistem_klasoru / "kilit.json"
        self._hazir = False
        self._owner_cache_zamani = None
        self._owner_cache = None

    # ---------------------------------------------------- yardımcılar --

    def _hazirla(self) -> bool:
        try:
            self.s.sistem_klasoru.mkdir(parents=True, exist_ok=True)
            self._gizle(self.s.sistem_klasoru)
            self._hazir = True
            return True
        except OSError:
            return False

    @staticmethod
    def _gizle(path: Path) -> None:
        if os.name != "nt":
            return
        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)


    def _network_owner_from_server(self) -> Optional[str]:
        """
        Gerçek ağ owner'ını Windows NetFileEnum ile server üzerinden sorgular.

        Önemli: Word ~$ dosyası bir 'gerçek açık dosya sahibi' kaynağı değildir;
        eski/kalmış bir ~$ dosyası başka bir kullanıcının adını taşıyabilir.
        Bu yüzden UNC/ağ dosyalarında önce daima server tarafındaki açık dosya
        kaydı aranır.
        """
        if os.name != "nt":
            return None

        try:
            import ctypes
            from ctypes import wintypes

            full = str(self.s.dosya_yolu)
            m = re.match(r"^\\\\([^\\]+)\\(.+)$", full)
            if not m:
                return None

            server_name = "\\\\" + m.group(1)
            server_relative = "\\" + m.group(2)
            target_full = full.casefold()
            target_name = Path(full).name.casefold()

            class FILE_INFO_3(ctypes.Structure):
                _fields_ = [
                    ("fi3_id", wintypes.DWORD),
                    ("fi3_permissions", wintypes.DWORD),
                    ("fi3_num_locks", wintypes.DWORD),
                    ("fi3_pathname", wintypes.LPWSTR),
                    ("fi3_username", wintypes.LPWSTR),
                ]

            netapi = ctypes.WinDLL("Netapi32.dll")
            NetFileEnum = netapi.NetFileEnum
            NetFileEnum.argtypes = [
                wintypes.LPWSTR,
                wintypes.LPWSTR,
                wintypes.LPWSTR,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD),
            ]
            NetFileEnum.restype = wintypes.DWORD

            NetApiBufferFree = netapi.NetApiBufferFree
            NetApiBufferFree.argtypes = [ctypes.c_void_p]
            NetApiBufferFree.restype = wintypes.DWORD

            buf = ctypes.c_void_p()
            read_count = wintypes.DWORD()
            total_count = wintypes.DWORD()
            resume = wintypes.DWORD(0)

            # Önce tam dosya yolu ile sorgula. Bazı Windows sürümlerinde
            # server-relative yol eşleşmeyebilir; o durumda tüm kayıtları
            # isteyip pathname ile filtreliyoruz.
            calls = [server_relative, None]

            for basepath in calls:
                try:
                    resume.value = 0
                    status = NetFileEnum(
                        server_name,
                        basepath,
                        None,
                        3,
                        ctypes.byref(buf),
                        0xFFFFFFFF,
                        ctypes.byref(read_count),
                        ctypes.byref(total_count),
                        ctypes.byref(resume),
                    )

                    if status not in (0, 234):  # NERR_Success / ERROR_MORE_DATA
                        continue

                    if not buf.value:
                        continue

                    arr = ctypes.cast(
                        buf,
                        ctypes.POINTER(FILE_INFO_3),
                    )

                    for i in range(read_count.value):
                        item = arr[i]
                        path = str(item.fi3_pathname or "").strip()
                        username = str(item.fi3_username or "").strip()

                        if not path or not username:
                            continue

                        p_cf = path.casefold()
                        if (
                            target_full in p_cf
                            or p_cf.endswith(target_full)
                            or p_cf.endswith(server_relative.casefold())
                            or Path(path).name.casefold() == target_name
                        ):
                            owner = self._owner_adi_temizle(username)
                            if owner:
                                return owner

                except Exception:
                    pass
                finally:
                    if buf.value:
                        try:
                            NetApiBufferFree(buf)
                        except Exception:
                            pass
                        buf = ctypes.c_void_p()

                # Tek çağrıda ERROR_MORE_DATA geldiyse, resume handle ile
                # devam etmek mümkün; fakat kullanıcı bilgisayarı üzerinde
                # bu sorgunun maliyetini büyütmemek için ikinci genel taramayı
                # yalnızca ilk hedefli sorgu başarısız olduysa yapıyoruz.

        except Exception:
            return None

        return None

    def _word_kilit_yolu(self) -> Optional[Path]:
        klasor = self.s.dosya_yolu.parent
        ad = self.s.dosya_adi
        ad_tail = ad[2:] if ad.startswith("~$") else ad

        adaylar = [klasor / f"~${ad_tail}"]
        if ad != ad_tail:
            adaylar.append(klasor / f"~${ad}")

        for aday in adaylar:
            try:
                if aday.exists():
                    return aday
            except OSError:
                pass

        # Fallback: klasördeki ~$ dosyalarını hedef dosyanın adına göre tara.
        try:
            hedef = Path(ad_tail).stem.casefold()
            hedef_tail = hedef[-8:]
            for aday in klasor.glob("~$*"):
                try:
                    if not aday.is_file():
                        continue
                    aday_ad = aday.name[2:] if aday.name.startswith("~$") else aday.name
                    stem = Path(aday_ad).stem.casefold()
                    if stem == hedef or (len(hedef_tail) == 8 and len(stem) >= 8 and stem[-8:] == hedef_tail):
                        return aday
                except OSError:
                    continue
        except Exception:
            pass
        return None

    def _word_kilit_dosyasi_var_mi(self) -> bool:
        return self._word_kilit_yolu() is not None

    @staticmethod
    def _temiz_ad(metin: str) -> Optional[str]:
        metin = metin.replace("\x00", " ").strip(" \t\r\n\x00")
        if not metin:
            return None
        while metin and ord(metin[-1]) < 32:
            metin = metin[:-1]
        metin = metin.strip()
        if not metin or len(metin) > 52:
            return None
        if any(ord(c) < 32 for c in metin):
            return None
        # Owner dosyasından gelen değerin en az bir harf/rakam içermesi,
        # ikili verinin rastgele printable olarak yorumlanmasını engeller.
        if not any(c.isalpha() or c.isdigit() for c in metin):
            return None
        return metin

    def _openfiles_owner_from_server(self) -> Optional[str]:
        """Windows openfiles.exe ile UNC dosyasının görünen kullanıcı adını bul."""
        if os.name != "nt":
            return None
        try:
            full = str(self.s.dosya_yolu)
            m = re.match(r"^\\\\([^\\]+)\\", full)
            if not m:
                return None
            server = "\\\\" + m.group(1)
            target = Path(full).name.casefold()

            cp = subprocess.run(
                ["openfiles.exe", "/query", "/s", server, "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            rows = list(csv.reader(io.StringIO(cp.stdout or "")))
            for row in rows:
                if not row:
                    continue
                line = " | ".join(x.strip() for x in row if x.strip())
                if target not in line.casefold():
                    continue
                for cell in row:
                    cell = cell.strip()
                    if not cell or cell.casefold() == target or len(cell) > 80:
                        continue
                    if not re.fullmatch(r"[A-Za-z0-9_.\\- ]+", cell):
                        continue
                    if cell.casefold() in {"system", "admin", "administrator", "everyone"}:
                        continue
                    return cell
        except Exception:
            pass
        return None

    def _word_kilit_sahibi(self) -> Optional[str]:
        """Word ~$ kilit dosyasından kullanıcı adını oku.

        UNC/Synology dahil tüm yollar için doğrudan ~$ dosyasını kullanır.
        Word açılmaz; yalnızca mevcut temp dosyası okunur.
        """
        try:
            yol = self._word_kilit_yolu()
            if yol is None:
                return None

            veri = yol.read_bytes()
            if len(veri) < 2:
                return None

            # Word'ün gerçek formatı: byte 54 = karakter sayısı,
            # byte 56'dan itibaren UTF-16-LE kullanıcı adı.
            if len(veri) >= 56:
                n = veri[54]
                if 1 <= n <= 52 and len(veri) >= 56 + n * 2:
                    raw = veri[56:56 + n * 2]
                    try:
                        aday = type(self)._owner_adi_temizle(
                            raw.decode("utf-16le", errors="strict")
                        )
                        if aday:
                            return aday
                    except Exception:
                        pass

            # Word sürüm/farklı format fallback'leri.
            for start in (56, 58, 60, 0x100):
                if start >= len(veri) - 4:
                    continue
                try:
                    metin = veri[start:start + 120].decode(
                        "utf-16le", errors="ignore"
                    )
                    metin = metin.split("\x00\x00", 1)[0]
                    metin = metin.replace("\x00", "").strip()
                    aday = type(self)._owner_adi_temizle(metin)
                    if aday:
                        return aday
                except Exception:
                    continue

            # Eski ASCII fallback.
            try:
                n = veri[0]
                if 1 <= n <= 52 and len(veri) >= 1 + n:
                    raw = veri[1:1 + n]
                    for enc in ("cp1254", "cp1252", "latin-1"):
                        try:
                            aday = type(self)._owner_adi_temizle(
                                raw.decode(enc, errors="strict")
                            )
                            if aday:
                                return aday
                        except Exception:
                            continue
            except Exception:
                pass
        except Exception as exc:
            try:
                print(f"[WORD ~$ OWNER] {exc}")
            except Exception:
                pass
        return None

    @staticmethod
    def _owner_adi_temizle(ad: str) -> Optional[str]:
        if not ad:
            return None
        ad = re.sub(r"[\x00\r\n]+", " ", ad).strip()
        ad = re.sub(r"\s+", " ", ad)
        if len(ad) < 2 or len(ad) > 80:
            return None
        if not re.fullmatch(r"[A-Za-zĞÜŞİÖÇğüşıöç0-9_.\- ]+", ad):
            return None
        if not any(ch.isalpha() for ch in ad):
            return None
        return ad

    def _owner_candidate(self, raw: bytes) -> Optional[str]:
        for enc in ("utf-16-le", "utf-16", "cp1254", "cp1252", "latin-1"):
            try:
                txt = raw.decode(enc, errors="ignore")
            except Exception:
                continue
            for part in re.split(r"\x00+", txt):
                part = part.strip()
                if not part:
                    continue
                ad = self._owner_adi_temizle(part)
                if ad:
                    return ad
        return None

    def _dosya_gercekten_kilitli_mi(self) -> bool:
        """
        Windows'ta dosyayı hiçbir paylaşım hakkı vermeden açmayı dener.

        Başka bir süreç dosyayı Word gibi paylaşım kısıtıyla açmışsa
        CreateFileW ERROR_SHARING_VIOLATION (32) döndürür ve dosya gerçekten
        kullanımda kabul edilir. Dosyaya erişim mümkünse burada False döner.

        Not: yalnızca ~$ owner dosyasının bulunması artık kilit kanıtı değildir;
        stale ~$ dosyaları bu sayede kullanıcıyı yanlış yönlendiremez.
        """
        path = self.s.dosya_yolu
        if os.name != "nt":
            # Linux/macOS'ta bu uygulamanın hedefi Windows olduğundan burada
            # en güvenli fallback, owner dosyasını kanıt saymamaktır.
            return False

        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        GENERIC_READ = 0x80000000
        FILE_SHARE_NONE = 0x00000000
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x00000080
        ERROR_FILE_NOT_FOUND = 2
        ERROR_PATH_NOT_FOUND = 3
        ERROR_ACCESS_DENIED = 5
        ERROR_SHARING_VIOLATION = 32

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        CreateFileW = kernel32.CreateFileW
        CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        CreateFileW.restype = ctypes.c_void_p

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [ctypes.c_void_p]
        CloseHandle.restype = ctypes.c_int

        handle = CreateFileW(
            str(path),
            GENERIC_READ,
            FILE_SHARE_NONE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle != INVALID_HANDLE_VALUE:
            try:
                return False
            finally:
                CloseHandle(handle)

        err = ctypes.get_last_error()
        if err == ERROR_SHARING_VIOLATION:
            return True
        if err in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
            return False
        if err == ERROR_ACCESS_DENIED:
            # Erişim reddi ile gerçek kilidi birbirine karıştırma. Ağdaki
            # ACL nedeniyle erişim yoksa burada yalnızca belirsiz kalabilir.
            return False
        return False

    # ------------------------------------------------------------ okuma --

    def dosya_gercekten_kilitli_mi(self) -> bool:
        """Servis katmanının kullanacağı public fiziksel kilit kontrolü."""
        return self._dosya_gercekten_kilitli_mi()


    def oku(self) -> Optional[KilitKaydi]:
        try:
            if not self._lock_path.exists():
                return None
            data = json.loads(self._lock_path.read_text(encoding="utf-8"))
            kayit = KilitKaydi.from_json(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

        try:
            son_hb = datetime.fromisoformat(kayit.son_heartbeat)
        except ValueError:
            son_hb = datetime.min

        # Heartbeat'i ölmüş uygulama kilidini hiçbir durumda canlı kabul etme.
        # Başka kullanıcı Word'ü açtıysa stale JSON eski kullanıcıyı göstermemeli.
        if (datetime.now() - son_hb) > timedelta(seconds=self.s.kilit_olu_esigi_sn):
            self._sil_guvensiz()
            return None

        return kayit

    def word_kilit_sahibi(self) -> Optional[str]:
        try:
            return self._word_kilit_sahibi()
        except Exception:
            return None

    def durum(self) -> DosyaDurumu:
        if not self.s.ag_yolu or not Path(self.s.ag_yolu).exists():
            return DosyaDurumu.BAGLANTI_YOK
        if not self.s.dosya_yolu.exists():
            return DosyaDurumu.DOSYA_YOK

        kayit = self.oku()
        try:
            fiziksel = bool(self._dosya_gercekten_kilitli_mi())
        except Exception:
            fiziksel = False

        if kayit is not None:
            if fiziksel:
                return (
                    DosyaDurumu.KULLANIMDA_BENDE
                    if kayit.kullanici == KULLANICI
                    else DosyaDurumu.KULLANIMDA_BASKASI
                )

            # Kilit önce ENCTakip tarafından alınır, Word dosyasının gerçek
            # Windows kilidi birkaç an sonra oluşabilir. Bu kısa rezervasyon
            # penceresinde kaydı silmek iki kontrol arasında yarışa neden olur.
            # Pencere, hem heartbeat aralığı hem de ölü-kilit eşiğiyle sınırlı;
            # kilidin acilis zamanına bağlıdır, heartbeat ile uzatılamaz.
            try:
                acilis = datetime.fromisoformat(str(kayit.acilis))
                simdi = datetime.now(acilis.tzinfo) if acilis.tzinfo else datetime.now()
                yas_sn = max(0.0, (simdi - acilis).total_seconds())
                olu_esigi = max(
                    1.0,
                    float(getattr(self.s, "kilit_olu_esigi_sn", 90) or 90),
                )
                heartbeat_araligi = max(
                    1.0,
                    float(getattr(self.s, "heartbeat_araligi_sn", 20) or 20),
                )
                rezervasyon_penceresi = min(olu_esigi, heartbeat_araligi)
                if yas_sn <= rezervasyon_penceresi:
                    return (
                        DosyaDurumu.KULLANIMDA_BENDE
                        if kayit.kullanici == KULLANICI
                        else DosyaDurumu.KULLANIMDA_BASKASI
                    )
            except (AttributeError, TypeError, ValueError, OverflowError):
                # Zaman damgası doğrulanamıyorsa kaydı aktif kabul etme.
                pass

            # Rezervasyon penceresi geçti ve gerçek Windows kilidi yok:
            # artık hayalet kilit olarak temizlenebilir.
            try:
                self._sil_guvensiz()
            except Exception:
                pass
            return DosyaDurumu.MUSAIT

        if fiziksel:
            return DosyaDurumu.BELIRSIZ
        return DosyaDurumu.MUSAIT

    def zorla_birak_sonuc(self) -> tuple[bool, str]:
        try:
            if self._dosya_gercekten_kilitli_mi():
                sahibi = None
                try:
                    sahibi = self._word_kilit_sahibi()
                except Exception:
                    pass
                if sahibi:
                    return False, (f"Dosya şu anda {sahibi} tarafından Word üzerinden kullanımda. "
                                   "Gerçek Windows kilidi kullanıcı tarafından kapatılmadan kaldırılamaz.")
                return False, "Dosya şu anda Word/Windows tarafından gerçekten kilitli. Aktif kullanıcı kapatmadan kaldırılamaz."
            program_ok = False
            fn = getattr(self, "zorla_birak", None)
            if callable(fn):
                try:
                    program_ok = bool(fn())
                except Exception:
                    pass
            owner_removed = False
            owner = self._word_kilit_yolu()
            if owner is not None and not self._dosya_gercekten_kilitli_mi():
                try:
                    if os.name == "nt":
                        ctypes.windll.kernel32.SetFileAttributesW(str(owner), 0x80)
                except Exception:
                    pass
                try:
                    owner.unlink()
                    owner_removed = True
                except OSError:
                    pass
            if program_ok or owner_removed:
                return True, "Eski/stale kilit kaydı temizlendi."
            return False, "Kaldırılacak aktif ENCTakip kilidi bulunamadı."
        except Exception as exc:
            return False, f"Kilit temizleme hatası: {exc}"

    def al(self) -> bool:
        if not self._hazir:
            self._hazirla()

        # Gerçek Word/ağ kilidi varsa uygulama kilidini alamayız.
        if self._dosya_gercekten_kilitli_mi():
            return False

        # Mikrosaniye hassasiyeti, rezervasyon penceresi hesabinda saniye siniri
        # yakininda yanlis erken temizlemeyi engeller. JSON yapisi ayni kalir.
        now = datetime.now().isoformat(timespec="microseconds")
        kayit = KilitKaydi(
            kullanici=KULLANICI,
            bilgisayar=BILGISAYAR,
            acilis=now,
            son_heartbeat=now,
        )

        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(kayit.to_json(), f, ensure_ascii=False)
            except Exception:
                os.close(fd)
                raise
            self._gizle(self._lock_path)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def heartbeat(self) -> None:
        kayit = self.oku()
        if kayit is None or kayit.kullanici != KULLANICI:
            return
        kayit.son_heartbeat = datetime.now().isoformat(timespec="seconds")
        with contextlib.suppress(OSError):
            self._lock_path.write_text(
                json.dumps(kayit.to_json(), ensure_ascii=False), encoding="utf-8"
            )

    def birak(self) -> bool:
        kayit = self.oku()
        if kayit is None or kayit.kullanici != KULLANICI:
            return False
        return self._sil_guvensiz()

    def zorla_birak(self) -> bool:
        return self._sil_guvensiz()

    def _sil_guvensiz(self) -> bool:
        try:
            if self._lock_path.exists():
                with contextlib.suppress(Exception):
                    if os.name == "nt":
                        ctypes.windll.kernel32.SetFileAttributesW(str(self._lock_path), 0x80)
                self._lock_path.unlink()
            return True
        except OSError:
            return False
