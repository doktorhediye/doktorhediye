"""Dependency-free WSGI backend. Use a production WSGI server behind HTTPS."""
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from . import paytr

ROOT = Path(__file__).resolve().parent.parent
STAGES = ['awaiting_acceptance', 'measurements', 'tailoring', 'shipped', 'delivered']

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

@dataclass
class Config:
    db_path: str
    secret: str
    admin_token: str = ''
    public_url: str = 'http://127.0.0.1:4180'
    mode: str = 'disabled'
    merchant_id: str = ''
    merchant_key: str = ''
    merchant_salt: str = ''
    live_confirmed: bool = False
    trusted_proxy_ip: str = ''
    test_user_ip: str = ''
    production: bool = False

    @classmethod
    def from_env(cls):
        return cls(db_path=os.getenv('DATABASE_PATH', str(ROOT/'backend/runtime/orders.sqlite3')),
            secret=os.getenv('APP_SECRET', ''), admin_token=os.getenv('ADMIN_TOKEN', ''),
            public_url=(os.getenv('PUBLIC_URL') or os.getenv('RENDER_EXTERNAL_URL', 'http://127.0.0.1:4180')).rstrip('/'),
            mode=os.getenv('PAYMENT_MODE', 'disabled'), merchant_id=os.getenv('PAYTR_MERCHANT_ID', ''),
            merchant_key=os.getenv('PAYTR_MERCHANT_KEY', ''), merchant_salt=os.getenv('PAYTR_MERCHANT_SALT', ''),
            live_confirmed=os.getenv('LIVE_SALES_CONFIRMED') == 'yes',
            trusted_proxy_ip=os.getenv('TRUSTED_PROXY_IP', ''), test_user_ip=os.getenv('PAYTR_TEST_USER_IP', ''),
            production=os.getenv('APP_ENV') == 'production')

class App:
    def __init__(self, config, provider=paytr.request_token):
        self.config, self.provider = config, provider
        if len(config.secret) < 32:
            raise ValueError('APP_SECRET must contain at least 32 random characters')
        if config.admin_token and len(config.admin_token) < 32:
            raise ValueError('ADMIN_TOKEN must contain at least 32 random characters')
        if config.mode not in ('disabled', 'test', 'live'):
            raise ValueError('PAYMENT_MODE must be disabled, test or live')
        url = urlsplit(config.public_url)
        if url.scheme not in ('http', 'https') or not url.netloc or url.path not in ('', '/') or url.query or url.fragment or url.username:
            raise ValueError('PUBLIC_URL must be an origin without path or credentials')
        if (config.production or config.mode != 'disabled') and url.scheme != 'https':
            raise ValueError('HTTPS PUBLIC_URL required for production and PayTR')
        if config.mode != 'disabled' and not all((config.merchant_id, config.merchant_key, config.merchant_salt)):
            raise ValueError('PayTR credentials are required')
        for value in (config.test_user_ip, config.trusted_proxy_ip):
            if value: ipaddress.ip_address(value)
        self.catalog = json.loads((ROOT/'backend/catalog.json').read_text())
        if config.mode == 'live' and not (config.live_confirmed and self.catalog['prices_confirmed']):
            raise ValueError('Live sales and actual catalog prices must be explicitly confirmed')
        self.doctors = {d['id']:d for d in json.loads((ROOT/'dist/doctors.json').read_text())}
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS orders (
              id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
              payload TEXT NOT NULL, amount INTEGER NOT NULL, mode TEXT NOT NULL,
              payment TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT 'awaiting_payment',
              session_state TEXT NOT NULL DEFAULT 'new', token TEXT, token_expires INTEGER,
              tracking TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0,
              created INTEGER NOT NULL, updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
              kind TEXT NOT NULL, detail TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_limits (
              bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL);
            ''')
        os.chmod(config.db_path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.config.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def access_token(self, oid):
        return hmac.new(self.config.secret.encode(), ('order:'+oid).encode(), hashlib.sha256).hexdigest()

    def get_order(self, db, oid):
        row = db.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not row: raise Problem(404, 'Sipariş bulunamadı.')
        return row

    def authorized(self, env, expected):
        supplied = env.get('HTTP_AUTHORIZATION', '')
        return bool(expected) and hmac.compare_digest(supplied.encode(), ('Bearer '+expected).encode())

    def require_order(self, env, oid):
        if not self.authorized(env, self.access_token(oid)):
            raise Problem(401, 'Sipariş erişim kodu geçersiz.')

    def admin(self, env):
        if not self.authorized(env, self.config.admin_token):
            raise Problem(401, 'Yönetici erişimi gerekli.')

    def ip(self, env):
        value = env.get('REMOTE_ADDR', '')
        # Enable only when the named proxy strips client-provided X-Real-IP.
        if self.config.trusted_proxy_ip and value == self.config.trusted_proxy_ip:
            value = env.get('HTTP_X_REAL_IP', '')
        try: return str(ipaddress.ip_address(value))
        except ValueError: raise Problem(400, 'İstemci IP adresi doğrulanamadı.')

    def rate_limit(self, ip, group, limit):
        now = int(time.time()); bucket = hashlib.sha256((group+ip).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM rate_limits WHERE expires<?', (now,))
            row = db.execute('SELECT count FROM rate_limits WHERE bucket=?', (bucket,)).fetchone()
            if row and row[0] >= limit: raise Problem(429, 'Çok fazla istek. Birkaç dakika sonra deneyin.')
            db.execute('INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (bucket, now+600))

    def read(self, env, form=False):
        try: size = int(env.get('CONTENT_LENGTH', '0'))
        except ValueError: raise Problem(400, 'Geçersiz içerik boyutu.')
        if size < 1 or size > 16384: raise Problem(413, 'İstek boyutu geçersiz.')
        expected = 'application/x-www-form-urlencoded' if form else 'application/json'
        if env.get('CONTENT_TYPE', '').split(';')[0] != expected:
            raise Problem(415, 'İçerik türü desteklenmiyor.')
        try:
            raw = env['wsgi.input'].read(size).decode('utf-8')
            if form:
                values = parse_qs(raw, keep_blank_values=True, max_num_fields=30)
                if any(len(v)!=1 for v in values.values()): raise ValueError()
                return {k:v[0] for k,v in values.items()}
            data = json.loads(raw)
            if not isinstance(data, dict): raise ValueError()
            return data
        except (ValueError, UnicodeError): raise Problem(400, 'İstek biçimi geçersiz.')

    def validate(self, data):
        allowed = {'doctor_id','product_id','cufflinks','buyer','note'}
        if set(data)-allowed: raise Problem(400, 'Bilinmeyen sipariş alanı; tutarı sunucu hesaplar.')
        doctor_id = data.get('doctor_id')
        if not isinstance(doctor_id, str) or doctor_id not in self.doctors:
            raise Problem(400, 'Geçerli bir doktor seçin.')
        product = next((p for p in self.catalog['products'] if p['id']==data.get('product_id')), None)
        if not product: raise Problem(400, 'Ürün bulunamadı.')
        cufflinks = data.get('cufflinks', False)
        if not isinstance(cufflinks, bool) or (cufflinks and not product['cufflinks']):
            raise Problem(400, 'Kol düğmesi yalnız çift manşetli modelle seçilebilir.')
        buyer = data.get('buyer')
        if not isinstance(buyer, dict) or set(buyer) != {'name','email','phone','address'}:
            raise Problem(400, 'Gönderen bilgilerini tamamlayın.')
        clean = {}
        for key, maximum in [('name',60),('email',100),('phone',20),('address',400)]:
            value = buyer[key]
            if not isinstance(value,str) or not 2 <= len(value.strip()) <= maximum or any(ord(c)<32 for c in value):
                raise Problem(400, 'Gönderen bilgileri geçersiz: '+key)
            clean[key] = value.strip()
        if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', clean['email']):
            raise Problem(400, 'Geçerli e-posta adresi girin.')
        if not re.fullmatch(r'\+?[0-9 ()-]{8,20}', clean['phone']):
            raise Problem(400, 'Geçerli telefon numarası girin.')
        note = data.get('note','')
        if not isinstance(note,str) or len(note)>400: raise Problem(400, 'Not en fazla 400 karakter olabilir.')
        items = [dict(id=product['id'], name=product['name'], price=product['price'])]
        if cufflinks: items.append(self.catalog['accessory'].copy())
        doctor = self.doctors[doctor_id]
        return dict(doctor={k:doctor[k] for k in ('id','name','title','branch','institution')},
                    items=items, buyer=clean, note=note.strip())

    def create_order(self, env):
        self.rate_limit(self.ip(env), 'create', 20)
        key = env.get('HTTP_IDEMPOTENCY_KEY','')
        if not re.fullmatch(r'[A-Za-z0-9-]{32,80}',key): raise Problem(400,'Benzersiz istek anahtarı gerekli.')
        payload = self.validate(self.read(env))
        encoded = json.dumps(payload,ensure_ascii=False,sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM orders WHERE idem=?',(key,)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint: raise Problem(409,'Bu istek anahtarı farklı bir siparişe ait.')
                oid = row['id']
            else:
                oid = 'DH'+secrets.token_hex(16); now=int(time.time())
                db.execute('INSERT INTO orders(id,idem,fingerprint,payload,amount,mode,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (oid,key,fingerprint,encoded,sum(i['price'] for i in payload['items']),self.config.mode,now,now))
                self.event(db,oid,'created','Sipariş kaydedildi; ödeme bekleniyor.')
            row = self.get_order(db,oid)
        return 201, dict(order=self.public_order(row), access_token=self.access_token(oid))

    def event(self, db, oid, kind, detail):
        db.execute('INSERT INTO events(order_id,kind,detail,created) VALUES(?,?,?,?)',
                   (oid,kind,detail,int(time.time())))

    def public_order(self, row):
        p = json.loads(row['payload'])
        return dict(id=row['id'],amount=row['amount'],currency='TL',payment=row['payment'],
                    stage=row['stage'],mode=row['mode'],items=p['items'],doctor=p['doctor'],
                    tracking=row['tracking'],version=row['version'],created=row['created'])

    def start_payment(self, env, oid):
        self.require_order(env,oid)
        ip=self.ip(env); self.rate_limit(ip,'payment',40)
        if self.config.mode == 'disabled': raise Problem(503,'PayTR henüz bağlı değil; ödeme alınmıyor.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE'); row=self.get_order(db,oid)
            if row['mode'] != self.config.mode: raise Problem(409,'Ödeme ortamı değişmiş; yeni sipariş oluşturun.')
            if row['payment'] != 'pending': raise Problem(409,'Bu sipariş için ödeme işlemi sonuçlandı.')
            if row['session_state']=='ready' and row['token_expires']>time.time():
                return 200, dict(iframe_url='https://www.paytr.com/odeme/guvenli/'+row['token'])
            if row['session_state'] != 'new':
                # Do not silently retry an ambiguous provider call or create a second payment attempt.
                raise Problem(409,'Ödeme oturumu başlatılmış veya süresi dolmuş. Destek ile kontrol edin.')
            db.execute("UPDATE orders SET session_state='requesting',updated=? WHERE id=?",(int(time.time()),oid))
        p=json.loads(row['payload']);p.update(id=oid,amount=row['amount'])
        if self.config.mode=='test' and self.config.test_user_ip:
            try: ip=str(ipaddress.ip_address(self.config.test_user_ip))
            except ValueError: raise Problem(503,'Test IP ayarı geçersiz.')
        try: token=self.provider(self.config,p,ip)
        except Exception:
            with self.db() as db:
                db.execute("UPDATE orders SET session_state='uncertain' WHERE id=?",(oid,))
                self.event(db,oid,'payment_session_uncertain','PayTR oturumu doğrulanamadı; manuel kontrol gerekli.')
            raise Problem(502,'Ödeme servisi yanıtı doğrulanamadı. Yeni ödeme yapmadan destek ile kontrol edin.')
        with self.db() as db:
            db.execute("UPDATE orders SET session_state='ready',token=?,token_expires=? WHERE id=? AND payment='pending'",(token,int(time.time())+25*60,oid))
        return 200, dict(iframe_url='https://www.paytr.com/odeme/guvenli/'+token)

    def callback(self, env):
        if self.config.mode == 'disabled': raise Problem(503,'Ödeme kapalı.')
        f=self.read(env,form=True)
        if not paytr.verify_callback(self.config,f): raise Problem(400,'Geçersiz bildirim imzası.')
        if f['status'] not in ('success','failed') or not re.fullmatch(r'[0-9]{1,12}',f['total_amount']):
            raise Problem(400,'Geçersiz bildirim.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=self.get_order(db,f['merchant_oid'])
            expected='paid' if f['status']=='success' else 'failed'
            if row['mode'] != self.config.mode: raise Problem(409,'Ödeme ortamı uyuşmuyor.')
            if row['session_state']=='new': raise Problem(409,'Ödeme oturumu oluşturulmamış.')
            if f.get('test_mode','0') != ('1' if row['mode']=='test' else '0'):
                raise Problem(400,'Test/canlı ortam uyuşmuyor.')
            if f.get('currency') not in ('TL','TRY') or f.get('payment_amount') != str(row['amount']):
                raise Problem(400,'Sipariş tutarı veya para birimi uyuşmuyor.')
            # Installments are disabled; any over/underpayment needs manual reconciliation.
            if f['status']=='success' and int(f['total_amount']) != row['amount']:
                raise Problem(400,'Tahsilat tutarı uyuşmuyor.')
            if row['payment'] != 'pending':
                if row['payment'] != expected: raise Problem(409,'Çelişkili ödeme sonucu; manuel kontrol gerekli.')
                return 200,'OK'
            db.execute('UPDATE orders SET payment=?,stage=?,token=NULL,version=version+1,updated=? WHERE id=?',
                       (expected,'awaiting_acceptance' if expected=='paid' else 'payment_failed',int(time.time()),row['id']))
            self.event(db,row['id'],'payment_'+expected,'PayTR imzalı bildirimi doğrulandı.')
        return 200,'OK'

    def update_stage(self, env, oid):
        self.admin(env);data=self.read(env)
        if set(data)-{'stage','version','tracking'}: raise Problem(400,'Bilinmeyen alan.')
        if type(data.get('version')) is not int: raise Problem(400,'Kayıt sürümü gerekli.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=self.get_order(db,oid)
            if data['version']!=row['version']: raise Problem(409,'Sipariş değişmiş; listeyi yenileyin.')
            if row['payment']!='paid': raise Problem(409,'Ödenmemiş sipariş üretime alınamaz.')
            current=row['stage'];target=data.get('stage')
            next_stage=STAGES[STAGES.index(current)+1] if current in STAGES[:-1] else None
            # A rejection is a refund request, never an assertion that money was refunded.
            allowed=[next_stage] if next_stage else []
            if current in STAGES[:3]:allowed.append('refund_requested')
            if target not in allowed: raise Problem(409,'Bu aşama geçişine izin verilmiyor.')
            tracking=data.get('tracking',row['tracking'])
            if not isinstance(tracking,str) or len(tracking)>160 or (target=='shipped' and not tracking.strip()):
                raise Problem(400,'Kargo firması ve takip numarası gerekli.')
            db.execute('UPDATE orders SET stage=?,tracking=?,version=version+1,updated=? WHERE id=?',
                       (target,tracking.strip(),int(time.time()),oid))
            self.event(db,oid,'stage_changed',target)
            result=self.public_order(self.get_order(db,oid))
        return 200,result

    def route(self, env):
        method=env['REQUEST_METHOD'];path=env.get('PATH_INFO','/')
        origin=env.get('HTTP_ORIGIN')
        if origin and origin!=self.config.public_url: raise Problem(403,'Kaynak izinli değil.')
        if method=='GET' and path=='/api/health':return 200,{'status':'ok'}
        if method=='GET' and path=='/api/storefront':return 200,{'checkout':True,'payment_mode':self.config.mode,'catalog':self.catalog}
        if method=='POST' and path=='/api/orders':return self.create_order(env)
        if method=='POST' and path=='/api/paytr/callback':return self.callback(env)
        match=re.fullmatch(r'/api/orders/(DH[a-f0-9]{32})(/payment)?',path)
        if match:
            oid,action=match.groups()
            if method=='POST' and action:return self.start_payment(env,oid)
            if method=='GET' and not action:
                self.rate_limit(self.ip(env),'read',120);self.require_order(env,oid)
                with self.db() as db:return 200,self.public_order(self.get_order(db,oid))
        if path.startswith('/api/admin/'):
            self.rate_limit(self.ip(env),'admin',120);self.admin(env)
            if method=='GET' and path=='/api/admin/orders':
                with self.db() as db:
                    rows=db.execute('SELECT * FROM orders ORDER BY created DESC,id DESC LIMIT 200').fetchall()
                    return 200,{'orders':[dict(self.public_order(r),buyer=json.loads(r['payload'])['buyer'],note=json.loads(r['payload'])['note']) for r in rows]}
            match=re.fullmatch(r'/api/admin/orders/(DH[a-f0-9]{32})',path)
            if method=='PATCH' and match:return self.update_stage(env,match[1])
        if path.startswith('/api/'):raise Problem(404,'Uç nokta bulunamadı.')
        if method not in ('GET','HEAD'):raise Problem(405,'Yöntem desteklenmiyor.')
        if path=='/admin':return 200,(ROOT/'backend/admin.html')
        if path=='/admin.js':return 200,(ROOT/'backend/admin.js')
        relative=path.lstrip('/') or 'index.html'
        # Only frontend files may be served; never the repository, database or environment.
        if '..' in relative.split('/') or any(p.startswith('.') for p in relative.split('/')):raise Problem(404,'Bulunamadı.')
        file=(ROOT/'dist'/relative).resolve()
        if not file.is_relative_to((ROOT/'dist').resolve()) or not file.is_file() or file.suffix not in ('.html','.js','.css','.png','.jpg','.webp','.json','.xlsx','.svg','.ico'):
            raise Problem(404,'Bulunamadı.')
        return 200,file

    def __call__(self, env, start_response):
        try: status,result=self.route(env)
        except Problem as e:status,result=e.status,{'error':e.message}
        except Exception:
            # No raw provider messages, database rows or customer data in HTTP responses.
            status,result=500,{'error':'İşlem tamamlanamadı. Lütfen destek ile iletişim kurun.'}
        if isinstance(result,Path):
            body=result.read_bytes();content_type=mimetypes.guess_type(str(result))[0] or 'application/octet-stream'
        elif isinstance(result,str):body=result.encode();content_type='text/plain; charset=utf-8'
        else:body=json.dumps(result,ensure_ascii=False).encode();content_type='application/json; charset=utf-8'
        headers=[('Content-Type',content_type),('Content-Length',str(len(body))),('Cache-Control','no-store'),
                 ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),('X-Frame-Options','DENY'),
                 ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src https://www.paytr.com; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")]
        if self.config.production:headers.append(('Strict-Transport-Security','max-age=31536000'))
        from http import HTTPStatus
        start_response(str(status)+' '+HTTPStatus(status).phrase,headers)
        return [b'' if env['REQUEST_METHOD']=='HEAD' else body]

def create_app():return App(Config.from_env())

if __name__=='__main__':
    from wsgiref.simple_server import make_server
    app=create_app()
    print('Yerel backend: http://127.0.0.1:4180 — PAYMENT_MODE='+app.config.mode)
    with make_server('127.0.0.1',4180,app) as server:server.serve_forever()
