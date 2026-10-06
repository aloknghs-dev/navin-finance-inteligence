"""Browser smoke on a disposable database, never the running hospital workspace."""
import os,subprocess,socket,time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

def test_browser_modules_and_real_isolation(tmp_path):
 root=Path(__file__).resolve().parents[1]
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 env={**os.environ,'NAVIN_DATA_DIR':str(tmp_path/'data'),'NAVIN_ADMIN_PASSWORD':'browser-only-test-password','NAVIN_COOKIE_SECURE':'false','PORT':str(port)}
 with open(tmp_path/'server.log','w') as log:
  process=subprocess.Popen(['bash','scripts/start.sh'],cwd=root,env=env,stdout=log,stderr=log)
  try:
   url=f'http://127.0.0.1:{port}'
   for _ in range(80):
    try:
     if httpx.get(url+'/health').status_code==200:break
    except httpx.ConnectError:pass
    time.sleep(.1)
   else:raise AssertionError('Browser fixture server did not start')
   with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.locator('[name=username]').fill('admin');page.locator('[name=password]').fill('browser-only-test-password');page.locator('#loginForm button').click()
    page.locator('#nav [data-nav]').first.wait_for();page.wait_for_function("() => document.querySelector('#content').textContent.includes('Synthetic demonstration')")
    pages=page.locator('#nav [data-nav]').evaluate_all('(els)=>els.map(e=>e.dataset.nav)');assert len(pages)==13
    for name in pages:
     page.locator(f'[data-nav={name}]').click();page.wait_for_function("() => !document.querySelector('#content').textContent.includes('Loading financial')");assert 'Synthetic demonstration' in page.locator('#content').inner_text()
    page.locator('[data-nav=overview]').click();page.wait_for_function("() => document.querySelector('#content').textContent.includes('Synthetic demonstration')")
    screens=Path(os.environ.get('NAVIN_PREVIEW_DIR',str(tmp_path/'screenshots')));screens.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(screens/'preview.png'),full_page=True)
    page.locator('#workspaceMode').select_option('real');page.wait_for_function("() => document.querySelector('#content').textContent.includes('Real finance records')");assert 'Unknown' in page.locator('#content').inner_text()
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(screens/'preview-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'), 'Page overflows mobile viewport'
    assert not errors,errors
    browser.close()
  finally:process.terminate();process.wait(timeout=10)
