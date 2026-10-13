# -*- coding: utf-8 -*-
"""ENCTakip v11 - Word 'File In Use' dialog owner probe.

Amacı:
- Synology/SMB sunucusuna yönetici erişimi gerektirmeden Word'ün kendi
  'dosya XXX kişisinde açık / locked for editing by XXX' bilgisini okumak.
- ~$ dosyasından yanlış/stale kullanıcı adı gösterme riskini ortadan kaldırmak.
- Her durum taramasında Word açmak yerine, sahibi bilinmiyorsa 5 sn cooldown ile
  tek arka plan probe başlatmak ve sonucu cache'lemek.

Sadece enc_takip/lock_service.py değiştirilir. app.py/service.py dokunulmaz.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import py_compile
import re
import shutil

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
BACKUP = LOCK.with_name(
    f"lock_service.py.bak_word_dialog_owner_v11_{STAMP}"
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def replace_method(src: str, name: str, body: str) -> str:
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
        "Güvenli insert noktası bulunamadı: " + ", ".join(names)
    )


IMPORTS = r'''\n# v11 Word UI owner probe için ek importlar.
import ctypes
import os
import threading
import time
import subprocess
'''


HELPER_METHODS = r'''    @staticmethod
    def _word_dialog_window_text(hwnd) -> str:
        """Bir pencerenin ve alt kontrollerinin metinlerini toplar."""
        if not hwnd:
            return ""

        user32 = ctypes.windll.user32
        parts: list[str] = []

        try:
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                txt = buf.value.strip()
                if txt:
                    parts.append(txt)
        except Exception:
            pass

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def child_proc(child, _lparam):
            try:
                n = user32.GetWindowTextLengthW(child)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(child, buf, n + 1)
                txt = buf.value.strip()
                if txt:
                    parts.append(txt)
            except Exception:
                pass
            return True

        try:
            user32.EnumChildWindows(hwnd, WNDENUMPROC(child_proc), 0)
        except Exception:
            pass

        # Office bazen metni iç içe alt pencere/grup içinde üretir.
        return "\n".join(dict.fromkeys(parts))

    @staticmethod
    def _word_dialog_owner_from_text(text: str) -> str | None:
        """Türkçe/İngilizce Word File In Use metninden kullanıcı adını çıkarır."""
        if not text:
            return None

        temiz = re.sub(r"[ \t]+", " ", text.replace("\r", "\n")).strip()

        # En güvenilir kalıp: tırnak içindeki kullanıcı adı.
        for m in re.finditer(
            r"[\"'“”‘’]([^\"'“”‘’\r\n]{2,80})[\"'“”‘’]",
            temiz,
        ):
            aday = self._owner_adi_temizle(m.group(1))
            if aday and not any(
                bad in aday.casefold()
                for bad in (
                    "locked",
                    "editing",
                    "file",
                    "read only",
                    "salt okunur",
                    "microsoft word",
                )
            ):
                return aday

        kaliplar = (
            r"locked\s+for\s+editing\s+by\s+([^\r\n]+)",
            r"edited\s+by\s+([^\r\n]+)",
            r"kilit(?:li|lendi)\s*(?:.*?\s+)?(?:tarafından|by)\s+([^\r\n]+)",
            r"([A-Za-zÇĞİÖŞÜçğıöşü0-9_.\- ]{2,80})\s+kişisinde\s+açık",
            r"([A-Za-zÇĞİÖŞÜçğıöşü0-9_.\- ]{2,80})\s+kişisinde\s+ac[iı]k",
            r"tarafından\s+([^\r\n]{2,80})\s+(?:açık|ac[iı]k)",
        )

        for pattern in kaliplar:
            m = re.search(pattern, temiz, flags=re.IGNORECASE)
            if not m:
                continue
            aday = self._owner_adi_temizle(m.group(1).strip(" .:;,-"))
            if aday:
                return aday

        return None

    @staticmethod
    def _word_exe_yolu() -> str | None:
        """WINWORD.EXE yolunu PATH ve yaygın Office kurulumlarından bulur."""
        if os.name != "nt":
            return None

        try:
            cp = subprocess.run(
                ["where", "winword.exe"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in (cp.stdout or "").splitlines():
                p = line.strip().strip('"')
                if p and Path(p).exists():
                    return p
        except Exception:
            pass

        candidates = []
        for base_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
            base = os.environ.get(base_name)
            if not base:
                continue
            candidates.extend(
                [
                    Path(base) / "Microsoft Office" / "root" / "Office16" / "WINWORD.EXE",
                    Path(base) / "Microsoft Office" / "Office16" / "WINWORD.EXE",
                    Path(base) / "Microsoft Office" / "root" / "Office15" / "WINWORD.EXE",
                ]
            )

        for p in candidates:
            try:
                if p.exists():
                    return str(p)
            except Exception:
                pass

        return None

    def _word_ui_owner_probe(self) -> str | None:
        """
        Word'ün gerçek 'File In Use / locked for editing by XXX' diyaloğunu
        bir yardımcı Word instance'ında tetikler, metni okur, diyaloğu hemen
        kapatır ve process'i temizler.

        Bu yöntem SMB/NAS yönetici izni istemez.
        """
        if os.name != "nt":
            return None

        now = time.monotonic()
        if now - getattr(self, "_word_ui_owner_son_deneme", 0.0) < 5.0:
            return getattr(self, "_word_ui_owner_cache", None)

        lock = getattr(self, "_word_ui_owner_thread_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._word_ui_owner_thread_lock = lock

        if not lock.acquire(blocking=False):
            return getattr(self, "_word_ui_owner_cache", None)

        self._word_ui_owner_son_deneme = now

        def worker():
            proc = None
            try:
                exe = self._word_exe_yolu()
                if not exe:
                    return

                hedef = str(self.s.dosya_yolu)
                if not Path(hedef).exists():
                    return

                proc = subprocess.Popen(
                    [exe, "/n", "/q", hedef],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

                user32 = ctypes.windll.user32
                EnumWindowsProc = ctypes.WINFUNCTYPE(
                    ctypes.c_bool,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )
                found_owner = None
                found_hwnd = None

                for _ in range(45):
                    handles: list[int] = []

                    def enum_proc(hwnd, _lparam):
                        try:
                            pid = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            if int(pid.value) != proc.pid:
                                return True

                            if not user32.IsWindowVisible(hwnd):
                                return True

                            handles.append(int(hwnd))
                        except Exception:
                            pass
                        return True

                    try:
                        user32.EnumWindows(EnumWindowsProc(enum_proc), 0)
                    except Exception:
                        pass

                    for hwnd in handles:
                        txt = self._word_dialog_window_text(hwnd)
                        if not txt:
                            continue

                        low = txt.casefold()
                        looks_like_lock_dialog = (
                            "file in use" in low
                            or "locked for editing" in low
                            or "kilit" in low and ("düzen" in low or "açık" in low or "acik" in low)
                            or "kişisinde açık" in low
                            or "kisinde acik" in low
                        )

                        if not looks_like_lock_dialog:
                            continue

                        owner = self._word_dialog_owner_from_text(txt)
                        if owner:
                            found_owner = owner
                            found_hwnd = hwnd
                            break

                    if found_owner:
                        try:
                            user32.PostMessageW(found_hwnd, 0x0010, 0, 0)  # WM_CLOSE
                        except Exception:
                            pass
                        break

                    if proc.poll() is not None:
                        break

                    time.sleep(0.10)

                if found_owner:
                    self._word_ui_owner_cache = found_owner
                    self._word_ui_owner_cache_time = time.monotonic()
                    return

            except Exception as exc:
                try:
                    print(f"[WORD UI OWNER] {exc}")
                except Exception:
                    pass
            finally:
                # Bizim probe instance'ımızın geride kalmasını istemiyoruz.
                if proc is not None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                lock.release()

        threading.Thread(
            target=worker,
            daemon=True,
            name="ENCTakip-WordOwnerProbe",
        ).start()

        return getattr(self, "_word_ui_owner_cache", None)
'''


PUBLIC_OWNER = r'''    def word_kilit_sahibi(self) -> Optional[str]:
        """Gerçek kullanıcı adını Word/SMB üzerinden güvenli biçimde çöz."""
        try:
            # 1) Ağ tarafında gerçek owner varsa en temiz kaynak odur.
            for name in ("_network_owner_from_server", "_openfiles_owner_from_server"):
                fn = getattr(self, name, None)
                if callable(fn):
                    try:
                        owner = fn()
                        if owner:
                            return owner
                    except Exception:
                        pass

            # 2) Synology gibi NAS'larda Windows server API'leri erişilemez.
            #    Bu durumda Word'ün kendi File In Use diyaloğunu oku.
            owner = self._word_ui_owner_probe()
            if owner:
                return owner

            # 3) Yerel dosyada eski ~$ parserını koru; UNC/NAS'ta stale isim
            #    göstermemek için doğrudan ~$ fallback'e dönme.
            try:
                full = str(self.s.dosya_yolu)
                is_unc = bool(re.match(r"^\\\\", full))
            except Exception:
                is_unc = False

            if not is_unc:
                owner_fn = getattr(self, "_word_kilit_sahibi", None)
                if callable(owner_fn):
                    try:
                        owner = owner_fn()
                        if owner:
                            return owner
                    except Exception:
                        pass

        except Exception:
            pass

        return None
'''


def ensure_imports(src: str) -> str:
    needed = [
        "import ctypes",
        "import os",
        "import threading",
        "import time",
        "import subprocess",
    ]
    for line in needed:
        if re.search(
            rf"(?m)^[ \t]*{re.escape(line)}[ \t]*$",
            src,
        ):
            continue
        future = list(re.finditer(r"(?m)^from __future__ import .*?$", src))
        if future:
            pos = future[-1].end()
            src = src[:pos] + "\n" + line + src[pos:]
        else:
            src = line + "\n" + src
    return src


def main() -> None:
    print("ENCTakip — WORD DİYALOG OWNER v11")
    print("====================================")

    if not LOCK.exists():
        raise SystemExit(f"[HATA] lock_service.py bulunamadı: {LOCK}")

    shutil.copy2(LOCK, BACKUP)
    print(f"[OK] Yedek: {BACKUP.name}")

    try:
        src = read_text(LOCK)
        src = ensure_imports(src)

        # Owner helper'lar yoksa ekle; varsa mevcut UI probe'u güvenli biçimde değiştir.
        for method_name, body in (
            ("_word_dialog_window_text", None),
            ("_word_dialog_owner_from_text", None),
            ("_word_exe_yolu", None),
            ("_word_ui_owner_probe", None),
        ):
            if re.search(rf"(?m)^    def {re.escape(method_name)}\s*\(", src):
                # Sonraki adımda toplu blok değişimi yapılacak.
                pass

        # Eski yardımcılar varsa dört metodu tek blokta güvenli şekilde yenile.
        if re.search(r"(?m)^    def _word_dialog_window_text\s*\(", src):
            src = replace_method(src, "_word_dialog_window_text", HELPER_METHODS.split("\n    @staticmethod\n    def _word_dialog_owner_from_text", 1)[0].rstrip())
            # Yukarıdaki kesme nedeniyle kalan üç methodu aşağıdan eklemek daha güvenli.
            # Önce varsa onları tek tek çıkar.
            for n in (
                "_word_dialog_owner_from_text",
                "_word_exe_yolu",
                "_word_ui_owner_probe",
            ):
                if re.search(rf"(?m)^    def {re.escape(n)}\s*\(", src):
                    # geçici olarak sil; komple blok aşağıda yeniden yerleştirilecek
                    src = replace_method(src, n, f"    def {n}(self):\n        return None")

        # Eski blok parçalı ise, metodları birebir aşağıdaki tam sürümlerle kur.
        # 4 bağımsız method tanımı çıkarılıp aynı anchor öncesine yerleştirilir.
        blocks = []
        names = [
            "_word_dialog_window_text",
            "_word_dialog_owner_from_text",
            "_word_exe_yolu",
            "_word_ui_owner_probe",
        ]
        temp = HELPER_METHODS
        # HELPER_METHODS içindeki başlangıç class-indentation bloklarını tek tek regex ile ayır.
        starts = []
        for n in names:
            m = re.search(rf"(?m)^    (?:@staticmethod\n    )?def {re.escape(n)}\s*\(", temp)
            if not m:
                raise RuntimeError(f"Patch içinde helper eksik: {n}")
            starts.append((n, m.start()))
        starts_sorted = sorted(starts, key=lambda x: x[1])
        for i, (n, st) in enumerate(starts_sorted):
            en = starts_sorted[i + 1][1] if i + 1 < len(starts_sorted) else len(temp)
            blocks.append((n, temp[st:en].rstrip()))

        for n, block in blocks:
            if re.search(rf"(?m)^    (?:@staticmethod\n    )?def {re.escape(n)}\s*\(", src):
                src = replace_method(src, n, block)
            else:
                src = insert_before_method(src, ["word_kilit_sahibi", "durum", "oku"], block)

        src = replace_method(src, "word_kilit_sahibi", PUBLIC_OWNER)

        LOCK.write_text(src, encoding="utf-8")

        print("[OK] Word File In Use diyaloğu owner probe'u eklendi.")
        print("[OK] NAS/Synology için SMB API başarısızsa Word arayüzü kaynak oluyor.")
        print("[OK] ~$ owner yalnızca yerel dosyada fallback olarak korunuyor.")
        print("[OK] Probe cooldown: 5 saniye; arka planda çalışıyor.")
        print("[OK] app.py ve service.py değiştirilmedi.")

        print("\nSözdizimi kontrolü:")
        py_compile.compile(str(LOCK), doraise=True)
        print("  [OK] enc_takip/lock_service.py")

        final = read_text(LOCK)
        checks = {
            "dialog text": "def _word_dialog_window_text" in final,
            "dialog parser": "def _word_dialog_owner_from_text" in final,
            "Word exe": "def _word_exe_yolu" in final,
            "Word probe": "def _word_ui_owner_probe" in final,
            "cooldown": "< 5.0" in final,
            "public owner": "def word_kilit_sahibi" in final,
            "no UNC ~$ direct fallback": "if not is_unc" in final,
        }
        print("\nKontroller:")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'HATA'}] {name}")
        if not all(checks.values()):
            raise RuntimeError("Kontroller başarısız.")

    except Exception as exc:
        print(f"\n[HATA] {exc}")
        shutil.copy2(BACKUP, LOCK)
        print("[OK] lock_service.py otomatik olarak geri alındı.")
        raise SystemExit(1)

    print("\n[OK] v11 TAMAMLANDI.")
    print("BUILD YAPMA. Önce kaynak koddan test et.")


if __name__ == "__main__":
    main()
