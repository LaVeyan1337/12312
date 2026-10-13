"""
Sahte tkinter (bkz. _tk_stub.py) ile Uygulama sınıfını gerçekten inşa
eder ve başlıca metodları çağırır. Böylece gerçek bir ekran olmadan,
GUI kodunun içinde bir NameError/AttributeError/yanlış argıman gibi
çalışma zamanı hatası olup olmadığını görebiliriz.
"""
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _tk_stub  # noqa: E402
import _ctk_stub  # noqa: E402

# tkinter'ı ve customtkinter'ı gerçek modüller yüklenmeden ÖNCE sahteyle değiştiriyoruz.
sys.modules["tkinter"] = _tk_stub.kur()
sys.modules["tkinter.font"] = sys.modules["tkinter"].font
sys.modules["tkinter.messagebox"] = sys.modules["tkinter"].messagebox
sys.modules["tkinter.filedialog"] = sys.modules["tkinter"].filedialog
sys.modules["customtkinter"] = _ctk_stub.kur()

from enc_takip.config import Settings  # noqa: E402
from enc_takip.gui.app import Uygulama  # noqa: E402
from enc_takip.models import DosyaDurumu  # noqa: E402
from enc_takip.service import DurumBilgisi  # noqa: E402


def test(ad, fn):
    try:
        fn()
        print(f"[OK ] {ad}")
    except Exception as e:
        print(f"[FAIL] {ad}: {type(e).__name__}: {e}")
        raise


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        klasor = tmp / "paylasim"
        klasor.mkdir()
        (klasor / "test.docx").write_bytes(b"sahte-docx")
        import enc_takip.config as cfg
        # KULLANICI, config modülü ilk import edildiğinde os.environ'dan bir
        # kez okunur (gerçek uygulamada da böyledir — çalışma anında
        # değişmez). Yönetici dallarını da test edebilmek için gerçek
        # KULLANICI değerini yönetici listesine ekliyoruz.
        settings = Settings(
            ag_yolu=str(tmp), dosya_klasoru="paylasim", dosya_adi="test.docx",
            yoneticiler=(cfg.KULLANICI,),
        )

        uygulama = None

        def kur():
            nonlocal uygulama
            uygulama = Uygulama(settings)

        test("Uygulama() inşa ediliyor (sidebar + tüm sekmeler)", kur)

        test("Dosya İzleme sekmesine geçiş", lambda: uygulama._sekme_degistir("izleme"))
        test("Mesajlar sekmesine geçiş", lambda: uygulama._sekme_degistir("mesaj"))
        test("Erişim Geçmişi sekmesine geçiş", lambda: uygulama._sekme_degistir("gunluk"))

        for durum in DosyaDurumu:
            d = DurumBilgisi(durum=durum, kullanici="ahmet", bilgisayar="PC1", acik_sure_sn=125)
            test(f"_durum_goster({durum.value})", lambda d=d: uygulama._durum_goster(d))

        test("Mesaj gönderme (GENEL)", lambda: (uygulama.ms_entry.insert("1.0", "merhaba ekip"), uygulama._mesaj_gonder()))
        test("Mesajları yükleme", uygulama._mesajlari_yukle)
        test("Kişi listesini yükleme", uygulama._kisi_listesini_yukle)

        test("Günlük yükleme", uygulama._gunlugu_yukle)
        test("Günlük filtre uygulama", lambda: (uygulama.gunluk_filtre_var.set("acildi"), uygulama._gunlugu_goster()))
        test("Günlük CSV dışa aktarma (boş yol -> no-op)", uygulama._gunlugu_disa_aktar)

        test("Hareketsizlik uyarı penceresi", lambda: uygulama._hareketsizlik_uyarisi_goster(300))
        test("Ayarlar penceresi (yönetici dahil)", uygulama._ayarlar_ac)
        test("Bildirim merkezi (boşken)", uygulama._bildirim_merkezi_ac)
        test("Bir bildirim üretme (toast + geçmiş + rozet)", lambda: uygulama._bildirim_goster("Test", "test mesajı", "bilgi"))
        test("Bildirim merkezi (dolu)", uygulama._bildirim_merkezi_ac)
        test("Yedekler penceresi (boşken)", uygulama._yedekler_ac)
        test("Teknik bilgi kopyalama", uygulama._teknik_bilgi_kopyala)
        test("Sıra göstergesi güncelleme", uygulama._sira_gostergesini_guncelle)
        test("Sıraya katıl / çık", lambda: (uygulama._sira_bas_bas(), uygulama._sira_bas_bas()))

        test("Bugün özeti hesaplama", uygulama.servis.bugun_ozeti)

        test("Kuyruk işleme (DURUM olayı)", lambda: (
            uygulama.kuyruk.put(("DURUM", DurumBilgisi(durum=DosyaDurumu.MUSAIT))),
            uygulama._kuyruk_isle(),
        ))

        print("\nTÜM GUI DUMAN TESTLERİ GEÇTİ ✅")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
