import base64
import hashlib
import hmac
import io
import json
import tempfile
import unittest
import uuid
from dataclasses import replace
from urllib.parse import urlencode
from backend.app import App, Config
from backend import paytr

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=Config(self.tmp.name+'/orders.sqlite3','s'*40,'a'*40,'https://example.test','test','123','key','salt')
        self.calls=0
        def provider(*args):
            self.calls+=1
            return 'mocktoken123'
        self.app=App(self.config,provider)
        self.payload=dict(doctor_id=next(iter(self.app.doctors)),product_id='french-cuff',cufflinks=True,
            buyer=dict(name='Test Kullanıcı',email='test@example.com',phone='05000000000',address='Örnek test adresi'),note='Teşekkür ederim.')

    def request(self,path,method='GET',data=None,token='',key='',form=False,origin=None):
        raw=(urlencode(data) if form else json.dumps(data)).encode() if data is not None else b''
        env=dict(REQUEST_METHOD=method,PATH_INFO=path,REMOTE_ADDR='127.0.0.1',CONTENT_LENGTH=str(len(raw)),
                 CONTENT_TYPE='application/x-www-form-urlencoded' if form else 'application/json',
                 HTTP_AUTHORIZATION='Bearer '+token,HTTP_IDEMPOTENCY_KEY=key)
        env['wsgi.input']=io.BytesIO(raw)
        if origin:env['HTTP_ORIGIN']=origin
        status=[]
        body=b''.join(self.app(env,lambda s,h:status.append((int(s.split()[0]),dict(h)))))
        if status[0][1]['Content-Type'].startswith('application/json'): body=json.loads(body)
        return status[0][0],body

    def order(self):
        status,result=self.request('/api/orders','POST',self.payload,key=str(uuid.uuid4()))
        self.assertEqual(status,201,result)
        return result['order']['id'],result['access_token']

    def start(self,oid,token):
        return self.request('/api/orders/'+oid+'/payment','POST',{},token)

    def notification(self,oid,status='success',amount='575000',**overrides):
        fields=dict(merchant_oid=oid,status=status,total_amount=amount,payment_amount='575000',currency='TL',test_mode='1')
        fields.update(overrides)
        msg=fields['merchant_oid']+'salt'+fields['status']+fields['total_amount']
        fields['hash']=base64.b64encode(hmac.new(b'key',msg.encode(),hashlib.sha256).digest()).decode()
        return fields

    def callback(self,fields):
        return self.request('/api/paytr/callback','POST',fields,form=True)

    def test_price_cuff_and_doctor_validation(self):
        for change in ({'amount':1},{'product_id':'signature'},{'doctor_id':'unknown'}):
            original=self.payload.copy();self.payload.update(change)
            status,_=self.request('/api/orders','POST',self.payload,key=str(uuid.uuid4()))
            self.assertEqual(status,400)
            self.payload=original
        oid,token=self.order()
        _,order=self.request('/api/orders/'+oid,token=token)
        self.assertEqual(order['amount'],575000)
        self.assertNotIn('buyer',order)

    def test_delivery_choice_persists_and_validates(self):
        for method in ('buyer', 'doctor'):
            self.payload['delivery_method'] = method
            oid,token=self.order()
            self.app=App(self.config)
            order=self.request('/api/orders/'+oid,token=token)[1]
            self.assertEqual(order['delivery_method'],method)
            admin=self.request('/api/admin/orders',token='a'*40)[1]
            saved=next(o for o in admin['orders'] if o['id']==oid)
            self.assertEqual(saved['delivery_method'],method)
            self.assertEqual(saved['buyer']['address'],self.payload['buyer']['address'])
            self.assertEqual(order['amount'],575000)
        self.payload['delivery_method']='invalid'
        self.assertEqual(self.request('/api/orders','POST',self.payload,key=str(uuid.uuid4()))[0],400)
        del self.payload['delivery_method']
        oid,token=self.order()
        self.assertEqual(self.request('/api/orders/'+oid,token=token)[1]['delivery_method'],'doctor')

    def test_idempotent_create_and_conflict(self):
        key=str(uuid.uuid4())
        first=self.request('/api/orders','POST',self.payload,key=key)
        second=self.request('/api/orders','POST',self.payload,key=key)
        self.assertEqual(first,second)
        self.payload['note']='Değişti'
        self.assertEqual(self.request('/api/orders','POST',self.payload,key=key)[0],409)

    def test_callback_signature_amount_and_duplicate(self):
        oid,token=self.order();self.assertEqual(self.start(oid,token)[0],200)
        fields=self.notification(oid);fields['hash']='invalid'
        self.assertEqual(self.callback(fields)[0],400)
        fields['hash']='ş'
        self.assertEqual(self.callback(fields)[0],400)
        self.assertEqual(self.callback(self.notification(oid,amount='1'))[0],400)
        self.assertEqual(self.callback(self.notification(oid,test_mode='0'))[0],400)
        self.assertEqual(self.callback(self.notification(oid)),(200,b'OK'))
        self.assertEqual(self.callback(self.notification(oid)),(200,b'OK'))
        self.assertEqual(self.callback(self.notification(oid,status='failed'))[0],409)
        with self.app.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='payment_paid'").fetchone()[0],1)
        self.app=App(self.config)
        self.assertEqual(self.request('/api/orders/'+oid,token=token)[1]['payment'],'paid')

    def test_no_payment_on_redirect_or_unstarted_notification(self):
        oid,token=self.order()
        self.assertEqual(self.callback(self.notification(oid))[0],409)
        self.assertEqual(self.request('/checkout.html')[0],200)
        self.assertEqual(self.request('/api/orders/'+oid,token=token)[1]['payment'],'pending')

    def test_authorization_and_static_secrets(self):
        oid,_=self.order()
        for path in ('/api/orders/'+oid,'/api/admin/orders'):
            self.assertEqual(self.request(path)[0],401)
        for path in ('/.env','/backend/runtime/orders.sqlite3','/../backend/catalog.json','/backend/app.py'):
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/api/admin/orders',token='a'*40)[0],200)
        self.assertEqual(self.request('/api/storefront',origin='https://evil.test')[0],403)

    def test_session_reuse_and_ambiguous_failure(self):
        oid,token=self.order();self.start(oid,token);self.start(oid,token)
        self.assertEqual(self.calls,1)
        def fail(*args):
            self.calls+=1
            raise TimeoutError()
        self.app.provider=fail
        oid,token=self.order()
        self.assertEqual(self.start(oid,token)[0],502)
        self.assertEqual(self.start(oid,token)[0],409)
        self.assertEqual(self.calls,2)

    def test_admin_stages_and_version(self):
        oid,token=self.order();path='/api/admin/orders/'+oid
        self.assertEqual(self.request(path,'PATCH',dict(stage='measurements',version=0),'a'*40)[0],409)
        self.start(oid,token);self.callback(self.notification(oid))
        self.assertEqual(self.request(path,'PATCH',dict(stage='measurements',version=0),'a'*40)[0],409)
        self.assertEqual(self.request(path,'PATCH',dict(stage='shipped',version=1),'a'*40)[0],409)
        for version,stage in enumerate(('measurements','tailoring','shipped','delivered'),1):
            if stage=='shipped':
                self.assertEqual(self.request(path,'PATCH',dict(stage=stage,version=version,tracking=''),'a'*40)[0],400)
            self.assertEqual(self.request(path,'PATCH',dict(stage=stage,version=version,tracking='Test Kargo 123'),'a'*40)[0],200)

    def test_disabled_and_live_gate(self):
        self.app=App(replace(self.config,mode='disabled'))
        oid,token=self.order()
        self.assertEqual(self.start(oid,token)[0],503)
        with self.assertRaises(ValueError):App(replace(self.config,mode='live',live_confirmed=False))
        with self.assertRaises(ValueError):App(replace(self.config,public_url='http://example.test'))

    def test_token_payload_matches_official_formula(self):
        oid,_=self.order()
        p=self.app.validate(self.payload);p.update(id=oid,amount=575000)
        f=paytr.token_fields(self.config,p,'203.0.113.1')
        message='123203.0.113.1'+oid+'test@example.com575000'+f['user_basket']+'10TL1salt'
        expected=base64.b64encode(hmac.new(b'key',message.encode(),hashlib.sha256).digest()).decode()
        self.assertEqual(f['paytr_token'],expected)
        self.assertEqual(json.loads(base64.b64decode(f['user_basket']))[1][1],'1250.00')
        self.assertEqual(f['no_installment'],'1')


class NotificationTests(unittest.TestCase):
    request=BackendTests.request
    order=BackendTests.order
    start=BackendTests.start
    callback=BackendTests.callback
    notification=BackendTests.notification

    def setUp(self):
        BackendTests.setUp(self)
        self.config=replace(self.config,mail_enabled=True,resend_api_key='fake-test-key',mail_from='orders@example.test',mail_notify_test=True)
        self.app=App(self.config,lambda *args:'mocktoken123')

    def test_durable_outbox_duplicates_and_privacy(self):
        from backend import notifications
        key=str(uuid.uuid4())
        _,r=self.request('/api/orders','POST',self.payload,key=key)
        self.request('/api/orders','POST',self.payload,key=key)
        oid=r['order']['id'];self.start(oid,r['access_token'])
        self.callback(self.notification(oid));self.callback(self.notification(oid))
        with self.app.db() as db:
            rows=db.execute('SELECT * FROM mail_outbox ORDER BY kind').fetchall()
        self.assertEqual(len(rows),2)
        for row in rows:
            payload=json.loads(row['payload'])
            self.assertEqual(payload['to'],['doktor-hediye@hotmail.com'])
            self.assertIn('TEST',payload['subject'])
            self.assertNotIn(self.payload['buyer']['email'],payload['text'])
            self.assertNotIn(self.payload['buyer']['name'],payload['text'])
            self.assertNotIn(self.payload['note'],payload['text'])
            self.assertNotIn(r['access_token'],payload['text'])
        self.app=App(self.config)
        calls=[]
        def sender(c,p,k):calls.append(k);return 'provider-123'
        self.assertTrue(notifications.process_one(self.app,sender))
        self.assertTrue(notifications.process_one(self.app,sender))
        self.assertFalse(notifications.process_one(self.app,sender))
        self.assertEqual(len(set(calls)),2)
        self.assertEqual(self.request('/api/admin/orders',token='a'*40)[1]['mail']['sent'],2)

    def test_two_workers_do_not_claim_same_email(self):
        from backend import notifications
        self.order()
        second=App(self.config)
        def sender(*args):
            self.assertFalse(notifications.process_one(second,lambda *a:self.fail('Duplicate claim')))
            return 'accepted'
        self.assertTrue(notifications.process_one(self.app,sender))

    def test_permanent_provider_failure_requires_review(self):
        from backend import notifications
        self.order()
        def sender(*args):raise notifications.MailError(False)
        notifications.process_one(self.app,sender)
        with self.app.db() as db:
            self.assertEqual(db.execute('SELECT state FROM mail_outbox').fetchone()[0],'review')
        self.assertFalse(notifications.process_one(self.app,sender))

    def test_retry_keeps_key_payload_and_does_not_fail_order(self):
        from backend import notifications
        self.order();calls=[]
        def fail(c,p,k):calls.append((p,k));raise TimeoutError()
        notifications.process_one(self.app,fail)
        with self.app.db() as db:
            self.assertEqual(db.execute('SELECT state FROM mail_outbox').fetchone()[0],'pending')
            db.execute('UPDATE mail_outbox SET next_attempt=0')
        def ok(c,p,k):calls.append((p,k));return 'accepted'
        notifications.process_one(self.app,ok)
        self.assertEqual(calls[0],calls[1])

    def test_expired_lease_and_retry_horizon(self):
        from backend import notifications
        import time
        self.order()
        with self.app.db() as db:
            db.execute("UPDATE mail_outbox SET state='sending',lease_until=?,first_attempt=?",(int(time.time())+60,int(time.time())))
        self.assertFalse(notifications.process_one(self.app,lambda *a:'accepted'))
        with self.app.db() as db:db.execute('UPDATE mail_outbox SET lease_until=0,first_attempt=?',(int(time.time())-24*3600,))
        self.assertFalse(notifications.process_one(self.app,lambda *a:self.fail('Must not resend after dedupe window')))
        with self.app.db() as db:self.assertEqual(db.execute('SELECT state FROM mail_outbox').fetchone()[0],'review')

    def test_test_records_off_and_missing_config(self):
        from backend import notifications
        self.app=App(replace(self.config,mail_notify_test=False))
        self.order()
        self.assertFalse(notifications.process_one(self.app,lambda *a:self.fail('Test email disabled')))
        with self.assertRaises(ValueError):App(replace(self.config,resend_api_key=''))
        with self.assertRaises(ValueError):App(replace(self.config,mail_from='invalid\naddress'))

if __name__=='__main__':unittest.main()
