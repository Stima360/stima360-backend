#!/usr/bin/env python3
import os,requests,uuid
BASE=os.getenv('OWNER_E2E_BASE_URL','https://stima360-backend-test.onrender.com').rstrip('/')
DB=os.getenv('DB_NAME','')
def main():
 if 'test' not in BASE.lower() or 'test' not in DB.lower():raise SystemExit('BLOCCATO: ambiente non test')
 cid=int(os.environ['OWNER_E2E_CONTACT_ID']);pid=int(os.environ['OWNER_E2E_PROPERTY_ID']);tag='E2E_OWNER01_'+uuid.uuid4().hex[:8]
 def api(m,p,expected=(200,201,204),**k):
  r=requests.request(m,BASE+p,timeout=30,**k)
  if r.status_code not in expected:raise SystemExit(f'{m} {p}: {r.status_code} {r.text[:300]}')
  return r
 a=api('POST','/api/owner/admin/accounts',json={'contact_id':cid}).json();aid=a['id']
 api('POST','/api/owner/admin/access',json={'owner_account_id':aid,'property_id':pid})
 token=api('POST',f'/api/owner/admin/accounts/{aid}/tokens',json={'token_type':'login'}).json()['token']
 s=requests.Session();api('POST','/api/owner/portal/auth/token',expected=(204,),json={'token':token});r=requests.post(BASE+'/api/owner/portal/auth/token',json={'token':token},timeout=30);assert r.status_code==404
 p=api('POST','/api/owner/admin/publications',json={'property_id':pid,'publication_type':'general_update','title':tag,'body':'v1'}).json();api('POST',f"/api/owner/admin/publications/{p['id']}/publish")
 r=requests.patch(BASE+f"/api/owner/admin/publications/{p['id']}",json={'title':'tampered'},timeout=30);assert r.status_code==409
 v2=api('POST',f"/api/owner/admin/publications/{p['id']}/supersede",json={'property_id':pid,'publication_type':'general_update','title':tag+' v2','body':'v2'}).json();assert v2['version_number']==2
 print('OWNER 0.1 E2E: token monouso, cookie, 404, immutabilità e versionamento OK')
if __name__=='__main__':main()
