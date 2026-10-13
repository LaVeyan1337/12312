"""
Giriş noktası.

Çalıştırma:  python main.py
İlk kurulumda `settings.json` yoksa, main.py'nin yanına örnek bir tane
üretir ve düzenlemeniz için uyarır.
"""
from __future__ import annotations

import sys
import traceback

from enc_takip.config import DEFAULT_SETTINGS_PATH, ornek_settings_yaz, yukle
from enc_takip.single_instance import TekOrnekKilidi


def main() -> None:
    arka_plan = "--background" in sys.argv
    kilit = TekOrnekKilidi("enc_takip")
    if not kilit.al():
        try:
            import tkinter as tk
            from tkinter import messagebox
            kok = tk.Tk()
            kok.withdraw()
            messagebox.showwarning("Zaten Çalışıyor", "Program bu bilgisayarda zaten açık.\nGörev çubuğunu / sistem tepsisini kontrol edin.")
            kok.destroy()
        except Exception:
            print("[BİLGİ] Program bu bilgisayarda zaten çalışıyor.")
        sys.exit(0)

    try:
        if not DEFAULT_SETTINGS_PATH.exists():
            ornek_settings_yaz(DEFAULT_SETTINGS_PATH)
            print("[BİLGİ] settings.json bulunamadığı için örnek bir tane oluşturuldu:")
            print(f"        {DEFAULT_SETTINGS_PATH}")
            print("        Ağ yolu / dosya adı / yönetici listesi doğru mu kontrol edin.")

        settings = yukle()

        from enc_takip.gui import Uygulama
        try:
            Uygulama(settings, arka_plan=arka_plan).calistir()
        except Exception:
            traceback.print_exc()
            input("\n[FATAL] Beklenmeyen hata. Çıkmak için Enter...")
            sys.exit(1)
    finally:
        kilit.birak()


if __name__ == "__main__":
    main()
