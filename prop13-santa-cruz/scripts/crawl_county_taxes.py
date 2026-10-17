#!/usr/bin/env python3
"""Collect official county tax bills through REL native browser interactions.

Read-only lookup only. Never follows payment links or submits payment forms.
The existing inventory database is read-only; results live in a separate SQLite
database. The shared run.lock prevents overlapping inventory/tax crawlers.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, parse_qs, quote
from urllib.request import Request as HttpRequest, urlopen

SITE = 'https://ttc.co.santa-cruz.ca.us'
SEARCH = SITE + '/TaxBills/'


async def ensure_session(args):
    """Manage only this collector's persistent configured session via loopback RPC."""
    endpoint=urlsplit(args.base_url)
    if endpoint.scheme!='http' or endpoint.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('REL management requires a loopback endpoint')
    path=args.session_file
    state=json.loads(path.read_text()) if path.exists() else {}
    if state and (state.get('profile')!=args.profile or state.get('base_url')!=args.base_url or state.get('group')!='prop13-county-taxes'):
        raise ValueError('County session belongs to another profile, group or endpoint')
    async def rpc(method,suffix,payload=None):
        def send():
            request=HttpRequest(args.base_url+suffix,method=method,
                data=json.dumps(payload).encode() if payload else None,headers={'Content-Type':'application/json'})
            try: response=urlopen(request,timeout=20)
            except HTTPError as error: response=error
            with response: return json.load(response)
        return await asyncio.to_thread(send)
    if state.get('session_id'):
        reply=await rpc('GET','/sessions/'+quote(state['session_id'],safe=''))
        if reply.get('status')=='ok':
            session=reply['data']['session']
            if session.get('profile')!=args.profile or session.get('group')!='prop13-county-taxes':
                raise ValueError('Refusing to use a session outside the county collector group')
            return state['session_id']
        if reply.get('error',{}).get('id')!='SESSION_NOT_FOUND': raise RuntimeError(str(reply.get('error')))
    reply=await rpc('POST','/sessions',{'profile':args.profile,'group':'prop13-county-taxes','lifetime':{'type':'indefinite'}})
    if reply.get('status')!='ok': raise RuntimeError(str(reply.get('error')))
    state={'profile':args.profile,'group':'prop13-county-taxes','base_url':args.base_url,'session_id':reply['data']['session']['id']}
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(state,indent=2)+'\n');temporary.replace(path)
    return state['session_id']


def validate_detail_url(url):
    parsed=urlsplit(url);query=parse_qs(parsed.query)
    if (parsed.scheme!='https' or parsed.netloc!=urlsplit(SITE).netloc
        or parsed.path!='/Taxbills/TaxBillDetails' or parsed.fragment
        or set(query)!={'TaxBillID','RevObjID'}
        or any(len(values)!=1 or not values[0].isdigit() for values in query.values())):
        raise ValueError('Refusing a URL outside the observed read-only bill-detail route')
    return url


def page_information(html):
    from selectolax.parser import HTMLParser
    tree=HTMLParser(html)
    for node in tree.css('script,style,noscript'): node.decompose()
    text=' '.join(tree.text(separator=' ',strip=True).split())
    rows=[]
    for row in tree.css('tr'):
        cells=[];node=row.child
        while node is not None:
            if node.tag in ('td','th'):
                if node.css_first('table') is not None: cells=[];break
                cells.append(' '.join(node.text(separator=' ',strip=True).split()))
            node=node.next
        if cells: rows.append(cells)
    links=[{'text':' '.join(n.text(separator=' ',strip=True).split()),'href':n.attributes['href']} for n in tree.css('a[href]')]
    return {'text':text,'rows':rows,'links':links}


def save_capture(db,apn,url,html,kind):
    info=page_information(html);stamp=datetime.now(timezone.utc).isoformat()
    with db:
        capture=db.execute('INSERT INTO captures(apn,captured_at,url,html,kind,text,data_json,sha256) VALUES(?,?,?,?,?,?,?,?)',
            (apn,stamp,url,html,kind,info['text'],json.dumps(info),hashlib.sha256(html.encode()).hexdigest())).lastrowid
        db.execute('INSERT INTO page_search(rowid,apn,kind,url,text) VALUES(?,?,?,?,?)',(capture,apn,kind,url,info['text']))
    return capture


def list_all_bills(html,apn):
    """Lossless row records for every bill type, separate from annual analysis."""
    from selectolax.parser import HTMLParser
    tree=HTMLParser(html);result=[];occurrences=Counter()
    for table in tree.css('table.TaxBillContainer'):
        heading=table.css_first('.TaxBillHeader')
        title=' '.join(heading.text(separator=' ',strip=True).split()) if heading else 'Unclassified bill'
        rows=[[' '.join(n.text(separator=' ',strip=True).split()) for n in row.css('th,td')] for row in table.css('tr')]
        installments=[]
        for row in rows:
            if len(row)==4 and re.search(r'Installment',row[0],re.I) and row[0].strip() not in ('Installment','Both Installment'):
                installments.append({'label':row[0],'due_date_text':row[1],'paid_date_text':row[2],'amount_text':row[3],
                    'payment_status':'unpaid' if row[2] in ('Not Paid','Past Due') else 'paid' if re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}',row[2]) else 'unknown'})
        identity=[apn,title,[(i['label'],i['due_date_text']) for i in installments]]
        key=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        occurrences[key]+=1;key+=':'+str(occurrences[key])
        link=table.css_first('a[href]');url=urljoin(SITE,link.attributes['href']) if link else None
        if url: validate_detail_url(url)
        year=re.search(r'(\d{4})\s*-\s*(\d{4})',title)
        # Link IDs are temporary county session handles, not permanent bill IDs.
        data_hash=hashlib.sha256(json.dumps([title,installments],sort_keys=True).encode()).hexdigest()
        result.append({'key':key,'title':title,'fiscal_start':int(year[1]) if year else None,
            'bill_type':re.sub(r'^\d{4}\s*-\s*\d{4}\s*','',title),'rows':rows,'installments':installments,
            'unpaid_installments':sum(i['payment_status']=='unpaid' for i in installments),
            'url':url,'data_hash':data_hash})
    return result


def save_listed_bills(db,apn,html,capture):
    entries=list_all_bills(html,apn)
    with db:
        for entry in entries:
            old=db.execute('SELECT data_hash,detail_status,detail_capture_id FROM listed_bills WHERE apn=? AND bill_key=?',(apn,entry['key'])).fetchone()
            same=old is not None and old['data_hash']==entry['data_hash']
            status=old['detail_status'] if same else 'pending' if entry['url'] else 'unavailable'
            detail=old['detail_capture_id'] if same else None
            db.execute('''INSERT OR REPLACE INTO listed_bills(apn,bill_key,title,bill_type,fiscal_start,rows_json,
                installments_json,unpaid_installments,source_url,history_capture_id,data_hash,detail_status,detail_capture_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(apn,entry['key'],entry['title'],entry['bill_type'],entry['fiscal_start'],
                json.dumps(entry['rows']),json.dumps(entry['installments']),entry['unpaid_installments'],entry['url'],capture,
                entry['data_hash'],status,detail))
    return entries


def normalize_apn(value):
    value = re.sub(r'[^0-9]', '', value)
    if not 8 <= len(value) <= 12:
        raise ValueError('Unexpected county parcel identifier')
    return value


def amount(value):
    match = re.fullmatch(r'\$([\d,]+\.\d{2})', value.strip())
    if not match:
        raise ValueError('Expected an exact county currency amount: ' + value)
    return Decimal(match[1].replace(',', ''))


def parse_bills(html, expected_apn, issues=None):
    from selectolax.parser import HTMLParser
    tree = HTMLParser(html)
    header = tree.css_first('table.paddedtable')
    text = header.text(separator=' ', strip=True) if header else ''
    match = re.search(r'Parcel/Account\s*#\s*([\d-]+)', text)
    if not match or normalize_apn(match[1]) != normalize_apn(expected_apn):
        raise ValueError('County result belongs to a different or unidentified parcel')
    bills = []
    for table in tree.css('table.TaxBillContainer'):
        heading = table.css_first('.TaxBillHeader')
        if not heading:
            continue
        title = heading.text(separator=' ', strip=True)
        year = re.fullmatch(r'(\d{4})\s*-\s*(\d{4})\s+Annual Tax Bill', title)
        if not year or int(year[2]) != int(year[1]) + 1:
            continue  # Supplemental/default/escape bills are not annual bills.
        try:
            installments = []
            for row in table.css('tr'):
                cells = row.css('td')
                if len(cells) != 4:
                    continue
                label = ' '.join(cells[0].text(separator=' ', strip=True).split())
                if label not in ('First Installment', 'Second Installment'):
                    continue
                due = cells[1].text(strip=True)
                paid = cells[2].text(strip=True)
                paid_date = None
                if paid != 'Not Paid':
                    paid_date = datetime.strptime(paid, '%m/%d/%Y').date().isoformat()
                installments.append({'installment': label, 'due_date': datetime.strptime(due, '%m/%d/%Y').date().isoformat(),
                                     'paid_date': paid_date, 'amount': str(amount(cells[3].text(strip=True)))})
            if {v['installment'] for v in installments} != {'First Installment','Second Installment'} or len(installments) != 2:
                if issues is not None:
                    issues.append({'fiscal_start':int(year[1]),'reason':'Annual bill does not contain exactly two identified installments'})
                    continue
                raise ValueError('Annual bill does not contain exactly two identified installments')
            link = table.css_first('a[href]')
            details_url = urljoin(SITE, link.attributes['href']) if link else None
            if details_url and (urlsplit(details_url).hostname != urlsplit(SITE).hostname
                                or urlsplit(details_url).path != '/Taxbills/TaxBillDetails'):
                raise ValueError('Unexpected bill-detail URL')
            total = sum((Decimal(v['amount']) for v in installments), Decimal(0))
            paid_total = sum((Decimal(v['amount']) for v in installments if v['paid_date']), Decimal(0))
            bills.append({'fiscal_start': int(year[1]), 'fiscal_end': int(year[2]),
                          'total_billed': str(total), 'amount_paid': str(paid_total),
                          'fully_paid': all(v['paid_date'] for v in installments),
                          'installments': installments, 'details_url': details_url})
        except ValueError as error:
            if issues is None: raise
            issues.append({'fiscal_start':int(year[1]),'reason':str(error)})
    duplicates={year for year,count in Counter(b['fiscal_start'] for b in bills).items() if count>1}
    if duplicates:
        if issues is None: raise ValueError('Multiple complete annual bills for the same fiscal year')
        issues.extend({'fiscal_start':year,'reason':'Multiple complete annual bills for the same fiscal year'} for year in sorted(duplicates))
        bills=[b for b in bills if b['fiscal_start'] not in duplicates]
    if not bills:
        raise ValueError('No complete annual tax bills in the county response')
    return bills


def init_database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS targets(apn TEXT PRIMARY KEY,zpid TEXT NOT NULL,address TEXT,
          status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
          captured_at TEXT,error TEXT);
      CREATE TABLE IF NOT EXISTS captures(id INTEGER PRIMARY KEY,apn TEXT NOT NULL,
          captured_at TEXT NOT NULL,url TEXT NOT NULL,html TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS bills(apn TEXT NOT NULL,fiscal_start INTEGER NOT NULL,
          fiscal_end INTEGER NOT NULL,total_billed TEXT NOT NULL,amount_paid TEXT NOT NULL,
          fully_paid INTEGER NOT NULL,installments_json TEXT NOT NULL,details_url TEXT,
          capture_id INTEGER NOT NULL,PRIMARY KEY(apn,fiscal_start));
      CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY,apn TEXT,occurred_at TEXT,error TEXT,terminal INTEGER);
      CREATE TABLE IF NOT EXISTS parse_issues(apn TEXT,fiscal_start INTEGER,reason TEXT,capture_id INTEGER);
      CREATE TABLE IF NOT EXISTS listed_bills(apn TEXT NOT NULL,bill_key TEXT NOT NULL,title TEXT,bill_type TEXT,
          fiscal_start INTEGER,rows_json TEXT,installments_json TEXT,unpaid_installments INTEGER,
          source_url TEXT,history_capture_id INTEGER,data_hash TEXT,detail_status TEXT,
          detail_capture_id INTEGER,detail_error TEXT,PRIMARY KEY(apn,bill_key));
      CREATE VIRTUAL TABLE IF NOT EXISTS page_search USING fts5(apn,kind,url UNINDEXED,text);
      CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(captures)')}
    with db:
        for name,definition in [('kind',"TEXT DEFAULT 'history'"),('text','TEXT'),('data_json','TEXT'),('sha256','TEXT')]:
            if name not in columns: db.execute(f'ALTER TABLE captures ADD COLUMN {name} {definition}')
    return db


def prepare_full_capture(db):
    """Upgrade earlier summary-only captures without deleting any history."""
    if db.execute("SELECT 1 FROM settings WHERE key='full-bill-pages-v1'").fetchone(): return
    for row in db.execute('SELECT id,apn,url,html FROM captures WHERE kind=\'history\' ORDER BY id').fetchall():
        info=page_information(row['html'])
        with db:
            db.execute('UPDATE captures SET text=?,data_json=?,sha256=? WHERE id=?',
                (info['text'],json.dumps(info),hashlib.sha256(row['html'].encode()).hexdigest(),row['id']))
            db.execute('INSERT OR REPLACE INTO page_search(rowid,apn,kind,url,text) VALUES(?,?,?,?,?)',
                (row['id'],row['apn'],'history',row['url'],info['text']))
            save_listed_bills(db,row['apn'],row['html'],row['id'])
    with db:
        db.execute("UPDATE targets SET status='history_complete' WHERE status='complete'")
        db.execute("INSERT INTO settings VALUES('full-bill-pages-v1','1')")


def seed_targets(db, inventory):
    source = sqlite3.connect(inventory.resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    # Keep the two small joins separate: the combined join can multiply parcel
    # scans on the large private capture database without changing its indexes.
    eligible = {r[0] for r in source.execute('''WITH latest AS
        (SELECT zpid,max(capture_id) AS capture_id FROM observations GROUP BY zpid)
        SELECT l.zpid FROM latest l JOIN observations o
        ON o.zpid=l.zpid AND o.capture_id=l.capture_id WHERE o.zestimate>0''')}
    rows = source.execute('''SELECT DISTINCT p.apn,a.zpid,a.address
        FROM inventory_parcels p JOIN inventory_addresses a USING(address_key)
        WHERE a.status='matched' AND p.classification='residential' ORDER BY a.address''')
    with db:
        for row in rows:
            if row['zpid'] not in eligible: continue
            db.execute('INSERT OR IGNORE INTO targets(apn,zpid,address) VALUES(?,?,?)',
                       (normalize_apn(row['apn']), row['zpid'], row['address']))
    source.close()


def summary(db):
    return {'targets': dict(db.execute('SELECT status,count(*) FROM targets GROUP BY status')),
            'annual_bills': db.execute('SELECT count(*) FROM bills').fetchone()[0],
            'history_years_needing_review': db.execute('SELECT count(*) FROM parse_issues').fetchone()[0],
            'all_bill_types': db.execute('SELECT count(*) FROM listed_bills').fetchone()[0],
            'detail_pages': dict(db.execute('SELECT detail_status,count(*) FROM listed_bills GROUP BY detail_status')),
            'last_capture': db.execute('SELECT max(captured_at) FROM captures').fetchone()[0]}


def seed_county_targets(db,path):
    if not path: return
    source=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    if not source.execute("SELECT 1 FROM inventory_state WHERE key='complete'").fetchone():
        raise ValueError('County inventory is incomplete; finish inventory before expanding the tax queue')
    with db:
        for apn,address in source.execute('SELECT apn,address FROM parcels WHERE residential=1 ORDER BY address'):
            db.execute('INSERT OR IGNORE INTO targets(apn,zpid,address) VALUES(?,?,?)',(normalize_apn(apn),'',address))
    source.close()


def import_priority_parcels(db,path):
    """Explicit county-sourced additions may lack a third-party estimate."""
    if not path: return []
    data=json.loads(path.read_text())
    source=data['source']
    if source!='https://sccgis.santacruzcountyca.gov/server/rest/services/giswebp/MapServer/25':
        raise ValueError('Priority parcel imports require the official county parcel layer')
    parcels=[(normalize_apn(row['apn']),row['address'].strip()) for row in data['parcels']]
    if any(not address for _,address in parcels) or len({apn for apn,_ in parcels})!=len(parcels):
        raise ValueError('Priority parcel import has missing addresses or duplicate APNs')
    with db:
        db.execute('CREATE TABLE IF NOT EXISTS priority_parcels(apn TEXT PRIMARY KEY,source_url TEXT,imported_at TEXT)')
        for apn,address in parcels:
            db.execute('INSERT OR IGNORE INTO targets(apn,zpid,address) VALUES(?,?,?)',(apn,'',address))
            db.execute('INSERT OR IGNORE INTO priority_parcels VALUES(?,?,?)',(apn,source,datetime.now(timezone.utc).isoformat()))
    return [apn for apn,address in sorted(parcels,key=lambda row:(int(re.match(r'\d+',row[1])[0]) if re.match(r'\d+',row[1]) else 0,row[1]))
            if db.execute('SELECT status FROM targets WHERE apn=?',(apn,)).fetchone()[0] in ('pending','running','history_complete')]


async def crawl(args, db):
    # Imports stay here so parser tests do not require the browser dependencies.
    from crawlee import ConcurrencySettings, Request
    from crawlee.configuration import Configuration
    from crawlee.storages import RequestQueue
    from crawlee.statistics import Statistics
    from crawlee.storage_clients import FileSystemStorageClient
    from crawlee.events import LocalEventManager
    from rel_crawlee import RelCrawler
    session = await ensure_session(args)
    config = Configuration(storage_dir=str(args.output/'county-crawlee'), purge_on_start=False, max_used_cpu_ratio=1.0)
    storage = FileSystemStorageClient()
    queue = await RequestQueue.open(name='county-full-bill-pages-v1', configuration=config, storage_client=storage)
    crawler = RelCrawler(session_id=session, rel_base_url=args.base_url, configuration=config,
        request_manager=queue, storage_client=storage, event_manager=LocalEventManager(),
        statistics=Statistics.with_default_state(persistence_enabled=False), max_requests_per_crawl=args.max_properties,
        max_request_retries=1, navigation_timeout=timedelta(seconds=60),
        request_handler_timeout=timedelta(minutes=15),
        concurrency_settings=ConcurrencySettings(max_concurrency=1,desired_concurrency=1,max_tasks_per_minute=args.rate))
    failures = 0

    def record_error(context, error, terminal):
        apn = context.request.user_data['apn']
        with db:
            db.execute('INSERT INTO errors(apn,occurred_at,error,terminal) VALUES(?,?,?,?)',
                       (apn,datetime.now(timezone.utc).isoformat(),str(error),int(terminal)))

    @crawler.router.default_handler
    async def handle(context):
        nonlocal failures
        apn = context.request.user_data['apn']
        with db:
            db.execute("UPDATE targets SET status='running',attempts=attempts+1,error=NULL WHERE apn=?",(apn,))
        page = context.page
        async def reject_challenge():
            from selectolax.parser import HTMLParser
            tree = HTMLParser(await page.content())
            for node in tree.css('script,style,noscript'): node.decompose()
            if re.search(r'captcha|verify.{0,30}human|access.{0,10}denied',tree.text(separator=' ',strip=True),re.I):
                context.request.no_retry = True
                crawler.stop('County access challenge; queue retained without session rotation')
                raise ValueError('Access denied / human verification challenge')
        await reject_challenge()
        await page.locator('#Parcel').wait_for(timeout=15000)
        await page.locator('#Parcel').fill(apn,timeout=15000)
        submit = page.locator('input[type="submit"][value="Submit"]')
        await submit.wait_for(timeout=15000)
        await submit.click(timeout=45000)
        await reject_challenge()
        await page.locator('table.TaxBillContainer').wait_for(timeout=20000)
        from selectolax.parser import HTMLParser
        tree = HTMLParser(await page.content())
        if tree.css_first('input[value="Show Tax Bill History"]'):
            control = page.locator('input[type="submit"][value="Show Tax Bill History"]')
            await control.wait_for(timeout=10000)
            await control.click(timeout=30000)
        await page.locator('table.TaxBillContainer').wait_for(timeout=15000)
        html = await page.content()
        if urlsplit(page.url).hostname != urlsplit(SITE).hostname:
            context.request.no_retry = True
            crawler.stop('Unexpected county redirect; manual inspection required')
            raise ValueError('County lookup left its allowed host')
        capture=save_capture(db,apn,page.url,html,'history')
        entries=save_listed_bills(db,apn,html,capture)
        # Keep evidence even when a county bill has a format we cannot parse.
        issues=[]
        try:
            bills = parse_bills(html, apn,issues)
        except ValueError as error:
            if str(error)!='No complete annual tax bills in the county response': raise
            bills=[]
            issues.append({'fiscal_start':None,'reason':str(error)})
        with db:
            db.execute('DELETE FROM parse_issues WHERE apn=?',(apn,))
            for issue in issues:
                db.execute('INSERT INTO parse_issues VALUES(?,?,?,?)',(apn,issue['fiscal_start'],issue['reason'],capture))
            for bill in bills:
                db.execute('''INSERT INTO bills VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(apn,fiscal_start)
                  DO UPDATE SET total_billed=excluded.total_billed,amount_paid=excluded.amount_paid,
                  fully_paid=excluded.fully_paid,installments_json=excluded.installments_json,
                  details_url=excluded.details_url,capture_id=excluded.capture_id''',
                  (apn,bill['fiscal_start'],bill['fiscal_end'],bill['total_billed'],bill['amount_paid'],
                   int(bill['fully_paid']),json.dumps(bill['installments']),bill['details_url'],capture))
            db.execute("UPDATE targets SET status='history_complete',error=NULL WHERE apn=?",(apn,))
        for entry in entries:
            saved=db.execute('SELECT detail_status FROM listed_bills WHERE apn=? AND bill_key=?',(apn,entry['key'])).fetchone()
            if saved['detail_status'] in ('complete','not_found','unavailable') or not entry['url']: continue
            # URL parameters are session-specific. Follow only URLs discovered
            # for this parcel in the current lookup, never old stored links.
            response=await page.goto(validate_detail_url(entry['url']),timeout=45000)
            await reject_challenge()
            if response.status in (401,403,429):
                context.request.no_retry=True;crawler.stop('County detail access denied; checkpoint retained')
                raise ValueError('Access denied on county bill detail')
            if response.status==404:
                with db: db.execute("UPDATE listed_bills SET detail_status='not_found',detail_error='HTTP 404' WHERE apn=? AND bill_key=?",(apn,entry['key']))
                continue
            detail_html=await page.content()
            detail=save_capture(db,apn,page.url,detail_html,'bill_detail')
            info=page_information(detail_html)
            identity=re.search(r'Parcel/Account\s*#\s*([\d-]+)',info['text'])
            if (response.status>=400 or urlsplit(page.url).hostname!=urlsplit(SITE).hostname
                or not identity or normalize_apn(identity[1])!=apn or entry['title'] not in info['text']):
                with db: db.execute("UPDATE listed_bills SET detail_status='failed',detail_error='Detail identity or response validation failed' WHERE apn=? AND bill_key=?",(apn,entry['key']))
                raise ValueError('County detail did not match the requested parcel and bill title')
            with db:
                db.execute("UPDATE listed_bills SET detail_status='complete',detail_capture_id=?,detail_error=NULL WHERE apn=? AND bill_key=?",(detail,apn,entry['key']))
            print(json.dumps({'parcel':apn,'bill_detail':entry['title'],'capture_id':detail}),flush=True)
            await asyncio.sleep(1)  # Conservative pacing, not a readiness wait.
        with db:
            db.execute("UPDATE targets SET status='complete',captured_at=?,error=NULL WHERE apn=?",(datetime.now(timezone.utc).isoformat(),apn))
        failures = 0
        print(json.dumps({'parcel':apn,'bills':len(bills),**summary(db)}),flush=True)

    @crawler.error_handler
    async def retry(context,error):
        record_error(context,error,False)
        context.request.forefront = True
        await asyncio.sleep(10)

    @crawler.failed_request_handler
    async def failed(context,error):
        nonlocal failures
        record_error(context,error,True)
        failures += 1
        status = getattr(error,'status_code',None)
        details = getattr(error,'details',None)
        if isinstance(details,dict): status = status or details.get('target_http_status')
        blocked = status in (401,403,429) or re.search(r'captcha|verify.*human|access.denied',str(error),re.I)
        state = 'blocked' if blocked else 'skipped' if status==404 else 'failed'
        with db:
            db.execute('UPDATE targets SET status=?,error=? WHERE apn=?',(state,str(error),context.request.user_data['apn']))
        if blocked or failures>=3:
            crawler.stop('Access restriction or consecutive-error safety limit; queue retained')

    seeds = [Request.from_url(SEARCH, unique_key='county-tax-full-v1:'+row['apn'],user_data={'apn':row['apn']})
             for row in db.execute("SELECT apn FROM targets WHERE status IN ('pending','running','history_complete') ORDER BY address")]
    for apn in reversed(import_priority_parcels(db,args.priority_file)):
        key='county-tax-full-v1:'+apn
        existing=await queue.get_request(key)
        if existing is not None:
            if existing.handled_at is None: await queue.reclaim_request(existing,forefront=True)
        else:
            await queue.add_requests([Request.from_url(SEARCH,unique_key=key,user_data={'apn':apn})],forefront=True,
                                     wait_for_all_requests_to_be_added=True)
    if args.repair_format_parcel:
        apn=normalize_apn(args.repair_format_parcel)
        row=db.execute('SELECT status,error FROM targets WHERE apn=?',(apn,)).fetchone()
        if row and row['status']=='failed' and row['error']=='Annual bill does not contain exactly two identified installments':
            # Explicit, versioned reprocessing of a known parser failure only.
            # Never retries access denials or erases the previous error record.
            await queue.add_requests([Request.from_url(SEARCH,unique_key='county-tax-format-v2:'+apn,user_data={'apn':apn})],
                                     forefront=True,wait_for_all_requests_to_be_added=True)
    print(json.dumps({'session':session,'profile':args.profile,'source':SEARCH,'resume':summary(db)}),flush=True)
    stats = await crawler.run(seeds,purge_request_queue=False)
    print(json.dumps({'finished':stats.requests_finished,'failed':stats.requests_failed,**summary(db)}),flush=True)
    return 2 if stats.requests_failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--base-url',default='http://127.0.0.1:17319/v1')
    parser.add_argument('--profile',choices=('Native','Datacenter'),default='Native',
                        help='REL browser profile; Datacenter explicitly uses the configured datacenter proxy')
    parser.add_argument('--session-file',type=Path,help='Persistent session state file (kept separate per profile)')
    parser.add_argument('--max-properties',default=12000,type=int)
    parser.add_argument('--rate',default=6,type=int,help='Parcel lookups per minute, serially')
    parser.add_argument('--summary',action='store_true')
    parser.add_argument('--priority-file',type=Path,help='County-sourced JSON parcels to add and move ahead of the saved queue')
    parser.add_argument('--county-inventory',type=Path,help='Completed county parcel inventory; adds residential targets without requiring estimates')
    parser.add_argument('--repair-format-parcel',help='Explicitly reprocess one known installment-format failure after a parser repair')
    args = parser.parse_args()
    if args.session_file is None:
        args.session_file=args.output/('county-session-datacenter.json' if args.profile=='Datacenter' else 'county-session.json')
    if not 1<=args.rate<=10 or args.max_properties<1: parser.error('rate must be 1–10 and max-properties positive')
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'run.lock').open('a') as lock:
        if not args.summary:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: parser.error('Another inventory or county-tax crawler holds run.lock')
        db = init_database(args.output/'county-tax.sqlite3')
        try:
            if args.summary: print(json.dumps(summary(db),indent=2));return 0
            prepare_full_capture(db)
            seed_targets(db,args.inventory)
            seed_county_targets(db,args.county_inventory)
            return asyncio.run(crawl(args,db))
        finally: db.close()


if __name__ == '__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
