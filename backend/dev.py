"""Local-only launcher; creates secrets once and never prints them."""
import os
from pathlib import Path
import secrets
from wsgiref.simple_server import make_server
from .app import ROOT, create_app
from .prepare_static import prepare

path=ROOT/'.env.local'
if not path.exists():
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write('APP_SECRET='+secrets.token_hex(32)+'\nADMIN_TOKEN='+secrets.token_hex(32)+'\nPAYMENT_MODE=disabled\nPUBLIC_URL=http://127.0.0.1:4180\n')
for line in path.read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        key,value=line.split('=',1)
        os.environ.setdefault(key.strip(),value.strip())
prepare()
app=create_app()
if app.config.mode!='disabled' or app.config.production:
    raise SystemExit('Bu komut yalnız ödeme kapalı yerel geliştirme içindir. Sunucuda gunicorn kullanın.')
print('Site: http://127.0.0.1:4180 | Yönetim: http://127.0.0.1:4180/admin',flush=True)
print('Yönetici kodu .env.local dosyasındaki ADMIN_TOKEN değeridir. PayTR ve gerçek ödeme kapalı.',flush=True)
with make_server('127.0.0.1',4180,app) as server:server.serve_forever()
