# Sipariş e-posta bildirimleri

Alıcı: doktorhediye@hotmail.com. Başka bir kişisel e-posta adresi gerekmez.

## Bağlantı
1. Resend hesabını mevcut e-posta adresinizle açın. https://resend.com
2. Gönderici alan adını (tercihen bildirim.doktorhediye.com alt alanı) doğrulayın. Resend'in verdiği DNS kayıtlarını Türkticaret'e ekleyin; mevcut web kayıtlarını değiştirmeyin.
3. Yalnız gönderim yetkili bir API anahtarı oluşturun. Anahtarı GitHub'a veya sohbete koymayın.
4. Render > doktorhediye > Environment bölümünde aşağıdaki değerleri ayarlayın:
   - RESEND_API_KEY: servis anahtarı
   - MAIL_FROM: doğrulanmış gönderici adresi, örneğin siparis@bildirim.doktorhediye.com (önce doğrulama gerekir)
   - MAIL_TO: doktorhediye@hotmail.com
   - MAIL_ENABLED: yes
   - MAIL_NOTIFY_TEST: yes (yalnız kurulum testi için)
5. Kaydedip deploy edin. Açıkça TEST olarak işaretlenmiş örnek bir siparişle alıcı gelen kutusunu kontrol edin. Sonra MAIL_NOTIFY_TEST=no yapın.
6. PayTR canlıya geçtiğinde canlı siparişlerde otomatik çalışır. Ödeme kapalıyken test bildirimleri varsayılan olarak gönderilmez.

## Bildirimler
- Yeni sipariş: ödeme bekleniyor; üretim yapılmamalı.
- PayTR imzalı başarılı ödeme: ayrı doğrulama bildirimi.
- Alıcı yalnız mağaza yöneticisidir; hastalara ve doktorlara otomatik e-posta gönderilmez.
- E-posta sipariş numarası, tutar, ürünler ve teslim türünü içerir. İsim, telefon, adres, özel hediye notu ve erişim anahtarları gönderilmez. Ayrıntılar admin girişi gerektirir.

## Güvenilirlik
Bildirimler siparişle aynı SQLite işlemi içinde kalıcı kuyruğa yazılır. Gunicorn çalışanları kuyruk işleyicisini otomatik başlatır; ek ücretli worker servisi gerekmez. İki çalışan aynı kaydı eşzamanlı göndermez. Geçici hatalar gecikmeli tekrar denenir, sağlayıcıya aynı idempotency anahtarı iletilir. Yeniden başlatmada kuyruk korunur.

Resend idempotency anahtarları 24 saat geçerlidir. Belirsiz gönderimler 23 saatten sonra otomatik tekrarlanmaz; panelde kontrol gereken olarak görünür. Kalıcı sağlayıcı hataları ve sekiz başarısız deneme de manuel inceleme gerektirir. Bu kayıtlar için sağlayıcı panelindeki sonucu kontrol edin; gelişigüzel tekrar gönderim yapmayın.

Panelde "servise iletilen" sayısı alıcının gelen kutusuna teslim edildiğini garanti etmez; spam/bounce durumu Resend panelinden takip edilir. Anahtar değiştirilirse bekleyen mesajların gönderici/alıcı bilgileri, oluşturuldukları anki haliyle kalır.

Bağlantı kapalıyken geçmiş siparişler sonradan otomatik e-postalanmaz. Etkinleştirmeden sonra oluşan uygun kayıtlar kuyruğa alınır. API anahtarı olmadan MAIL_ENABLED=yes yapılmamalıdır.

Kaynaklar: https://resend.com/docs/api-reference/emails/send-email ve https://resend.com/docs/dashboard/emails/idempotency-keys
