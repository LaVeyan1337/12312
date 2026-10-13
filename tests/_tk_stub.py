"""
Bu sandboxta gerçek bir ekran (display) olmadığı için tkinter kurulamıyor.
Bu dosya, tkinter'ın API yüzeyinin küçük ama yeterli bir taklidini
(stub) sağlayarak GUI kodunun İÇİNDEKİ mantığı (widget oluşturma
sırası, yanlış değişken adı, yanlış argüman vb.) gerçek bir ekran
olmadan test etmeyi sağlar. Bu, py_compile'ın yakalayamayacağı
çalışma zamanı hatalarını yakalar.
"""
from __future__ import annotations

import types


class TclError(Exception):
    pass


class Variable:
    def __init__(self, value=None, master=None, name=None):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v

    def trace_add(self, *a, **kw):
        return None

    def trace(self, *a, **kw):
        return None


class StringVar(Variable):
    def __init__(self, value="", **kw):
        super().__init__(value, **kw)


class BooleanVar(Variable):
    def __init__(self, value=False, **kw):
        super().__init__(value, **kw)


class Widget:
    def __init__(self, master=None, **kw):
        self.master = master
        self._kw = dict(kw)
        self._children = []
        self._text_content = kw.get("text", "") if isinstance(kw.get("text", ""), str) else ""
        if master is not None and hasattr(master, "_children"):
            master._children.append(self)

    # --- geometri yönetimi (no-op) ---
    def pack(self, **kw):
        return self

    def pack_propagate(self, *a, **kw):
        return self

    def pack_forget(self):
        return self

    def grid(self, **kw):
        return self

    def grid_forget(self):
        return self

    def grid_columnconfigure(self, *a, **kw):
        return self

    def grid_rowconfigure(self, *a, **kw):
        return self

    # --- genel ---
    def configure(self, **kw):
        self._kw.update(kw)
        return self

    config = configure

    def cget(self, key):
        return self._kw.get(key)

    def bind(self, *a, **kw):
        return self

    def destroy(self):
        return None

    def winfo_children(self):
        return list(self._children)

    def winfo_exists(self):
        return True

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def update_idletasks(self):
        return None

    def after(self, ms, fn=None, *args):
        # Kasıtlı olarak çağırmıyoruz: gerçek uygulamadaki tekrarlı
        # (recursive) after() zincirlerinin (polling döngüleri) bu
        # testte sonsuz döngüye girmesini engellemek için.
        return "after#0"

    def after_cancel(self, *a):
        return None

    def attributes(self, *a, **kw):
        if len(a) == 1:
            return 1.0
        return None

    def focus_set(self):
        return None

    def focus_force(self):
        return None

    def lift(self):
        return None

    def deiconify(self):
        return None

    def withdraw(self):
        return None

    def see(self, *a):
        return None

    def yview(self, *a):
        return (0.0, 1.0)

    def resizable(self, *a, **kw):
        return None

    def overrideredirect(self, *a):
        return None

    def title(self, *a, **kw):
        return None

    def geometry(self, *a, **kw):
        return None

    def minsize(self, *a, **kw):
        return None

    def protocol(self, *a, **kw):
        return None

    def mainloop(self, *a, **kw):
        return None

    def iconify(self):
        return None


class Frame(Widget):
    pass


class Label(Widget):
    pass


class Button(Widget):
    pass


class Checkbutton(Widget):
    pass


class Entry(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._val = ""

    def insert(self, index, s):
        self._val = self._val[:index] + s + self._val[index:] if isinstance(index, int) else self._val + s

    def get(self):
        return self._val

    def delete(self, *a):
        self._val = ""


class Text(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._buf = ""
        self._tags = {}

    def insert(self, index, s, *tags):
        self._buf += s

    def delete(self, *a):
        self._buf = ""

    def get(self, *a):
        return self._buf

    def tag_configure(self, name, **kw):
        self._tags[name] = kw

    def see(self, *a):
        return None


class Scrollbar(Widget):
    def set(self, *a):
        return None


class Toplevel(Widget):
    pass


class Tk(Widget):
    pass


def _make_font_module():
    mod = types.ModuleType("tkinter.font")

    class _Font:
        def configure(self, **kw):
            return None

    def nametofont(name):
        return _Font()

    mod.nametofont = nametofont
    return mod


def _make_messagebox_module():
    mod = types.ModuleType("tkinter.messagebox")
    mod.askyesno = lambda *a, **kw: False
    mod.showinfo = lambda *a, **kw: None
    mod.showerror = lambda *a, **kw: None
    mod.showwarning = lambda *a, **kw: None
    return mod


def _make_filedialog_module():
    mod = types.ModuleType("tkinter.filedialog")
    mod.asksaveasfilename = lambda *a, **kw: ""
    mod.askopenfilename = lambda *a, **kw: ""
    return mod


def kur() -> types.ModuleType:
    """Sahte tkinter modülünü inşa edip döndürür (sys.modules'a kaydetmeden)."""
    mod = types.ModuleType("tkinter")
    for ad, deger in [
        ("Tk", Tk), ("Toplevel", Toplevel), ("Frame", Frame), ("Label", Label),
        ("Button", Button), ("Checkbutton", Checkbutton), ("Entry", Entry),
        ("Text", Text), ("Scrollbar", Scrollbar), ("StringVar", StringVar),
        ("BooleanVar", BooleanVar), ("TclError", TclError),
        ("LEFT", "left"), ("RIGHT", "right"), ("TOP", "top"), ("BOTTOM", "bottom"),
        ("X", "x"), ("Y", "y"), ("BOTH", "both"), ("W", "w"), ("E", "e"),
        ("N", "n"), ("S", "s"), ("NW", "nw"), ("NE", "ne"), ("SW", "sw"), ("SE", "se"),
        ("CENTER", "center"), ("FLAT", "flat"), ("SOLID", "solid"), ("RAISED", "raised"),
        ("DISABLED", "disabled"), ("NORMAL", "normal"), ("WORD", "word"), ("END", "end"),
        ("NSEW", "nsew"),
    ]:
        setattr(mod, ad, deger)

    mod.font = _make_font_module()
    mod.messagebox = _make_messagebox_module()
    mod.filedialog = _make_filedialog_module()
    return mod
