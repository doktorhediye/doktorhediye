# Render'da yayınlama

Bu proje mevcut GitHub Pages görünümünü korur. Python backend ve kalıcı sipariş kaydı için Render Web Service kullanılır. PayTR ilk yayında kapalıdır.

## Kolay kurulum: Blueprint

1. Render panelinde **New → Blueprint** seçin.
2. `doktorhediye/doktorhediye` deposunu ve `main` dalını seçin.
3. Render kökteki `render.yaml` ayarlarını okur. Site ve sipariş depolaması tek hizmette hazırlanır.
4. Gösterilen aylık hizmet + disk ücretini kontrol edip onayladığınızda oluşturun. Bu ücretli bir kurulumdur; ücretsiz seçenek siparişleri kalıcı saklamaz.
5. Yayın tamamlanınca Render'ın verdiği `https://...onrender.com` adresini açın. Yönetim ekranı aynı adresin `/admin` yolundadır.
6. Render → Environment bölümündeki otomatik oluşturulmuş `ADMIN_TOKEN`, yönetim giriş kodudur. GitHub'a veya mesajlara yazmayın. `APP_SECRET` değerini değiştirmeyin.

PUBLIC_URL ilk kurulumda Render'ın RENDER_EXTERNAL_URL değerinden otomatik alınır. Daha sonra doktorhediye.com bağlanınca PUBLIC_URL=https://doktorhediye.com ayarlayın.

Açık olan “New Web Service” formunu kullanmak yerine Blueprint seçeneğini kullanmak, ayarların tek tek girilmesini önler. Otomatik yayın kapalıdır; sonraki kod güncellemeleri kontrol edilerek elle yayımlanır.

## Ne çalışır?

Tasarım, görseller ve doktor dizini korunur. Deneme siparişleri kalıcı diske kaydolur; yönetim ekranında görünür. Gerçek ödeme, PayTR ve şirket bilgileri eklenip test edilene kadar kapalıdır. Denemede yalnız örnek müşteri bilgileri girin.

PayTR ve satışa açılış adımları BACKEND-KURULUM.md içindedir. Doktor araması, ölçü ve kargo işlemleri ilk sürümde manuel yürütülür.

## Dosya düzeni

GitHub Pages için HTML/JS/CSS, doktor listeleri ve assets kökte kalır. Render derlemesinde backend/prepare_static.py yalnız site dosyalarını dist/ içine kopyalar. Backend sadece dist/ dosyalarını sunar; kaynak kodu, anahtarları veya veritabanını sunmaz. Yerel geliştirmede mevcut dist/ kullanılabilir.

Veritabanı /var/data/orders.sqlite3 konumundadır. Disk ve gizli ortam değişkenleri GitHub'a aktarılmaz. Düzenli güvenli yedekleme ve geri yükleme denemesi yayın işletiminin parçasıdır.

Resmi belgeler: https://render.com/docs/blueprint-spec ve https://render.com/docs/disks
