"""
customtkinter'ın (bu projede kullandığımız kısmının) minimal taklidi.
_tk_stub.py'deki temel Widget davranışını kullanır, CTk'ye özgü
widget'ları (CTkButton, CTkSwitch, CTkScrollableFrame, CTkToplevel,
CTkSegmentedButton) ve set_appearance_mode/get_appearance_mode
fonksiyonlarını üzerine ekler.
"""
from __future__ import annotations

import types

import _tk_stub

_mod_durum = {"appearance": "Light"}


def set_appearance_mode(mode: str) -> None:
    m = mode if mode in ("Light", "Dark") else "Light"
    _mod_durum["appearance"] = m


def get_appearance_mode() -> str:
    return _mod_durum["appearance"]


class CTk(_tk_stub.Tk):
    def clipboard_clear(self):
        return None

    def clipboard_append(self, *a):
        return None


class CTkToplevel(_tk_stub.Toplevel):
    pass


class CTkFrame(_tk_stub.Frame):
    pass


class CTkScrollableFrame(_tk_stub.Frame):
    pass


class CTkLabel(_tk_stub.Label):
    pass


class CTkButton(_tk_stub.Button):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._command = kw.get("command")

    def invoke(self):
        if self._command:
            self._command()


class CTkEntry(_tk_stub.Entry):
    pass


class CTkSwitch(_tk_stub.Widget):
    def __init__(self, master=None, variable=None, **kw):
        super().__init__(master, **kw)
        self._variable = variable

    def get(self):
        return self._variable.get() if self._variable else False

    def select(self):
        if self._variable:
            self._variable.set(True)

    def deselect(self):
        if self._variable:
            self._variable.set(False)


class CTkSegmentedButton(_tk_stub.Widget):
    def __init__(self, master=None, values=None, variable=None, **kw):
        super().__init__(master, **kw)
        self._values = values or []
        self._variable = variable

    def get(self):
        return self._variable.get() if self._variable else (self._values[0] if self._values else None)


def kur() -> types.ModuleType:
    mod = types.ModuleType("customtkinter")
    mod.set_appearance_mode = set_appearance_mode
    mod.get_appearance_mode = get_appearance_mode
    mod.CTk = CTk
    mod.CTkToplevel = CTkToplevel
    mod.CTkFrame = CTkFrame
    mod.CTkScrollableFrame = CTkScrollableFrame
    mod.CTkLabel = CTkLabel
    mod.CTkButton = CTkButton
    mod.CTkEntry = CTkEntry
    mod.CTkSwitch = CTkSwitch
    mod.CTkSegmentedButton = CTkSegmentedButton
    return mod
