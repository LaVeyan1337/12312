"""Dosya takip servisi.

GUI'den bağımsız iş mantığı. Mesaj sistemi özellikle ağdaki JSONL deposunu
sürekli baştan okumaz: yeni mesajlar dosya byte-offset'i üzerinden artımlı
okunur; geçmiş mesajlar gerektiğinde sayfalanır.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import List, Optional, Set

from .activity import HareketsizlikTakipcisi
from .backup import YedekYoneticisi
from .config import BILGISAYAR, KULLANICI, Settings
from .docx_meta import DocxMeta, oku as docx_oku
from .lock_service import KilitServisi
from .models import DosyaDurumu, LogKaydi, Mesaj
from .presence import Varlik, VarlikServisi
from .storage import MesajDeposu, OlayGunlugu, teknik_logger
from .waitqueue import BeklemeSirasi

SISTEM_GONDEREN = "SİSTEM"
HAREKETSIZLIK_ESIGI_SN = 180
HAREKETSIZLIK_TOAST_ARALIGI_SN = 120


@dataclass
class GunOzeti:
    acilma_sayisi: int = 0
    toplam_sure_sn: int = 0


@dataclass
class DurumBilgisi:
    durum: DosyaDurumu = DosyaDurumu.BELIRSIZ
    kullanici: str = ""
    bilgisayar: str = ""
    acik_sure_sn: int = 0
    boyut_bayt: int = 0
    degisim_zamani: Optional[datetime] = None
    son_degistiren: Optional[str] = None
    son_degisim: Optional[datetime] = None


def sure_metni(saniye: int) -> str:
    s = max(0, int(saniye))
    gun, kalan = divmod(s, 86400)
    saat, kalan = divmod(kalan, 3600)
    dakika, sn = divmod(kalan, 60)
    if gun:
        return f"{gun} gün {saat} sa"
    if saat:
        return f"{saat} sa {dakika} dk"
    if dakika:
        return f"{dakika} dk {sn} sn"
    return f"{sn} sn"


class DosyaTakipServisi:
    def __init__(self, settings: Settings, kuyruk: Queue):
        self.s = settings
        self.kuyruk = kuyruk

        self.kilit = KilitServisi(settings)
        self.mesajlar = MesajDeposu(settings.sistem_klasoru / "mesajlar.jsonl")
        self.gunluk = OlayGunlugu(settings.sistem_klasoru / "gunluk.jsonl")
        self.varlik = VarlikServisi(settings)
        self.debug = teknik_logger(settings)
        self.hareketsizlik = HareketsizlikTakipcisi(settings)
        self.yedek = YedekYoneticisi(settings)
        self.sira = BeklemeSirasi(settings.sistem_klasoru / "bekleme_sirasi.jsonl")

        self._calisiyor = False
        self._bende_acik = False
        self._acilis_zamani: Optional[datetime] = None
        self._word_kilit_kontrol_sayaci = 0
        self._gorulen_mesaj_id: Set[str] = set()

        # Mesaj JSONL watcher state. Ağ dosyası değişmediyse JSONL hiç açılmaz.
        self._mesaj_offset = 0
        self._mesaj_dosya_imza = (0, 0)
        self._mesaj_watcher_hazir = False
        self._mesaj_offset_lock = threading.Lock()

        self._onceki_kullanan: str = ""
        self._son_gonderilen_mesaj: Optional[Mesaj] = None
        self._son_bilinen_mtime: Optional[float] = None
        self._hareketsizlik_uyarisi_gonderildi = False
        self._son_hareketsizlik_toast_zamani: Optional[datetime] = None
        self._onceki_baglanti_var: Optional[bool] = None

        # Eski kurulumlarda bulunan mesaj ID'lerini başlangıçta yalnızca bir kez
        # tail'den alıyoruz; normal döngü artık offset ile çalışıyor.
        try:
            for m in self.mesajlar.son_mesajlar(300):
                self._gorulen_mesaj_id.add(m.id)
        except Exception:
            self.debug.exception("mesaj başlangıç tail okuması başarısız")

    def baslat(self) -> None:
        self._calisiyor = True
        for hedef in (
            self._tarama_dongusu,
            self._heartbeat_dongusu,
            self._mesaj_dongusu,
            self._varlik_dongusu,
        ):
            threading.Thread(target=hedef, daemon=True, name=f"ENCTakip-{hedef.__name__}").start()

    def durdur(self) -> None:
        self._calisiyor = False
        if self._bende_acik:
            self.kapat()

    def _mesaj_watcher_baslat(self) -> None:
        """İlk watcher konumunu EOF'a al.

        Uygulamanın lokal SQLite senkronu geçmişi ayrı yönetir. Servis thread'i
        yalnızca program çalışmaya başladıktan sonra gelen mesajları event olarak
        yayınlamakla sorumludur.
        """
        with self._mesaj_offset_lock:
            size, mtime = self.mesajlar.dosya_bilgisi()
            self._mesaj_offset = size
            self._mesaj_dosya_imza = (size, mtime)
            self._mesaj_watcher_hazir = True

    def _mesaj_dongusu(self) -> None:
        self._mesaj_watcher_baslat()
        while self._calisiyor:
            try:
                size, mtime = self.mesajlar.dosya_bilgisi()
                with self._mesaj_offset_lock:
                    old_size, old_mtime = self._mesaj_dosya_imza
                    offset = self._mesaj_offset

                # Aynı boyut + mtime: ağ dosyasına hiç dokunma.
                if (size, mtime) == (old_size, old_mtime):
                    time.sleep(1.0)
                    continue

                # Dosya küçüldüyse veya aynı boyutta yeniden oluşturulduysa
                # offset geçersizdir. Güvenli taraftan son kayıtları bir kez alıp
                # EOF'a geçiyoruz. Normal append akışında bu dal çalışmaz.
                if size < offset or (size == old_size and mtime != old_mtime):
                    for m in self.mesajlar.son_mesajlar(300):
                        if m.id not in self._gorulen_mesaj_id:
                            self._gorulen_mesaj_id.add(m.id)
                            self._mesaj_event_gonder(m)
                    offset = size
                else:
                    mesajlar, yeni_offset = self.mesajlar.sondan_offsete_kadar(offset)
                    for m in mesajlar:
                        if m.id in self._gorulen_mesaj_id:
                            continue
                        self._gorulen_mesaj_id.add(m.id)
                        self._mesaj_event_gonder(m)
                    offset = yeni_offset

                with self._mesaj_offset_lock:
                    self._mesaj_offset = offset
                    self._mesaj_dosya_imza = (size, mtime)
            except Exception:
                self.debug.exception("mesaj artımlı izleme döngüsü hatası")
            time.sleep(1.0)

    def _mesaj_event_gonder(self, m: Mesaj) -> None:
        if m.gonderen != KULLANICI and m.hedef in ("GENEL", "ODA_ECED", "ODA_DILEK", KULLANICI):
            self.kuyruk.put(("YENI_MESAJ", m))

    def mesaj_gecmisi_son(self, limit: int = 1500) -> List[Mesaj]:
        return self.mesajlar.son_mesajlar(limit)

    def mesaj_gecmisi_onceki(self, before_zaman: Optional[str], limit: int = 300, hedef: Optional[str] = None) -> List[Mesaj]:
        return self.mesajlar.oncekileri_getir(before_zaman, limit, hedef=hedef, kullanici=KULLANICI)

    def mesaj_ara(self, sorgu: str, limit: int = 5000) -> List[Mesaj]:
        return self.mesajlar.ara(sorgu, limit)

    def mesaj_ara_sohbet(self, sorgu: str, hedef: str, limit: int = 5000) -> List[Mesaj]:
        return self.mesajlar.ara_sohbet(sorgu, hedef, limit, kullanici=KULLANICI)

    def mesaj_receiptleri(self, mesaj_id: str, hedef: str = "") -> List[Mesaj]:
        return self.mesajlar.receiptleri(mesaj_id, hedef=hedef, kullanici=KULLANICI)

    # ------------------------------ mevcut dosya takip döngüleri
    def _tarama_dongusu(self) -> None:
        while self._calisiyor:
            try:
                d = self._durum_hesapla()
                if self._bende_acik and self._acilis_zamani:
                    gecen_sn = (datetime.now() - self._acilis_zamani).total_seconds()
                    if gecen_sn >= 5:
                        try:
                            fiziksel_kilit = self.kilit.dosya_gercekten_kilitli_mi()
                            if fiziksel_kilit:
                                self._word_kilit_kontrol_sayaci = 0
                            else:
                                self._word_kilit_kontrol_sayaci += 1
                                if self._word_kilit_kontrol_sayaci >= 2:
                                    self._word_kilit_kontrol_sayaci = 0
                                    self.kapat()
                                    d = self._durum_hesapla()
                        except Exception:
                            self._word_kilit_kontrol_sayaci = 0
                            self.debug.exception("Word fiziksel kilit kontrolü başarısız")
                self._olay_transitionlarini_tespit_et(d)
                self._hareketsizlik_kontrol_et()
                self.kuyruk.put(("DURUM", d))
            except Exception as e:
                self.debug.exception("tarama döngüsü hatası")
                self.kuyruk.put(("HATA", str(e)))
            time.sleep(self.s.tarama_araligi_sn)

    def _heartbeat_dongusu(self) -> None:
        while self._calisiyor:
            if self._bende_acik:
                try:
                    self.kilit.heartbeat()
                except Exception:
                    self.debug.exception("heartbeat hatası")
            time.sleep(self.s.heartbeat_araligi_sn)

    def _varlik_dongusu(self) -> None:
        while self._calisiyor:
            try:
                self.varlik.bildir()
            except Exception:
                self.debug.exception("varlık döngüsü hatası")
            time.sleep(25)

    # ---------------------------------------------------------------- durum
    def _durum_hesapla(self) -> DurumBilgisi:
        durum = self.kilit.durum()
        kayit = self.kilit.oku()
        word_sahibi = self.kilit.word_kilit_sahibi() if kayit is None else None
        acik_sn = 0
        if kayit is not None:
            try:
                acilis = datetime.fromisoformat(kayit.acilis)
                acik_sn = int((datetime.now() - acilis).total_seconds())
            except ValueError:
                pass
        boyut = 0
        degisim_zamani = None
        meta = DocxMeta()
        if self.s.dosya_yolu.exists():
            try:
                st = self.s.dosya_yolu.stat()
                boyut = st.st_size
                degisim_zamani = datetime.fromtimestamp(st.st_mtime)
            except OSError:
                pass
            meta = docx_oku(self.s.dosya_yolu)
        return DurumBilgisi(
            durum=durum,
            kullanici=kayit.kullanici if kayit else (word_sahibi or ""),
            bilgisayar=kayit.bilgisayar if kayit else "Word / ağ paylaşımı",
            acik_sure_sn=acik_sn,
            boyut_bayt=boyut,
            degisim_zamani=degisim_zamani,
            son_degistiren=meta.son_degistiren,
            son_degisim=meta.son_degisim,
        )

    def _hareketsizlik_kontrol_et(self) -> None:
        if not self._bende_acik:
            self.hareketsizlik.sifirla()
            self._hareketsizlik_uyarisi_gonderildi = False
            self._son_hareketsizlik_toast_zamani = None
            return
        hareketsiz_sn = self.hareketsizlik.hareketsiz_saniye()
        if hareketsiz_sn < HAREKETSIZLIK_ESIGI_SN:
            self._son_hareketsizlik_toast_zamani = None
            self._hareketsizlik_uyarisi_gonderildi = False
            return
        simdi = datetime.now()
        son_uyari = self._son_hareketsizlik_toast_zamani
        if son_uyari is None or (simdi - son_uyari).total_seconds() >= HAREKETSIZLIK_TOAST_ARALIGI_SN:
            self._son_hareketsizlik_toast_zamani = simdi
            self._hareketsizlik_uyarisi_gonderildi = True
            self.kuyruk.put(("UYARI_HAREKETSIZLIK_TOAST", hareketsiz_sn))
            self._olay_kaydet("UYARI_GONDERILDI", f"{sure_metni(hareketsiz_sn)} hareketsizlik uyarısı gösterildi", hareketsiz_sn)

    def hareketsizlik_sifirla(self) -> None:
        self.hareketsizlik.sifirla()
        self._hareketsizlik_uyarisi_gonderildi = False
        self._son_hareketsizlik_toast_zamani = None

    def bugun_ozeti(self) -> GunOzeti:
        bugun = datetime.now().date()
        ozet = GunOzeti()
        for k in self.gunluk.tumunu_oku(2000):
            try:
                zaman = datetime.fromisoformat(k.zaman)
            except ValueError:
                continue
            if zaman.date() != bugun:
                continue
            if k.olay == "ACILDI":
                ozet.acilma_sayisi += 1
            elif k.olay in ("KAPATILDI", "UYARI_KAPAT"):
                ozet.toplam_sure_sn += k.sure_sn
        return ozet

    def _olay_transitionlarini_tespit_et(self, d: DurumBilgisi) -> None:
        def temiz_isim(deger) -> str:
            s = str(deger or "").strip()
            if not s:
                return ""
            if s.casefold() in {"bilinmiyor", "unknown", "?", "başka bir kullanıcı", "baska bir kullanici", "kullanıcı adı alınamadı", "kullanici adi alinamadi"}:
                return ""
            return s
        def gercek_owner() -> str:
            owner = temiz_isim(getattr(d, "kullanici", ""))
            if owner:
                return owner
            try:
                fn = getattr(self.kilit, "word_kilit_sahibi", None)
                if callable(fn):
                    owner = temiz_isim(fn())
                    if owner:
                        return owner
            except Exception:
                pass
            return ""
        baglanti_var = d.durum != DosyaDurumu.BAGLANTI_YOK
        if self._onceki_baglanti_var is False and baglanti_var:
            self.kuyruk.put(("OLAY", {"tip": "baglanti_geldi", "msg": "Ağ paylaşımına yeniden bağlanıldı"}))
        self._onceki_baglanti_var = baglanti_var
        disaridan_acik = d.durum in (DosyaDurumu.KULLANIMDA_BASKASI, DosyaDurumu.BELIRSIZ)
        if disaridan_acik:
            kullanan = gercek_owner()
            if not kullanan:
                self._onceki_kullanan = ""
            elif self._onceki_kullanan != kullanan:
                self.kuyruk.put(("OLAY", {"tip": "acildi", "msg": f"{kullanan} dilekçe dosyasını açtı", "kullanan": kullanan}))
                self._onceki_kullanan = kullanan
        elif self._onceki_kullanan:
            kapanan = self._onceki_kullanan
            self.kuyruk.put(("OLAY", {"tip": "kapandi", "msg": f"{kapanan} dosyayı kapattı", "kullanan": kapanan}))
            self._onceki_kullanan = ""
        if d.degisim_zamani is not None:
            mtime = d.degisim_zamani.timestamp()
            if self._son_bilinen_mtime is not None and mtime != self._son_bilinen_mtime:
                kim = temiz_isim(getattr(d, "son_degistiren", ""))
                if kim and kim.casefold() != str(KULLANICI).casefold():
                    self.kuyruk.put(("OLAY", {"tip": "kaydet", "msg": f"{kim} dosyayı kaydetti", "kullanan": kim}))
                self.yedek.yedekle()
            self._son_bilinen_mtime = mtime

    def zorla_kilit_kaldir(self) -> tuple[bool, str]:
        try:
            fn = getattr(self.kilit, "zorla_birak_sonuc", None)
            if callable(fn):
                return fn()
            return False, "Güvenli zorla-kaldırma desteği bulunamadı."
        except Exception as exc:
            return False, f"Kilit işlemi başarısız: {exc}"

    def ac(self) -> tuple[bool, str]:
        if not self.kilit.al():
            return False, "Kilit alınamadı — başka biri az önce açmış olabilir. Lütfen yenileyin."
        self._bende_acik = True
        self._acilis_zamani = datetime.now()
        self._word_kilit_kontrol_sayaci = 0
        self.hareketsizlik.sifirla()
        self._hareketsizlik_uyarisi_gonderildi = False
        self._son_hareketsizlik_toast_zamani = None
        self.sira.ayril(KULLANICI)
        self._olay_kaydet("ACILDI", "Dosyayı açtı")
        try:
            os.startfile(str(self.s.dosya_yolu))  # type: ignore[attr-defined]
        except Exception as e:
            self.debug.exception("dosya açılamadı")
            return False, f"Kilit alındı ama dosya açılamadı: {e}"
        return True, "Dosya açıldı. İşiniz bitince 'Kapattım' butonuna basın."

    def kapat(self, otomatik: bool = False) -> str:
        sure_sn = int((datetime.now() - self._acilis_zamani).total_seconds()) if self._acilis_zamani else 0
        self.kilit.birak()
        self._bende_acik = False
        self._acilis_zamani = None
        self._word_kilit_kontrol_sayaci = 0
        self.hareketsizlik.sifirla()
        self._hareketsizlik_uyarisi_gonderildi = False
        self._son_hareketsizlik_toast_zamani = None
        self._olay_kaydet("UYARI_KAPAT" if otomatik else "KAPATILDI", f"{sure_metni(sure_sn)} açık kaldı" + (" (hareketsizlik nedeniyle otomatik kapatıldı)" if otomatik else ""), sure_sn)
        self._sirayi_bildir()
        return sure_metni(sure_sn)

    def _sirayi_bildir(self) -> None:
        try:
            ilk = self.sira.ilkini_bildir_ve_cikar()
            if ilk and ilk != KULLANICI:
                self.sistem_bildirimi_gonder(ilk, "🔔 Dosya kapandı, sıra sizde — şimdi açabilirsiniz.")
        except Exception:
            self.debug.exception("sıra bildirimi hatası")

    def sistem_bildirimi_gonder(self, hedef: str, metin: str) -> bool:
        mesaj = Mesaj.yeni(SISTEM_GONDEREN, hedef, metin)
        if self.mesajlar.gonder(mesaj):
            self._gorulen_mesaj_id.add(mesaj.id)
            return True
        return False

    def siraya_katil(self) -> None:
        if self.sira.bendemi(KULLANICI):
            return
        self.sira.katil(KULLANICI)
        try:
            d = self._durum_hesapla()
            sahip = (d.kullanici or "").strip()
            if sahip and sahip.lower() != KULLANICI.lower():
                self.sistem_bildirimi_gonder(sahip, f"🔔 {KULLANICI} DİLEKÇELER için sıraya girdi. Dosyayı kapatmanızı bekliyor.")
        except Exception:
            self.debug.exception("sıra bekleyene bildirim gönderilemedi")

    def siradan_cik(self) -> None:
        self.sira.ayril(KULLANICI)

    def sirada_miyim(self) -> bool:
        return self.sira.bendemi(KULLANICI)

    def sira_uzunlugu(self) -> int:
        return len(self.sira.guncel_sira())

    def sira_listesi(self) -> List[str]:
        return self.sira.detayli_sira()

    def zorla_kaldir(self) -> bool:
        kayit = self.kilit.oku()
        onceki = kayit.kullanici if kayit else "bilinmiyor"
        sonuc = self.kilit.zorla_birak()
        self._olay_kaydet("ZORLA_KALDIRILDI", f"Önceki sahip: {onceki}")
        return sonuc

    def mesaj_gonder_mesaj(self, metin: str, hedef: str = "GENEL") -> Optional[Mesaj]:
        """Ağ JSONL yazımını yapar ve gerçek mesaj nesnesini döndürür.

        GUI'nin ağ yazımını worker thread'ine taşıyabilmesi için bool yerine
        gerçek Mesaj nesnesini de sunuyoruz. Mevcut mesaj_gonder API'si korunur.
        """
        mesaj = Mesaj.yeni(KULLANICI, hedef, metin)
        if self.mesajlar.gonder(mesaj):
            self._gorulen_mesaj_id.add(mesaj.id)
            self._son_gonderilen_mesaj = mesaj
            return mesaj
        return None

    def mesaj_gonder(self, metin: str, hedef: str = "GENEL") -> bool:
        return self.mesaj_gonder_mesaj(metin, hedef) is not None

    def son_gonderilen_mesaj_al(self) -> Optional[Mesaj]:
        m = self._son_gonderilen_mesaj
        self._son_gonderilen_mesaj = None
        return m

    def aktif_kullanicilar(self) -> List[Varlik]:
        return self.varlik.aktifler()

    def yedek_listesi(self):
        return self.yedek.liste()

    def son_yedek_zamani(self):
        return self.yedek.son_yedek_zamani()

    def teknik_ozet(self) -> str:
        import platform
        satirlar = [
            "ENÇEV Dosya Takip — Teknik Özet",
            f"Zaman: {datetime.now().isoformat(timespec='seconds')}",
            f"Kullanıcı: {KULLANICI}  •  Bilgisayar: {BILGISAYAR}",
            f"Python: {platform.python_version()}  •  Platform: {platform.platform()}",
            f"Ağ yolu erişilebilir: {self.s.ag_yolu and Path(self.s.ag_yolu).exists()}",
            f"Dosya var mı: {self.s.dosya_yolu.exists()}",
            f"Kilit durumu: {self.kilit.durum().value}",
        ]
        debug_dosya = self.s.sistem_klasoru / f"debug_{BILGISAYAR}.log"
        if debug_dosya.exists():
            try:
                son_satirlar = debug_dosya.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
                if son_satirlar:
                    satirlar.append("\nSon hata kayıtları:")
                    satirlar.extend(son_satirlar)
            except OSError:
                pass
        return "\n".join(satirlar)

    def _olay_kaydet(self, olay: str, detay: str, sure_sn: int = 0) -> None:
        self.gunluk.yaz(LogKaydi(
            zaman=datetime.now().isoformat(timespec="seconds"),
            kullanici=KULLANICI,
            bilgisayar=BILGISAYAR,
            olay=olay,
            detay=detay,
            sure_sn=sure_sn,
        ))
