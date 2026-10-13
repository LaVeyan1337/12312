from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import socket
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlparse

CHAT_VERSION = "4.0.2"

@contextmanager
def file_lock(path: Path):
    """Cross-process lock for profile JSON writes; no-op only if locking API is unavailable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            if fh.tell() == 0:
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            locked = True
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        fh.close()


# ---------------------------------------------------------------------------
# WebChat sabitleri / yardımcıları
# ---------------------------------------------------------------------------
USER_ROLES = {"user", "admin"}
ADMIN_ROLES = {"admin"}
PROFILE_STATUSES = {"online": "Çevrimiçi", "busy": "Meşgul", "meeting": "Toplantıda", "away": "Dışarıda"}
PROFILE_MAX_TEXT = 255
STATUS_MESSAGE_MAX = 500
MAX_PROFILE_IMAGE = 8 * 1024 * 1024
MAX_UPLOAD = 25 * 1024 * 1024
PRESENCE_TTL = 45
PROFILE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
PROFILE_FIELDS = {"ad_soyad", "unvan", "telefon", "eposta", "departman", "durum", "durum_mesaji", "profil_fotografi", "profil_fotografi_sil"}
ADMIN_FIELDS = PROFILE_FIELDS | {"rol", "aktif"}
ROOMS = [
    {"id": "GENEL", "name": "Genel Sohbet", "description": "Tüm aktif ENCTakip kullanıcılarının ortak sohbeti."},
    {"id": "ODA_ECED", "name": "EÇED KOD", "description": "EÇED çalışma odası."},
    {"id": "ODA_DILEK", "name": "DİLEKÇELER", "description": "Dilekçe çalışma odası."},
]
MARKERS = {
    "__ENCTAKIP_EDIT__": "edit",
    "__ENCTAKIP_DELETE__": "delete",
    "__ENCTAKIP_READ__": "read",
    "__ENCTAKIP_DELIVERED__": "delivered",
    "__ENCTAKIP_REACTION__": "reaction",
    "__ENCTAKIP_REACTION_REMOVE__": "reaction_remove",
    "__ENCTAKIP_FILE__": "file",
}

def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")

def safe_json(text: str, default=None):
    try:
        return json.loads(text)
    except Exception:
        return default

def safe_filename(name: str) -> str:
    name = Path(str(name or "dosya")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "dosya"

def atomic_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

EMBEDDED_INDEX_HTML = '<!doctype html>\n<html lang="tr">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>ENCTakip • Kurumsal Sohbet</title>\n<style>\n:root{--bg:#eef3f4;--panel:#fff;--panel2:#f7faf9;--line:#dbe4e7;--text:#182329;--muted:#738087;--accent:#0c9b7d;--accent2:#087963;--mine:#def7ed;--bubble:#fff;--red:#d95858;--green:#1aa56f;--amber:#dda23d;--purple:#795ac4;--shadow:0 18px 55px rgba(18,39,46,.15)}\n*{box-sizing:border-box}html,body{height:100%;margin:0;font-family:Segoe UI,system-ui,-apple-system,sans-serif;color:var(--text);background:var(--bg)}button,input,textarea,select{font:inherit}button{border:0;cursor:pointer}.app{height:100vh;display:grid;grid-template-columns:340px minmax(0,1fr);overflow:hidden}.sidebar{min-height:0;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column}.brand{padding:17px 18px 12px;display:flex;align-items:center;gap:10px}.logo{width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(135deg,#18b58c,#087a65);color:#fff;font-weight:900;box-shadow:0 7px 20px rgba(12,155,125,.2)}.brand h1{font-size:16px;margin:0}.brand small{display:block;color:var(--muted);margin-top:2px}.sidebar-actions{display:flex;gap:7px;padding:0 13px 10px}.tool-btn{height:34px;padding:0 10px;border:1px solid var(--line);background:#f7faf9;color:#52636a;border-radius:10px;font-size:11px;font-weight:750}.tool-btn:hover{background:#edf5f3}.tool-btn.admin{background:#e6f5f0;color:#087963;border-color:#c7ebe1}.search{margin:0 13px 11px;position:relative}.search input{width:100%;border:1px solid var(--line);background:#f7f9fa;padding:10px 12px 10px 36px;border-radius:12px;outline:none}.search span{position:absolute;left:12px;top:8px;color:var(--muted)}.scroll{overflow:auto;padding:2px 9px 10px;flex:1;min-height:0}.section-title{font-size:10px;font-weight:900;color:#89969c;letter-spacing:.08em;padding:12px 9px 6px}.chat-item{display:flex;align-items:center;gap:10px;padding:9px;border-radius:12px;margin:2px 0;background:transparent;cursor:pointer}.chat-item:hover{background:#f4f8f7}.chat-item.active{background:#e7f6f1}.chat-item.inactive{opacity:.58}.avatar{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;flex:0 0 40px;background:#e7eff1;color:#38515b;font-weight:850;position:relative;overflow:hidden}.avatar img,.msg-avatar img,.profile-avatar img,.mini-avatar img{width:100%;height:100%;object-fit:cover}.presence{position:absolute;right:0;bottom:1px;width:11px;height:11px;border-radius:50%;border:2px solid #fff;background:#bcc7cc}.presence.on{background:var(--green)}.presence.busy{background:var(--red)}.presence.meeting{background:var(--purple)}.presence.away{background:var(--amber)}.item-main{min-width:0;flex:1}.item-name{font-weight:760;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.item-last{color:var(--muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}.item-meta{align-self:stretch;display:flex;flex-direction:column;align-items:flex-end;justify-content:space-between}.time{font-size:10px;color:#99a4a9}.badge{min-width:19px;height:19px;border-radius:10px;padding:0 6px;background:var(--accent);color:#fff;font-size:10px;font-weight:800;display:grid;place-items:center}.me{padding:10px 14px;border-top:1px solid var(--line);display:flex;align-items:center;gap:9px;cursor:pointer}.me-info{min-width:0;flex:1}.me-name{font-weight:800;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.me-role{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.main{min-width:0;min-height:0;display:flex;flex-direction:column;background:#edf2f3;overflow:hidden}.topbar{height:70px;flex:0 0 auto;background:rgba(255,255,255,.94);backdrop-filter:blur(14px);border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 18px;justify-content:space-between;gap:10px}.head-left{display:flex;align-items:center;gap:10px;min-width:0}.head-avatar{cursor:pointer}.head-title{font-weight:850;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.head-sub{font-size:10px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.head-actions{display:flex;gap:6px;flex:0 0 auto}.icon-btn{width:36px;height:36px;border-radius:10px;background:#f4f8f8;color:#52636b}.icon-btn:hover{background:#e7eeee}.messages{flex:1;min-height:0;overflow:auto;padding:18px 22px}.day{display:flex;align-items:center;gap:10px;color:#8d999f;font-size:10px;font-weight:800;justify-content:center;margin:3px 0 14px}.day:before,.day:after{content:"";height:1px;background:#d9e1e3;width:90px}.msg-row{display:flex;margin:7px 0;gap:8px;align-items:flex-end}.msg-row.mine{justify-content:flex-end}.msg-avatar{width:31px;height:31px;border-radius:50%;display:grid;place-items:center;background:#dfe8ea;color:#3c4e55;font-size:10px;font-weight:900;overflow:hidden;flex:none;cursor:pointer}.bubble-wrap{max-width:min(75%,760px);position:relative}.sender{font-size:10px;font-weight:850;color:#64747c;margin:0 0 4px 4px;cursor:pointer}.bubble{background:var(--bubble);border:1px solid #dfe6e8;border-radius:16px 16px 16px 5px;padding:9px 12px;box-shadow:0 2px 8px rgba(23,32,38,.035);line-height:1.45;overflow-wrap:anywhere}.mine .bubble{background:var(--mine);border-color:#c8eee2;border-radius:16px 16px 5px 16px}.body-text{font-size:13px;white-space:pre-wrap;word-break:break-word}.edited{font-size:9px;color:#789087;margin-left:7px}.mention-link{display:inline;padding:0 2px;border-radius:4px;background:#dff3ee;color:#087963;font-weight:800;cursor:pointer}.msg-foot{display:flex;align-items:center;justify-content:flex-end;gap:6px;margin-top:4px;color:#7e8b91;font-size:9px}.ticks{font-weight:900;letter-spacing:-2px;color:#849097}.ticks.delivered{color:#6e8896}.ticks.read{color:#0e9f80}.reply-preview{display:flex;gap:8px;background:#f0f7f5;border-left:3px solid var(--accent);padding:6px 8px;border-radius:8px;margin-bottom:7px;color:#56656b;font-size:10px}.file-card{display:flex;gap:10px;align-items:center;background:rgba(255,255,255,.56);border:1px solid rgba(100,130,140,.16);padding:9px;border-radius:11px}.file-ico{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;background:#e7f5f0;color:#16886e;font-weight:900}.file-name{font-size:12px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.file-size{font-size:10px;color:var(--muted)}.file-card a{color:inherit;text-decoration:none}.file-preview{max-width:min(300px,65vw);max-height:280px;border-radius:10px;display:block;object-fit:contain;margin-bottom:7px}.deleted{font-style:italic;color:#879399}.reactions{display:flex;flex-wrap:wrap;gap:4px;margin-top:7px}.reaction{background:#fff;border:1px solid #d6e1df;border-radius:999px;padding:3px 7px;font-size:10px}.reaction.mine{border-color:#8fd6c5;background:#eefbf7}.composer{position:relative;z-index:30;flex:0 0 auto;padding:10px 16px 14px;background:rgba(244,248,248,.97);border-top:1px solid var(--line)}\n.attachments-preview{display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;padding:0 0 8px;min-height:0;scrollbar-width:thin}.attachments-preview:empty{display:none}.pending-item{position:relative;flex:0 0 78px;width:78px;height:78px;border:1px solid var(--line);border-radius:12px;background:#fff;overflow:hidden;box-shadow:0 2px 8px rgba(23,32,38,.06)}.pending-image{width:100%;height:100%;display:block;object-fit:cover}.pending-file{width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;padding:7px;text-align:center;color:#53636a}.pending-file .ico{font-size:25px;line-height:1}.pending-file .name{width:100%;font-size:9px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pending-file .size{font-size:8px;color:var(--muted)}.pending-remove{position:absolute;top:4px;right:4px;width:22px;height:22px;border-radius:50%;background:rgba(22,28,31,.78);color:#fff;display:grid;place-items:center;font-size:14px;line-height:1;font-weight:900;box-shadow:0 2px 5px rgba(0,0,0,.18)}.pending-remove:hover{background:#d95858}.compose-context{position:fixed;display:none;z-index:250;background:#fff;border:1px solid var(--line);box-shadow:var(--shadow);border-radius:11px;overflow:hidden;min-width:150px}.compose-context.show{display:block}.compose-context button{width:100%;padding:9px 12px;text-align:left;background:#fff;color:#26343a;font-size:12px}.compose-context button:hover{background:#f1f7f5}.reply{display:none;align-items:center;gap:9px;background:#edf6f3;border:1px solid #d6ebe5;padding:8px 11px;border-radius:11px;margin-bottom:8px}.reply.show{display:flex}.reply .x{margin-left:auto}.compose-row{display:flex;align-items:flex-end;gap:7px}.round{width:40px;height:40px;flex:0 0 40px;border-radius:12px;background:#fff;border:1px solid var(--line);color:#5d6d74}.textwrap{position:relative;flex:1;min-width:0}.textwrap textarea{width:100%;resize:none;min-height:42px;max-height:145px;border:1px solid var(--line);background:#fff;border-radius:14px;padding:11px 12px;outline:none;display:block}.send{width:46px;height:42px;flex:0 0 46px;border-radius:12px;background:var(--accent);color:#fff;font-weight:850;display:grid;place-items:center}.send svg{width:20px;height:20px;fill:currentColor;transform:translateX(1px)}.send:hover{background:var(--accent2)}.composer-tools{display:flex;align-items:center;justify-content:space-between;margin-top:5px;color:#879399;font-size:9px}.typing{min-height:15px}.emoji-pop{position:absolute;left:0;bottom:50px;background:#fff;border:1px solid var(--line);box-shadow:var(--shadow);padding:10px;border-radius:15px;display:none;width:min(420px,calc(100vw - 30px));max-height:min(430px,64vh);overflow:hidden;grid-template-rows:auto auto 1fr;z-index:45}.emoji-pop.show{display:grid}.emoji-head{display:flex;gap:7px;align-items:center}.emoji-search{flex:1;border:1px solid var(--line);border-radius:10px;padding:7px 9px;outline:none;font-size:11px}.emoji-count{font-size:9px;color:var(--muted);white-space:nowrap}.emoji-tabs{display:flex;gap:4px;overflow:auto;padding:7px 0}.emoji-tab{padding:6px 8px;border-radius:9px;background:#f4f7f7;color:#627178;font-size:10px;white-space:nowrap}.emoji-tab.active{background:#e5f5f0;color:#087963}.emoji-grid{overflow:auto;display:grid;grid-template-columns:repeat(10,1fr);gap:3px;padding-right:2px}.emoji-grid button{background:transparent;padding:4px;border-radius:8px;font-size:20px;line-height:1.1}.emoji-grid button:hover{background:#eef4f3}.mention-list{position:absolute;left:0;right:0;bottom:47px;background:#fff;border:1px solid var(--line);border-radius:13px;box-shadow:var(--shadow);overflow:auto;display:none;max-height:230px;z-index:44}.mention-list.show{display:block}.mention-row{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:pointer}.mention-row:hover,.mention-row.active{background:#eef7f4}.drop{position:absolute;inset:70px 0 82px;border:3px dashed #5dc0ab;background:rgba(228,249,243,.88);display:none;align-items:center;justify-content:center;z-index:40}.drop.show{display:flex}.drop-card{padding:28px 34px;text-align:center;border-radius:18px;background:#fff;box-shadow:var(--shadow)}.drop-card h3{margin:0 0 7px}.drop-card p{margin:0;color:var(--muted);font-size:12px}.context{position:fixed;display:none;z-index:90;background:#fff;border:1px solid var(--line);box-shadow:var(--shadow);border-radius:12px;overflow:hidden;min-width:190px}.context.show{display:block}.context button{width:100%;padding:9px 12px;text-align:left;background:#fff;color:#26343a;font-size:12px}.context button:hover{background:#f4f8f7}.modal-bg{position:fixed;inset:0;background:rgba(15,25,30,.30);backdrop-filter:blur(3px);display:none;align-items:center;justify-content:center;z-index:100}.modal-bg.show{display:flex}.modal{width:min(620px,calc(100vw - 26px));max-height:90vh;overflow:auto;background:#fff;border-radius:18px;box-shadow:var(--shadow);padding:19px}.modal.wide{width:min(900px,calc(100vw - 26px))}.modal h3{margin:0 0 5px}.modal p{font-size:12px;color:var(--muted);margin:0 0 12px}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field{display:flex;flex-direction:column;gap:5px}.field.full{grid-column:1/-1}.field span{font-size:10px;font-weight:850;color:#607078}.field input,.field textarea,.field select{width:100%;border:1px solid var(--line);border-radius:11px;padding:9px;outline:none;background:#fff}.field textarea{resize:vertical;min-height:80px}.field input:focus,.field textarea:focus,.field select:focus{border-color:#9fdccc;box-shadow:0 0 0 3px rgba(12,155,125,.08)}.locked{background:#f5f7f7!important;color:#7f898d}.avatar-editor{display:flex;align-items:center;gap:12px;padding:10px 0}.profile-avatar{width:74px;height:74px;border-radius:21px;display:grid;place-items:center;overflow:hidden;background:#e7eff1;color:#38515b;font-size:22px;font-weight:900;position:relative;flex:none}.avatar-editor-actions{display:flex;flex-wrap:wrap;gap:6px}.small-btn{padding:7px 10px;border:1px solid var(--line);border-radius:9px;background:#f6f9f8;color:#53646b;font-size:10px;font-weight:800}.small-btn:hover{background:#edf4f2}.profile-top{display:flex;align-items:center;gap:13px;padding-bottom:12px;border-bottom:1px solid var(--line);margin-bottom:12px}.status-chip{display:inline-flex;gap:6px;align-items:center;background:#f0f5f5;border-radius:999px;padding:5px 8px;font-size:10px;color:#53646b}.profile-grid{display:grid;grid-template-columns:120px 1fr;gap:7px 14px;font-size:11px}.profile-grid b{color:#69777e}.muted-note{font-size:10px;color:var(--muted)}.status-menu{display:flex;gap:6px;flex-wrap:wrap}.status-choice{border:1px solid var(--line);background:#f5f8f7;padding:7px 9px;border-radius:9px;font-size:10px;color:#59686e}.status-choice.active{border-color:#9ad9c9;background:#e8f7f2;color:#087963;font-weight:800}.modal-actions{display:flex;justify-content:flex-end;gap:7px;margin-top:16px}.primary,.secondary,.danger{padding:9px 12px;border-radius:10px;font-size:11px;font-weight:850}.primary{background:var(--accent);color:#fff}.primary:hover{background:var(--accent2)}.secondary{background:#f1f5f5;color:#516168}.secondary:hover{background:#e8eeee}.danger{background:#feecec;color:#ae3f3f}.danger:hover{background:#fde1e1}.error-box{display:none;border:1px solid #f1bbbb;background:#fff0f0;color:#a03f3f;border-radius:10px;padding:9px;font-size:10px;margin-bottom:10px}.error-box.show{display:block}.loading{opacity:.65;pointer-events:none}.admin-table{border:1px solid var(--line);border-radius:13px;overflow:hidden}.admin-toolbar{display:flex;gap:7px;align-items:center;margin:10px 0}.admin-toolbar input{flex:1;border:1px solid var(--line);border-radius:10px;padding:8px 9px}.admin-toolbar select{border:1px solid var(--line);border-radius:10px;padding:8px 9px}.user-row{display:grid;grid-template-columns:minmax(0,2fr) minmax(120px,1fr) minmax(90px,.7fr) auto;gap:10px;align-items:center;padding:9px 10px;border-top:1px solid var(--line);font-size:11px}.user-row:first-child{border-top:0}.user-row:hover{background:#f7faf9}.user-name{min-width:0;display:flex;align-items:center;gap:8px}.user-name-text{min-width:0}.user-name-text b,.user-name-text small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.user-name-text small{color:var(--muted);margin-top:2px}.tag{display:inline-flex;align-items:center;padding:4px 7px;border-radius:999px;background:#edf4f2;color:#547067;font-size:9px;font-weight:800}.tag.admin{background:#e7f0ff;color:#4565a3}.tag.off{background:#f2f2f2;color:#888}.check-row{display:flex;align-items:center;gap:8px;font-size:10px}.toast{position:fixed;right:17px;bottom:17px;background:#1f2e33;color:#fff;padding:10px 13px;border-radius:11px;box-shadow:var(--shadow);font-size:11px;opacity:0;transform:translateY(8px);pointer-events:none;transition:.18s;z-index:150}.toast.show{opacity:1;transform:translateY(0)}.empty{height:100%;display:grid;place-items:center}.empty-card{text-align:center;color:#839197}.admin-note{background:#f3f7f7;border:1px solid var(--line);padding:9px;border-radius:10px;font-size:10px;color:#617078;margin-bottom:10px}.no-select{user-select:none}@media(max-width:900px){.app{grid-template-columns:290px 1fr}.field-grid{grid-template-columns:1fr}.user-row{grid-template-columns:1fr auto}.user-row .admin-meta{display:none}}@media(max-width:680px){.app{grid-template-columns:1fr}.sidebar{display:none}.main{min-width:0}.bubble-wrap{max-width:86%}.head-actions{gap:3px}.messages{padding:12px}.composer{padding-left:8px;padding-right:8px}}\n</style>\n</head>\n<body>\n<div class="app">\n  <aside class="sidebar">\n    <div class="brand"><div class="logo">E</div><div><h1>ENCTakip Sohbet</h1><small>Kurumsal iletişim merkezi</small></div></div>\n    <div class="sidebar-actions"><button class="tool-btn" id="newGroup">＋ Grup</button><button class="tool-btn admin" id="adminUsers" hidden>⚙ Kullanıcılar</button></div>\n    <div class="search"><span>⌕</span><input id="search" placeholder="Kişi veya sohbet ara"></div>\n    <div class="scroll" id="chatList"></div>\n    <div class="me" id="meCard"><div class="avatar" id="meAvatar"></div><div class="me-info"><div class="me-name" id="meName"></div><div class="me-role" id="meRole"></div></div><button class="icon-btn" id="meEdit" title="Profilimi düzenle">✎</button></div>\n  </aside>\n  <main class="main">\n    <div class="topbar">\n      <div class="head-left"><div class="avatar head-avatar" id="headAvatar"></div><div style="min-width:0"><div class="head-title" id="headTitle"></div><div class="head-sub" id="headSub"></div></div></div>\n      <div class="head-actions"><button class="icon-btn" id="searchChat" title="Sohbette ara">⌕</button><button class="icon-btn" id="infoChat" title="Bilgi">ⓘ</button></div>\n    </div>\n    <div class="messages" id="messages"></div>\n    <div class="composer">\n      <div class="reply" id="reply"><span>↪</span><div style="min-width:0"><b id="replyName"></b><div id="replyText" class="muted-note"></div></div><button class="icon-btn x" id="cancelReply">×</button></div>\n      <div class="attachments-preview" id="attachmentsPreview" aria-label="Ekler"></div>\n      <div class="compose-row">\n        <button class="round" id="attach" title="Dosya ekle">📎</button>\n        <div class="textwrap">\n          <div class="mention-list" id="mentionList"></div>\n          <div class="emoji-pop" id="emojiPop"><div class="emoji-head"><input class="emoji-search" id="emojiSearch" placeholder="Emoji ara…"><span class="emoji-count" id="emojiCount"></span></div><div class="emoji-tabs" id="emojiTabs"></div><div class="emoji-grid" id="emojiGrid"></div></div>\n          <textarea id="input" rows="1" placeholder="Mesaj yazın…" spellcheck="false" autocomplete="off" autocorrect="off" autocapitalize="sentences"></textarea>\n        </div>\n        <button class="round" id="emoji" title="Emoji">😊</button>\n        <button class="send" id="send" title="Gönder" aria-label="Gönder"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.4 4.1 20.2 10c.9.3.9 1.5 0 1.8L3.4 17.9c-.8.3-1.5-.5-1.2-1.3l2-4.8 9.2-.9-9.2-.9-2-4.8c-.3-.8.4-1.6 1.2-1.3Z"></path></svg></button>\n      </div>\n    </div>\n    <div class="drop" id="drop"><div class="drop-card"><div style="font-size:38px">📎</div><h3>Dosyaları bırakın</h3><p>25 MB\'a kadar dosya veya görsel gönderebilirsiniz.</p></div></div>\n  </main>\n</div>\n<input id="fileInput" type="file" multiple hidden>\n<div class="compose-context" id="composeContext"><button type="button" data-compose-act="paste">Yapıştır</button><button type="button" data-compose-act="cut">Kes</button><button type="button" data-compose-act="copy">Kopyala</button><button type="button" data-compose-act="selectall">Tümünü seç</button></div>\n<div class="context" id="context"><button data-act="reply">↩ Yanıtla</button><button data-act="copy">▣ Kopyala</button><button data-act="react">☺ Tepki</button><button data-act="status">✓ Mesaj durumu</button><button data-act="edit">✎ Düzenle</button><button data-act="delete">⌫ Sil</button></div>\n<div class="modal-bg" id="modalBg"><div class="modal" id="modal"></div></div>\n<div class="toast" id="toast"></div>\n<script>\nconst $=id=>document.getElementById(id), norm=s=>String(s??\'\').trim().toLocaleLowerCase(\'tr-TR\');\nconst state={me:null,admin:false,users:[],groups:[],rooms:[],presences:{},profileMap:new Map(),target:\'GENEL\',targetKind:\'room\',messages:[],poll:null,presencePoll:null,heartbeat:null,typingTimer:null,typing:false,search:\'\',reply:null,selected:null,lastSignature:\'\',emojiCategory:\'Simgeler\',emojiQuery:\'\',unread:{}};\nconst statusNames={online:\'Çevrimiçi\',busy:\'Meşgul\',meeting:\'Toplantıda\',away:\'Dışarıda\'};\nconst emojiSets={\n  \'Sık kullanılan\':\'😀 😃 😄 😁 😆 😅 😂 🤣 😊 🙂 🙃 😉 😍 🥰 😘 😎 🤩 🤔 😐 😶 🙄 😏 😔 😕 🙁 😢 😭 😤 😠 😡 🤬 😱 😴 🤓 😛 😜 🤪 🤨 🧐 🤗 🤭 🤫 🤥 😇 😈 👿 💀 ☠️ 🤖 👻 👽 💩\',\n  \'İnsanlar\':\'👍 👎 👌 ✌️ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ ✋ 🤚 🖐️ 🖖 👋 🤏 💪 👏 🙌 👐 🤲 🙏 🤝 💅 👀 👁️ 🧠 🫶 ❤️ 🩷 🧡 💛 💚 💙 💜 🖤 🤍 🤎\',\n  \'Doğa\':\'🌿 🌱 🌲 🌳 🌴 🌵 🌷 🌹 🌻 🌸 🌼 🌺 🍀 🍁 🍂 🍃 🌾 🌊 🌤️ ☀️ 🌞 🌈 ⭐ 🌟 ✨ 🌙 🌕 🌧️ ☔ ❄️ ⚡ 🔥 💧 🌍 🌎 🌏 🌙 🐾\',\n  \'Hayvanlar\':\'🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🐔 🐧 🐦 🐤 🦅 🦉 🦇 🐺 🐗 🐴 🦄 🐝 🦋 🐌 🐞 🐢 🐍 🦎 🐙 🦑 🦀 🐠 🐟 🐬 🐳\',\n  \'Yemek\':\'🍏 🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍈 🍒 🍑 🥭 🍍 🥥 🥝 🍅 🥑 🥕 🌽 🥔 🍞 🧀 🥚 🍗 🍔 🍟 🍕 🌮 🍝 🍜 🍣 🍚 🍩 🍪 🎂 🍰 🍫 🍿 ☕ 🫖 🥤\',\n  \'Seyahat\':\'🚗 🚕 🚌 🚎 🏎️ 🚓 🚑 🚒 🚜 🛵 🚲 ✈️ 🛫 🛬 🚁 🚢 ⛵ 🚂 🚉 🗺️ 🧭 🗿 🏖️ 🏝️ 🏕️ 🏠 🏢 🏭 🏫 🏥 🛒 ⛽\',\n  \'İşaretler\':\'✅ ❌ ❗ ❕ ❓ ❔ ⚠️ ⛔ 🚫 🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🟤 🔥 💯 🎯 🚀 📌 📍 🔔 ⭐ ✨ ✔️ ☑️ ✳️ ❇️ ©️ ®️ ™️\',\n  \'Ofis\':\'📄 📃 📑 📝 📊 📈 📉 📚 📖 📎 🖇️ 📁 📂 🗂️ 🗃️ 🗄️ 🖨️ 💻 🖥️ ⌨️ 🖱️ 📱 ☎️ 📞 🔒 🔑 💡 🧾 📅 🗓️ ⏰\',\n  \'Kutlama\':\'🎉 🎊 🎈 🎁 🎂 🥳 🏆 🥇 🥈 🥉 🎖️ 🏅 🎵 🎶 🎤 🎸 🎹 🎺 🎻 🪘 🎬 🎥 🎨 🖼️ 🎭 🎪 🎮 🕹️ 🧩 🎲 🎯 🚀 🥂\',\n  \'Semboller\':\'❤️ 🩷 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❤️\u200d🔥 💕 💞 💓 💗 💖 💘 💝 💟 ❣️ ☮️ ✝️ ☪️ ☯️ ⚛️ ♻️ ☑️ ✔️ ➕ ➖ ✖️ ➗ 〰️ ⭕ 🔱 ☢️ ☣️\'\n};\nconst emojiAll=[...new Set(Object.values(emojiSets).join(\' \').split(/\\s+/).filter(Boolean))];\nfunction esc(s){return String(s??\'\').replace(/[&<>\'"]/g,c=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',"\'":\'&#39;\',\'"\':\'&quot;\'}[c]));}\nfunction initials(s){const a=String(s||\'?\').trim().split(/\\s+/).filter(Boolean);return ((a[0]?.[0]||\'?\')+(a.length>1?(a[a.length-1]?.[0]||\'\'):\'\' )).toUpperCase().slice(0,2)}\nfunction person(username){const key=norm(username);const live=state.profileMap.get(key);if(live)return live;const adminRow=(typeof adminCache!==\'undefined\'&&Array.isArray(adminCache)?adminCache.find(x=>norm(x.kullanici_adi)===key):null);return adminRow||{kullanici_adi:username,ad_soyad:username,unvan:\'\',departman:\'\',telefon:\'\',eposta:\'\',durum:\'online\',durum_mesaji:\'\',rol:\'user\',aktif:true,profil_fotografi:\'\'}}\nfunction livePresence(username){return state.presences[norm(username)]||{kullanici_adi:username,online:false,durum:\'offline\',durum_adi:\'Çevrimdışı\',last_seen:\'\',typing:false}}\nfunction presenceClass(u){const p=livePresence(u.kullanici_adi);if(p.online)return p.durum===\'busy\'?\'busy\':p.durum===\'meeting\'?\'meeting\':p.durum===\'away\'?\'away\':\'on\';return \'\'}\nfunction fmtTime(t){try{return new Date(t).toLocaleTimeString(\'tr-TR\',{hour:\'2-digit\',minute:\'2-digit\'})}catch{return \'\'}}\nfunction fmtDay(t){try{const d=new Date(t),today=new Date();const yesterday=new Date(today);yesterday.setDate(today.getDate()-1);if(d.toDateString()===today.toDateString())return \'Bugün\';if(d.toDateString()===yesterday.toDateString())return \'Dün\';return d.toLocaleDateString(\'tr-TR\',{day:\'2-digit\',month:\'long\',year:\'numeric\'})}catch{return \'\'}}\nfunction fmtLastSeen(t){if(!t)return \'Son görülme bilinmiyor\';try{return new Date(t).toLocaleString(\'tr-TR\',{day:\'2-digit\',month:\'2-digit\',hour:\'2-digit\',minute:\'2-digit\'})}catch{return \'Son görülme bilinmiyor\'}}\nfunction sizeText(b){b=Number(b||0);if(b<1024)return b+\' B\';if(b<1024**2)return (b/1024).toFixed(0)+\' KB\';if(b<1024**3)return (b/1024**2).toFixed(1)+\' MB\';return (b/1024**3).toFixed(2)+\' GB\'}\nasync function api(path,opts={}){const r=await fetch(path,{cache:\'no-store\',...opts,headers:{\'Content-Type\':\'application/json\',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok||d.ok===false){if(r.status===403&&/pasif|pasifleştir|devre dışı/i.test(String(d.error||\'\'))){accountBlocked(d.error||\'Hesabınız pasif durumda.\')}throw new Error(d.error||(\'HTTP \'+r.status));}return d}\nfunction avatarUrl(u){return \'/api/avatar?user=\'+encodeURIComponent(u.kullanici_adi)+\'&v=\'+encodeURIComponent(u.profil_fotografi||\'none\')}\nfunction renderAvatar(el,u){el.innerHTML=\'\';if(u?.profil_fotografi){const img=new Image();img.alt=\'\';img.src=avatarUrl(u);img.onload=()=>el.appendChild(img);img.onerror=()=>{el.textContent=initials(u.ad_soyad||u.kullanici_adi)};}else el.textContent=initials(u?.ad_soyad||u?.kullanici_adi||\'?\');}\nfunction targetMeta(target=state.target){if(ROOMS_MAP.has(target))return ROOMS_MAP.get(target);if(String(target).startsWith(\'G_\')){const g=state.groups.find(x=>x.id===target);return g?{...g,kind:\'group\'}:{id:target,name:\'Grup\',kind:\'group\',members:[]}}const u=person(target);return {...u,id:u.kullanici_adi,user:u.kullanici_adi,name:u.ad_soyad||u.kullanici_adi,kind:\'direct\'}}\nconst ROOMS_MAP=new Map();\nfunction statusText(u){const p=livePresence(u.kullanici_adi);return p.online?(statusNames[p.durum]||\'Çevrimiçi\'):\'Çevrimdışı\'}\nfunction searchMatch(u,q){q=norm(q);return !q||norm(u.ad_soyad).includes(q)||norm(u.kullanici_adi).includes(q)||norm(u.unvan).includes(q)||norm(u.departman).includes(q)}\nfunction renderList(){const box=$(\'chatList\');box.innerHTML=\'\';const q=norm($(\'search\').value);const addSection=(title,items)=>{if(!items.length)return;const h=document.createElement(\'div\');h.className=\'section-title\';h.textContent=title;box.appendChild(h);items.forEach(item=>box.appendChild(makeChatItem(item))) };\n const rooms=state.rooms.filter(r=>!q||norm(r.name).includes(q));\n addSection(\'ODALAR\',rooms);\n const groups=state.groups.filter(g=>!q||norm(g.name).includes(q));addSection(\'GRUPLAR\',groups);\n const users=state.users.filter(u=>searchMatch(u,q)&&norm(u.kullanici_adi)!==norm(state.me.kullanici_adi));addSection(\'KİŞİLER\',users);\n if(!rooms.length&&!groups.length&&!users.length){const e=document.createElement(\'div\');e.className=\'muted-note\';e.style.padding=\'24px 12px\';e.textContent=\'Aramanızla eşleşen sohbet bulunamadı.\';box.appendChild(e)} }\nfunction makeChatItem(item){const isUser=item.kullanici_adi!=null;const id=isUser?item.kullanici_adi:item.id;const row=document.createElement(\'div\');row.className=\'chat-item\'+(id===state.target?\' active\':\'\')+(isUser&&!item.aktif?\' inactive\':\'\');row.dataset.target=id;const av=document.createElement(\'div\');av.className=\'avatar\';renderAvatar(av,isUser?item:{ad_soyad:item.name});if(isUser){const p=document.createElement(\'span\');p.className=\'presence \'+presenceClass(item);av.appendChild(p)}row.appendChild(av);const main=document.createElement(\'div\');main.className=\'item-main\';const name=document.createElement(\'div\');name.className=\'item-name\';name.textContent=isUser?(item.ad_soyad||item.kullanici_adi):item.name;main.appendChild(name);const sub=document.createElement(\'div\');sub.className=\'item-last\';sub.textContent=isUser?((livePresence(item.kullanici_adi).online?statusText(item):item.unvan||item.departman||\'Kurumsal kullanıcı\')):(item.description||((item.members||[]).length+\' kişi\'));main.appendChild(sub);row.appendChild(main);const meta=document.createElement(\'div\');meta.className=\'item-meta\';if(state.unread[id]){const b=document.createElement(\'span\');b.className=\'badge\';b.textContent=state.unread[id]>99?\'99+\':state.unread[id];meta.appendChild(b)}row.appendChild(meta);row.addEventListener(\'click\',()=>{delete state.unread[id];openTarget(id)});return row}\nfunction updateMe(){const u=state.me||{};renderAvatar($(\'meAvatar\'),u);$(\'meName\').textContent=u.ad_soyad||u.kullanici_adi||\'\';$(\'meRole\').textContent=[u.unvan,u.departman].filter(Boolean).join(\' · \')||(\'@\'+u.kullanici_adi);$(\'adminUsers\').hidden=!state.admin}\nfunction updateHead(){const m=targetMeta();$(\'headTitle\').textContent=m.name||m.ad_soyad||\'Sohbet\';let sub=\'\';if(m.kind===\'direct\'){sub=(livePresence(m.kullanici_adi).online?statusText(m):(m.unvan||m.departman||\'Çevrimdışı\'))+(m.durum_mesaji?(\' · \'+m.durum_mesaji):\'\');renderAvatar($(\'headAvatar\'),m)}else{sub=m.description||((m.members||[]).length?((m.members||[]).length+\' katılımcı\'):\'\');renderAvatar($(\'headAvatar\'),{ad_soyad:m.name})}$(\'headSub\').textContent=sub}\nfunction mentionHtml(text){let safe=esc(text);return safe.replace(/@([A-Za-z0-9_.çğıöşüÇĞİÖŞÜ-]+)/g,(all,name)=>state.profileMap.has(norm(name))?\'<button type="button" class="mention-link" data-user="\'+esc(name)+\'">@\'+esc(name)+\'</button>\':all).replace(/\\n/g,\'<br>\')}\nfunction messageStatus(m){if(norm(m.sender)!==norm(state.me.kullanici_adi))return \'\';const delivered=(m.delivered_to||[]).length>0,read=(m.read_by||[]).length>0;return \'<span class="ticks \'+(read?\'read\':delivered?\'delivered\':\'\')+\'" title="\'+(read?\'Okundu\':delivered?\'İletildi\':\'Gönderildi\')+\'">\'+(read||delivered?\'✓✓\':\'✓\')+\'</span>\'}\nfunction renderMessages(){const box=$(\'messages\'),oldTop=box.scrollTop,nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<90;box.innerHTML=\'\';let last=\'\';state.messages.forEach(m=>{const day=fmtDay(m.time);if(day!==last){const d=document.createElement(\'div\');d.className=\'day\';d.textContent=day;box.appendChild(d);last=day}const mine=norm(m.sender)===norm(state.me.kullanici_adi);const row=document.createElement(\'div\');row.className=\'msg-row \'+(mine?\'mine\':\'\');const av=document.createElement(\'div\');av.className=\'msg-avatar\';renderAvatar(av,person(m.sender));av.addEventListener(\'click\',()=>openProfile(m.sender));row.appendChild(av);const wrap=document.createElement(\'div\');wrap.className=\'bubble-wrap\';if(!mine&&state.targetKind!==\'direct\'){const sender=document.createElement(\'div\');sender.className=\'sender\';sender.textContent=person(m.sender).ad_soyad||m.sender;sender.addEventListener(\'click\',()=>openProfile(m.sender));wrap.appendChild(sender)}const bub=document.createElement(\'div\');bub.className=\'bubble\';if(m.reply?.text){const rp=document.createElement(\'div\');rp.className=\'reply-preview\';rp.innerHTML=\'<b>\'+esc(m.reply.sender||\'Yanıt\')+\'</b><span>\'+esc(m.reply.text)+\'</span>\';bub.appendChild(rp)}if(m.kind===\'file\'&&m.file){if(/\\.(png|jpe?g|gif|webp|bmp)$/i.test(m.file.name)){const img=document.createElement(\'img\');img.className=\'file-preview\';img.src=\'/api/file?rel=\'+encodeURIComponent(m.file.rel);bub.appendChild(img)}const fc=document.createElement(\'div\');fc.className=\'file-card\';fc.innerHTML=\'<div class="file-ico">↧</div><div style="min-width:0"><a target="_blank" href="/api/file?rel=\'+encodeURIComponent(m.file.rel)+\'"><div class="file-name">\'+esc(m.file.name)+\'</div><div class="file-size">\'+sizeText(m.file.size)+\'</div></a></div>\';bub.appendChild(fc);if(m.text){const t=document.createElement(\'div\');t.className=\'body-text\';t.style.marginTop=\'7px\';t.innerHTML=mentionHtml(m.text);bub.appendChild(t)}}else{const t=document.createElement(\'div\');t.className=\'body-text \'+(m.deleted?\'deleted\':\'\');t.innerHTML=mentionHtml(m.text);bub.appendChild(t)}if(m.edited&&!m.deleted){const ed=document.createElement(\'span\');ed.className=\'edited\';ed.textContent=\'düzenlendi\';bub.appendChild(ed)}const foot=document.createElement(\'div\');foot.className=\'msg-foot\';foot.innerHTML=\'<span>\'+fmtTime(m.time)+\'</span>\'+messageStatus(m);bub.appendChild(foot);if(Object.keys(m.reactions||{}).length){const rh=document.createElement(\'div\');rh.className=\'reactions\';Object.entries(m.reactions).forEach(([emoji,users])=>{const rb=document.createElement(\'button\');rb.className=\'reaction \'+(users.map(norm).includes(norm(state.me.kullanici_adi))?\'mine\':\'\');rb.textContent=emoji+\' \'+users.length;rb.addEventListener(\'click\',e=>{e.stopPropagation();toggleReaction(m,emoji)});rh.appendChild(rb)});bub.appendChild(rh)}bub.addEventListener(\'contextmenu\',e=>showContext(e,m));wrap.appendChild(bub);if(mine){row.insertBefore(wrap,av);row.appendChild(av)}else row.appendChild(wrap);box.appendChild(row);if(!mine&&!m.read_by?.map(norm).includes(norm(state.me.kullanici_adi))){markDelivered(m.id);markRead(m.id)}});box.scrollTop=nearBottom?box.scrollHeight:oldTop;wireMentionLinks()}\nfunction wireMentionLinks(){document.querySelectorAll(\'.mention-link\').forEach(b=>b.addEventListener(\'click\',e=>{e.stopPropagation();openProfile(b.dataset.user)}))}\nfunction sig(){return state.messages.map(x=>x.id+\':\'+x.text+\':\'+x.edited+\':\'+x.deleted+\':\'+JSON.stringify(x.read_by||[])+\':\'+JSON.stringify(x.delivered_to||[])+\':\'+JSON.stringify(x.reactions||{})).join(\'|\')}\nasync function loadMessages(){try{const d=await api(\'/api/messages?target=\'+encodeURIComponent(state.target)+\'&limit=1000\'+(state.search?\'&q=\'+encodeURIComponent(state.search):\'\'));const incoming=d.messages||[];const s=incoming.map(x=>x.id+\':\'+x.text+\':\'+x.edited+\':\'+x.deleted+\':\'+JSON.stringify(x.read_by||[])+\':\'+JSON.stringify(x.delivered_to||[])+\':\'+JSON.stringify(x.reactions||{})).join(\'|\');if(s!==state.lastSignature){const oldIds=new Set(state.messages.map(x=>x.id));const newIncoming=incoming.filter(x=>!oldIds.has(x.id)&&norm(x.sender)!==norm(state.me.kullanici_adi));if(newIncoming.length&&!document.hidden){}else if(newIncoming.length&&!oldIds.size){}else if(newIncoming.length){state.unread[state.target]=(state.unread[state.target]||0)+newIncoming.length}state.messages=incoming;state.lastSignature=s;renderMessages();renderList()}}catch(e){showToast(e.message||\'Mesajlar alınamadı\')}}\nasync function openTarget(target){if(!target)return;const old=state.target;state.target=target;const m=targetMeta(target);state.targetKind=m.kind||\'room\';state.targetMeta=m;state.search=\'\';$(\'searchChat\').title=\'Sohbette ara\';state.lastSignature=\'\';updateHead();renderList();await loadMessages();if(old!==target)$(\'input\').focus()}\nfunction showContext(e,m){e.preventDefault();state.selected=m;const own=norm(m.sender)===norm(state.me.kullanici_adi);$(\'context\').querySelector(\'[data-act="edit"]\').style.display=own&&!m.deleted?\'block\':\'none\';$(\'context\').querySelector(\'[data-act="delete"]\').style.display=own&&!m.deleted?\'block\':\'none\';$(\'context\').style.left=Math.min(e.clientX,innerWidth-200)+\'px\';$(\'context\').style.top=Math.min(e.clientY,innerHeight-250)+\'px\';$(\'context\').classList.add(\'show\')}\nfunction hideContext(){$(\'context\').classList.remove(\'show\')}\nasync function actContext(a){const m=state.selected;hideContext();if(!m)return;if(a===\'reply\'){state.reply={id:m.id,text:m.text||m.file?.name||\'\',sender:person(m.sender).ad_soyad||m.sender};$(\'replyName\').textContent=\'Yanıt: \'+state.reply.sender;$(\'replyText\').textContent=state.reply.text;$(\'reply\').classList.add(\'show\');$(\'input\').focus();return}if(a===\'copy\'){copyText(m.text||m.file?.name||\'\');return}if(a===\'status\'){openStatusModal(m);return}if(a===\'react\'){openReactionPicker(m);return}if(a===\'delete\'){if(confirm(\'Bu mesajı silmek istediğinize emin misiniz?\')){await api(\'/api/delete\',{method:\'POST\',body:JSON.stringify({target:state.target,id:m.id})});state.lastSignature=\'\';await loadMessages()}return}if(a===\'edit\'){const t=prompt(\'Mesajı düzenle:\',m.text||\'\');if(t!==null&&t.trim()&&t!==m.text){await api(\'/api/edit\',{method:\'POST\',body:JSON.stringify({target:state.target,id:m.id,text:t.trim()})});state.lastSignature=\'\';await loadMessages()}}}\ndocument.addEventListener(\'click\',e=>{if(!e.target.closest(\'.context\'))hideContext();if(!e.target.closest(\'#emoji\')&&!e.target.closest(\'#emojiPop\'))closeEmoji();if(!e.target.closest(\'#mentionList\')&&!e.target.closest(\'#input\'))$(\'mentionList\').classList.remove(\'show\')});$(\'context\').addEventListener(\'click\',e=>{const b=e.target.closest(\'button\');if(b){e.stopPropagation();actContext(b.dataset.act)}})\nasync function copyText(text){try{await navigator.clipboard.writeText(text)}catch{const ta=document.createElement(\'textarea\');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand(\'copy\');ta.remove()}showToast(\'Panoya kopyalandı\')}\nasync function send(){let text=$(\'input\').value;if(!text.trim()&&!pendingFiles.length)return;let payload=text.trim();if(state.reply&&payload)payload=\'↪ \'+state.reply.sender+\': \'+state.reply.text+\'\\n\'+payload;$(\'input\').value=\'\';resizeInput();$(\'mentionList\').classList.remove(\'show\');hideComposeContext();try{if(payload)await api(\'/api/send\',{method:\'POST\',body:JSON.stringify({target:state.target,text:payload})});if(pendingFiles.length){await uploadFiles(pendingFiles);clearPendingFiles()}state.reply=null;$(\'reply\').classList.remove(\'show\');state.lastSignature=\'\';await loadMessages()}catch(e){showToast(e.message||\'Gönderilemedi\')}}\n$(\'send\').addEventListener(\'click\',send);$(\'input\').addEventListener(\'keydown\',e=>{if(e.key===\'Enter\'&&!e.shiftKey){e.preventDefault();send()}else{scheduleTyping();if(e.key===\'@\')setTimeout(updateMention,0)}});$(\'input\').addEventListener(\'input\',()=>{resizeInput();updateMention();scheduleTyping()});function resizeInput(){$(\'input\').style.height=\'42px\';$(\'input\').style.height=Math.min(145,$(\'input\').scrollHeight)+\'px\'}\nfunction updateMention(){const v=$(\'input\').value,pos=$(\'input\').selectionStart||0,left=v.slice(0,pos),m=left.match(/@([\\wçğıöşüÇĞİÖŞÜ.-]*)$/);if(!m){$(\'mentionList\').classList.remove(\'show\');return}const q=norm(m[1]);const arr=state.users.filter(u=>norm(u.kullanici_adi)!==norm(state.me.kullanici_adi)&&(norm(u.kullanici_adi).startsWith(q)||norm(u.ad_soyad).includes(q))).slice(0,8);$(\'mentionList\').innerHTML=\'\';arr.forEach((u,i)=>{const d=document.createElement(\'div\');d.className=\'mention-row\'+(i===0?\' active\':\'\');const av=document.createElement(\'div\');av.className=\'avatar\';av.style.width=\'30px\';av.style.height=\'30px\';av.style.flex=\'0 0 30px\';renderAvatar(av,u);d.appendChild(av);const t=document.createElement(\'div\');t.innerHTML=\'<b>@\'+esc(u.kullanici_adi)+\'</b><br><small>\'+esc(u.ad_soyad||\'\')+\'</small>\';d.appendChild(t);d.addEventListener(\'click\',()=>insertMention(u));$(\'mentionList\').appendChild(d)});$(\'mentionList\').classList.toggle(\'show\',arr.length>0)}\nfunction insertMention(u){const v=$(\'input\').value,pos=$(\'input\').selectionStart||0,left=v.slice(0,pos),m=left.match(/@([\\wçğıöşüÇĞİÖŞÜ.-]*)$/);if(!m)return;const start=pos-m[0].length;const nv=v.slice(0,start)+\'@\'+u.kullanici_adi+\' \'+v.slice(pos);$(\'input\').value=nv;const p=start+u.kullanici_adi.length+2;$(\'input\').setSelectionRange(p,p);$(\'mentionList\').classList.remove(\'show\');$(\'input\').focus();resizeInput()}\n$(\'cancelReply\').addEventListener(\'click\',()=>{state.reply=null;$(\'reply\').classList.remove(\'show\')});\nlet pendingFiles=[];\nfunction pendingKey(f){return [f.name,f.size,f.lastModified,f.type].join(\'|\')}\nfunction addPendingFiles(files){const seen=new Set(pendingFiles.map(x=>pendingKey(x.file)));for(const raw of Array.from(files||[])){const f=raw instanceof File?raw:null;if(!f||f.size>25*1024*1024)continue;const k=pendingKey(f);if(seen.has(k))continue;seen.add(k);pendingFiles.push({file:f,url:f.type.startsWith(\'image/\')?URL.createObjectURL(f):\'\'})}renderPendingFiles()}\nfunction removePendingFile(index){const item=pendingFiles[index];if(item?.url){try{URL.revokeObjectURL(item.url)}catch{}}pendingFiles.splice(index,1);renderPendingFiles();$(\'input\').focus()}\nfunction clearPendingFiles(){for(const item of pendingFiles){if(item.url){try{URL.revokeObjectURL(item.url)}catch{}}}pendingFiles=[];renderPendingFiles()}\nfunction renderPendingFiles(){const wrap=$(\'attachmentsPreview\');wrap.innerHTML=\'\';if(!pendingFiles.length)return;pendingFiles.forEach((item,index)=>{const f=item.file,box=document.createElement(\'div\');box.className=\'pending-item\';if(item.url){const img=document.createElement(\'img\');img.className=\'pending-image\';img.src=item.url;img.alt=f.name;box.appendChild(img)}else{const info=document.createElement(\'div\');info.className=\'pending-file\';const ico=document.createElement(\'div\');ico.className=\'ico\';ico.textContent=\'📎\';const name=document.createElement(\'div\');name.className=\'name\';name.textContent=f.name;const size=document.createElement(\'div\');size.className=\'size\';size.textContent=sizeText(f.size);info.append(ico,name,size);box.appendChild(info)}const x=document.createElement(\'button\');x.type=\'button\';x.className=\'pending-remove\';x.title=\'Ekten kaldır\';x.setAttribute(\'aria-label\',\'Ekten kaldır\');x.textContent=\'×\';x.addEventListener(\'click\',e=>{e.stopPropagation();removePendingFile(index)});box.appendChild(x);wrap.appendChild(box)})}\n$(\'attach\').addEventListener(\'click\',()=>$(\'fileInput\').click());$(\'fileInput\').addEventListener(\'change\',e=>{addPendingFiles(e.target.files);e.target.value=\'\'});\nasync function uploadFiles(items){for(const item of items){const f=item.file||item;if(f.size>25*1024*1024){showToast(f.name+\' 25 MB sınırını aşıyor\');continue}const b64=await new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(String(r.result).split(\',\')[1]);r.onerror=rej;r.readAsDataURL(f)});await api(\'/api/upload\',{method:\'POST\',body:JSON.stringify({target:state.target,name:f.name,data:b64,caption:\'\'})})}}\n$(\'input\').addEventListener(\'paste\',e=>{const files=[...(e.clipboardData?.items||[])].map(i=>i.kind===\'file\'?i.getAsFile():null).filter(Boolean);if(files.length){e.preventDefault();addPendingFiles(files);return}})\nlet dragDepth=0;$(\'drop\').addEventListener(\'dragover\',e=>{e.preventDefault();$(\'drop\').classList.add(\'show\')});$(\'drop\').addEventListener(\'drop\',e=>{e.preventDefault();$(\'drop\').classList.remove(\'show\');dragDepth=0;addPendingFiles(e.dataTransfer.files)});document.addEventListener(\'dragenter\',e=>{if(e.dataTransfer?.types?.includes(\'Files\')){dragDepth++;$(\'drop\').classList.add(\'show\')}});document.addEventListener(\'dragleave\',e=>{if(e.dataTransfer?.types?.includes(\'Files\')){dragDepth--;if(dragDepth<=0)$(\'drop\').classList.remove(\'show\')}})\n\nfunction hideComposeContext(){$(\'composeContext\').classList.remove(\'show\')}\nfunction positionComposeContext(x,y){const m=$(\'composeContext\');m.classList.add(\'show\');const r=m.getBoundingClientRect();m.style.left=Math.min(x,window.innerWidth-r.width-8)+\'px\';m.style.top=Math.min(y,window.innerHeight-r.height-8)+\'px\'}\nfunction insertTextAtCaret(text){const input=$(\'input\'),start=input.selectionStart??input.value.length,end=input.selectionEnd??input.value.length;input.value=input.value.slice(0,start)+text+input.value.slice(end);const p=start+text.length;input.focus();input.setSelectionRange(p,p);resizeInput();updateMention()}\nasync function pasteFromClipboard(){hideComposeContext();$(\'input\').focus();try{if(navigator.clipboard?.read){const items=await navigator.clipboard.read(),imgs=[];for(const item of items){const type=item.types.find(t=>t.startsWith(\'image/\'));if(type){const blob=await item.getType(type);imgs.push(new File([blob],\'clipboard-\'+Date.now()+\'-\'+imgs.length+\'.png\',{type}))}}if(imgs.length){addPendingFiles(imgs);return}}}catch{}try{if(navigator.clipboard?.readText){const text=await navigator.clipboard.readText();if(text)insertTextAtCaret(text)}}catch{showToast(\'Yapıştırmak için Ctrl+V kullanabilirsiniz.\')}}\n$(\'input\').addEventListener(\'contextmenu\',e=>{e.preventDefault();positionComposeContext(e.clientX,e.clientY)});$(\'composeContext\').addEventListener(\'click\',e=>{const act=e.target.closest(\'button\')?.dataset.composeAct;if(!act)return;hideComposeContext();$(\'input\').focus();if(act===\'paste\')pasteFromClipboard();else if(act===\'cut\')document.execCommand(\'cut\');else if(act===\'copy\')document.execCommand(\'copy\');else if(act===\'selectall\'){$(\'input\').select()}});document.addEventListener(\'click\',e=>{if(!e.target.closest(\'#composeContext\'))hideComposeContext()});window.addEventListener(\'blur\',hideComposeContext);document.addEventListener(\'scroll\',hideComposeContext,true)\nfunction buildEmojiPicker(){const tabs=$(\'emojiTabs\');tabs.innerHTML=\'\';Object.keys(emojiSets).forEach((name,i)=>{const b=document.createElement(\'button\');b.type=\'button\';b.className=\'emoji-tab\'+(i===0?\' active\':\'\');b.textContent=name;b.addEventListener(\'click\',e=>{e.stopPropagation();state.emojiCategory=name;document.querySelectorAll(\'.emoji-tab\').forEach(x=>x.classList.remove(\'active\'));b.classList.add(\'active\');renderEmojis()});tabs.appendChild(b)});$(\'emojiSearch\').addEventListener(\'input\',e=>{state.emojiQuery=norm(e.target.value);renderEmojis()});renderEmojis()}\nfunction renderEmojis(){let arr=state.emojiQuery?emojiAll:(emojiSets[state.emojiCategory]||\'\').split(/\\s+/).filter(Boolean);arr=[...new Set(arr)];$(\'emojiCount\').textContent=arr.length+\' emoji\';const grid=$(\'emojiGrid\');grid.innerHTML=\'\';arr.forEach(e=>{const b=document.createElement(\'button\');b.type=\'button\';b.textContent=e;b.title=e;b.addEventListener(\'click\',ev=>{ev.stopPropagation();insertEmoji(e)});grid.appendChild(b)})}\nfunction insertEmoji(e){const input=$(\'input\'),start=input.selectionStart??input.value.length,end=input.selectionEnd??input.value.length;input.value=input.value.slice(0,start)+e+input.value.slice(end);const p=start+e.length;input.focus();input.setSelectionRange(p,p);resizeInput();updateMention()}\nfunction closeEmoji(){$(\'emojiPop\').classList.remove(\'show\')}\n$(\'emoji\').addEventListener(\'click\',e=>{e.stopPropagation();$(\'emojiPop\').classList.toggle(\'show\');if($(\'emojiPop\').classList.contains(\'show\')){if(!$(\'emojiTabs\').children.length)buildEmojiPicker();$(\'emojiSearch\').focus()}});$(\'emojiPop\').addEventListener(\'click\',e=>e.stopPropagation())\nfunction toggleReaction(m,emoji){const mine=(m.reactions?.[emoji]||[]).map(norm).includes(norm(state.me.kullanici_adi));api(\'/api/react\',{method:\'POST\',body:JSON.stringify({target:state.target,id:m.id,emoji,remove:mine})}).then(()=>{state.lastSignature=\'\';loadMessages()}).catch(e=>showToast(e.message||\'Tepki eklenemedi\'))}\nfunction openReactionPicker(m){const list=[...emojiAll.slice(0,24),\'👍\',\'❤️\',\'😂\',\'😮\',\'😢\',\'😡\',\'🎉\',\'🔥\',\'✅\',\'👏\',\'🙏\'];$(\'modal\').innerHTML=\'<h3>Tepki ekle</h3><p>Bir emojiye tıklayın; tekrar tıklarsanız kendi tepkiniz kaldırılır.</p><div id="reactionGrid" style="display:flex;flex-wrap:wrap;gap:7px">\'+[...new Set(list)].map(e=>\'<button class="secondary reaction-choice" data-emoji="\'+esc(e)+\'" style="font-size:20px;padding:7px 10px">\'+e+\'</button>\').join(\'\')+\'</div><div class="modal-actions"><button class="secondary" id="closeModalBtn">Kapat</button></div>\';$(\'modalBg\').classList.add(\'show\');document.querySelectorAll(\'.reaction-choice\').forEach(b=>b.addEventListener(\'click\',()=>{toggleReaction(m,b.dataset.emoji);closeModal()}));$(\'closeModalBtn\').addEventListener(\'click\',closeModal)}\nfunction openStatusModal(m){const all=[...(m.delivered_to||[])].map(x=>\'<div>✓✓ İletildi: <b>\'+esc(person(x).ad_soyad||x)+\'</b></div>\').join(\'\')+[...(m.read_by||[])].map(x=>\'<div style="color:#0e9f80">✓✓ Okundu: <b>\'+esc(person(x).ad_soyad||x)+\'</b></div>\').join(\'\');$(\'modal\').innerHTML=\'<h3>Mesaj durumu</h3><p>\'+esc(fmtDay(m.time)+\' \'+fmtTime(m.time))+\'</p><div class="member-list" style="padding:12px;line-height:1.7">\'+(all||\'<div class="muted-note">Henüz teslim/okundu bilgisi oluşmadı.</div>\')+\'</div><div class="modal-actions"><button class="secondary" id="closeStatusBtn">Kapat</button></div>\';$(\'modalBg\').classList.add(\'show\');$(\'closeStatusBtn\').addEventListener(\'click\',closeModal)}\nfunction openProfile(username){const u=person(username),p=livePresence(u.kullanici_adi),self=norm(u.kullanici_adi)===norm(state.me.kullanici_adi);const roleText=u.rol===\'user\'?\'Kullanıcı\':\'Yönetici\';$(\'modal\').innerHTML=\'<div class="profile-top"><div class="profile-avatar" id="profileAvatar"></div><div style="min-width:0"><h3 style="margin:0">\'+esc(u.ad_soyad||u.kullanici_adi)+\'</h3><div class="muted-note">@\'+esc(u.kullanici_adi)+\' · \'+esc(roleText)+\'</div><div class="status-chip" style="margin-top:7px"><span class="presence \'+presenceClass(u)+\'" style="position:static;width:8px;height:8px;border:0"></span>\'+esc(p.durum_adi||u.durum||\'Çevrimdışı\')+\'</div></div></div><div class="profile-grid"><b>Unvan</b><span>\'+esc(u.unvan||\'—\')+\'</span><b>Departman</b><span>\'+esc(u.departman||\'—\')+\'</span><b>Telefon</b><span>\'+esc(u.telefon||\'—\')+\'</span><b>E-posta</b><span>\'+esc(u.eposta||\'—\')+\'</span><b>Durum mesajı</b><span>\'+esc(u.durum_mesaji||\'—\')+\'</span><b>Son görülme</b><span>\'+esc(p.online?\'Şimdi\':fmtLastSeen(p.last_seen))+\'</span></div><div class="modal-actions" id="profileActions"></div>\';$(\'modalBg\').classList.add(\'show\');renderAvatar($(\'profileAvatar\'),u);const actions=$(\'profileActions\');if(!self){const chat=document.createElement(\'button\');chat.className=\'primary\';chat.textContent=\'Sohbete git\';chat.addEventListener(\'click\',()=>{closeModal();openTarget(u.kullanici_adi)});actions.appendChild(chat)}const edit=document.createElement(\'button\');edit.className=\'primary\';edit.textContent=\'Profili düzenle\';edit.addEventListener(\'click\',()=>openProfileEditor(u.kullanici_adi,state.admin&&!self));actions.appendChild(edit);if(self){const stat=document.createElement(\'button\');stat.className=\'secondary\';stat.textContent=\'Durumunu değiştir\';stat.addEventListener(\'click\',()=>openStatusEditor(u));actions.appendChild(stat)}else{const close=document.createElement(\'button\');close.className=\'secondary\';close.textContent=\'Kapat\';close.addEventListener(\'click\',closeModal);actions.appendChild(close)}}\nfunction openStatusEditor(u){$(\'modal\').innerHTML=\'<h3>Durumunu güncelle</h3><div class="muted-note" style="margin-bottom:10px">Durum ve kısa durum mesajın diğer çalışanlara gösterilir.</div><div class="field"><span>Durum</span><select id="statusSel">\'+Object.entries(statusNames).map(([k,v])=>\'<option value="\'+k+\'">\'+v+\'</option>\').join(\'\')+\'</select></div><div class="field" style="margin-top:10px"><span>Durum mesajı</span><input id="statusMsg" maxlength="120" placeholder="Örn. Saha çalışmasındayım" value="\'+esc(u.durum_mesaji||\'\')+\'"></div><div class="error-box" id="statusErr"></div><div class="modal-actions"><button class="secondary" id="statusCancel">İptal</button><button class="primary" id="statusSave">Kaydet</button></div>\';$(\'modalBg\').classList.add(\'show\');$(\'statusSel\').value=u.durum||\'online\';$(\'statusCancel\').addEventListener(\'click\',closeModal);$(\'statusSave\').addEventListener(\'click\',async()=>{await saveStatusForm()})}\nasync function saveStatusForm(){const btn=$(\'statusSave\'),err=$(\'statusErr\');err.classList.remove(\'show\');btn.classList.add(\'loading\');try{await api(\'/api/status\',{method:\'POST\',body:JSON.stringify({status:$(\'statusSel\').value,status_message:$(\'statusMsg\').value})});await refreshBootstrap();closeModal();openProfile(state.me.kullanici_adi);showToast(\'Durum güncellendi\')}catch(e){err.textContent=e.message;err.classList.add(\'show\')}finally{btn.classList.remove(\'loading\')}}\nfunction openProfileEditor(username,adminMode=false){const u=person(username);const self=norm(username)===norm(state.me.kullanici_adi);if(!self&&!adminMode){showToast(\'Bu profili düzenlemek için yönetici yetkisi gerekli.\');return}const adminFields=adminMode?\'<div class="field"><span>Rol</span><select id="pRole"><option value="user">Kullanıcı</option><option value="admin">Yönetici</option><option value="yonetici">Yönetici (uyumluluk)</option></select></div><div class="field"><span>Hesap</span><select id="pActive"><option value="true">Aktif</option><option value="false">Pasif</option></select></div>\':\'\';$(\'modal\').innerHTML=\'<h3>Profili düzenle</h3><div class="muted-note" style="margin-bottom:10px">@\'+esc(u.kullanici_adi)+\' · Kullanıcı adı sistem kimliğidir ve değiştirilemez.</div><div class="avatar-editor"><div class="profile-avatar" id="editorAvatar"></div><div><div class="avatar-editor-actions"><button class="small-btn" id="chooseAvatar">Fotoğraf seç</button><button class="small-btn" id="pasteAvatar">Panodan al</button><button class="small-btn" id="removeAvatar">Fotoğrafı kaldır</button></div><div class="muted-note" style="margin-top:6px">PNG/JPG/WEBP · en fazla 8 MB</div><input type="file" id="profileFile" accept="image/png,image/jpeg,image/webp" hidden></div></div><div class="error-box" id="profileErr"></div><div class="field-grid"><div class="field"><span>Ad Soyad</span><input id="pName" maxlength="255"></div><div class="field"><span>Unvan</span><input id="pTitle" maxlength="255"></div><div class="field"><span>Departman</span><input id="pDept" maxlength="255"></div><div class="field"><span>Telefon</span><input id="pPhone" maxlength="255"></div><div class="field"><span>E-posta</span><input id="pMail" maxlength="255" type="email"></div><div class="field"><span>Durum</span><select id="pStatus">\'+Object.entries(statusNames).map(([k,v])=>\'<option value="\'+k+\'">\'+v+\'</option>\').join(\'\')+\'</select></div><div class="field full"><span>Durum mesajı</span><input id="pStatusMsg" maxlength="120" placeholder="Örn. Toplantıdayım, 14:00 sonrası döneceğim"></div>\'+adminFields+\'</div><div class="modal-actions"><button class="secondary" id="profileCancel">İptal</button><button class="primary" id="profileSave">Kaydet</button></div>\';$(\'modalBg\').classList.add(\'show\');renderAvatar($(\'editorAvatar\'),u);$(\'pName\').value=u.ad_soyad||\'\';$(\'pTitle\').value=u.unvan||\'\';$(\'pDept\').value=u.departman||\'\';$(\'pPhone\').value=u.telefon||\'\';$(\'pMail\').value=u.eposta||\'\';$(\'pStatus\').value=u.durum||\'online\';$(\'pStatusMsg\').value=u.durum_mesaji||\'\';if(adminMode){$(\'pRole\').value=(u.rol||\'user\').toLowerCase();$(\'pActive\').value=u.aktif===false?\'false\':\'true\'}let avatarData=null,removeAvatar=false;$(\'chooseAvatar\').addEventListener(\'click\',()=>$(\'profileFile\').click());$(\'removeAvatar\').addEventListener(\'click\',()=>{avatarData=null;removeAvatar=true;$(\'editorAvatar\').innerHTML=initials(u.ad_soyad||u.kullanici_adi)});$(\'profileFile\').addEventListener(\'change\',()=>{const f=$(\'profileFile\').files?.[0];if(f)previewProfileImage(f)});$(\'pasteAvatar\').addEventListener(\'click\',async()=>{try{if(!navigator.clipboard?.read)throw new Error(\'clipboard-read\');const items=await navigator.clipboard.read();for(const item of items){const type=item.types.find(x=>x.startsWith(\'image/\'));if(type){const blob=await item.getType(type);previewProfileImage(new File([blob],\'clipboard.png\',{type}));return}}showToast(\'Panoda desteklenen bir görsel bulunamadı.\')}catch{showToast(\'Panodan görsel almak için dosyayı doğrudan bu pencereye yapıştırabilirsiniz.\')}});\n function previewProfileImage(file){if(file.size>8*1024*1024){showToast(\'Profil fotoğrafı 8 MB sınırını aşıyor\');return}const r=new FileReader();r.onload=()=>{avatarData={name:file.name,data:String(r.result).split(\',\')[1]};removeAvatar=false;$(\'editorAvatar\').innerHTML=\'<img alt="">\';$(\'editorAvatar\').firstElementChild.src=String(r.result)};r.readAsDataURL(file)}\n $(\'editorAvatar\').addEventListener(\'paste\',e=>{const f=[...(e.clipboardData?.items||[])].map(i=>i.kind===\'file\'?i.getAsFile():null).find(Boolean);if(f){e.preventDefault();previewProfileImage(f)}});$(\'profileCancel\').addEventListener(\'click\',()=>openProfile(username));$(\'profileSave\').addEventListener(\'click\',async()=>{const btn=$(\'profileSave\'),err=$(\'profileErr\');err.classList.remove(\'show\');if(!$(\'pName\').value.trim()){err.textContent=\'Ad Soyad boş bırakılamaz.\';err.classList.add(\'show\');return}if($(\'pMail\').value && !/^.+@.+\\..+$/.test($(\'pMail\').value.trim())){err.textContent=\'E-posta biçimi geçersiz.\';err.classList.add(\'show\');return}const body={kullanici_adi:username,ad_soyad:$(\'pName\').value,unvan:$(\'pTitle\').value,departman:$(\'pDept\').value,telefon:$(\'pPhone\').value,eposta:$(\'pMail\').value,durum:$(\'pStatus\').value,durum_mesaji:$(\'pStatusMsg\').value};if(avatarData)body.profil_fotografi=avatarData;if(removeAvatar)body.profil_fotografi_sil=true;if(adminMode){body.rol=$(\'pRole\').value;body.aktif=$(\'pActive\').value===\'true\'}btn.classList.add(\'loading\');try{await api(adminMode?\'/api/admin/profile/save\':\'/api/profile/save\',{method:\'POST\',body:JSON.stringify(body)});await refreshBootstrap();if(state.admin&&adminMode){await refreshAdminUsers();closeModal();await openAdminUsers();showToast(\'Yönetici değişikliği kaydedildi.\')}else{closeModal();openProfile(username);showToast(\'Profil başarıyla güncellendi.\')}}catch(e){err.textContent=e.message;err.classList.add(\'show\')}finally{btn.classList.remove(\'loading\')}})}\nasync function openAdminUsers(){if(!state.admin){showToast(\'Yönetici yetkisi gerekli.\');return}const d=await api(\'/api/admin/users\');adminCache=d.users||[];renderAdminUsers();$(\'modal\').className=\'modal wide\';$(\'modal\').innerHTML=\'<h3>Kullanıcı yönetimi</h3><div class="admin-note">Buradan tüm profilleri, hesapların aktif/pasif durumunu ve rollerini yönetebilirsiniz. Kullanıcı adı değiştirilemez; mesaj geçmişini korumak için sabit kimliktir.</div><div class="admin-toolbar"><input id="adminSearch" placeholder="Ad, kullanıcı adı, departman…"><select id="adminFilter"><option value="all">Tümü</option><option value="active">Aktif</option><option value="inactive">Pasif</option><option value="admin">Yöneticiler</option></select></div><div class="admin-table" id="adminTable"></div><div class="modal-actions"><button class="secondary" id="adminClose">Kapat</button></div>\';$(\'modalBg\').classList.add(\'show\');$(\'adminSearch\').addEventListener(\'input\',renderAdminUsers);$(\'adminFilter\').addEventListener(\'change\',renderAdminUsers);$(\'adminClose\').addEventListener(\'click\',closeModal);renderAdminUsers()}\nlet adminCache=[];async function refreshAdminUsers(){if(!state.admin)return;try{const d=await api(\'/api/admin/users\');adminCache=d.users||[]}catch(e){showToast(e.message||\'Kullanıcılar alınamadı\')}}\nfunction renderAdminUsers(){if(!$(\'adminTable\'))return;const q=norm($(\'adminSearch\')?.value),filter=$(\'adminFilter\')?.value||\'all\';let rows=adminCache.filter(u=>{const text=[u.ad_soyad,u.kullanici_adi,u.departman,u.unvan,u.eposta].map(norm).join(\' \');if(q&&!text.includes(q))return false;if(filter===\'active\'&&!u.aktif)return false;if(filter===\'inactive\'&&u.aktif)return false;if(filter===\'admin\'&&!(u.rol===\'admin\'||u.rol===\'yonetici\'))return false;return true});const box=$(\'adminTable\');box.innerHTML=\'\';if(!rows.length){box.innerHTML=\'<div class="muted-note" style="padding:18px">Kayıt bulunamadı.</div>\';return}rows.forEach(u=>{const r=document.createElement(\'div\');r.className=\'user-row\';const name=document.createElement(\'div\');name.className=\'user-name\';const av=document.createElement(\'div\');av.className=\'avatar\';av.style.width=\'34px\';av.style.height=\'34px\';av.style.flex=\'0 0 34px\';renderAvatar(av,u);name.appendChild(av);const nt=document.createElement(\'div\');nt.className=\'user-name-text\';nt.innerHTML=\'<b>\'+esc(u.ad_soyad||u.kullanici_adi)+\'</b><small>@\'+esc(u.kullanici_adi)+\' · \'+esc(u.departman||u.unvan||\'\')+\'</small>\';name.appendChild(nt);r.appendChild(name);const mid=document.createElement(\'div\');mid.className=\'admin-meta\';mid.innerHTML=\'<span class="tag \'+((u.rol===\'admin\'||u.rol===\'yonetici\')?\'admin\':\'\')+\'">\'+esc(u.rol===\'user\'?\'Kullanıcı\':\'Yönetici\')+\'</span> \'+(u.aktif?\'<span class="tag">Aktif</span>\':\'<span class="tag off">Pasif</span>\');r.appendChild(mid);const last=document.createElement(\'div\');last.className=\'admin-meta muted-note\';last.textContent=u.online?\'Şimdi\':fmtLastSeen(u.last_seen);r.appendChild(last);const b=document.createElement(\'button\');b.className=\'primary\';b.textContent=\'Düzenle\';b.addEventListener(\'click\',()=>openProfileEditor(u.kullanici_adi,true));r.appendChild(b);box.appendChild(r)})}\n$(\'adminUsers\').addEventListener(\'click\',openAdminUsers);$(\'meCard\').addEventListener(\'click\',e=>{if(!e.target.closest(\'#meEdit\'))openProfile(state.me.kullanici_adi)});$(\'meEdit\').addEventListener(\'click\',e=>{e.stopPropagation();openProfileEditor(state.me.kullanici_adi,false)});$(\'meName\').addEventListener(\'click\',()=>openProfile(state.me.kullanici_adi));$(\'meAvatar\').addEventListener(\'click\',()=>openProfile(state.me.kullanici_adi));$(\'headAvatar\').addEventListener(\'click\',()=>{const m=targetMeta();openProfile(m.kind===\'direct\'?m.kullanici_adi:state.me.kullanici_adi)});$(\'search\').addEventListener(\'input\',renderList);$(\'searchChat\').addEventListener(\'click\',()=>{const q=prompt(\'Bu sohbette ara:\',state.search||\'\');if(q!==null){state.search=q.trim();state.lastSignature=\'\';loadMessages()}});$(\'infoChat\').addEventListener(\'click\',()=>{const m=targetMeta();if(m.kind===\'direct\')openProfile(m.kullanici_adi);else if(m.kind===\'group\')openGroupInfo(m);else openRoomInfo(m)});\nfunction openGroupInfo(g){$(\'modal\').className=\'modal\';$(\'modal\').innerHTML=\'<h3>\'+esc(g.name)+\'</h3><p>\'+esc(g.description||\'Grup\')+\'</p><div class="member-list" style="max-height:320px">\'+(g.members||[]).map(x=>{const u=person(x);return \'<div class="mention-row" data-user="\'+esc(x)+\'"><div class="avatar" style="width:32px;height:32px;flex:0 0 32px"></div><div><b>\'+esc(u.ad_soyad||x)+\'</b><br><small>@\'+esc(x)+\'</small></div></div>\'}).join(\'\')+\'</div><div class="modal-actions"><button class="secondary" id="groupClose">Kapat</button></div>\';$(\'modalBg\').classList.add(\'show\');$(\'modal\').querySelectorAll(\'[data-user]\').forEach(r=>{renderAvatar(r.querySelector(\'.avatar\'),person(r.dataset.user));r.addEventListener(\'click\',()=>openProfile(r.dataset.user))});$(\'groupClose\').addEventListener(\'click\',closeModal)}\nfunction openRoomInfo(r){$(\'modal\').className=\'modal\';$(\'modal\').innerHTML=\'<h3>\'+esc(r.name)+\'</h3><p>\'+esc(r.description||\'\')+\'</p><div class="admin-note">Bu oda tüm aktif ENCTakip kullanıcılarının ortak çalışma alanıdır. Kullanıcılar @etiket ile birbirlerini doğrudan profilden açabilir.</div><div class="modal-actions"><button class="secondary" id="roomClose">Kapat</button></div>\';$(\'modalBg\').classList.add(\'show\');$(\'roomClose\').addEventListener(\'click\',closeModal)}\n$(\'newGroup\').addEventListener(\'click\',()=>{const users=state.users.filter(u=>norm(u.kullanici_adi)!==norm(state.me.kullanici_adi));$(\'modal\').className=\'modal\';$(\'modal\').innerHTML=\'<h3>Yeni grup</h3><p>Grup adı, açıklaması ve katılımcıları seçin.</p><div class="field"><span>Grup adı</span><input id="gName" maxlength="80" placeholder="Örn. GES Ekibi"></div><div class="field" style="margin-top:10px"><span>Açıklama</span><textarea id="gDesc" maxlength="200" placeholder="Kısa açıklama"></textarea></div><div class="check-list" style="max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:12px;margin-top:10px">\'+users.map(u=>\'<label class="check-row" style="padding:8px 10px"><input type="checkbox" value="\'+esc(u.kullanici_adi)+\'"><span><b>\'+esc(u.ad_soyad||u.kullanici_adi)+\'</b><br><small>@\'+esc(u.kullanici_adi)+\'</small></span></label>\').join(\'\')+\'</div><div class="modal-actions"><button class="secondary" id="groupCancel">İptal</button><button class="primary" id="groupSave">Grubu oluştur</button></div>\';$(\'modalBg\').classList.add(\'show\');$(\'groupCancel\').addEventListener(\'click\',closeModal);$(\'groupSave\').addEventListener(\'click\',async()=>{const members=[...document.querySelectorAll(\'#modal input[type=checkbox]:checked\')].map(x=>x.value),name=$(\'gName\').value.trim();if(!name){showToast(\'Grup adı gerekli.\');return}try{const d=await api(\'/api/group/create\',{method:\'POST\',body:JSON.stringify({name,members,description:$(\'gDesc\').value.trim()})});state.groups.push(d.group);closeModal();renderList();openTarget(d.group.id);showToast(\'Grup oluşturuldu\')}catch(e){showToast(e.message||\'Grup oluşturulamadı\')}})});\n$(\'modalBg\').addEventListener(\'click\',e=>{if(e.target===$(\'modalBg\'))closeModal()});function closeModal(){$(\'modalBg\').classList.remove(\'show\');$(\'modal\').className=\'modal\'}window.closeModal=closeModal;\nfunction scheduleTyping(){clearTimeout(state.typingTimer);state.typing=true;state.typingTimer=setTimeout(()=>state.typing=false,2500)}\nfunction startPoll(){clearInterval(state.poll);state.poll=setInterval(async()=>{try{await loadMessages()}catch{}} ,1200)}\nfunction startPresencePoll(){clearInterval(state.presencePoll);state.presencePoll=setInterval(async()=>{try{const d=await api(\'/api/presence\');state.presences=d.presence||{};renderList();updateHead();const active=Object.values(state.presences).filter(x=>x.typing&&norm(x.kullanici_adi)!==norm(state.me.kullanici_adi));$(\'typing\').textContent=active.length?active.map(x=>(x.ad_soyad||x.kullanici_adi)+\' yazıyor…\').join(\', \'):\'\'}catch{}},1500)}\nfunction startHeartbeat(){const beat=()=>api(\'/api/heartbeat\',{method:\'POST\',body:JSON.stringify({status:state.me?.durum||\'online\',typing:state.typing})}).catch(()=>{});beat();clearInterval(state.heartbeat);state.heartbeat=setInterval(beat,15000);window.addEventListener(\'beforeunload\',()=>{navigator.sendBeacon(\'/api/heartbeat\',new Blob([JSON.stringify({status:state.me?.durum||\'online\',offline:true})],{type:\'application/json\'}))})}\nfunction markDelivered(id){const k=\'del_\'+state.target+\'_\'+id;if(sessionStorage.getItem(k))return;sessionStorage.setItem(k,\'1\');api(\'/api/delivered\',{method:\'POST\',body:JSON.stringify({target:state.target,id})}).catch(()=>{})}\nfunction markRead(id){const k=\'read_\'+state.target+\'_\'+id;if(sessionStorage.getItem(k))return;sessionStorage.setItem(k,\'1\');api(\'/api/read\',{method:\'POST\',body:JSON.stringify({target:state.target,id})}).catch(()=>{})}\nasync function refreshBootstrap(){const d=await api(\'/api/bootstrap?user=\'+encodeURIComponent(state.me?.kullanici_adi||new URLSearchParams(location.search).get(\'user\')||\'\'));state.admin=!!d.admin;state.users=d.users||[];state.rooms=d.rooms||[];state.groups=d.groups||[];state.me=d.me;state.profileMap=new Map(state.users.map(u=>[norm(u.kullanici_adi),u]));state.manualStatus=state.me.durum||\'online\';ROOMS_MAP.clear();state.rooms.forEach(r=>ROOMS_MAP.set(r.id,r));updateMe();renderList();updateHead()}\nfunction accountBlocked(message){if(state.blocked)return;state.blocked=true;clearInterval(state.poll);clearInterval(state.presencePoll);clearInterval(state.heartbeat);const wrap=document.createElement(\'div\');wrap.style=\'position:fixed;inset:0;z-index:300;background:rgba(20,29,33,.78);display:grid;place-items:center;padding:20px;font-family:Segoe UI,system-ui,sans-serif\';wrap.innerHTML=\'<div style="width:min(460px,100%);background:#fff;border-radius:18px;padding:24px;box-shadow:0 25px 70px rgba(0,0,0,.25)"><div style="font-size:36px">🔒</div><h2 style="margin:8px 0">Hesap erişimi kapatıldı</h2><p style="color:#647178;font-size:13px;line-height:1.5">\'+esc(message||\'Bu kullanıcı hesabı pasif durumda.\')+\'</p><p style="color:#8a969b;font-size:11px">Yönetici hesabı yeniden aktif edene kadar sohbet bağlantısı durduruldu.</p></div>\';document.body.appendChild(wrap)}\nfunction showToast(t){$(\'toast\').textContent=t;$(\'toast\').classList.add(\'show\');clearTimeout(showToast.t);showToast.t=setTimeout(()=>$(\'toast\').classList.remove(\'show\'),2600)}\nasync function boot(){const supplied=new URLSearchParams(location.search).get(\'user\')||\'\';const d=await api(\'/api/bootstrap?user=\'+encodeURIComponent(supplied));state.admin=!!d.admin;state.me=d.me;state.users=d.users||[];state.rooms=d.rooms||[];state.groups=d.groups||[];state.profileMap=new Map(state.users.map(u=>[norm(u.kullanici_adi),u]));state.manualStatus=state.me.durum||\'online\';state.rooms.forEach(r=>ROOMS_MAP.set(r.id,r));updateMe();renderList();buildEmojiPicker();await openTarget(\'GENEL\');startPoll();startPresencePoll();startHeartbeat();}\n$(\'modalBg\').addEventListener(\'keydown\',e=>{if(e.key===\'Escape\')closeModal()});document.addEventListener(\'keydown\',e=>{if(e.key===\'Escape\'){closeEmoji();hideContext();closeModal()}});\nboot().catch(e=>{document.body.innerHTML=\'<div style="padding:40px;font-family:Segoe UI"><h2>ENCTakip chat başlatılamadı</h2><p>\'+esc(e.message||\'Bilinmeyen hata\')+\'</p></div>\'});\n</script>\n</body>\n</html>\n'

class Store:
    def __init__(self, root: Path, user: str, system: str = ""):
        self.root = root.resolve()
        self.user = str(user or os.environ.get("USERNAME", "kullanici")).strip() or "kullanici"
        self.system = Path(system).resolve() if system else self._detect_system_dir()
        self.system.mkdir(parents=True, exist_ok=True)
        self.messages = self.system / "mesajlar.jsonl"
        self.legacy_users_file = self._find_legacy_users()
        self.profile_file = self.system / "chat_profiles.json"
        self.groups_file = self.system / "mesaj_gruplari.json"
        self.presence_file = self.system / "chat_presence.json"
        self.audit_file = self.system / "chat_audit.jsonl"
        self.files_dir = self.system / "mesaj_dosyalari"
        self.avatar_dir = self.system / "profil_fotograflari"
        self.lock_file = self.system / ".chat_profiles.lock"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.avatar_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_profile_store()

    def _detect_system_dir(self) -> Path:
        for key in ("ENCTAKIP_SISTEM_KLASORU", "ENCEV_SISTEM_KLASORU"):
            val = os.environ.get(key)
            if val:
                p = Path(val)
                if p.exists() or p.parent.exists():
                    return p
        candidates = (
            self.root / ".encev_sistem",
            self.root / "data",
            self.root / "enc_takip" / ".encev_sistem",
        )
        for candidate in candidates:
            if (candidate / "mesajlar.jsonl").exists() or (candidate / "kullanicilar.json").exists():
                return candidate
        for base in (self.root, self.root.parent, self.root.parent.parent):
            try:
                hits = list(base.glob("**/mesajlar.jsonl"))
                if hits:
                    return hits[0].parent
            except Exception:
                pass
        return self.root / ".encev_sistem"

    def _find_legacy_users(self) -> Path:
        candidates = [self.system / "kullanicilar.json", self.root / "kullanicilar.json", self.root / "enc_takip" / "kullanicilar.json"]
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    @staticmethod
    def _default_user_record(username: str) -> dict:
        return {
            "kullanici_adi": username,
            "ad_soyad": username,
            "unvan": "",
            "telefon": "",
            "eposta": "",
            "departman": "",
            "profil_fotografi": "",
            "durum": "online",
            "durum_mesaji": "",
            "rol": "user",
            "aktif": True,
        }

    def _legacy_rows(self) -> list[dict]:
        try:
            if not self.legacy_users_file.exists():
                return []
            raw = json.loads(self.legacy_users_file.read_text(encoding="utf-8-sig"))
            rows = raw.get("kullanicilar", []) if isinstance(raw, dict) else []
            return [r for r in rows if isinstance(r, dict) and str(r.get("kullanici_adi", "")).strip()]
        except Exception:
            return []

    def _normalize_profile(self, raw: dict, username: str | None = None) -> dict:
        u = dict(raw or {})
        name = str(u.get("kullanici_adi", username or "")).strip() or (username or "kullanici")
        role = str(u.get("rol", "user") or "user").strip().lower()
        if name.casefold() == "burak":
            role = "admin"
            u["aktif"] = True
        if role not in USER_ROLES:
            role = "user"
        status = str(u.get("durum", "online") or "online").strip()
        if status not in PROFILE_STATUSES:
            status = "online"
        return {
            "kullanici_adi": name,
            "ad_soyad": str(u.get("ad_soyad", "") or name).strip()[:PROFILE_MAX_TEXT] or name,
            "unvan": str(u.get("unvan", "") or "").strip()[:PROFILE_MAX_TEXT],
            "telefon": str(u.get("telefon", "") or "").strip()[:PROFILE_MAX_TEXT],
            "eposta": str(u.get("eposta", "") or "").strip()[:PROFILE_MAX_TEXT],
            "departman": str(u.get("departman", "") or "").strip()[:PROFILE_MAX_TEXT],
            "profil_fotografi": str(u.get("profil_fotografi", "") or "").strip(),
            "durum": status,
            "durum_mesaji": str(u.get("durum_mesaji", "") or "").strip()[:STATUS_MESSAGE_MAX],
            "rol": role,
            "aktif": bool(u.get("aktif", True)),
        }

    def _read_profile_raw(self) -> dict:
        try:
            if self.profile_file.exists():
                raw = json.loads(self.profile_file.read_text(encoding="utf-8-sig"))
                return raw if isinstance(raw, dict) else {"schema_version": 1, "kullanicilar": []}
        except Exception:
            pass
        return {"schema_version": 1, "kullanicilar": []}

    def _ensure_profile_store(self) -> None:
        with self._lock, file_lock(self.lock_file):
            raw = self._read_profile_raw()
            existing = {str(r.get("kullanici_adi", "")).casefold(): r for r in raw.get("kullanicilar", []) if isinstance(r, dict) and r.get("kullanici_adi")}
            changed = False
            for row in self._legacy_rows():
                key = str(row.get("kullanici_adi", "")).strip()
                k = key.casefold()
                if k not in existing:
                    existing[k] = self._normalize_profile(row)
                    changed = True
                else:
                    norm = self._normalize_profile(existing[k], key)
                    legacy_norm = self._normalize_profile(row, key)
                    for field in ("ad_soyad", "unvan", "telefon", "eposta", "departman", "profil_fotografi", "rol", "aktif", "durum", "durum_mesaji"):
                        if field not in existing[k] or existing[k].get(field) in (None, ""):
                            if legacy_norm.get(field) not in (None, ""):
                                norm[field] = legacy_norm[field]
                                changed = True
                    existing[k] = norm
            if self.user.casefold() not in existing:
                existing[self.user.casefold()] = self._default_user_record(self.user)
                changed = True
            profiles = [self._normalize_profile(v) for v in existing.values()]
            raw = {"schema_version": 1, "updated_at": iso_now(), "kullanicilar": profiles}
            if changed or not self.profile_file.exists():
                atomic_json(self.profile_file, raw)
            self._sync_legacy_locked(profiles)

    def _sync_legacy_locked(self, profiles: list[dict]) -> None:
        if not self.legacy_users_file.parent.exists():
            return
        try:
            if self.legacy_users_file.exists():
                legacy_raw = json.loads(self.legacy_users_file.read_text(encoding="utf-8-sig"))
                if not isinstance(legacy_raw, dict):
                    return
                rows = legacy_raw.get("kullanicilar", [])
                by_name = {str(r.get("kullanici_adi", "")).casefold(): r for r in rows if isinstance(r, dict)}
                touched = False
                for p in profiles:
                    row = by_name.get(p["kullanici_adi"].casefold())
                    if not row:
                        continue
                    for field in ("ad_soyad", "unvan", "telefon", "eposta", "departman", "profil_fotografi", "durum", "durum_mesaji", "rol", "aktif"):
                        if row.get(field) != p.get(field):
                            row[field] = p.get(field)
                            touched = True
                if touched:
                    atomic_json(self.legacy_users_file, legacy_raw)
        except Exception:
            pass

    def _all_profiles(self) -> list[dict]:
        raw = self._read_profile_raw()
        rows = raw.get("kullanicilar", []) if isinstance(raw, dict) else []
        out = [self._normalize_profile(r) for r in rows if isinstance(r, dict) and r.get("kullanici_adi")]
        if not any(x["kullanici_adi"].casefold() == self.user.casefold() for x in out):
            out.append(self._default_user_record(self.user))
        return out

    def profile(self, username: str | None = None) -> dict:
        target = str(username or self.user).strip()
        for p in self._all_profiles():
            if p["kullanici_adi"].casefold() == target.casefold():
                return p
        return self._default_user_record(target)

    def is_active(self, username: str | None = None) -> bool:
        p = self.profile(username)
        if str(p.get("rol", "")).casefold() in ADMIN_ROLES and self._settings_admin_users() and p["kullanici_adi"].casefold() in self._settings_admin_users():
            return True
        return bool(p.get("aktif", True))

    def active_profiles(self) -> list[dict]:
        return [p for p in self._all_profiles() if p.get("aktif", True)]

    def current_profile(self) -> dict:
        return self.profile(self.user)

    def _save_profiles_locked(self, profiles: list[dict]) -> None:
        raw = {"schema_version": 1, "updated_at": iso_now(), "kullanicilar": profiles}
        if self.profile_file.exists():
            try:
                shutil.copy2(self.profile_file, self.profile_file.with_suffix(self.profile_file.suffix + '.bak'))
            except Exception:
                pass
        atomic_json(self.profile_file, raw)
        self._sync_legacy_locked(profiles)

    def _settings_admin_users(self) -> set[str]:
        names: set[str] = set()
        candidates = [self.root / "settings.json", self.root / "enc_takip" / "settings.json", self.system / "settings.json", self.system / "ayarlar.json"]
        for path in candidates:
            try:
                if not path.exists():
                    continue
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                vals = data.get("yoneticiler", []) if isinstance(data, dict) else []
                if isinstance(vals, str):
                    vals = [vals]
                if isinstance(vals, list):
                    names.update(str(x).strip().casefold() for x in vals if str(x).strip())
            except Exception:
                continue
        return names

    def is_admin(self) -> bool:
        me = self.user.casefold()
        # Burak Can Erdağ (@burak) is the fixed WebChat administrator.
        # This is server-side so published clients do not depend on a local
        # settings.json/chat_profiles.json copy for his administrator access.
        if me == "burak":
            return True
        if me in self._settings_admin_users():
            return True
        return str(self.current_profile().get("rol", "")).casefold() in ADMIN_ROLES and self.is_active(self.user)

    def _admin_count_after(self, profiles: list[dict]) -> int:
        settings_admins = self._settings_admin_users()
        count = 0
        for p in profiles:
            key = p["kullanici_adi"].casefold()
            if key in settings_admins or (p.get("aktif", True) and str(p.get("rol", "")).casefold() in ADMIN_ROLES):
                count += 1
        return count

    def save_profile(self, payload: dict, admin: bool = False) -> dict:
        target = str(payload.get("kullanici_adi", self.user) if admin else self.user).strip()
        if not target:
            raise ValueError("Kullanıcı adı gerekli.")
        if not admin and target.casefold() != self.user.casefold():
            raise PermissionError("Kullanıcı yalnızca kendi profilini düzenleyebilir.")
        if admin and not self.is_admin():
            raise PermissionError("Bu işlem yalnızca yöneticiler içindir.")
        with self._lock, file_lock(self.lock_file):
            profiles = self._all_profiles()
            idx = next((i for i, p in enumerate(profiles) if p["kullanici_adi"].casefold() == target.casefold()), None)
            if idx is None:
                raise ValueError("Kullanıcı bulunamadı.")
            current = profiles[idx]
            updated = dict(current)
            allowed = ADMIN_FIELDS if admin else PROFILE_FIELDS
            for key in allowed:
                if key not in payload:
                    continue
                val = payload[key]
                if key == "aktif":
                    val = bool(val)
                elif key == "rol":
                    val = str(val or "user").strip().lower()
                    if val not in USER_ROLES:
                        raise ValueError("Geçersiz kullanıcı rolü.")
                elif key == "durum":
                    val = str(val or "online").strip().lower()
                    if val not in PROFILE_STATUSES:
                        raise ValueError("Geçersiz durum.")
                elif key == "durum_mesaji":
                    val = str(val or "").strip()[:STATUS_MESSAGE_MAX]
                else:
                    val = str(val or "").strip()[:PROFILE_MAX_TEXT]
                updated[key] = val

            if admin and target.casefold() == self.user.casefold() and not updated.get("aktif", True):
                raise ValueError("Kendi yönetici hesabınızı devre dışı bırakamazsınız.")
            if admin and target.casefold() == self.user.casefold() and str(updated.get("rol", "")).casefold() not in ADMIN_ROLES:
                raise ValueError("Kendi yönetici yetkinizi kaldıramazsınız.")
            trial = list(profiles)
            trial[idx] = updated
            if self._admin_count_after(trial) < 1:
                raise ValueError("Sistemde en az bir aktif yönetici kalmalıdır.")
            profiles = trial

            avatar = payload.get("profil_fotografi")
            remove_avatar = bool(payload.get("profil_fotografi_sil"))
            if avatar and isinstance(avatar, dict) and avatar.get("data"):
                name = safe_filename(str(avatar.get("name") or "avatar.png"))
                ext = Path(name).suffix.lower()
                if ext not in PROFILE_IMAGE_EXTS:
                    raise ValueError("Profil fotoğrafı PNG, JPG, JPEG veya WEBP olmalı.")
                try:
                    raw = base64.b64decode(str(avatar.get("data", "")).encode("ascii"), validate=True)
                except Exception as exc:
                    raise ValueError("Profil fotoğrafı verisi geçersiz.") from exc
                if len(raw) > MAX_PROFILE_IMAGE:
                    raise ValueError("Profil fotoğrafı 8 MB sınırını aşıyor.")
                out = self.avatar_dir / f"{uuid.uuid4().hex}{ext}"
                out.write_bytes(raw)
                old = str(updated.get("profil_fotografi") or "")
                updated["profil_fotografi"] = str(out.relative_to(self.system)).replace("\\", "/")
                self._safe_remove_system_file(old)
            elif remove_avatar:
                old = str(updated.get("profil_fotografi") or "")
                updated["profil_fotografi"] = ""
                self._safe_remove_system_file(old)

            profiles[idx] = updated
            self._save_profiles_locked(profiles)
            self.audit("profile.update", target, {"fields": sorted(set(payload).intersection(ADMIN_FIELDS if admin else PROFILE_FIELDS)), "admin": bool(admin), "actor": self.user})
            return self._public_profile(updated)

    def _safe_remove_system_file(self, relative: str) -> None:
        if not relative:
            return
        try:
            p = (self.system / str(relative).replace("\\", "/").lstrip("/")).resolve()
            p.relative_to(self.system.resolve())
            if p.is_file():
                p.unlink()
        except Exception:
            pass

    def _public_profile(self, p: dict) -> dict:
        return dict(p)

    def admin_users(self) -> list[dict]:
        if not self.is_admin():
            raise PermissionError("Bu işlem yalnızca yöneticiler içindir.")
        live = self._live_presence()
        out = []
        for p in sorted(self._all_profiles(), key=lambda x: (not bool(x.get("aktif", True)), (x.get("ad_soyad") or x["kullanici_adi"]).casefold())):
            x = dict(p)
            pr = live.get(p["kullanici_adi"].casefold(), {})
            x.update({"online": bool(pr.get("online")), "last_seen": pr.get("last_seen", ""), "presence_status": pr.get("durum", "offline")})
            out.append(x)
        return out

    def audit(self, action: str, target: str, details: dict | None = None) -> None:
        row = {"id": uuid.uuid4().hex, "time": iso_now(), "actor": self.user, "action": action, "target": target, "details": details or {}}
        try:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                f.flush()
        except Exception:
            pass

    def _read_presence(self) -> dict:
        try:
            raw = json.loads(self.presence_file.read_text(encoding="utf-8")) if self.presence_file.exists() else {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def heartbeat(self, status: str | None = None, typing: bool = False, offline: bool = False) -> None:
        now = iso_now()
        with self._lock:
            data = self._read_presence()
            row = data.get(self.user, {}) if isinstance(data.get(self.user), dict) else {}
            row.update({"kullanici_adi": self.user, "last_seen": now, "typing": bool(typing), "updated_at": now, "offline": bool(offline)})
            if status in PROFILE_STATUSES:
                row["durum"] = status
            data[self.user] = row
            atomic_json(self.presence_file, data)

    def _live_presence(self) -> dict[str, dict]:
        raw = self._read_presence()
        users = {u["kullanici_adi"].casefold(): u for u in self.active_profiles()}
        out: dict[str, dict] = {}
        now = datetime.now().astimezone()
        for key, p in users.items():
            row = raw.get(p["kullanici_adi"], {}) if isinstance(raw.get(p["kullanici_adi"]), dict) else {}
            last = row.get("last_seen") or ""
            online = False
            if last and not row.get("offline", False):
                try:
                    online = (now - datetime.fromisoformat(last)).total_seconds() <= PRESENCE_TTL
                except Exception:
                    online = False
            manual = row.get("durum") or p.get("durum") or "online"
            manual = manual if manual in PROFILE_STATUSES else "online"
            out[key] = {
                "kullanici_adi": p["kullanici_adi"],
                "ad_soyad": p.get("ad_soyad") or p["kullanici_adi"],
                "online": bool(online),
                "durum": manual if online else "offline",
                "durum_adi": PROFILE_STATUSES.get(manual, "Çevrimdışı") if online else "Çevrimdışı",
                "durum_mesaji": p.get("durum_mesaji", "") if online else "",
                "last_seen": last,
                "typing": bool(row.get("typing", False)) and online,
            }
        return out

    def update_status(self, status: str, status_message: str | None = None) -> dict:
        payload = {"durum": status}
        if status_message is not None:
            payload["durum_mesaji"] = status_message
        return self.save_profile(payload, admin=False)

    def _groups(self) -> list[dict]:
        try:
            if self.groups_file.exists():
                data = json.loads(self.groups_file.read_text(encoding="utf-8-sig"))
                groups = data.get("groups", []) if isinstance(data, dict) else []
                return [g for g in groups if isinstance(g, dict) and g.get("id")]
        except Exception:
            pass
        return []

    def groups_for_user(self) -> list[dict]:
        me = self.user.casefold()
        out = []
        for g in self._groups():
            members = [str(x) for x in g.get("members", [])]
            if any(x.casefold() == me for x in members):
                out.append({"id": str(g["id"]), "name": str(g.get("name", "Grup")), "description": str(g.get("description", "")), "members": members, "created_by": str(g.get("created_by", "")), "created_at": str(g.get("created_at", ""))})
        return out

    def create_group(self, name: str, members: list[str], description: str = "") -> dict:
        profiles = self.active_profiles()
        canonical = {p["kullanici_adi"].casefold(): p["kullanici_adi"] for p in profiles}
        clean = []
        for m in list(members) + [self.user]:
            c = canonical.get(str(m).casefold())
            if c and c not in clean:
                clean.append(c)
        g = {"id": "G_" + uuid.uuid4().hex[:12], "name": (name or "Yeni Grup").strip()[:80] or "Yeni Grup", "description": (description or "").strip()[:200], "members": clean or [self.user], "created_by": self.user, "created_at": iso_now()}
        with self._lock:
            data = {"schema_version": 1, "groups": self._groups()}
            data["groups"].append(g)
            atomic_json(self.groups_file, data)
        return g

    def group(self, gid: str) -> dict | None:
        for g in self._groups():
            if str(g.get("id")) == str(gid):
                return g
        return None

    def allowed(self, target: str) -> bool:
        if target in {r["id"] for r in ROOMS}:
            return True
        if target.startswith("G_"):
            g = self.group(target)
            return bool(g and any(str(x).casefold() == self.user.casefold() for x in g.get("members", [])))
        users = {u["kullanici_adi"].casefold() for u in self.active_profiles()}
        return target.casefold() in users

    @staticmethod
    def _marker(text: str):
        for prefix, kind in MARKERS.items():
            if text.startswith(prefix):
                data = safe_json(text[len(prefix):], {})
                return kind, data if isinstance(data, dict) else {}
        return None, None

    def _tail_records(self, max_bytes: int = 10 * 1024 * 1024) -> list[dict]:
        if not self.messages.exists():
            return []
        try:
            size = self.messages.stat().st_size
            with open(self.messages, "rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                raw = f.read()
            out = []
            for line in raw.splitlines():
                try:
                    d = json.loads(line.decode("utf-8"))
                    if isinstance(d, dict):
                        out.append(d)
                except Exception:
                    pass
            return out
        except Exception:
            return []

    def _in_target(self, d: dict, target: str) -> bool:
        s, r = str(d.get("gonderen", "")), str(d.get("hedef", ""))
        if target in {x["id"] for x in ROOMS} or target.startswith("G_"):
            return r == target
        return (s.casefold() == self.user.casefold() and r.casefold() == target.casefold()) or (s.casefold() == target.casefold() and r.casefold() == self.user.casefold())

    def messages_for(self, target: str, limit: int = 250, q: str = "") -> list[dict]:
        if not self.allowed(target):
            return []
        records = self._tail_records()
        state: dict[str, dict] = {}
        order: list[str] = []
        delivered: dict[str, set[str]] = {}
        read: dict[str, set[str]] = {}
        reactions: dict[str, dict[str, set[str]]] = {}
        for d in records:
            if not self._in_target(d, target):
                continue
            mid, text = str(d.get("id", "")), str(d.get("metin", ""))
            kind, data = self._marker(text)
            if kind == "edit":
                base = str(data.get("id", ""))
                if base in state:
                    state[base]["text"] = str(data.get("text", ""))
                    state[base]["edited"] = True
                continue
            if kind == "delete":
                base = str(data.get("id", ""))
                if base in state:
                    state[base]["deleted"] = True
                continue
            if kind in ("delivered", "read"):
                base, who = str(data.get("id", "")), str(data.get("user", ""))
                if base and who:
                    (read if kind == "read" else delivered).setdefault(base, set()).add(who)
                continue
            if kind in ("reaction", "reaction_remove"):
                base, who, emoji = str(data.get("id", "")), str(data.get("user", "")), str(data.get("emoji", ""))
                if base and who and emoji:
                    bucket = reactions.setdefault(base, {}).setdefault(emoji, set())
                    if kind == "reaction":
                        bucket.add(who)
                    else:
                        bucket.discard(who)
                continue
            if not mid or not d.get("zaman"):
                continue
            item = {"id": mid, "time": str(d.get("zaman", "")), "sender": str(d.get("gonderen", "")), "target": str(d.get("hedef", "")), "text": text, "kind": "text", "edited": False, "deleted": False, "file": None, "reply": None}
            if kind == "file":
                item["kind"] = "file"
                item["text"] = str(data.get("caption", ""))
                item["file"] = {"name": str(data.get("name", "Dosya")), "rel": str(data.get("rel", "")), "size": int(data.get("size", 0) or 0), "caption": str(data.get("caption", ""))}
            state[mid] = item
            order.append(mid)
        out = []
        for mid in order:
            x = state[mid]
            x["delivered_to"] = sorted(delivered.get(mid, set()))
            x["read_by"] = sorted(read.get(mid, set()))
            x["reactions"] = {e: sorted(v) for e, v in reactions.get(mid, {}).items() if v}
            if x.get("deleted"):
                x["text"] = "Bu mesaj silindi."
            if q:
                ql = q.casefold()
                if ql not in str(x.get("text", "")).casefold() and ql not in str((x.get("file") or {}).get("name", "")).casefold():
                    continue
            out.append(x)
        return out[-max(1, min(int(limit), 1000)):]

    def append(self, sender: str, target: str, text: str) -> dict:
        if sender.casefold() != self.user.casefold() or not self.allowed(target):
            raise PermissionError("Bu sohbete yazma yetkiniz yok.")
        payload = {"id": uuid.uuid4().hex, "zaman": datetime.now().astimezone().isoformat(timespec="microseconds"), "gonderen": sender, "hedef": target, "metin": text}
        with self._lock:
            self.messages.parent.mkdir(parents=True, exist_ok=True)
            with open(self.messages, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return payload

    def marker(self, target: str, prefix: str, data: dict) -> dict:
        return self.append(self.user, target, prefix + json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def upload(self, target: str, name: str, raw_b64: str, caption: str = "") -> dict:
        if not self.allowed(target):
            raise PermissionError("Bu sohbete dosya gönderme yetkiniz yok.")
        raw = base64.b64decode(raw_b64.encode("ascii"), validate=True)
        if len(raw) > MAX_UPLOAD:
            raise ValueError("Dosya 25 MB sınırını aşıyor.")
        clean = safe_filename(name)
        out = self.files_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{clean}"
        out.write_bytes(raw)
        rel = str(out.relative_to(self.system)).replace("\\", "/")
        return self.marker(target, "__ENCTAKIP_FILE__", {"name": name, "rel": rel, "size": len(raw), "caption": caption})

    def file_path(self, rel: str) -> Path | None:
        rel = str(rel or "").replace("\\", "/").lstrip("/")
        p = (self.system / rel).resolve()
        try:
            p.relative_to(self.system.resolve())
        except ValueError:
            return None
        return p if p.exists() and p.is_file() else None

    def avatar_path(self, user: str) -> Path | None:
        raw = str(self.profile(user).get("profil_fotografi") or "").strip()
        if not raw:
            return None
        p = (self.system / raw.replace("\\", "/").lstrip("/")).resolve()
        try:
            p.relative_to(self.system.resolve())
        except ValueError:
            return None
        return p if p.is_file() else None


class Handler(BaseHTTPRequestHandler):
    store: Store
    static_dir: Path

    def _headers(self, status=200, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > 45 * 1024 * 1024:
            raise ValueError("İstek çok büyük.")
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _require_active(self):
        if not self.store.is_active(self.store.user):
            raise PermissionError("Bu kullanıcı hesabı pasif durumda.")

    def do_OPTIONS(self):
        self._headers(204, "text/plain; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/health":
                return self._json(200, {"ok": True, "version": CHAT_VERSION, "user": self.store.user})
            self._require_active()
            if u.path == "/api/version":
                return self._json(200, {"ok": True, "version": CHAT_VERSION})
            if u.path == "/api/bootstrap":
                supplied = (q.get("user") or [self.store.user])[0]
                if supplied.casefold() != self.store.user.casefold():
                    return self._json(403, {"ok": False, "error": "Kullanıcı doğrulaması başarısız."})
                users = self.store.active_profiles()
                return self._json(200, {"ok": True, "version": CHAT_VERSION, "me": self.store.current_profile(), "admin": self.store.is_admin(), "users": users, "rooms": ROOMS, "groups": self.store.groups_for_user()})
            if u.path == "/api/presence":
                return self._json(200, {"ok": True, "presence": self.store._live_presence()})
            if u.path == "/api/admin/users":
                return self._json(200, {"ok": True, "users": self.store.admin_users()})
            if u.path == "/api/messages":
                target = (q.get("target") or [""])[0]
                limit = int((q.get("limit") or [250])[0])
                query = (q.get("q") or [""])[0]
                return self._json(200, {"ok": True, "messages": self.store.messages_for(target, limit, query)})
            if u.path == "/api/avatar":
                user = (q.get("user") or [""])[0]
                p = self.store.avatar_path(user)
                if not p:
                    return self._json(404, {"ok": False, "error": "Avatar bulunamadı."})
                self._headers(200, mimetypes.guess_type(p.name)[0] or "application/octet-stream")
                self.wfile.write(p.read_bytes())
                return
            if u.path == "/api/file":
                p = self.store.file_path((q.get("rel") or [""])[0])
                if not p:
                    return self._json(404, {"ok": False, "error": "Dosya bulunamadı."})
                self._headers(200, mimetypes.guess_type(p.name)[0] or "application/octet-stream")
                self.wfile.write(p.read_bytes())
                return
            if u.path in ("/", "/index.html"):
                index_path = self.static_dir / "index.html" if self.static_dir else None
                raw = index_path.read_bytes() if index_path and index_path.exists() else EMBEDDED_INDEX_HTML.encode("utf-8")
                self._headers(200, "text/html; charset=utf-8")
                self.wfile.write(raw)
                return
            return self._json(404, {"ok": False, "error": "Bulunamadı."})
        except PermissionError as e:
            return self._json(403, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
            self._require_active()
            target = str(body.get("target", ""))
            if u.path == "/api/send":
                text = str(body.get("text", "")).strip()
                if not text:
                    return self._json(400, {"ok": False, "error": "Boş mesaj gönderilemez."})
                msg = self.store.append(self.store.user, target, text)
                return self._json(200, {"ok": True, "message": {"id": msg["id"], "time": msg["zaman"], "sender": msg["gonderen"], "target": msg["hedef"], "text": msg["metin"], "kind": "text", "edited": False, "deleted": False, "file": None, "delivered_to": [], "read_by": [], "reactions": {}}})
            if u.path in ("/api/edit", "/api/delete"):
                mid = str(body.get("id", ""))
                items = self.store.messages_for(target, 1000)
                own = next((x for x in items if x["id"] == mid and x["sender"].casefold() == self.store.user.casefold()), None)
                if not own:
                    return self._json(403, {"ok": False, "error": "Yalnızca kendi mesajınızı değiştirebilirsiniz."})
                if u.path == "/api/edit":
                    text = str(body.get("text", "")).strip()
                    if not text:
                        return self._json(400, {"ok": False, "error": "Geçersiz düzenleme."})
                    self.store.marker(target, "__ENCTAKIP_EDIT__", {"id": mid, "text": text})
                else:
                    self.store.marker(target, "__ENCTAKIP_DELETE__", {"id": mid, "user": self.store.user})
                return self._json(200, {"ok": True})
            if u.path in ("/api/read", "/api/delivered"):
                mid = str(body.get("id", ""))
                kind = "read" if u.path.endswith("read") else "delivered"
                if mid:
                    self.store.marker(target, "__ENCTAKIP_READ__" if kind == "read" else "__ENCTAKIP_DELIVERED__", {"id": mid, "user": self.store.user})
                return self._json(200, {"ok": True})
            if u.path == "/api/react":
                mid = str(body.get("id", ""))
                emoji = str(body.get("emoji", ""))[:16]
                remove = bool(body.get("remove", False))
                if not mid or not emoji:
                    return self._json(400, {"ok": False, "error": "Geçersiz tepki."})
                if not any(x["id"] == mid for x in self.store.messages_for(target, 1000)):
                    return self._json(404, {"ok": False, "error": "Mesaj bulunamadı."})
                self.store.marker(target, "__ENCTAKIP_REACTION_REMOVE__" if remove else "__ENCTAKIP_REACTION__", {"id": mid, "emoji": emoji, "user": self.store.user})
                return self._json(200, {"ok": True})
            if u.path == "/api/upload":
                msg = self.store.upload(target, str(body.get("name", "dosya")), str(body.get("data", "")), str(body.get("caption", "")))
                return self._json(200, {"ok": True, "message_id": msg["id"]})
            if u.path == "/api/group/create":
                g = self.store.create_group(str(body.get("name", "Yeni Grup")), list(body.get("members", [])), str(body.get("description", "")))
                return self._json(200, {"ok": True, "group": g})
            if u.path == "/api/profile/save":
                profile = self.store.save_profile(body, admin=False)
                return self._json(200, {"ok": True, "profile": profile})
            if u.path == "/api/admin/profile/save":
                profile = self.store.save_profile(body, admin=True)
                return self._json(200, {"ok": True, "profile": profile, "users": self.store.admin_users()})
            if u.path == "/api/status":
                profile = self.store.update_status(str(body.get("status", "online")), body.get("status_message"))
                self.store.heartbeat(status=profile.get("durum", "online"))
                return self._json(200, {"ok": True, "profile": profile})
            if u.path == "/api/heartbeat":
                self.store.heartbeat(status=body.get("status"), typing=bool(body.get("typing", False)), offline=bool(body.get("offline", False)))
                return self._json(200, {"ok": True})
            return self._json(404, {"ok": False, "error": "Bulunamadı."})
        except PermissionError as e:
            return self._json(403, {"ok": False, "error": str(e)})
        except TimeoutError as e:
            return self._json(409, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def log_message(self, format, *args):
        return


def pick_port(start=38171, end=38250):
    for p in range(start, end + 1):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    raise OSError("Yerel chat portu bulunamadı.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--system", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    static_dir = root / "enc_takip" / "webchat"
    store = Store(root, args.user, args.system)
    store.heartbeat(status=store.current_profile().get("durum", "online"))
    port = args.port or pick_port()
    Handler.store = store
    Handler.static_dir = static_dir
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ENCTakip WebChat v{CHAT_VERSION}: http://127.0.0.1:{port}/?user={args.user}", flush=True)
    server.serve_forever(poll_interval=.35)


if __name__ == "__main__":
    main()
