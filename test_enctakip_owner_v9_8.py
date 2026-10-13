# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import csv
import io
import os
import re
import subprocess
import sys
from pathlib import Path


def ps(cmd, timeout=15):
    try:
        cp = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()
    except Exception as e:
        return -1, "", repr(e)


def net_file_enum(unc):
    if os.name != "nt":
        return [], "not-windows"
    m = re.match(r"^\\\\([^\\]+)\\", unc)
    if not m:
        return [], "UNC yolu çözülemedi"
    server = "\\\\" + m.group(1)

    class FILE_INFO_3(ctypes.Structure):
        _fields_ = [
            ("fi3_id", ctypes.c_uint32),
            ("fi3_permissions", ctypes.c_uint32),
            ("fi3_num_locks", ctypes.c_uint32),
            ("fi3_pathname", ctypes.c_wchar_p),
            ("fi3_username", ctypes.c_wchar_p),
        ]

    api = ctypes.WinDLL("Netapi32.dll")
    fn = api.NetFileEnum
    fn.restype = ctypes.c_uint32
    fn.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    free = api.NetApiBufferFree
    free.restype = ctypes.c_uint32
    free.argtypes = [ctypes.c_void_p]

    buf = ctypes.c_void_p()
    read = ctypes.c_uint32()
    total = ctypes.c_uint32()
    resume = ctypes.c_void_p()
    rc = fn(server, None, None, 3, ctypes.byref(buf), 0xFFFFFFFF, ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume))
    print(f"  NetFileEnum return code: {rc} (0=başarılı)")
    print(f"  entriesread={read.value} totalentries={total.value}")
    rows = []
    try:
        if rc == 0 and buf.value and read.value:
            arr = ctypes.cast(buf, ctypes.POINTER(FILE_INFO_3 * read.value)).contents
            target = Path(unc).name.casefold()
            for x in arr:
                path = str(x.fi3_pathname or "")
                user = str(x.fi3_username or "")
                rows.append((path, user, int(x.fi3_num_locks), int(x.fi3_id)))
                if target in Path(path).name.casefold() or target in path.casefold():
                    print("  [EŞLEŞME]")
                    print(f"    path   : {path}")
                    print(f"    user   : {user!r}")
                    print(f"    locks  : {int(x.fi3_num_locks)}")
                    print(f"    id     : {int(x.fi3_id)}")
    finally:
        if buf.value:
            free(buf)
    return rows, str(rc)


def openfiles(unc):
    m = re.match(r"^\\\\([^\\]+)\\", unc)
    if not m:
        return
    server = "\\\\" + m.group(1)
    target = Path(unc).name.casefold()
    print(f"  openfiles hedef sunucu: {server}")
    try:
        cp = subprocess.run(
            ["openfiles.exe", "/query", "/s", server, "/fo", "csv", "/nh"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"  [HATA] openfiles çalıştırılamadı: {e}")
        return
    print(f"  returncode={cp.returncode}")
    if cp.stderr.strip():
        print(f"  stderr={cp.stderr.strip()}")
    rows = list(csv.reader(io.StringIO(cp.stdout or "")))
    hits = 0
    for row in rows:
        line = " | ".join(x.strip() for x in row if x.strip())
        if target in line.casefold():
            hits += 1
            print("  [EŞLEŞME]", line)
    print(f"  eşleşen satır: {hits}")


def powershell_smb(unc):
    m = re.match(r"^\\\\([^\\]+)\\", unc)
    if not m:
        return
    server = m.group(1)
    target = Path(unc).name.replace("'", "''")
    print(f"  PowerShell Get-SmbOpenFile sunucu: {server}")
    cmd = (
        "$ErrorActionPreference='Stop'; "
        f"$s=New-CimSession -ComputerName '{server.replace(chr(39), chr(39)*2)}'; "
        f"Get-SmbOpenFile -CimSession $s | Where-Object {{$_.Path -like '*{target}*'}} | "
        "Select-Object ClientComputerName,ClientUserName,Path,SessionId,Locks | Format-List; "
        "Remove-CimSession $s"
    )
    rc, out, err = ps(cmd, timeout=20)
    print(f"  returncode={rc}")
    if out:
        print(out)
    if err:
        print("  stderr:", err)


def main():
    print("ENCTakip — OWNER TEŞHİS v9.8")
    print("================================")
    unc = input("Dosyanın UNC yolunu yapıştır (örn. \\\\SUNUCU\\Paylasim\\dosya.docx): ").strip().strip('"')
    if not unc.startswith("\\\\"):
        print("[HATA] UNC yolu \\\\ ile başlamalı.")
        raise SystemExit(2)
    print("\n[1] NetFileEnum")
    rows, _ = net_file_enum(unc)
    if rows:
        print(f"  Toplam dönen kayıt: {len(rows)}")
    else:
        print("  [UYARI] NetFileEnum kayıt döndürmedi veya eşleşme yok.")
    print("\n[2] openfiles.exe")
    openfiles(unc)
    print("\n[3] PowerShell Get-SmbOpenFile")
    powershell_smb(unc)
    print("\nBitti. Yukarıdaki çıktının tamamını gönder.")


if __name__ == "__main__":
    main()
