from __future__ import annotations

import os
import socket
import subprocess
import threading
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

_PROC = None
_PORT = None
_URL = None
_LOG_HANDLE = None


def _free_port(start=38171, end=38250):
    for port in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
    raise OSError("Yerel chat portu bulunamadı.")


def _edge_path():
    for raw in (
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ):
        p = Path(raw)
        if p.exists():
            return p
    return None


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _runtime_paths():
    root = _project_root()
    try:
        from .config import KULLANICI, Settings
        user = str(KULLANICI)
        system = str(Settings().sistem_klasoru)
    except Exception:
        user = os.environ.get("USERNAME", "kullanici")
        system = os.environ.get("ENCTAKIP_SISTEM_KLASORU", "")
    if not system:
        system = str(root / ".encev_sistem")
    return root, user, system


def _start_embedded(root: Path, user: str, system: str, port: int):
    from . import webchat_server as ws
    store = ws.Store(root, user, system)
    store.heartbeat(status=store.current_profile().get("durum", "online"))
    port = port or ws.pick_port()
    ws.Handler.store = store
    static_dir = root / "enc_takip" / "webchat"
    ws.Handler.static_dir = static_dir if static_dir.exists() else None
    server = ws.ThreadingHTTPServer(("127.0.0.1", port), ws.Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.35}, daemon=True, name="ENCTakip-WebChat")
    thread.start()
    return server, port


def _wait_ready(port: int, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as r:
                if r.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.12)
    raise RuntimeError(f"WebChat sunucusu başlatılamadı. Ayrıntı: {last_error or 'bilinmeyen hata'}")


def _open_log(root: Path, system: str):
    global _LOG_HANDLE
    folder = Path(system) if system else root / ".encev_sistem"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "chat_server.log"
    try:
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.rename(folder / "chat_server.log.1")
    except Exception:
        pass
    _LOG_HANDLE = open(path, "a", encoding="utf-8", buffering=1)
    _LOG_HANDLE.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] WebChat başlatılıyor\n")
    return path


def open_chat():
    global _PROC, _PORT, _URL
    root, user, system = _runtime_paths()
    alive = _PROC is not None
    if alive:
        try:
            alive = _PROC.poll() is None
        except AttributeError:
            alive = True
    if not alive:
        port = _free_port()
        log_path = _open_log(root, system)
        if getattr(sys, "frozen", False):
            _PROC, _PORT = _start_embedded(root, user, system, port)
            _URL = f"http://127.0.0.1:{_PORT}/?user={quote(user)}"
        else:
            server = root / "enc_takip" / "webchat_server.py"
            if not server.exists():
                raise FileNotFoundError(f"WebChat sunucusu bulunamadı: {server}")
            cmd = [sys.executable, str(server), "--root", str(root), "--user", user, "--port", str(port)]
            if system:
                cmd += ["--system", system]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _PROC = subprocess.Popen(cmd, cwd=str(root), stdin=subprocess.DEVNULL, stdout=_LOG_HANDLE, stderr=_LOG_HANDLE, creationflags=flags)
            _PORT = port
            _URL = f"http://127.0.0.1:{port}/?user={quote(user)}"
        try:
            _wait_ready(_PORT)
        except Exception:
            try:
                if hasattr(_PROC, "poll") and _PROC.poll() is None:
                    _PROC.terminate()
                elif hasattr(_PROC, "shutdown"):
                    _PROC.shutdown(); _PROC.server_close()
            except Exception:
                pass
            raise RuntimeError(f"Chat sunucusu açılmadı. Log: {log_path}")
    url = _URL or f"http://127.0.0.1:{_PORT}/?user={quote(user)}"
    edge = _edge_path()
    if edge:
        subprocess.Popen([str(edge), f"--app={url}", "--new-window", "--disable-features=msEdgeSidebarV2"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        webbrowser.open(url)
    return url


def stop_chat():
    global _PROC, _LOG_HANDLE
    try:
        if _PROC is not None and hasattr(_PROC, "shutdown"):
            _PROC.shutdown(); _PROC.server_close()
        elif _PROC is not None and hasattr(_PROC, "poll") and _PROC.poll() is None:
            _PROC.terminate()
            try: _PROC.wait(timeout=2)
            except Exception: pass
    except Exception:
        pass
    _PROC = None
    if _LOG_HANDLE:
        try: _LOG_HANDLE.flush(); _LOG_HANDLE.close()
        except Exception: pass
        _LOG_HANDLE = None

