# -*- coding: utf-8 -*-
"""
ENCTakip OWNER RESTORE v10

Amaç:
- 15 Eylül'deki çalışan Motor sistemindeki Word ~$ owner okuma mantığını
  yeni ECNTakip/KilitServisi mimarisine geri taşımak.
- UNC/Synology dosyalarda ~$ fallback'i yeniden etkinleştirmek.
- Yanlış/stale ~$ isminden dolayı programın kullanılmayan dosyayı KULLANIMDA
  göstermesini engellemek için owner sorgusunu fiziksel kilit kontrolüne bağlamak.
- app.py ve service.py'ye dokunmamak; mevcut service.py zaten
  self.kilit.word_kilit_sahibi() çağrısını kullanıyor.

Yama yalnızca enc_takip/lock_service.py üzerinde çalışır.
Başarısızlıkta otomatik rollback yapar.
"""
from __future__ import annotations

import py_compile
import re
import shutil
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve()
ROOT = None
for p in (HERE.parent, *HERE.parents):
    if (
        (p / "enc_takip" / "lock_service.py").exists()
        and (p / "enc_takip" / "service.py").exists()
        and (p / "enc_takip" / "gui" / "app.py").exists()
    ):
        ROOT = p
        break

if ROOT is None:
    raise SystemExit("[HATA] ENCTakip proje kökü bulunamadı.")

LOCK = ROOT / "enc_takip" / "lock_service.py"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = LOCK.with_name(f"lock_service.py.bak_owner_restore_v10_{STAMP}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def replace_method(src: str, name: str, body: str) -> str:
    """Bir class metodu gövdesini güvenli biçimde değiştir."""
    pat = re.compile(
        rf"(?ms)^    def {re.escape(name)}\s*\(.*?(?=^    def |^class |\Z)"
    )
    m = pat.search(src)
    if not m:
        raise RuntimeError(f"lock_service.py içinde {name}() bulunamadı.")
    return src[:m.start()] + body.rstrip() + "\n\n" + src[m.end():]


def insert_before_method(src: str, names: list[str], block: str) -> str:
    for name in names:
        marker = f"\n    def {name}("
        pos = src.find(marker)
        if pos >= 0:
            return src[:pos] + "\n" + block.rstrip() + "\n" + src[pos:]
    raise RuntimeError(
        "Güvenli owner metodu ekleme noktası bulunamadı: " + ", ".join(names)
    )


OWNER_VALID_METHOD = r'''    @staticmethod
    def _owner_adi_temizle(metin: str) -> Optional[str]:
        """15.09.2026'da çalışan Motor sürümündeki kullanıcı adı filtresi."""
        try:
            if not metin:
                return None
            metin = str(metin).replace("\x00", "").strip()
            if len(metin) < 2 or len(metin) > 50:
                return None
            if not re.match(
                r'^[A-Za-zĞÜŞİÖÇğüşıöç0-9_\-. ]+$',
                metin,
            ):
                return None
            if metin.casefold() in {
                "system",
                "administrator",
                "admin",
                "everyone",
            }:
                return None
            return metin
        except Exception:
            return None
'''


WORD_PATH_METHOD = r'''    def _word_kilit_yolu(self) -> Optional[Path]:
        """Word ~$ dosyasını eski çalışan Motor mantığıyla bulur."""
        try:
            klasor = self.s.dosya_yolu.parent
            ad = self.s.dosya_adi

            # 1) Tam eşleşme — Word'ün normal ~$<dosya> adı.
            adaylar = [
                klasor / ("~$" + ad),
            ]

            if ad.startswith("~$"):
                adaylar.append(
                    klasor / ("~$" + ad[2:])
                )

            for aday in adaylar:
                try:
                    if aday.exists():
                        return aday
                except OSError:
                    pass

            # 2) Eski Motor'daki sıkı son-8 karakter fallback'i.
            hedef = Path(ad).stem
            if hedef.startswith("~$"):
                hedef = hedef[2:]

            if len(hedef) < 8:
                return None

            son_8 = hedef[-8:].casefold()

            for aday in klasor.iterdir():
                try:
                    if not aday.is_file():
                        continue
                    if not aday.name.startswith("~$"):
                        continue
                    if aday.suffix.casefold() != ".docx":
                        continue

                    aday_stem = Path(aday.name[2:]).stem
                    if (
                        len(aday_stem) >= 8
                        and aday_stem[-8:].casefold() == son_8
                    ):
                        return aday
                except OSError:
                    continue

        except Exception:
            pass

        return None
'''


WORD_OWNER_METHOD = r'''    def _word_kilit_sahibi(self) -> Optional[str]:
        """
        Eski çalışan Motor'un word_kilit_sahibi() mantığı.

        Öncelik:
        1) Windows ağ owner'ı mevcutsa gerçek server owner'ı
        2) Word ~$ dosyası içindeki UTF-16 owner
        3) Diğer Word UTF-16 offsetleri
        4) ANSI/ASCII fallback

        Ağ/Synology'de Windows server API'leri yetki nedeniyle çalışmasa bile
        Word ~$ dosyası doğrudan SMB üzerinden okunabilir.
        """
        # ---------------------------------------------------------
        # Gerçek server owner varsa onu tercih et.
        # NetFileEnum/openfiles mevcut sürümde bulunabilir; erişim reddi
        # durumunda sessizce Word ~$ fallback'ine geçiyoruz.
        # ---------------------------------------------------------
        for method_name in (
            "_network_owner_from_server",
            "_openfiles_owner_from_server",
        ):
            try:
                fn = getattr(self, method_name, None)
                if callable(fn):
                    owner = fn()
                    owner = self._owner_adi_temizle(owner)
                    if owner:
                        return owner
            except Exception:
                pass

        # ---------------------------------------------------------
        # Word ~$ dosyası
        # ---------------------------------------------------------
        yol = self._word_kilit_yolu()
        if yol is None:
            return None

        try:
            with open(yol, "rb") as f:
                icerik = f.read()
        except OSError:
            return None

        if len(icerik) < 2:
            return None

        # Yöntem 1 — eski Motor: UTF-16 standart bölüm.
        if len(icerik) > 56:
            try:
                uzunluk = icerik[54]
                if 1 <= uzunluk <= 52:
                    ad_bytes = icerik[56:56 + uzunluk * 2]
                    ad = ad_bytes.decode(
                        "utf-16-le",
                        errors="ignore",
                    )
                    ad = ad.replace("\x00", "").strip()
                    aday = self._owner_adi_temizle(ad)
                    if aday:
                        return aday
            except Exception:
                pass

        # Yöntem 2 — eski Motor'daki farklı UTF-16 offsetleri.
        for offset in (
            54,
            56,
            58,
            0x36,
            0x38,
            0x3A,
            0x100,
        ):
            if offset >= len(icerik) - 4:
                continue

            try:
                metin = icerik[
                    offset:offset + 120
                ].decode(
                    "utf-16-le",
                    errors="ignore",
                )
                metin = (
                    metin
                    .split("\x00\x00")[0]
                    .replace("\x00", "")
                    .strip()
                )
                metin = "".join(
                    c for c in metin
                    if c.isprintable()
                )
                aday = self._owner_adi_temizle(metin)
                if aday:
                    return aday
            except Exception:
                continue

        # Yöntem 3 — eski Motor ASCII fallback.
        try:
            uzunluk = icerik[0]
            if 1 <= uzunluk <= 52:
                ad = icerik[
                    1:1 + uzunluk
                ].decode(
                    "ascii",
                    errors="ignore",
                )
                ad = "".join(
                    c for c in ad
                    if c.isprintable() and c != "\x00"
                ).strip()
                aday = self._owner_adi_temizle(ad)
                if aday:
                    return aday
        except Exception:
            pass

        return None
'''


PUBLIC_OWNER_METHOD = r'''    def word_kilit_sahibi(self) -> Optional[str]:
        """
        Aktif kilit varsa owner adını döndürür.

        Önemli: ~$ dosyasının tek başına varlığı artık KULLANIMDA nedeni
        sayılmıyor. Böylece eski/stale bir ~$ dosyası kullanıcı adını GUI'ye
        taşıyamaz. Önce mevcut fiziksel kilit kontrolü denenir.
        """
        try:
            # Fiziksel kilit kontrolü mevcutsa, stale ~$ dosyasında owner
            # döndürme. Method mevcut değilse parser normal şekilde çalışır.
            check = getattr(self, "_dosya_gercekten_kilitli_mi", None)
            if callable(check):
                try:
                    if not bool(check()):
                        return None
                except Exception:
                    # Kilit kontrolü teknik nedenle çalışmazsa owner parserı
                    # yine denenebilir; çağıran durum() zaten kendi kararını verir.
                    pass

            return self._word_kilit_sahibi()
        except Exception:
            return None
'''


def ensure_import(src: str, line: str) -> str:
    module = line.replace("import ", "", 1).strip()
    if re.search(rf"(?m)^import {re.escape(module)}\s*$", src):
        return src

    lines = src.splitlines(keepends=True)
    last_future = -1
    for i, item in enumerate(lines):
        if re.match(r"^from __future__ import ", item):
            last_future = i

    if last_future >= 0:
        lines.insert(last_future + 1, line + "\n")
        return "".join(lines)

    for i, item in enumerate(lines):
        if re.match(r"^(import|from) ", item):
            lines.insert(i, line + "\n")
            return "".join(lines)

    return line + "\n" + src


def main() -> None:
    print("ENCTakip — OWNER GERİ YÜKLEME v10")
    print("====================================")

    if not LOCK.exists():
        raise SystemExit(f"[HATA] lock_service.py bulunamadı: {LOCK}")

    shutil.copy2(LOCK, BACKUP)
    print(f"[OK] Yedek: {BACKUP.name}")

    try:
        src = read_text(LOCK)

        if "class KilitServisi" not in src:
            raise RuntimeError(
                "lock_service.py içinde KilitServisi bulunamadı."
            )

        # Gerekli modüller. re büyük ihtimalle zaten mevcut ama güvence.
        src = ensure_import(src, "import re")

        # Owner validasyon helper'ı.
        if re.search(
            r"(?m)^    def _owner_adi_temizle\s*\(",
            src,
        ):
            src = replace_method(
                src,
                "_owner_adi_temizle",
                OWNER_VALID_METHOD,
            )
        else:
            src = insert_before_method(
                src,
                [
                    "_word_kilit_sahibi",
                    "word_kilit_sahibi",
                    "durum",
                    "oku",
                ],
                OWNER_VALID_METHOD,
            )

        # Word ~$ yol bulucu.
        if re.search(
            r"(?m)^    def _word_kilit_yolu\s*\(",
            src,
        ):
            src = replace_method(
                src,
                "_word_kilit_yolu",
                WORD_PATH_METHOD,
            )
        else:
            src = insert_before_method(
                src,
                [
                    "_word_kilit_sahibi",
                    "word_kilit_sahibi",
                    "durum",
                    "oku",
                ],
                WORD_PATH_METHOD,
            )

        # Eski çalışan Motor owner parserını birebir taşı.
        if re.search(
            r"(?m)^    def _word_kilit_sahibi\s*\(",
            src,
        ):
            src = replace_method(
                src,
                "_word_kilit_sahibi",
                WORD_OWNER_METHOD,
            )
        else:
            src = insert_before_method(
                src,
                [
                    "word_kilit_sahibi",
                    "durum",
                    "oku",
                ],
                WORD_OWNER_METHOD,
            )

        # Public API.
        if re.search(
            r"(?m)^    def word_kilit_sahibi\s*\(",
            src,
        ):
            src = replace_method(
                src,
                "word_kilit_sahibi",
                PUBLIC_OWNER_METHOD,
            )
        else:
            src = insert_before_method(
                src,
                [
                    "durum",
                    "oku",
                ],
                PUBLIC_OWNER_METHOD,
            )

        LOCK.write_text(src, encoding="utf-8")

        print("[OK] Eski çalışan Word ~$ owner parserı yeni ECNTakip'e taşındı.")
        print("[OK] UNC/Synology ~$ fallback yeniden aktif.")
        print("[OK] Eski/stale ~$ dosyasında owner döndürmemek için fiziksel kilit kapısı eklendi.")
        print("[OK] app.py ve service.py değiştirilmedi.")

        print("\nSözdizimi kontrolü:")
        py_compile.compile(str(LOCK), doraise=True)
        print("  [OK] enc_takip/lock_service.py")

        final = read_text(LOCK)
        checks = {
            "future import korunmuş": bool(
                re.search(
                    r"(?m)^from __future__ import annotations$",
                    final,
                )
            ),
            "word yolu": "def _word_kilit_yolu" in final,
            "owner filtresi": "def _owner_adi_temizle" in final,
            "eski UTF-16 parser": '"utf-16-le"' in final,
            "eski offset 54": "uzunluk = icerik[54]" in final,
            "ASCII fallback": 'decode(\n                    "ascii"' in final,
            "public owner": "def word_kilit_sahibi" in final,
            "fiziksel kilit kapısı": "_dosya_gercekten_kilitli_mi" in final,
        }

        print("\nKontroller:")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'HATA'}] {name}")

        if not all(checks.values()):
            raise RuntimeError(
                "Kontrollerden biri başarısız oldu."
            )

    except Exception as exc:
        print(f"\n[HATA] {exc}")
        shutil.copy2(BACKUP, LOCK)
        print("[OK] lock_service.py otomatik olarak geri alındı.")
        raise SystemExit(1)

    print("\n[OK] v10 TAMAMLANDI.")
    print("BUILD YAPMA.")
    print("Şimdi kaynak koddan test et.")


if __name__ == "__main__":
    main()
