#!/usr/bin/env python3
"""Capture the county parcel inventory through REL, retaining boundary provenance."""
import argparse,asyncio,fcntl,json,sqlite3
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request as HttpRequest,urlopen
from urllib.error import HTTPError
from selectolax.parser import HTMLParser

SOURCE='https://sccgis.santacruzcountyca.gov/server/rest/services/giswebp/MapServer/25'
FIELDS='OBJECTID,APNNODASH,SITEADD,SITCITY,SITZIP,ASRUSECD,USECDDESC,CITYLIMITS'
RESIDENTIAL={'020','021','023','024','025','027','028','029','030','031','032','033','034','041','042','043','044','045','046','060','061','062','068','100','101','122'}


def parse_response(html):
    data=json.loads(HTMLParser(html).text())
    if 'error' in data or data.get('exceededTransferLimit'): raise ValueError('County inventory query failed or was truncated')
    return data


def initialize(path):
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS parcels(object_id INTEGER PRIMARY KEY,apn TEXT,address TEXT,postal_city TEXT,postal_code TEXT,use_code TEXT,use_description TEXT,municipality TEXT,in_santa_cruz_city INTEGER,residential INTEGER,captured_at TEXT,source_url TEXT);
      CREATE INDEX IF NOT EXISTS parcel_apn ON parcels(apn);
      CREATE TABLE IF NOT EXISTS captures(batch INTEGER PRIMARY KEY,captured_at TEXT,url TEXT,html TEXT);
      CREATE TABLE IF NOT EXISTS inventory_state(key TEXT PRIMARY KEY,value TEXT);''')
    return db


def save_batch(db,batch,url,html,expected):
    data=parse_response(html);features=data['features']
    if {f['attributes']['OBJECTID'] for f in features}!=set(expected): raise ValueError('Inventory batch IDs do not match')
    stamp=datetime.now(timezone.utc).isoformat()
    with db:
        for f in features:
            a=f['attributes'];municipality=(a.get('CITYLIMITS') or '').strip()
            # CITYLIMITS is the county's spatial boundary field, not postal city.
            flag=1 if municipality=='City of Santa Cruz' else 0 if municipality else None
            code=(a.get('ASRUSECD') or '').split('-')[0].strip()
            db.execute('INSERT OR REPLACE INTO parcels VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (a['OBJECTID'],a['APNNODASH'],a.get('SITEADD'),a.get('SITCITY'),a.get('SITZIP'),code,a.get('USECDDESC'),municipality,flag,int(code in RESIDENTIAL and bool(a.get('SITEADD'))),stamp,SOURCE))
        db.execute('INSERT OR REPLACE INTO captures VALUES(?,?,?,?)',(batch,stamp,url,html))
    return len(features)


def inventory_session(output):
    endpoint='http://127.0.0.1:17319/v1';state_path=output/'county-inventory-session.json'
    def rpc(method,path,payload=None):
        req=HttpRequest(endpoint+path,method=method,data=json.dumps(payload).encode() if payload else None,headers={'Content-Type':'application/json'})
        try: response=urlopen(req,timeout=20)
        except HTTPError as error: response=error
        with response: return json.load(response)
    if state_path.exists():
        state=json.loads(state_path.read_text());reply=rpc('GET','/sessions/'+state['session_id'])
        if reply.get('status')=='ok':
            session=reply['data']['session']
            if session.get('profile')!='Native' or session.get('group')!='prop13-county-inventory': raise ValueError('Inventory session profile/group mismatch')
            return state['session_id']
        if reply.get('error',{}).get('id')!='SESSION_NOT_FOUND': raise ValueError(str(reply.get('error')))
    reply=rpc('POST','/sessions',{'profile':'Native','group':'prop13-county-inventory','lifetime':{'type':'indefinite'}})
    if reply.get('status')!='ok': raise ValueError(str(reply.get('error')))
    session=reply['data']['session']['id'];state_path.write_text(json.dumps({'session_id':session}));return session


async def run(args,db):
    from crawlee import Request,ConcurrencySettings
    from crawlee.configuration import Configuration
    from crawlee.storages import RequestQueue
    from crawlee.storage_clients import FileSystemStorageClient
    from crawlee.statistics import Statistics
    from crawlee.events import LocalEventManager
    from rel_crawlee import RelCrawler
    ids=parse_response(args.ids_html.read_text())['objectIds']
    if not ids or len(set(ids))!=len(ids): raise ValueError('Missing or duplicate parcel IDs')
    with db: db.execute("INSERT OR REPLACE INTO inventory_state VALUES('expected_parcels',?)",(str(len(ids)),))
    config=Configuration(storage_dir=str(args.output/'county-inventory-queue'),purge_on_start=False)
    storage=FileSystemStorageClient();queue=await RequestQueue.open(name='county-inventory-v1',configuration=config,storage_client=storage)
    session=await asyncio.to_thread(inventory_session,args.output)
    crawler=RelCrawler(session_id=session,rel_base_url='http://127.0.0.1:17319/v1',configuration=config,request_manager=queue,storage_client=storage,event_manager=LocalEventManager(),statistics=Statistics.with_default_state(persistence_enabled=False),max_requests_per_crawl=150,max_request_retries=1,navigation_timeout=timedelta(seconds=60),concurrency_settings=ConcurrencySettings(max_concurrency=1,desired_concurrency=1,max_tasks_per_minute=20))
    @crawler.router.default_handler
    async def handle(context):
        html=await context.page.content();expected=context.request.user_data['ids'];batch=context.request.user_data['batch']
        count=save_batch(db,batch,context.page.url,html,expected)
        print(json.dumps({'batch':batch,'parcels':count,'stored':db.execute('SELECT count(*) FROM parcels').fetchone()[0],'expected':len(ids)}),flush=True)
    @crawler.failed_request_handler
    async def failed(context,error):
        crawler.stop('County inventory batch failed; partial inventory retained')
    seeds=[]
    for batch,start in enumerate(range(0,len(ids),1000)):
        selected=ids[start:start+1000]
        if db.execute('SELECT 1 FROM captures WHERE batch=?',(batch,)).fetchone(): continue
        url=SOURCE+'/query?'+urlencode({'objectIds':','.join(map(str,selected)),'outFields':FIELDS,'returnGeometry':'false','f':'json'})
        seeds.append(Request.from_url(url,unique_key='county-inventory-v1:'+str(batch)+(':local-rel-recovery-v1' if batch==args.recover_rel_batch else ''),user_data={'ids':selected,'batch':batch}))
    stats=await crawler.run(seeds,purge_request_queue=False)
    count=db.execute('SELECT count(*) FROM parcels').fetchone()[0]
    if count!=len(ids) or stats.requests_failed: raise ValueError('County inventory incomplete; resume saved queue')
    with db: db.execute("INSERT OR REPLACE INTO inventory_state VALUES('complete','1')")
    print(json.dumps({'complete':True,'parcels':count,'residential':db.execute('SELECT count(*) FROM parcels WHERE residential=1').fetchone()[0]}),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--ids-html',type=Path,required=True);p.add_argument('--recover-rel-batch',type=int,help='Explicitly recover one batch that failed only in local REL startup');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    with (a.output/'county-inventory.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);db=initialize(a.output/'county-inventory.sqlite3')
        try: asyncio.run(run(a,db))
        finally: db.close()

if __name__=='__main__': main()
