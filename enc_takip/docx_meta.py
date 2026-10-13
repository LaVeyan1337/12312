"""
docx metadata okuma.

DİKKAT: Bu, Word'ün ~$ kilit dosyasının binary içeriğini tahmin etmekten
TAMAMEN FARKLI bir şey. .docx dosyasının kendisi standart bir ZIP
arşividir ve içindeki `docProps/core.xml`, OOXML standardının resmi,
belgelenmiş bir parçasıdır (`cp:lastModifiedBy`, `dcterms:modified`
gibi alanlar Microsoft'un kendi dokümantasyonunda tanımlıdır). Burada
tahmin/offset okuma yok, standart XML parse var — bu yüzden güvenilir.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dcterms": "http://purl.org/dc/terms/",
}


@dataclass
class DocxMeta:
    son_degistiren: Optional[str] = None
    son_degisim: Optional[datetime] = None


def oku(docx_yolu: Path, saat_farki: int = 3) -> DocxMeta:
    """saat_farki: UTC'den yerel saate çevirmek için saat (TR = +3)."""
    try:
        with zipfile.ZipFile(docx_yolu, "r") as z:
            icerik = z.read("docProps/core.xml").decode("utf-8")
        root = ET.fromstring(icerik)

        kisi_el = root.find("cp:lastModifiedBy", _NS)
        kisi = kisi_el.text if kisi_el is not None else None

        tarih_el = root.find("dcterms:modified", _NS)
        tarih = None
        if tarih_el is not None and tarih_el.text:
            try:
                tarih = datetime.fromisoformat(tarih_el.text.replace("Z", "+00:00"))
                tarih = tarih.replace(tzinfo=None) + timedelta(hours=saat_farki)
            except ValueError:
                pass

        return DocxMeta(son_degistiren=kisi, son_degisim=tarih)
    except (OSError, KeyError, ET.ParseError):
        return DocxMeta()
