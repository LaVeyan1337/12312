# ENÇEV Dosya Takip — v8.2

## Neden yeniden yazıldı? (v6/v7 -> v8)

Orijinal (`file_monitor.py`, tek dosya, ~3000 satır) üç temel sorun taşıyordu:

1. **Race condition:** kilit/mesaj bilgisi "oku → değiştir → tüm dosyayı
   geri yaz" şeklinde tutuluyordu; iki kişi aynı anda işlem yaparsa biri
   diğerini fark etmeden ezebiliyordu.
2. **Word'ün `~$` kilit dosyasının içeriği tahminle (byte offset
   deneyerek) parse ediliyordu.** Belgelenmemiş, kırılgan bir format —
   "hayalet kilit" (`Nazan Kaya — 11 gün 18 sa`) hatasının kök nedeni buydu.
3. **~200 yerde `except Exception: pass`** — bir şey bozulduğunda hiçbir
   iz kalmıyordu.

Detaylı mimari gerekçeler için her modülün başındaki docstring'lere bakın
(`lock_service.py`, `storage.py`, `waitqueue.py`, `backup.py`,
`single_instance.py` — hepsi "neden böyle" açıklaması içeriyor).

## v8.2'de neler var (kronolojik olarak eklenenler)

**Backend / mimari:**
- Atomik kilitleme (`O_CREAT|O_EXCL`), Word `~$` içeriği yerine sadece
  varlığına bakan self-healing kilit doğrulama
- JSON Lines (append-only) mesaj/log/sıra depolama — yarış durumu yok
- `config / models / lock_service / storage / docx_meta / activity /
  presence / autostart / local_prefs / waitqueue / backup /
  single_instance / service / gui` olarak modüler yapı
- Ayarlar: `settings.json` (şirket geneli, ağ paylaşımında) +
  `~/.enc_takip_tercihler.json` (kişisel, her bilgisayarda ayrı)

**Özellikler:**
- Hareketsizlik uyarısı (dosya açık ama Word'de işlem yoksa otomatik sorar)
- Özel + genel mesajlaşma, online kullanıcı listesi
- Erişim Geçmişi'nde filtre + Excel'e (CSV) dışa aktarma
- Uygulama içi Ayarlar penceresi (settings.json'ı elle düzenlemeye gerek yok)
- Sesli bildirim, sistem tepsisine küçültme, Windows açılışında otomatik başlatma
- **Bekleme sırası** — dosya kullanımdayken "🔔 Boşalınca Haber Ver"
  butonuna basan kişiye, dosya serbest kalınca otomatik özel mesaj gider
- **Otomatik yedekleme** — her kaydetmede arka planda zaman damgalı bir
  kopya alınır (son 30 sürüm saklanır), "Yedekler" penceresinden görülebilir
- **Tek-örnek koruması** — aynı bilgisayarda program yanlışlıkla iki kez
  açılamaz (çökme sonrası kalan ölü kilitler PID kontrolüyle otomatik temizlenir)
- **Bağlantı geri geldi bildirimi** — ağ koptuktan sonra yeniden
  ulaşılabilir olunca haber verir
- **"Teknik Bilgi Kopyala"** — destek isterken panoya kullanıcı/PC/ağ
  durumu/son hata kayıtlarını kopyalayan buton
- **Bildirim merkezi** — kaçırdığınız bildirimleri (🔔 ikonu) sonradan
  görebileceğiniz bir geçmiş paneli

**Arayüz:**
- Düz tkinter yerine **CustomTkinter** — yuvarlak köşeler, gerçek
  açık/koyu/sistem tema, modern switch'ler ve segmented button. Mesaj/log
  alanları bilinçli olarak düz `tkinter.Text` (renkli etiketleme için daha
  sağlam), geri kalan her şey CTk widget'ları.

## Önemli — bu ortamda test edemediğim kısım

Bu geliştirme ortamında **ne ekran ne de `customtkinter`/`tkinter`
paketleri kurulu** (internet erişimi yok). Bu yüzden:

- Backend/mantık katmanı (`tests/_manuel_dogrulama.py`) gerçekten
  çalıştırılıp doğrulandı — kilitleme, sıra, yedekleme, tek-örnek koruması
  dahil hepsi gerçek dosya sistemi üzerinde test edildi.
- GUI katmanı (`tests/_gui_duman_testi.py`) sahte bir tkinter +
  customtkinter (`tests/_tk_stub.py`, `tests/_ctk_stub.py`) ile inşa
  edilip tüm sekmeler, tüm durum renkleri, mesajlaşma, ayarlar/yedekler/
  bildirim merkezi pencereleri, sıra bekleme gerçekten çalıştırılarak
  test edildi — bu, NameError/AttributeError/yanlış argüman gibi kod
  hatalarını yakalar.
- **Ancak** gerçek `customtkinter` paketinin pikselde nasıl göründüğünü
  (renk tonları, boşluklar, gerçek animasyonlar) bu ortamda göremedim.
  Kodun çalışacağından eminim (yukarıdaki testler bunu gösteriyor) ama
  "gerçekten şık mı" sorusunun cevabını sizin makinenizde görmemiz gerekiyor.
  **Lütfen ilk çalıştırdığınızda göz atıp "şurası tuhaf durdu" derseniz
  hemen düzeltirim.**

## Çalıştırma

```
pip install -r requirements.txt
python main.py
```

İlk çalıştırmada `settings.json` yoksa otomatik oluşturulur.

## Test

```
pytest tests/ -v
```

pytest kurulu değilse:

```
python tests/_manuel_dogrulama.py     # backend (kilit/mesaj/log/sıra/yedek/tek-örnek)
python tests/_gui_duman_testi.py      # GUI — sahte tkinter+customtkinter ile ekran olmadan
```

## Kasıtlı olarak eklemediklerim

- **Birden fazla dosya izleme** — şu an tek bir dilekçe dosyasını
  izliyor. Mimari buna hazır (her `KilitServisi`/`DosyaTakipServisi` bir
  `Settings` örneğiyle çalışıyor), GUI'ye bir "dosya seçici" eklemek yeterli.
- Okunmamış mesaj/bildirim sayaçlarının yeniden başlatma sonrası
  hatırlanması — şu an sadece o oturum boyunca tutuluyor.
- Uygulamayı tek `.exe`'ye paketleme (`pyinstaller`) — aşağıdaki nota bakın.

## Dağıtım / kurulum

Windows dağıtımı için `build_windows.bat` kullanılabilir. Script önce gerekli Python paketlerini ve PyInstaller'ı kurar, sonra `dist\ENCTakip.exe` üretir. Bilgisayarda Inno Setup kuruluysa ayrıca `installer_out\ENCTakip_Kurulum.exe` oluşturur.

Kurulum paketi: kullanıcıların yalnızca `ENCTakip_Kurulum.exe` dosyasına çift tıklaması yeterlidir. Kurulum sonunda uygulama arka planda başlar ve sistem tepsisinde çalışır. Windows açılışında da `--background` parametresiyle sessizce başlatılır.

Not: Windows EXE üretimi Windows üzerinde yapılmalıdır; bu geliştirme ortamında Windows/PyInstaller çalıştırılamadığı için burada derlenmiş EXE üretmedim.

## Otomatik güncelleme

Kurulu program her 5 dakikada bir ağdaki şu klasörü kontrol eder:

```
<ag_yolu>\.encev_sistem\updates\latest.json
```

Örnek `latest.json`:

```json
{
  "version": "8.5",
  "path": "\\192.168.1.55\Genel Evrak\.encev_sistem\updates\ENCTakip_8.5.exe",
  "sha256": "..."
}
```

Yeni EXE önce `%TEMP%` içine kopyalanır ve SHA-256 değeri doğrulanır. Sonra `ENCTakipUpdater.exe` mevcut programı kapatıp yeni EXE'yi yerine koyar ve programı yeniden açar. Kullanıcının tekrar kurulum yapması gerekmez.

Yeni sürüm dağıtmak için yalnızca yeni `ENCTakip.exe` dosyasını ağdaki update klasörüne koyup `latest.json` dosyasını değiştirmek yeterlidir.

**Not:** Bu yapı şirket içi ağ paylaşımı üzerinden çalışır. İnternete açık bir güncelleme sunucusu kullanmak istenirse aynı mekanizma HTTPS + imzalı manifest ile de taşınabilir. SHA-256 bütünlük kontrolüdür; kaynağın gerçekten ENÇEV tarafından yayınlandığını kriptografik olarak kanıtlamak için ayrıca kod imzalama önerilir.

## Yeni sürüm yayınlama

Geliştirici bilgisayarında kodu değiştirip `enc_takip/version.py` içindeki `APP_VERSION` değerini artır. Ardından `build_windows.bat` çalıştır. Sonra `publish_update.bat` çalıştır. Bu script yeni EXE'yi ağdaki `.encev_sistem\updates` klasörüne kopyalar, SHA-256 üretir ve `latest.json` dosyasını günceller.

Kurulu istemciler en geç 5 dakika içinde yeni sürümü görür, EXE'yi indirip doğrular, kendisini kapatır ve `ENCTakipUpdater.exe` üzerinden yeni sürümü otomatik kurar. Kullanıcının tekrar kurulum yapmasına gerek yoktur.
