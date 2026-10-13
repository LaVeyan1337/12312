"""
Windows'ta oturum açılışında otomatik başlatma.

HKEY_CURRENT_USER altına yazar -> yönetici hakkı gerektirmez, sadece
o Windows kullanıcısını etkiler (şirket için doğru kapsam: herkes
kendi tercihini yönetir). Windows dışı platformlarda ya da winreg
erişilemezse sessizce no-op olur, uygulamayı çökertmez.
"""
from __future__ import annotations

import sys

_ANAHTAR_YOLU = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _winreg():
    if sys.platform != "win32":
        return None
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def etkin_mi(uygulama_adi: str) -> bool:
    wr = _winreg()
    if wr is None:
        return False
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _ANAHTAR_YOLU, 0, wr.KEY_READ) as anahtar:
            wr.QueryValueEx(anahtar, uygulama_adi)
            return True
    except OSError:
        return False


def ayarla(ac: bool, uygulama_adi: str, komut: str) -> bool:
    """komut: örn. 'pythonw.exe "C:\\...\\main.py"' ya da paketlenmiş .exe yolu."""
    wr = _winreg()
    if wr is None:
        return False
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _ANAHTAR_YOLU, 0, wr.KEY_SET_VALUE) as anahtar:
            if ac:
                wr.SetValueEx(anahtar, uygulama_adi, 0, wr.REG_SZ, komut)
            else:
                try:
                    wr.DeleteValue(anahtar, uygulama_adi)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
