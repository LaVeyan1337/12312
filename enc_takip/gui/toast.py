"""
Sağ-alt köşe toast bildirimleri.

Bilinçli olarak düz tkinter.Toplevel kullanıyoruz (CTkToplevel değil):
kenarlıksız (overrideredirect), ekran köşesine hassas pikselle
konumlanan, otomatik solan pencereler için düz tkinter daha öngörülebilir
davranıyor. Renkler colorutil.r() ile mevcut açık/koyu moda göre seçiliyor.
"""
from __future__ import annotations

import tkinter as tk

from . import theme as T
from .colorutil import r

_RENK_HARITASI = {
    "bilgi": (T.BILGI, "ℹ"),
    "basari": (T.OK, "✓"),
    "uyari": (T.UYARI, "⚠"),
    "hata": (T.HATA, "✕"),
    "mesaj": (T.MSG_DIGER, "M"),
    "sistem_mesaj": (T.SISTEM_MESAJ, "!"),
    "dosya_ac": (T.BILGI, "D"),
    "dosya_kapa": (T.OK, "✓"),
    "kaydet": (T.ANA, "K"),
}

_aktif_bildirimler: list["Toast"] = []


class Toast:
    def __init__(self, root: tk.Misc, baslik: str, mesaj: str, tip: str = "bilgi", sure_ms: int = 5000):
        try:
                        # Bildirimler ekranda gereğinden uzun kalmasın.
            sure_ms = max(3000, min(sure_ms, 5000))
            self.pen = tk.Toplevel(root)
            self.pen.overrideredirect(True)
            self.pen.attributes("-topmost", True)
            self.pen.attributes("-alpha", 0.0)
            self._kapatiliyor = False

            renk_ham, ikon = _RENK_HARITASI.get(tip, _RENK_HARITASI["bilgi"])
            renk = r(renk_ham)
            kart_bg = r(T.KART)
            metin_renk = r(T.METIN)
            metin2_renk = r(T.METIN2)
            metin3_renk = r(T.METIN3)

            genislik, yukseklik = 390, 96
            self.pen.update_idletasks()
            ekran_g = self.pen.winfo_screenwidth()
            ekran_y = self.pen.winfo_screenheight()
            offset = 42 + len(_aktif_bildirimler) * (yukseklik + 12)
            x = ekran_g - genislik - 20
            y = ekran_y - yukseklik - offset
            self.pen.geometry(f"{genislik}x{yukseklik}+{x}+{y}")
            self.pen.configure(bg=renk)

            tk.Frame(self.pen, bg=renk, width=4).pack(side=tk.LEFT, fill=tk.Y)
            ic = tk.Frame(self.pen, bg=kart_bg)
            ic.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            tk.Label(ic, text=ikon, font=(T.FONT, 20, "bold"), bg=kart_bg, fg=renk).pack(side=tk.LEFT, padx=(12, 8))
            metin_f = tk.Frame(ic, bg=kart_bg)
            metin_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)

            tk.Label(
                metin_f,
                text=baslik,
                font=(T.FONT, 10, "bold"),
                bg=kart_bg,
                fg=metin_renk,
                anchor="w",
            ).pack(fill=tk.X, pady=(1, 2))

            tk.Label(
                metin_f, text=mesaj, font=(T.FONT, 10), bg=kart_bg, fg=metin2_renk,
                anchor="w", wraplength=270, justify="left",
            ).pack(fill=tk.X)

            kapat = tk.Label(ic, text="✕", font=(T.FONT, 10, "bold"), bg=kart_bg, fg=metin3_renk, cursor="hand2")
            kapat.pack(side=tk.RIGHT, padx=10, anchor="n", pady=4)
            kapat.bind("<Button-1>", lambda e: self._kapat())

            _aktif_bildirimler.append(self)
            self._fade_in()
            self._timer = self.pen.after(sure_ms, self._fade_out)
        except tk.TclError:
            pass

    def _fade_in(self) -> None:
        try:
            a = self.pen.attributes("-alpha")
            if a < 0.97:
                self.pen.attributes("-alpha", a + 0.15)
                self.pen.after(12, self._fade_in)
        except tk.TclError:
            pass

    def _fade_out(self) -> None:
        try:
            a = self.pen.attributes("-alpha")
            if a > 0.1:
                self.pen.attributes("-alpha", a - 0.08)
                self.pen.after(35, self._fade_out)
            else:
                self._kapat()
        except tk.TclError:
            self._kapat()

    def _kapat(self) -> None:
        if self._kapatiliyor:
            return
        self._kapatiliyor = True
        try:
            if self in _aktif_bildirimler:
                _aktif_bildirimler.remove(self)
            self.pen.destroy()
        except tk.TclError:
            pass
