"""
Sistem tepsisi (system tray) entegrasyonu.

`pystray` + `Pillow` kuruluysa pencereyi kapatma tuşu tamamen çıkmak
yerine tepsiye küçültür (izleme arka planda çalışmaya devam eder,
görev çubuğunu kirletmez). Bu paketler kurulu değilse özellik sessizce
devre dışı kalır — uygulamanın çekirdek işlevselliği hiçbir zaman bu
opsiyonel bağımlılığa muhtaç değildir.

Kurulum (isteğe bağlı): pip install pystray pillow
"""
from __future__ import annotations

import threading
from typing import Any, Callable

try:
    import pystray
    from PIL import Image, ImageDraw
    KULLANILABILIR = True
except ImportError:
    KULLANILABILIR = False


def _simge_uret():
    img = Image.new("RGB", (64, 64), "#0a3d3a")
    d = ImageDraw.Draw(img)
    d.ellipse((14, 14, 50, 50), fill="#0d7c66")
    return img


class TepsiSimgesi:
    def __init__(self, baslik: str, goster_cb: Callable[[], None], cik_cb: Callable[[], None]):
        self._goster_cb = goster_cb
        self._cik_cb = cik_cb
        self._icon: Any = None
        self._baslik = baslik

    def baslat(self) -> None:
        if not KULLANILABILIR:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Göster", lambda: self._goster_cb(), default=True),
            pystray.MenuItem("Çıkış", lambda: self._cik_cb()),
        )
        self._icon = pystray.Icon("enc_takip", _simge_uret(), self._baslik, menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def durdur(self) -> None:
        if self._icon is not None:
            self._icon.stop()
