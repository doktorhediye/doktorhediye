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
        with self.assertRaises(ValueError):App(replace(self.config,mode='live',live_confirmed=True))
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

if __name__=='__main__':unittest.main()
