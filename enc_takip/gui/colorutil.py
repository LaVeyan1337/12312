"""
CTk widget'ları (açık,koyu) renk tuple'larını otomatik çözer. Ama mesaj/
log alanları gibi düz `tkinter.Text` widget'ları (renkli etiketler —
tag_configure — gerektirdiği için CTkTextbox yerine bilinçli olarak düz
Text kullanıyoruz) bu otomasyondan yararlanamaz. Bu modül, o widget'lar
için "şu an hangi mod aktifse ona göre rengi seç" işini yapar.

Not: tema değişikliği (Ayarlar > Görünüm) çalışma anında düz Text
widget'larının rengini otomatik güncellemez — kullanıcıdan programı
yeniden başlatması istenir. Bu bilinçli bir sadeleştirme: karma
(ctk + düz tk) bir arayüzde canlı yeniden temalama gerçek karmaşıklık
katar, oysa "tema değiştirince bir kez yeniden başlat" kabul edilebilir
ve çok daha az risklidir.
"""
from __future__ import annotations

from typing import Tuple, Union

import customtkinter as ctk

Renk = Union[str, Tuple[str, str]]


def r(renk: Renk) -> str:
    if isinstance(renk, tuple):
        return renk[1] if ctk.get_appearance_mode() == "Dark" else renk[0]
    return renk
