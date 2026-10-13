"""ENCTakip GUI paketi."""

__all__ = ["Uygulama"]


def __getattr__(name: str):
    if name == "Uygulama":
        from .app import Uygulama
        return Uygulama
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
