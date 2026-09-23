"""Build dist from the existing GitHub Pages root without moving originals."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parent.parent
FILES=('index.html','app.js','style.css','checkout.html','checkout.js','checkout.css','order-progress.js','order-progress.css',
       'doctors.json','directory.json','directory.js','doktorlar.html','Medipol-Doktor-Listesi.xlsx')

def prepare(root=ROOT):
    dist=root/'dist'
    if (root/'index.html').is_file():
        dist.mkdir(exist_ok=True)
        for name in FILES:
            shutil.copy2(root/name,dist/name)
        shutil.copytree(root/'assets',dist/'assets',dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*.mp4','*.mov','*.webm'))
    for name in FILES:
        if not (dist/name).is_file(): raise RuntimeError('Eksik site dosyası: '+name)
    if not (dist/'assets').is_dir(): raise RuntimeError('Görsel klasörü eksik')

if __name__=='__main__':prepare()
