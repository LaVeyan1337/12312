"""
pytest kurulu değilken de mantığı doğrulamak için bağımsız betik.
Çalıştırma: python tests/_manuel_dogrulama.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enc_takip.config import Settings
from enc_takip.lock_service import KilitServisi
from enc_takip.models import DosyaDurumu
from enc_takip.storage import MesajDeposu, OlayGunlugu
from enc_takip.models import Mesaj, LogKaydi
from enc_takip.waitqueue import BeklemeSirasi
from enc_takip.backup import YedekYoneticisi
from enc_takip.single_instance import TekOrnekKilidi


def gecici_settings(tmp: Path) -> Settings:
    klasor = tmp / "paylasim"
    klasor.mkdir()
    (klasor / "test.docx").write_bytes(b"sahte-docx")
    return Settings(ag_yolu=str(tmp), dosya_klasoru="paylasim", dosya_adi="test.docx", kilit_olu_esigi_sn=1)


def test(ad, kosul):
    durum = "OK " if kosul else "FAIL"
    print(f"[{durum}] {ad}")
    if not kosul:
        raise SystemExit(1)


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        s = gecici_settings(tmp)

        k1 = KilitServisi(s)
        test("boş dosya müsait görünür", k1.durum() == DosyaDurumu.MUSAIT)

        test("kilit alınabiliyor", k1.al() is True)
        test("kilit alınca kullanımda_bende görünür", k1.durum() == DosyaDurumu.KULLANIMDA_BENDE)

        k2 = KilitServisi(s)
        test("aynı kilit ikinci kez alınamıyor (atomiklik)", k2.al() is False)

        k1.birak()
        test("bırakılan kilit müsait olur", k1.durum() == DosyaDurumu.MUSAIT)

        # Hayalet kilit senaryosu
        k1.al()
        time.sleep(1.2)
        test("heartbeat eskiyip Word kanıtı yoksa kilit kendini temizler", k1.durum() == DosyaDurumu.MUSAIT)

        # Word dışarıdan açılmış senaryosu
        klasor = Path(s.ag_yolu) / s.dosya_klasoru
        (klasor / f"~${s.dosya_adi}").write_bytes(b"dummy")
        k3 = KilitServisi(s)
        test("Word ~$ var ama app kilidi yoksa 'belirsiz' gösterilir", k3.durum() == DosyaDurumu.BELIRSIZ)

        # Depolama katmanı
        mesaj_deposu = MesajDeposu(tmp / "mesajlar.jsonl")
        m1 = Mesaj.yeni("ahmet", "GENEL", "merhaba")
        m2 = Mesaj.yeni("burak", "GENEL", "selam")
        mesaj_deposu.gonder(m1)
        mesaj_deposu.gonder(m2)
        okunan = mesaj_deposu.tumunu_oku()
        test("mesajlar sırayla ve eksiksiz okunuyor", [m.metin for m in okunan] == ["merhaba", "selam"])

        gunluk = OlayGunlugu(tmp / "gunluk.jsonl")
        gunluk.yaz(LogKaydi(zaman="2026-09-15T10:00:00", kullanici="burak", bilgisayar="PC1", olay="ACILDI"))
        test("günlük kaydı okunuyor", len(gunluk.tumunu_oku()) == 1)

        # Bekleme sırası
        sira = BeklemeSirasi(tmp / "sira.jsonl")
        sira.katil("ahmet")
        sira.katil("mehmet")
        test("sıraya katılanlar doğru sırada", sira.guncel_sira() == ["ahmet", "mehmet"])
        test("aynı kişi ikinci kez katılınca tekrarlanmıyor", (sira.katil("ahmet"), sira.guncel_sira())[1] == ["ahmet", "mehmet"])
        ilk = sira.ilkini_bildir_ve_cikar()
        test("ilk bildirilen doğru kişi", ilk == "ahmet")
        test("bildirilen kişi sıradan çıkıyor", sira.guncel_sira() == ["mehmet"])

        # Otomatik yedekleme
        yedek_klasoru = tmp / "yedek_test"
        yedek_klasoru.mkdir()
        (yedek_klasoru / "belge.docx").write_bytes(b"icerik-v1")
        yedek_settings = Settings(ag_yolu=str(tmp), dosya_klasoru="yedek_test", dosya_adi="belge.docx")
        yedek = YedekYoneticisi(yedek_settings, azami_adet=2)
        test("ilk yedek alınabiliyor", yedek.yedekle() is True)
        time.sleep(1.1)
        (yedek_klasoru / "belge.docx").write_bytes(b"icerik-v2")
        yedek.yedekle()
        time.sleep(1.1)
        (yedek_klasoru / "belge.docx").write_bytes(b"icerik-v3")
        yedek.yedekle()
        test("azami sayı aşılınca eski yedekler silinir", len(yedek.liste()) == 2)
        test("en yeni yedek en başta", yedek.liste()[0].read_bytes() == b"icerik-v3")

        # Tek örnek koruması
        kilit1 = TekOrnekKilidi(ad=f"enc_takip_test_{tmp.name}")
        kilit2 = TekOrnekKilidi(ad=f"enc_takip_test_{tmp.name}")
        test("ilk örnek kilidi alabiliyor", kilit1.al() is True)
        test("ikinci örnek (aynı süreç canlıyken) alamıyor", kilit2.al() is False)
        kilit1.birak()
        test("bırakılınca yeniden alınabiliyor", kilit2.al() is True)
        kilit2.birak()

        print("\nTÜM TESTLER GEÇTİ ✅")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
