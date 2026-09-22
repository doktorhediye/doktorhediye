'use strict';
const el=id=>document.getElementById(id);
const format=n=>(n/100).toLocaleString('tr-TR',{style:'currency',currency:'TRY'});
const labels={pending:'Ödeme bekleniyor',paid:'Ödeme doğrulandı',failed:'Ödeme başarısız',awaiting_payment:'Ödeme bekleniyor',awaiting_acceptance:'Doktorun kabulü bekleniyor',measurements:'Ölçüler ve tercihler alınıyor',tailoring:'Dikimde',shipped:'Kargoya verildi',delivered:'Teslim edildi',refund_requested:'İade talebi inceleniyor',payment_failed:'Ödeme başarısız'};
const deliveryLabels={buyer:'Size teslim · Doktorunuza siz hediye edin',doctor:'Doğrudan doktorunuza teslim'};
document.querySelectorAll('input[name=delivery]').forEach(input=>input.onchange=()=>{
 const toBuyer=input.value==='buyer';
 el('addressTitle').textContent=toBuyer?'Adresiniz / Hediyenin teslim edileceği adres':'Gönderenin adresi';
 el('deliveryHint').textContent=toBuyer?'Hediye kutusunu bu adrese göndereceğiz. Doktorunuza siz teslim edebilirsiniz.':'Bu alan gönderenin adresidir. Doktorun teslimat adresini hediye kabulünden sonra kendisinden alacağız.';
});
let config,product,doctor,orderId='',access='',idempotency='',lastPayload='';
async function api(path,options={}){
 const res=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(access?{Authorization:'Bearer '+access}:{}),...options.headers}});
 const data=await res.json();if(!res.ok)throw new Error(data.error||'İşlem tamamlanamadı.');return data;
}
function totals(){el('total').textContent='Toplam: '+format(product.price+(el('checkoutForm').elements.cufflinks.checked?config.catalog.accessory.price:0));}
async function refresh(){
 if(!orderId||!access)return;
 try{
 const order=await api('/api/orders/'+encodeURIComponent(orderId));
 el('orderResult').hidden=false;
 el('orderStatus').textContent=order.id+' · '+format(order.amount)+' · '+labels[order.payment]+' · '+labels[order.stage]+' · '+(deliveryLabels[order.delivery_method]||deliveryLabels.doctor)+(order.mode==='test'?' · TEST İŞLEMİ':'')+(order.tracking?' · '+order.tracking:'');
 el('savedOrder').value=orderId;el('savedAccess').value=access;
 if(order.payment!=='pending')el('payment').replaceChildren();
 }catch(error){el('message').textContent=error.message;}
}
el('refresh').onclick=refresh;
el('trackingForm').onsubmit=e=>{e.preventDefault();orderId=e.target.elements.order.value.trim();access=e.target.elements.access.value.trim();refresh();};
el('checkoutForm').onsubmit=async e=>{
 e.preventDefault();const form=e.target;el('submit').disabled=true;el('message').textContent='Sipariş kaydediliyor…';
 const payload={doctor_id:doctor.id,product_id:product.id,delivery_method:form.elements.delivery.value,cufflinks:form.elements.cufflinks.checked,buyer:{name:form.elements.name.value.trim(),email:form.elements.email.value.trim(),phone:form.elements.phone.value.trim(),address:form.elements.address.value.trim()},note:form.elements.note.value.trim()};
 const serialized=JSON.stringify(payload);if(serialized!==lastPayload){lastPayload=serialized;idempotency=crypto.randomUUID();}
 try{
 const result=await api('/api/orders',{method:'POST',headers:{'Idempotency-Key':idempotency},body:serialized});
 orderId=result.order.id;access=result.access_token;
 // Capability only, no name/address/note persisted in browser storage.
 sessionStorage.setItem('dh-order',JSON.stringify({orderId,access}));
 form.hidden=true;await refresh();
 if(config.payment_mode==='disabled'){el('message').textContent='Deneme sipariş kaydı oluşturuldu. PayTR bağlı değil; ücret alınmadı.';return;}
 el('message').textContent='Güvenli ödeme formu hazırlanıyor…';
 const payment=await api('/api/orders/'+orderId+'/payment',{method:'POST',body:'{}'});
 const url=new URL(payment.iframe_url);if(url.origin!=='https://www.paytr.com'||!url.pathname.startsWith('/odeme/guvenli/'))throw new Error('Ödeme adresi doğrulanamadı.');
 const frame=document.createElement('iframe');frame.src=url.href;frame.title='PayTR güvenli ödeme';frame.referrerPolicy='no-referrer';el('payment').replaceChildren(frame);
 el('message').textContent=config.payment_mode==='test'?'PayTR test ödemesi. Gerçek kart kullanmayın.':'Kart bilgilerinizi PayTR güvenli formuna girin. Ödeme sonrasında durumu yenileyebilirsiniz.';
 }catch(error){el('message').textContent=error.message;}finally{el('submit').disabled=false;}
};
(async()=>{
 try{
 config=await api('/api/storefront');
 el('environment').textContent=config.payment_mode==='live'?'Ödeme PayTR güvenli formunda tamamlanır.':config.payment_mode==='test'?'PayTR test ortamı: örnek bilgiler ve PayTR test kartları kullanın.':'Ödeme kapalı. Bu geliştirme ortamında deneme siparişleri veritabanına kaydedilir; örnek bilgiler kullanın.';
 const params=new URLSearchParams(location.search);
 if(params.has('return')){
 const saved=JSON.parse(sessionStorage.getItem('dh-order')||'null');
 el('message').textContent='Bu sayfaya dönmeniz ödemenin onaylandığı anlamına gelmez. Sunucunun doğruladığı durum aşağıda gösterilir.';
 if(saved){orderId=saved.orderId;access=saved.access;await refresh();}else el('message').textContent+=' Sipariş numarası ve takip erişim koduyla sorgulayabilirsiniz.';
 return;
 }
 product=config.catalog.products.find(p=>p.id===params.get('product'));
 const doctors=await (await fetch('doctors.json')).json();doctor=doctors.find(d=>d.id===params.get('doctor'));
 if(!product||!doctor)throw new Error('Ana sayfadan doktorunuzu ve hediyenizi seçin.');
 el('selection').textContent=doctor.title+' '+doctor.name+' · '+doctor.institution+' · '+product.name;
 el('accessoryLabel').hidden=!product.cufflinks;
 el('accessoryPrice').textContent='(+'+format(config.catalog.accessory.price)+')';
 el('checkoutForm').elements.cufflinks.checked=product.cufflinks&&params.get('cufflinks')==='1';
 el('checkoutForm').elements.cufflinks.onchange=totals;totals();
 el('checkoutForm').hidden=false;
 el('submit').textContent=config.payment_mode==='disabled'?'Deneme siparişini kaydet':'Siparişi kaydet ve ödemeye geç →';
 }catch(error){el('message').textContent=error.message;el('environment').textContent='Sipariş servisine ulaşılamadı. GitHub Pages bu backend’i çalıştırmaz.';}
})();
