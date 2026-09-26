"""Durable owner notifications; Resend HTTPS, no customer PII in emails."""
import json
import threading
import time
import uuid
import urllib.request
import urllib.error

SCHEMA = '''CREATE TABLE IF NOT EXISTS mail_outbox (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), kind TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt INTEGER NOT NULL DEFAULT 0, first_attempt INTEGER, lease_until INTEGER NOT NULL DEFAULT 0,
 lease_token TEXT, provider_id TEXT, last_error TEXT, created INTEGER NOT NULL,
 UNIQUE(order_id,kind));'''

class MailError(Exception):
    def __init__(self, retryable): self.retryable=retryable

def send(config, payload, key):
    request=urllib.request.Request('https://api.resend.com/emails',
        data=json.dumps(payload).encode(),method='POST',headers={
        'Authorization':'Bearer '+config.resend_api_key,'Content-Type':'application/json',
        'Idempotency-Key':key,'User-Agent':'DoktorHediye/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            result=json.loads(response.read(65536))
        if not isinstance(result.get('id'),str) or not result['id']: raise MailError(True)
        return result['id']
    except urllib.error.HTTPError as exc:
        raise MailError(exc.code in (408,409,429) or exc.code>=500) from None

def ready(config):
    return config.mail_enabled and bool(config.resend_api_key and config.mail_from and config.mail_to)

def enqueue(app, db, row, kind):
    c=app.config
    if not ready(c) or (row['mode']!='live' and not c.mail_notify_test): return
    p=json.loads(row['payload']);test=row['mode']!='live'
    heading='Yeni sipariş — ödeme bekleniyor' if kind=='created' else 'Ödeme doğrulandı — siparişi kontrol edin'
    if test: heading='[TEST — GERÇEK TAHSİLAT DEĞİL] '+heading
    amount=f"{row['amount']/100:,.2f}".replace(',','X').replace('.',',').replace('X','.')+' TL'
    delivery='Gönderene teslim; doktoruna kendisi hediye edecek.' if p.get('delivery_method')=='buyer' else 'Doğrudan doktora teslim; adres doktorla doğrulanacak.'
    body='\n'.join([heading,'','Sipariş: '+row['id'],'Toplam: '+amount,
        'Ürün: '+', '.join(i['name'] for i in p['items']),'Teslim: '+delivery,
        '','Ödeme bekleyen sipariş için üretim veya gönderim yapmayın.' if kind=='created' else 'Üretim öncesinde doktorun hediye kabulünü ve ölçülerini doğrulayın.',
        '','Sipariş ayrıntıları (yönetici girişi gerekir): '+c.public_url+'/admin',
        '','Bu mesaj müşteriye değil, mağaza yöneticisine gönderilmiştir.'])
    payload=dict(sender=c.mail_from,to=[c.mail_to],subject=heading+' | '+row['id'],text=body)
    payload['from']=payload.pop('sender')
    db.execute('INSERT OR IGNORE INTO mail_outbox(id,order_id,kind,payload,created) VALUES(?,?,?,?,?)',
        ('dh-'+row['id']+'-'+kind,row['id'],kind,json.dumps(payload,ensure_ascii=False),int(time.time())))

def process_one(app, sender=send):
    if not ready(app.config): return False
    now=int(time.time());lease=uuid.uuid4().hex
    # Idle polling must not acquire the writer lock (or update every row).
    # Recheck under BEGIN IMMEDIATE below to keep claims safe across workers.
    with app.db() as db:
        due=db.execute("SELECT 1 FROM mail_outbox WHERE (state='pending' AND next_attempt<=?) OR (state='sending' AND lease_until<=?) OR (state IN ('pending','sending') AND first_attempt<? AND lease_until<=?) LIMIT 1",(now,now,now-23*3600,now)).fetchone()
    if not due:return False
    with app.db() as db:
        db.execute('BEGIN IMMEDIATE')
        # Never retry beyond Resend's 24h deduplication window after ambiguous acceptance.
        db.execute("UPDATE mail_outbox SET state='review',last_error='retry_window_expired' WHERE state IN ('pending','sending') AND first_attempt IS NOT NULL AND first_attempt<? AND lease_until<=?",(now-23*3600,now))
        row=db.execute("SELECT * FROM mail_outbox WHERE ((state='pending' AND next_attempt<=?) OR (state='sending' AND lease_until<=?)) ORDER BY created,id LIMIT 1",(now,now)).fetchone()
        if not row:return False
        db.execute("UPDATE mail_outbox SET state='sending',attempts=attempts+1,first_attempt=COALESCE(first_attempt,?),lease_until=?,lease_token=? WHERE id=?",(now,now+90,lease,row['id']))
    try:
        provider_id=sender(app.config,json.loads(row['payload']),row['id'])
    except Exception as exc:
        retryable=not isinstance(exc,MailError) or exc.retryable
        attempts=row['attempts']+1;state='pending' if retryable and attempts<8 else 'review'
        with app.db() as db:
            db.execute('UPDATE mail_outbox SET state=?,next_attempt=?,lease_until=0,last_error=? WHERE id=? AND lease_token=?',
                (state,now+min(3600,30*2**attempts),'temporary_send_error' if retryable else 'provider_configuration_error',row['id'],lease))
    else:
        with app.db() as db:
            db.execute("UPDATE mail_outbox SET state='sent',provider_id=?,lease_until=0,last_error=NULL WHERE id=? AND lease_token=?",(provider_id,row['id'],lease))
    return True

def status(app, db):
    counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM mail_outbox GROUP BY state')}
    return dict(configured=ready(app.config),test_enabled=app.config.mail_notify_test,
        pending=counts.get('pending',0)+counts.get('sending',0),sent=counts.get('sent',0),review=counts.get('review',0))

def start_worker(app):
    if not ready(app.config):return
    def run():
        while True:
            try:
                # Bounded throughput; shared SQLite leases serialize claims across workers.
                worked=process_one(app)
            except Exception:
                worked=False
            time.sleep(1 if worked else 30)
    threading.Thread(target=run,name='owner-mail-outbox',daemon=True).start()
