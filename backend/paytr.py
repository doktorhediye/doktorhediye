"""PayTR iFrame API. Amounts are integer kurus; card data never enters this app."""
import base64
import hashlib
import hmac
import json
import urllib.parse
import urllib.request

TOKEN_URL = 'https://www.paytr.com/odeme/api/get-token'

def signature(key, message):
    return base64.b64encode(hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()).decode()

def token_fields(config, order, ip):
    basket = base64.b64encode(json.dumps([
        [line['name'], format(line['price'] / 100, '.2f'), 1]
        for line in order['items']
    ], ensure_ascii=False, separators=(',', ':')).encode()).decode()
    fields = dict(merchant_id=config.merchant_id, user_ip=ip,
                  merchant_oid=order['id'], email=order['buyer']['email'],
                  payment_amount=str(order['amount']), user_basket=basket,
                  no_installment='1', max_installment='0', currency='TL',
                  test_mode='1' if config.mode == 'test' else '0')
    message = ''.join(fields[k] for k in (
        'merchant_id', 'user_ip', 'merchant_oid', 'email', 'payment_amount',
        'user_basket', 'no_installment', 'max_installment', 'currency', 'test_mode'))
    fields['paytr_token'] = signature(config.merchant_key, message + config.merchant_salt)
    fields.update(user_name=order['buyer']['name'], user_phone=order['buyer']['phone'],
                  user_address=order['buyer']['address'], debug_on='0', lang='tr',
                  timeout_limit='30', merchant_ok_url=config.public_url + '/checkout.html?return=1',
                  merchant_fail_url=config.public_url + '/checkout.html?return=1')
    return fields

def request_token(config, order, ip):
    request = urllib.request.Request(TOKEN_URL,
        data=urllib.parse.urlencode(token_fields(config, order, ip)).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(65537)
    if len(body) > 65536:
        raise ValueError('Provider response too large')
    result = json.loads(body)
    if result.get('status') != 'success' or not isinstance(result.get('token'), str):
        raise ValueError('Provider rejected token request')
    token = result['token']
    if not token.isalnum() or len(token) > 256:
        raise ValueError('Invalid provider token')
    return token

def verify_callback(config, fields):
    if not all(isinstance(fields.get(k), str) for k in ('merchant_oid', 'status', 'total_amount', 'hash')):
        return False
    expected = signature(config.merchant_key, fields['merchant_oid'] + config.merchant_salt
                         + fields['status'] + fields['total_amount'])
    return hmac.compare_digest(expected.encode(), fields['hash'].encode())
