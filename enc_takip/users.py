"""ENCTakip merkezi kullanıcı/profil rehberi.

Kullanıcı profilleri ağdaki `.encev_sistem/kullanicilar.json` dosyasında tutulur.
İstemciler dosyayı doğrudan veritabanı gibi kullanmaz; okuma tarafında hafif bir
bellek bulunur, yazma tarafında ise atomik ve kısa süreli dosya kilidi kullanılır.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
PROFILE_DIR_NAME = "profiles"

DURUMLAR = {
    "online": ("Çevrimiçi", "#00a884"),
    "busy": ("Meşgul", "#e74c3c"),
    "meeting": ("Toplantıda", "#7c3aed"),
    "away": ("Dışarıda", "#f59e0b"),
}
VARSAYILAN_DURUM = "online"


def _normalize(value: str) -> str:
    """Türkçe karakter farklarını da kaldırarak güvenli karşılaştırma anahtarı üretir."""
    text = str(value or "").strip().casefold()
    text = text.translate(str.maketrans({
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s",
        "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    }))
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


@dataclass(frozen=True)
class KullaniciProfili:
    kullanici_adi: str
    ad_soyad: str
    unvan: str = ""
    telefon: str = ""
    eposta: str = ""
    departman: str = ""
    profil_fotografi: str = ""
    bilgisayarlar: tuple[str, ...] = field(default_factory=tuple)
    kullanici_aliaslari: tuple[str, ...] = field(default_factory=tuple)
    durum: str = VARSAYILAN_DURUM
    rol: str = "user"
    aktif: bool = True

    @property
    def gorunen_ad(self) -> str:
        return self.ad_soyad or self.kullanici_adi

    @property
    def bas_harfler(self) -> str:
        parcalar = [x for x in self.gorunen_ad.strip().split() if x]
        if not parcalar:
            return (self.kullanici_adi[:1] or "?").upper()
        if len(parcalar) == 1:
            return parcalar[0][:1].upper()
        return (parcalar[0][:1] + parcalar[-1][:1]).upper()

    @property
    def durum_adi(self) -> str:
        return DURUMLAR.get(self.durum, DURUMLAR[VARSAYILAN_DURUM])[0]

    @property
    def durum_rengi(self) -> str:
        return DURUMLAR.get(self.durum, DURUMLAR[VARSAYILAN_DURUM])[1]


VARSAYILAN_KULLANICILAR: tuple[KullaniciProfili, ...] = (
    KullaniciProfili(
        kullanici_adi="burak",
        ad_soyad="Burak Can Erdağ",
        unvan="Biyolog",
        telefon="0542 665 0159",
        eposta="burak@encev.com.tr",
        bilgisayarlar=(),
        kullanici_aliaslari=("burak",),
        rol="admin",
    ),
    KullaniciProfili(
        kullanici_adi="tuğçe",
        ad_soyad="Tuğçe Çırakoğlu",
        unvan="Çevre Mühendisi",
        telefon="0539 229 93 57",
        eposta="tugce@encev.com.tr",
        bilgisayarlar=(),
        kullanici_aliaslari=("tugce",),
        rol="user",
    ),
)


class KullaniciRehberi:
    """Merkezi kullanıcı/profil kaynağı için hafif istemci erişimi ve güvenli yazma."""

    def __init__(self, settings: Any):
        self._settings = settings
        self._yol = Path(settings.sistem_klasoru) / "kullanicilar.json"
        self._profil_klasoru = Path(settings.sistem_klasoru) / PROFILE_DIR_NAME
        self._kilit_yolu = self._yol.with_suffix(self._yol.suffix + ".lock")
        self._lock = threading.RLock()
        self._profil_map: dict[str, KullaniciProfili] = {}
        self._mtime_ns = -1
        self._yukle(force=True)

    @property
    def yol(self) -> Path:
        return self._yol

    @property
    def profil_klasoru(self) -> Path:
        return self._profil_klasoru

    def _varsayilan_map(self) -> dict[str, KullaniciProfili]:
        return {_normalize(p.kullanici_adi): p for p in VARSAYILAN_KULLANICILAR}

    def _profil_olustur(self, raw: dict[str, Any]) -> KullaniciProfili | None:
        username = str(raw.get("kullanici_adi", "") or "").strip()
        if not username:
            return None
        computers = raw.get("bilgisayarlar", [])
        aliases = raw.get("kullanici_aliaslari", [])
        if not isinstance(computers, list):
            computers = []
        if not isinstance(aliases, list):
            aliases = []
        durum = str(raw.get("durum", VARSAYILAN_DURUM) or VARSAYILAN_DURUM).strip().casefold()
        if durum not in DURUMLAR:
            durum = VARSAYILAN_DURUM
        rol = str(raw.get("rol", "user") or "user").strip().casefold()
        if rol not in {"admin", "user"}:
            rol = "user"
        return KullaniciProfili(
            kullanici_adi=username,
            ad_soyad=str(raw.get("ad_soyad", "") or "").strip(),
            unvan=str(raw.get("unvan", "") or "").strip(),
            telefon=str(raw.get("telefon", "") or "").strip(),
            eposta=str(raw.get("eposta", "") or "").strip(),
            departman=str(raw.get("departman", "") or "").strip(),
            profil_fotografi=str(raw.get("profil_fotografi", "") or "").strip(),
            bilgisayarlar=tuple(str(x).strip() for x in computers if str(x).strip()),
            kullanici_aliaslari=tuple(str(x).strip() for x in aliases if str(x).strip()),
            durum=durum,
            rol=rol,
            aktif=bool(raw.get("aktif", True)),
        )

    def _ham_veriyi_oku(self) -> dict[str, Any]:
        if self._yol.exists():
            try:
                data = json.loads(self._yol.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "schema_version": SCHEMA_VERSION,
            "kullanicilar": [self._profil_to_raw(p) for p in VARSAYILAN_KULLANICILAR],
        }

    @staticmethod
    def _profil_to_raw(p: KullaniciProfili) -> dict[str, Any]:
        return asdict(p) | {
            "bilgisayarlar": list(p.bilgisayarlar),
            "kullanici_aliaslari": list(p.kullanici_aliaslari),
        }

    def _yukle(self, force: bool = False) -> None:
        with self._lock:
            try:
                imza = self._yol.stat().st_mtime_ns
            except OSError:
                imza = -1
            if not force and imza == self._mtime_ns:
                return

            profil_map = self._varsayilan_map()
            try:
                if self._yol.exists():
                    data = json.loads(self._yol.read_text(encoding="utf-8-sig"))
                    records = data.get("kullanicilar", []) if isinstance(data, dict) else []
                    for raw in records:
                        if not isinstance(raw, dict):
                            continue
                        profil = self._profil_olustur(raw)
                        if profil is not None:
                            profil_map[_normalize(profil.kullanici_adi)] = profil
            except Exception:
                pass

            self._profil_map = profil_map
            self._mtime_ns = imza

            if not self._yol.exists():
                self._merkezi_baslangic_kaydini_olustur()

    def _merkezi_baslangic_kaydini_olustur(self) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "kullanicilar": [self._profil_to_raw(p) for p in VARSAYILAN_KULLANICILAR],
        }
        try:
            self._yol.parent.mkdir(parents=True, exist_ok=True)
            self._atomik_yaz(data)
        except Exception:
            try:
                gecici = Path(tempfile.gettempdir()) / "ENCTakip_kullanicilar_default.json"
                gecici.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _lock_al(self, timeout: float = 8.0):
        self._kilit_yolu.parent.mkdir(parents=True, exist_ok=True)
        bas = time.monotonic()
        while True:
            try:
                fd = os.open(str(self._kilit_yolu), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii", "ignore"))
                return fd
            except FileExistsError:
                try:
                    if time.time() - self._kilit_yolu.stat().st_mtime > 30:
                        self._kilit_yolu.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() - bas >= timeout:
                    raise TimeoutError("Kullanıcı veritabanı kilidi alınamadı.")
                time.sleep(0.05)

    def _lock_birak(self, fd: int) -> None:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            self._kilit_yolu.unlink(missing_ok=True)
        except Exception:
            pass

    def _atomik_yaz(self, data: dict[str, Any]) -> None:
        self._yol.parent.mkdir(parents=True, exist_ok=True)
        fd_lock = self._lock_al()
        try:
            gecici = self._yol.with_suffix(self._yol.suffix + ".tmp")
            gecici.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(gecici, self._yol)
        finally:
            self._lock_birak(fd_lock)

    def _kayitlari_guncelle(self, mutator) -> KullaniciProfili | None:
        with self._lock:
            fd_lock = self._lock_al()
            try:
                data = self._ham_veriyi_oku()
                records = data.get("kullanicilar", []) if isinstance(data, dict) else []
                if not isinstance(records, list):
                    records = []
                result = mutator(records)
                data["schema_version"] = SCHEMA_VERSION
                data["kullanicilar"] = records
                gecici = self._yol.with_suffix(self._yol.suffix + ".tmp")
                self._yol.parent.mkdir(parents=True, exist_ok=True)
                gecici.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(gecici, self._yol)
            finally:
                self._lock_birak(fd_lock)
            self._mtime_ns = -1
            self._yukle(force=True)
            return result

    def yenile(self) -> None:
        self._yukle(force=False)

    def cache_profili(self, kullanici_adi: str) -> KullaniciProfili | None:
        """Yalnızca bellekteki rehberi kullanır; ağ/mtime kontrolü yapmaz."""
        with self._lock:
            key = _normalize(kullanici_adi)
            direct = self._profil_map.get(key)
            if direct is not None:
                return direct
            for p in self._profil_map.values():
                if any(_normalize(x) == key for x in p.kullanici_aliaslari):
                    return p
                if any(_normalize(x) == key for x in p.bilgisayarlar):
                    return p
        return None

    def cache_tumu(self, dahil_pasif: bool = False) -> list[KullaniciProfili]:
        """Bellekteki kullanıcı snapshot'ını döndürür; GUI hızlı yolları için ağ I/O yoktur."""
        with self._lock:
            profiller = list(self._profil_map.values())
        if not dahil_pasif:
            profiller = [x for x in profiller if x.aktif]
        return sorted(profiller, key=lambda p: (p.ad_soyad or p.kullanici_adi).casefold())

    def profil(self, kullanici_adi: str) -> KullaniciProfili | None:
        self._yukle(force=False)
        key = _normalize(kullanici_adi)
        direct = self._profil_map.get(key)
        if direct is not None:
            return direct
        for p in self._profil_map.values():
            if any(_normalize(x) == key for x in p.kullanici_aliaslari):
                return p
            if any(_normalize(x) == key for x in p.bilgisayarlar):
                return p
        return None

    def profil_esle(self, kullanici_adi: str = "", bilgisayar: str = "") -> KullaniciProfili | None:
        self._yukle(force=False)
        kul_key = _normalize(kullanici_adi)
        pc_key = _normalize(bilgisayar)
        if kul_key:
            direct = self._profil_map.get(kul_key)
            if direct is not None:
                return direct
            for p in self._profil_map.values():
                if any(_normalize(x) == kul_key for x in p.kullanici_aliaslari):
                    return p
        if pc_key:
            for p in self._profil_map.values():
                if any(_normalize(x) == pc_key for x in p.bilgisayarlar):
                    return p
        return None

    def tumu(self, dahil_pasif: bool = False) -> list[KullaniciProfili]:
        self._yukle(force=False)
        profiller = list(self._profil_map.values())
        if not dahil_pasif:
            profiller = [x for x in profiller if x.aktif]
        return sorted(profiller, key=lambda p: (p.ad_soyad or p.kullanici_adi).casefold())

    def kullanici_adlari(self, kendini_haric: str = "", bilgisayar: str = "") -> list[str]:
        kendisi = self.profil_esle(kendini_haric, bilgisayar)
        kendisi_key = _normalize(kendisi.kullanici_adi) if kendisi else _normalize(kendini_haric)
        return [p.kullanici_adi for p in self.tumu() if _normalize(p.kullanici_adi) != kendisi_key]

    def guncelle(self, kullanici_adi: str, **alanlar: Any) -> KullaniciProfili | None:
        canonical = self.profil(kullanici_adi)
        if canonical is None:
            return None
        allowed = {
            "ad_soyad", "unvan", "telefon", "eposta", "departman",
            "profil_fotografi", "bilgisayarlar", "kullanici_aliaslari", "durum", "rol", "aktif",
        }

        def mutator(records):
            sonuc = None
            for raw in records:
                if not isinstance(raw, dict):
                    continue
                if _normalize(str(raw.get("kullanici_adi", ""))) != _normalize(canonical.kullanici_adi):
                    continue
                for key, val in alanlar.items():
                    if key not in allowed:
                        continue
                    if key in {"bilgisayarlar", "kullanici_aliaslari"}:
                        raw[key] = [str(x).strip() for x in (val or []) if str(x).strip()]
                    elif key == "durum":
                        v = str(val or VARSAYILAN_DURUM).casefold()
                        raw[key] = v if v in DURUMLAR else VARSAYILAN_DURUM
                    elif key == "rol":
                        v = str(val or "user").strip().casefold()
                        raw[key] = v if v in {"admin", "user"} else "user"
                    elif key == "aktif":
                        raw[key] = bool(val)
                    else:
                        raw[key] = str(val or "").strip()
                raw.setdefault("kullanici_adi", canonical.kullanici_adi)
                sonuc = self._profil_olustur(raw)
                break
            if sonuc is None:
                raw = self._profil_to_raw(canonical)
                for key, val in alanlar.items():
                    if key in allowed:
                        raw[key] = val
                records.append(raw)
                sonuc = self._profil_olustur(raw)
            return sonuc

        return self._kayitlari_guncelle(mutator)

    def yonetici_mi(self, kullanici_adi: str) -> bool:
        """Merkezi kullanıcı kaydındaki rol alanına göre yönetici kontrolü."""
        profil = self.profil_esle(kullanici_adi, os.environ.get("COMPUTERNAME", ""))
        return bool(profil and profil.aktif and profil.rol == "admin")

    def durum_degistir(self, kullanici_adi: str, durum: str) -> KullaniciProfili | None:
        durum = str(durum or VARSAYILAN_DURUM).casefold()
        if durum not in DURUMLAR:
            durum = VARSAYILAN_DURUM
        return self.guncelle(kullanici_adi, durum=durum)

    def profil_fotografi_kaydet(self, kullanici_adi: str, kaynak: str | Path) -> KullaniciProfili | None:
        profil = self.profil_esle(kullanici_adi, os.environ.get("COMPUTERNAME", ""))
        if profil is None:
            return None
        source = Path(kaynak)
        if not source.is_file():
            return None
        if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            raise ValueError("Profil fotoğrafı JPG, PNG, WEBP veya BMP olmalıdır.")
        self._profil_klasoru.mkdir(parents=True, exist_ok=True)
        ext = source.suffix.lower()
        hedef = self._profil_klasoru / f"{_normalize(profil.kullanici_adi)}{ext}"
        gecici = hedef.with_suffix(hedef.suffix + ".tmp")
        shutil.copy2(source, gecici)
        os.replace(gecici, hedef)
        rel = f"{PROFILE_DIR_NAME}/{hedef.name}"
        return self.guncelle(profil.kullanici_adi, profil_fotografi=rel)

    def avatar_yolu(self, profil: KullaniciProfili) -> Path | None:
        raw = str(profil.profil_fotografi or "").strip()
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = Path(self._settings.sistem_klasoru) / p
        try:
            root = Path(self._settings.sistem_klasoru).resolve()
            resolved = p.resolve()
            if root not in resolved.parents and resolved != root:
                return None
            return resolved if resolved.is_file() else None
        except Exception:
            return None
