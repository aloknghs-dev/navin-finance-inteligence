/* Preview adapter: no hospital connection, credentials or financial writes. */
(() => {
 const rawFetch=window.fetch.bind(window),ready=rawFetch('/snapshot.json').then(r=>r.json());
 let role='management';
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 window.fetch=async(input,init={})=>{
  const u=new URL(String(input),location.href);if(!u.pathname.startsWith('/api/'))return rawFetch(input,init);
  const snapshots=await ready,s=snapshots[role],w=u.searchParams.get('workspace')||'demo',path=u.pathname;
  if(init.method&&init.method!=='GET')return response({detail:'Preview only. Use the Python app to save records, request names or approve imports.'},400);
  if(path==='/api/me')return response(s.me);
  if(path==='/api/masters')return response(s[w].masters);
  if(path==='/api/analytics')return response(s[w].analytics);
  if(path==='/api/metrics')return response(s.metrics);
  if(path==='/api/close')return response(s[w].close);
  if(path==='/api/rules')return response(s[w].rules||[]);
  if(path==='/api/users')return response([]);
  if(path.startsWith('/api/records/')){const r=s[w].analytics.asof_records.find(r=>r.id===Number(path.split('/').pop()));return response(r?{...r,fields:r,original:{source:'Synthetic demonstration',...r},allocations:[]}:{});}
  return response([]);
 };
 window.addEventListener('DOMContentLoaded',()=>{
  const bar=document.createElement('div');bar.className='preview-bar';bar.innerHTML='<div><strong>INTERACTIVE PREVIEW</strong><span>Synthetic figures · 1–6 Oct 2026 · Saving and uploads disabled</span></div><label>View as <select id="previewRole"><option value="management">Management</option><option value="billing">Billing / TPA</option><option value="cashier">Cashier</option><option value="stores">Stores</option><option value="pharmacy">Pharmacy</option><option value="hr">HR / payroll</option><option value="accounts">Accounts</option><option value="finance">Finance checker</option></select></label>';
  document.body.prepend(bar);document.body.classList.add('preview-mode');
  new MutationObserver(()=>document.querySelectorAll('#filters input,#filters select,#filters button').forEach(el=>{el.disabled=true;el.title='Preview uses a fixed sample period. Run the Python app to filter live calculations.'})).observe(document.querySelector('#filters'),{childList:true,subtree:true});
  document.querySelector('#previewRole').onchange=async e=>{role=e.target.value;filters={start:'2026-10-01',end:'2026-10-06'};moduleTab='';await start();};
  document.querySelector('#workspaceMode option[value=real]').textContent='Empty real workspace example';
  document.querySelector('#logout').hidden=true;
  document.addEventListener('click',e=>{const b=e.target.closest('[data-export],[data-template]');if(b){e.preventDefault();e.stopImmediatePropagation();toast('Preview only. CSV and Excel downloads are available in the Python app.');}},true);
 });
})();
