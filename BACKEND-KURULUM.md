# Doktor Hediye — PayTR backend

Mevcut tasarım, 373 kişilik seçim listesi ve 427 kaynak kaydını içeren dizin korunmuştur. Backend Python 3.10+ ve SQLite kullanır. GitHub Pages statik önizleme olarak kalır; Python çalıştırmaz. Bu kod henüz canlı sunucuya kurulmadı ve gerçek PayTR hesabıyla uçtan uca test edilmedi.

## Yerelde çalıştırma

Proje kökünde:

```sh
python3 -m backend.dev
```

Site: http://127.0.0.1:4180
Yönetim: http://127.0.0.1:4180/admin

İlk çalıştırma `.env.local` içinde rastgele APP_SECRET ve ADMIN_TOKEN oluşturur. Yönetim ekranına ADMIN_TOKEN değerini girin. Dosyayı, anahtarları ve veritabanını GitHub'a yüklemeyin. Yerelde örnek müşteri bilgileri kullanın. Ödeme kapalıdır; deneme siparişleri backend/runtime/orders.sqlite3 içinde kalıcı kaydolur. `.env.local` yalnız geliştirme komutuyla yüklenir; üretimde değişkenleri hosting yönetiminden verin.

Ana sayfada doktor ve ürün seçildikten sonra backend bulunan ortamda checkout.html açılır. Backend olmayan statik yayında önceki önizleme akışı devam eder.

Testler:

```sh
python3 -m unittest discover -s backend/tests -v
```

## Hazır olanlar

- Sunucudaki ürün kataloğundan kuruş cinsinden fiyat hesaplama; istemcinin tutar göndermesi reddedilir.
- Kol düğmesi yalnız çift manşetli ürüne eklenir. Doktor kaydı sunucuda doğrulanır.
- Sipariş, gönderen bilgileri ve hediye notu kalıcı kaydedilir. Siparişe doktor/ürün anlık kopyası yazılır.
- Aynı istek anahtarı aynı siparişi döndürür; farklı içerik çakışması reddedilir.
- PayTR iFrame oturumu sunucudan oluşturulur. Kart bilgileri bu uygulamaya gelmez.
- Ödeme sadece imzalı sunucudan sunucuya PayTR bildirimiyle onaylanır. Başarı sayfasına dönmek onay değildir.
- Tekrarlanan bildirim yeniden üretim/sipariş oluşturmaz; ortam, tutar ve para birimi doğrulanır.
- Müşteri sipariş numarası + gizli erişim koduyla durumunu sorgular. Tarayıcıya kişi/adres/not kaydedilmez.
- Yönetici siparişleri, notları ve gönderen bilgilerini görür; kabul → ölçü → dikim → kargo → teslim aşamalarını günceller. Kargo aşamasında takip bilgisi gerekir. Ödenmeyen sipariş üretime alınamaz.
- İade talebi ayrı bir durumdur; otomatik para iadesi yapmaz.

## PayTR hesabı hazır olduğunda

Şahıs işletmeniz adına PayTR başvurusu yapın. Üye işyeri kabulü ve istenen belgeler PayTR değerlendirmesine bağlıdır. Panelde verilen Merchant ID, Merchant Key ve Merchant Salt değerlerini yalnız sunucunun gizli ortam değişkenlerine girin.

1. Site ve backend'i aynı HTTPS alan adı altında Python/Docker destekleyen sunucuda yayınlayın. Örneğin PUBLIC_URL=https://doktorhediye.com. GitHub Pages bu iş için yeterli değildir.
2. PAYMENT_MODE=test ayarlayın ve üç PayTR bilgisini ekleyin. PUBLIC_URL kök alan adı olmalı; sonuna /doktorhediye veya /api eklemeyin.
3. PayTR panelinde bildirim URL'sini `https://doktorhediye.com/api/paytr/callback` olarak tanımlayın. Adres internete açık ve POST kabul eder olmalı.
4. Proxy istemci IP'sini güvenli iletmeli. TRUSTED_PROXY_IP yalnız uygulamanın gerçekten gördüğü güvenilir proxy IP'si olsun. Proxy gelen X-Real-IP değerini silip gerçek istemci IP'siyle yeniden yazmalı. Rastgele bir başlığa güvenmeyin. Test için PAYTR_TEST_USER_IP dış IP ile kullanılabilir.
5. PayTR test kartlarıyla başarılı, başarısız ve tekrarlanan bildirim senaryolarını deneyin. Test siparişi yönetimde TEST olarak görünür. Gerçek kart kullanmayın.
6. Gerçek ürün/fiyatları backend/catalog.json içinde kuruş cinsinden belirleyin. Ana sayfanın örnek fiyat yazıları da aynı değerlerle güncellenmeli. Kargo bedeli hesaplaması yok; ürün fiyatınıza dahil politikanızı netleştirin.
7. İşletme/iletişim bilgileri, müşteriye gösterilecek satış ve veri işleme metinleri, teslimat süreleri, doktor kabulü ve iade işleyişi tamamlanmalı. Mevcut örnek/önizleme açıklamalarını gerçek işleyişe göre güncelleyin.
8. Gerçek satışa geçerken katalogdaki prices_confirmed=true, LIVE_SALES_CONFIRMED=yes, PAYMENT_MODE=live ve APP_ENV=production ayarlayın. Çift kilit yanlışlıkla canlı tahsilatı önler; iş gerekliliklerinin otomatik denetimi değildir.

Resmi entegrasyon: [iFrame 1. adım](https://dev.paytr.com/iframe-api/iframe-api-1-adim), [bildirim / 2. adım](https://dev.paytr.com/iframe-api/iframe-api-2-adim).

## Sunucu

Dockerfile veya standart Python hosting kullanılabilir:

```sh
pip install -r backend/requirements.txt
gunicorn --bind 0.0.0.0:8080 --workers 2 --timeout 40 'backend.app:create_app()'
```

Sunucu HTTPS ters proxy arkasında olmalı. APP_SECRET ve ADMIN_TOKEN için farklı en az 32 karakterlik rastgele değerler kullanın. APP_SECRET değişirse eski müşteri erişim kodları geçersiz olur. Yönetici anahtarı sadece yetkili kişide bulunmalı; ekip büyürse kullanıcı bazlı giriş ve MFA eklenmeli.

DATABASE_PATH kalıcı diske bağlanmalı (Docker'da /data). Disk UID 10001 kullanıcısı tarafından yazılabilir olmalı. Tek uygulama kurulumu ve yerel kalıcı disk içindir; geçici/serverless disk veya birden fazla bağımsız sunucuya dağıtmayın. Düzenli SQLite backup API yedekleri, erişim sınırı, şifreli disk/yedek ve geri yükleme testi sağlayın. Çalışan SQLite dosyasını WAL dosyalarından bağımsız kopyalamayın.

Sunucu yeniden başlatılınca siparişler aynı veritabanından açılır. Kaynak kod GitHub'da; sipariş verileri özel sunucuda tutulur. Veri saklama/silme sürecini satıştan önce belirleyin.

## Sınırlar ve sonraki işler

- Bu ilk sürümde doktoru arama, kabul alma, ölçü toplama, üretim ve kargo yönetimi insan tarafından yürütülür. Otomatik e-posta/SMS ve kargo firması bağlantısı yoktur.
- İade talebi gerçek iade değildir; PayTR panelinden işlem ve mutabakat gerekir. İade API'si eklenmemiştir.
- PayTR oturumu açılırken zaman aşımı olursa veya 25 dakikalık tekrar kullanım süresi geçerse yeni ödeme otomatik başlatılmaz. Çift tahsilatı önlemek için PayTR panelinden kontrol ederek destek süreci yürütün.
- Taksit kapalıdır; başarılı bildirimin tutarı sipariş tutarıyla tam eşleşmelidir. Taksit açılacaksa mutabakat mantığı ayrıca değişmelidir.
- Yönetim ekranı en son 200 siparişi gösterir. Büyümede sayfalama/arama, kullanıcı bazlı yetki ve gelişmiş izleme gerekir.
- Doktor kayıtlarının kamuya açık olması katılım veya hediye kabulü onayı anlamına gelmez. Sipariş akışı doktor kabulü aşamasını bu yüzden ayrıca tutar.

## API

| Yöntem | Yol | Erişim |
|---|---|---|
| GET | /api/health | Genel |
| GET | /api/storefront | Katalog, ödeme modu |
| POST | /api/orders | Idempotency-Key başlığı; doktor/ürün/gönderen/not |
| GET | /api/orders/{id} | Bearer sipariş erişim kodu |
| POST | /api/orders/{id}/payment | Bearer sipariş erişim kodu |
| POST | /api/paytr/callback | PayTR imzalı form bildirimi |
| GET | /api/admin/orders | Bearer ADMIN_TOKEN |
| PATCH | /api/admin/orders/{id} | Bearer ADMIN_TOKEN; stage, version, tracking |
