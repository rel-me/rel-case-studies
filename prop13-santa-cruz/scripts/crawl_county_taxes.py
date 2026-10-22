#!/usr/bin/env python3
"""Collect official county tax bills through REL native browser interactions.

Read-only lookup only. Never follows payment links or submits payment forms.
The Zillow database supplies the read-only inventory. Tax records use namespaced
tables in the same SQLite database when --tax-database points to it; the shared
run.lock prevents overlapping inventory/tax crawlers.
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
import random
import re
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "zillow"))
from crawl_areas import parcel_areas
from profile_session import ProfileSessionApi, resolve_profile_session
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
    state = await resolve_profile_session(ProfileSessionApi(args.base_url), state, args.profile, 'prop13-county-taxes')
    state['base_url'] = args.base_url
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
        capture=db.execute('INSERT INTO county_tax_captures(apn,captured_at,url,html,kind,text,data_json,sha256) VALUES(?,?,?,?,?,?,?,?)',
            (apn,stamp,url,html,kind,info['text'],json.dumps(info),hashlib.sha256(html.encode()).hexdigest())).lastrowid
        db.execute('INSERT INTO county_tax_page_search(rowid,apn,kind,url,text) VALUES(?,?,?,?,?)',(capture,apn,kind,url,info['text']))
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
            old=db.execute('SELECT data_hash,detail_status,detail_capture_id FROM county_tax_listed_bills WHERE apn=? AND bill_key=?',(apn,entry['key'])).fetchone()
            same=old is not None and old['data_hash']==entry['data_hash']
            status=old['detail_status'] if same else 'pending' if entry['url'] else 'unavailable'
            detail=old['detail_capture_id'] if same else None
            db.execute('''INSERT OR REPLACE INTO county_tax_listed_bills(apn,bill_key,title,bill_type,fiscal_start,rows_json,
                installments_json,unpaid_installments,source_url,history_capture_id,data_hash,detail_status,detail_capture_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(apn,entry['key'],entry['title'],entry['bill_type'],entry['fiscal_start'],
                json.dumps(entry['rows']),json.dumps(entry['installments']),entry['unpaid_installments'],entry['url'],capture,
                entry['data_hash'],status,detail))
    return entries


def latest_annual_bill_keys(entries):
    """Return links for the newest annual bill only; history rows remain saved."""
    annual=[entry for entry in entries
            if entry['bill_type']=='Annual Tax Bill' and entry['fiscal_start'] is not None and entry['url']]
    if not annual: return set()
    latest=max(entry['fiscal_start'] for entry in annual)
    return {entry['key'] for entry in annual if entry['fiscal_start']==latest}


def normalize_apn(value):
    value = re.sub(r'[^0-9]', '', value)
    if not 8 <= len(value) <= 12:
        raise ValueError('Unexpected county parcel identifier')
    return value


def detail_matches_listing(apn, listing_text, detail_text, title, status, url):
    """Accept only the requested parcel or its explicitly paired listing alias."""
    identity = re.search(r'Parcel/Account\s*#\s*([\d-]+)', detail_text)
    if (status >= 400 or urlsplit(url).hostname != urlsplit(SITE).hostname
            or not identity or title not in detail_text):
        return False
    try:
        detail_apn = normalize_apn(identity[1])
        requested_apn = normalize_apn(apn)
        if detail_apn == requested_apn:
            return True
        # Only the parcel heading can authorize an alternate, never an address,
        # other bill row, or a number appearing elsewhere in the page.
        headings = list(re.finditer(r'Parcel/Account\s*#\s*([\d-]+)(?:\s*\(\s*([\d-]+)\s*\))?', listing_text))
        if len(headings) != 1:
            return False
        heading = headings[0]
        return (normalize_apn(heading[1]) == requested_apn and heading[2] is not None
                and normalize_apn(heading[2]) == detail_apn)
    except ValueError:
        return False


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
      CREATE TABLE IF NOT EXISTS county_tax_targets(apn TEXT PRIMARY KEY,zpid TEXT NOT NULL,address TEXT,
          status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
          captured_at TEXT,error TEXT);
      CREATE TABLE IF NOT EXISTS county_tax_captures(id INTEGER PRIMARY KEY,apn TEXT NOT NULL,
          captured_at TEXT NOT NULL,url TEXT NOT NULL,html TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS county_tax_bills(apn TEXT NOT NULL,fiscal_start INTEGER NOT NULL,
          fiscal_end INTEGER NOT NULL,total_billed TEXT NOT NULL,amount_paid TEXT NOT NULL,
          fully_paid INTEGER NOT NULL,installments_json TEXT NOT NULL,details_url TEXT,
          capture_id INTEGER NOT NULL,PRIMARY KEY(apn,fiscal_start));
      CREATE TABLE IF NOT EXISTS county_tax_errors(id INTEGER PRIMARY KEY,apn TEXT,occurred_at TEXT,error TEXT,terminal INTEGER);
      CREATE TABLE IF NOT EXISTS county_tax_parse_issues(apn TEXT,fiscal_start INTEGER,reason TEXT,capture_id INTEGER);
      CREATE TABLE IF NOT EXISTS county_tax_listed_bills(apn TEXT NOT NULL,bill_key TEXT NOT NULL,title TEXT,bill_type TEXT,
          fiscal_start INTEGER,rows_json TEXT,installments_json TEXT,unpaid_installments INTEGER,
          source_url TEXT,history_capture_id INTEGER,data_hash TEXT,detail_status TEXT,
          detail_capture_id INTEGER,detail_error TEXT,PRIMARY KEY(apn,bill_key));
      CREATE VIRTUAL TABLE IF NOT EXISTS county_tax_page_search USING fts5(apn,kind,url UNINDEXED,text);
      CREATE TABLE IF NOT EXISTS county_tax_settings(key TEXT PRIMARY KEY,value TEXT);
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(county_tax_captures)')}
    with db:
        for name,definition in [('kind',"TEXT DEFAULT 'history'"),('text','TEXT'),('data_json','TEXT'),('sha256','TEXT')]:
            if name not in columns: db.execute(f'ALTER TABLE county_tax_captures ADD COLUMN {name} {definition}')
    return db


def prepare_full_capture(db):
    """Upgrade earlier summary-only captures without deleting any history."""
    if db.execute("SELECT 1 FROM county_tax_settings WHERE key='full-bill-pages-v1'").fetchone(): return
    for row in db.execute('SELECT id,apn,url,html FROM county_tax_captures WHERE kind=\'history\' ORDER BY id').fetchall():
        info=page_information(row['html'])
        with db:
            db.execute('UPDATE county_tax_captures SET text=?,data_json=?,sha256=? WHERE id=?',
                (info['text'],json.dumps(info),hashlib.sha256(row['html'].encode()).hexdigest(),row['id']))
            db.execute('INSERT OR REPLACE INTO county_tax_page_search(rowid,apn,kind,url,text) VALUES(?,?,?,?,?)',
                (row['id'],row['apn'],'history',row['url'],info['text']))
            save_listed_bills(db,row['apn'],row['html'],row['id'])
    with db:
        db.execute("UPDATE county_tax_targets SET status='history_complete' WHERE status='complete'")
        db.execute("INSERT INTO county_tax_settings VALUES('full-bill-pages-v1','1')")


def estimated_parcels(inventory):
    """Return verified residential APN matches whose latest observation has an estimate."""
    source = sqlite3.connect(inventory.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        eligible = {r[0] for r in source.execute('''WITH latest AS
            (SELECT zpid,max(capture_id) AS capture_id FROM observations GROUP BY zpid)
            SELECT l.zpid FROM latest l JOIN observations o
            ON o.zpid=l.zpid AND o.capture_id=l.capture_id WHERE o.zestimate>0''')}
        rows = source.execute('''SELECT DISTINCT p.apn,a.zpid,a.address
            FROM inventory_parcels p JOIN inventory_addresses a USING(address_key)
            WHERE a.status='matched' AND p.classification='residential' ORDER BY a.address''')
        return [(normalize_apn(apn), zpid, address) for apn,zpid,address in rows
                if apn and zpid in eligible]
    finally:
        source.close()


def seed_targets(db, inventory):
    with db:
        for apn,zpid,address in estimated_parcels(inventory):
            db.execute('INSERT OR IGNORE INTO county_tax_targets(apn,zpid,address) VALUES(?,?,?)',
                       (apn,zpid,address))


def ordered_pending_targets(db, inventory, explicit_priority, county_inventory=None):
    """Geographic area first; Dougmar and saved estimates first within each area."""
    areas = parcel_areas(county_inventory)
    estimated = {apn for apn,_,_ in estimated_parcels(inventory)}
    explicit = {apn:index for index,apn in enumerate(explicit_priority)}
    pending = list(db.execute("SELECT apn,address FROM county_tax_targets WHERE status IN ('pending','running','history_complete')"))
    pending.sort(key=lambda row: (areas.get(normalize_apn(row[0]),10),
                 0 if row[0] in explicit else 1 if row[0] in estimated else 2,
                 explicit.get(row[0],0),row[1] or '',row[0]))
    ordered = [row[0] for row in pending]
    # Reorder only pending resources; never clear the queue or requeue terminal entries.
    forefront = ordered if county_inventory else [apn for apn in ordered if apn in explicit or apn in estimated]
    return ordered,forefront


def summary(db):
    return {'targets': dict(db.execute('SELECT status,count(*) FROM county_tax_targets GROUP BY status')),
            'annual_bills': db.execute('SELECT count(*) FROM county_tax_bills').fetchone()[0],
            'history_years_needing_review': db.execute('SELECT count(*) FROM county_tax_parse_issues').fetchone()[0],
            'all_bill_types': db.execute('SELECT count(*) FROM county_tax_listed_bills').fetchone()[0],
            'detail_pages': dict(db.execute('SELECT detail_status,count(*) FROM county_tax_listed_bills GROUP BY detail_status')),
            'last_capture': db.execute('SELECT max(captured_at) FROM county_tax_captures').fetchone()[0]}


def seed_county_targets(db,path):
    if not path: return
    source=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    if not source.execute("SELECT 1 FROM inventory_state WHERE key='complete'").fetchone():
        raise ValueError('County inventory is incomplete; finish inventory before expanding the tax queue')
    with db:
        for apn,address in source.execute('SELECT apn,address FROM parcels WHERE residential=1 ORDER BY address'):
            db.execute('INSERT OR IGNORE INTO county_tax_targets(apn,zpid,address) VALUES(?,?,?)',(normalize_apn(apn),'',address))
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
        db.execute('CREATE TABLE IF NOT EXISTS county_tax_priority_parcels(apn TEXT PRIMARY KEY,source_url TEXT,imported_at TEXT)')
        for apn,address in parcels:
            db.execute('INSERT OR IGNORE INTO county_tax_targets(apn,zpid,address) VALUES(?,?,?)',(apn,'',address))
            db.execute('INSERT OR IGNORE INTO county_tax_priority_parcels VALUES(?,?,?)',(apn,source,datetime.now(timezone.utc).isoformat()))
    return [apn for apn,address in sorted(parcels,key=lambda row:(int(re.match(r'\d+',row[1])[0]) if re.match(r'\d+',row[1]) else 0,row[1]))
            if db.execute('SELECT status FROM county_tax_targets WHERE apn=?',(apn,)).fetchone()[0] in ('pending','running','history_complete')]


class CountyNavigationError(RuntimeError):
    def __init__(self, status):
        self.status_code = status
        super().__init__(f'County navigation returned HTTP {status}')


async def bind_history_page(args, session):
    """REL history requires an observed active page, not just a saved capture."""
    def observe():
        request=HttpRequest(args.base_url+'/observe',method='POST',
            data=json.dumps({'session_id':session,'mode':'semantic','timeout':15,'wait':0}).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=25) as response: reply=json.load(response)
        if reply.get('status')!='ok': raise RuntimeError(str(reply.get('error')))
    await asyncio.to_thread(observe)


async def restore_search_form(page, activate, history_supported=True, force_reload=False):
    """Reuse normal history; retry via a fresh GET instead of trusting stale state."""
    def is_search():
        return urlsplit(page.url).hostname==urlsplit(SITE).hostname and urlsplit(page.url).path.lower().rstrip('/')=='/taxbills'
    if not force_reload and is_search():
        await page.locator('#Parcel').wait_for(timeout=15000)
        return 'already_on_form'
    path=urlsplit(page.url).path.lower()
    if not force_reload and history_supported and urlsplit(page.url).hostname==urlsplit(SITE).hostname and path in ('/taxbills/taxbills','/taxbills/taxbilldetails'):
        await activate()
        response=await page.go_back(timeout=20000)
        if response and response.status in (401,403,429): raise CountyNavigationError(response.status)
        if is_search():
            await page.locator('#Parcel').wait_for(timeout=15000)
            return 'history_return'
    response=await page.goto(SEARCH,timeout=45000)
    if response.status>=400: raise CountyNavigationError(response.status)
    await page.locator('#Parcel').wait_for(timeout=15000)
    return 'form_reload'


async def crawl(args, db):
    # Imports stay here so parser tests do not require the browser dependencies.
    from crawlee import ConcurrencySettings, Request
    from crawlee.configuration import Configuration
    from crawlee.storages import RequestQueue
    from crawlee.statistics import Statistics
    from crawlee.storage_clients import FileSystemStorageClient
    from crawlee.events import LocalEventManager
    from crawlee.crawlers import BasicCrawler
    from rel_playwright.async_api import async_playwright
    session = await ensure_session(args)
    # The installed legacy adapter sends tab IDs to REL's group-based history
    # route. Use one GET form load per parcel until that adapter is compatible.
    # All site traffic still goes through the same REL tab and profile.
    history_supported = not json.loads(args.session_file.read_text()).get('group_id')
    config = Configuration(storage_dir=str(args.output/'county-crawlee'), purge_on_start=False, max_used_cpu_ratio=1.0)
    storage = FileSystemStorageClient()
    queue = await RequestQueue.open(name='county-full-bill-pages-v1', configuration=config, storage_client=storage)
    # BasicCrawler owns the same persistent queue/retries. The awaited REL Page
    # owns serial navigation so Crawlee does not reload SEARCH before each APN.
    page = None
    crawler = BasicCrawler(configuration=config, use_session_pool=False, retry_on_blocked=False,
        respect_robots_txt_file=False,
        request_manager=queue, storage_client=storage, event_manager=LocalEventManager(),
        statistics=Statistics.with_default_state(persistence_enabled=False), max_requests_per_crawl=args.max_properties,
        max_request_retries=1,
        request_handler_timeout=timedelta(minutes=15),
        concurrency_settings=ConcurrencySettings(max_concurrency=1,desired_concurrency=1,max_tasks_per_minute=args.rate))
    progress_path=args.output/'county-progress.json'
    progress=json.loads(progress_path.read_text()) if progress_path.exists() else {}
    failures=progress.get('consecutive_failures',0)
    limit=getattr(args,'max_consecutive_failures',10)
    def save_progress(reason=None):
        progress.update(consecutive_failures=failures, failure_limit=limit,
                        stop_reason=reason, updated_at=datetime.now(timezone.utc).isoformat())
        temporary=progress_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(progress,indent=2)+'\n');temporary.replace(progress_path)
    if failures>limit:
        print('Consecutive failure limit already reached; explicit continuation required',flush=True)
        return 2

    def record_error(context, error, terminal):
        apn = context.request.user_data['apn']
        with db:
            db.execute('INSERT INTO county_tax_errors(apn,occurred_at,error,terminal) VALUES(?,?,?,?)',
                       (apn,datetime.now(timezone.utc).isoformat(),str(error),int(terminal)))

    @crawler.router.default_handler
    async def handle(context):
        nonlocal failures
        apn = context.request.user_data['apn']
        with db:
            db.execute("UPDATE county_tax_targets SET status='running',attempts=attempts+1,error=NULL WHERE apn=?",(apn,))
        async def search_form(force_reload=False):
            try:
                return await restore_search_form(page,lambda:bind_history_page(args,session),history_supported,force_reload=force_reload)
            except CountyNavigationError as error:
                if error.status_code in (401,403,429):
                    context.request.no_retry=True
                    crawler.stop('County access denied; checkpoint retained')
                raise
        # The adapter's cached URL/DOM can be stale after a timed-out action.
        # Spend the existing Crawlee retry on one clean GET of the search form,
        # not on waiting for an input on the previous results/detail page.
        mode=await search_form(force_reload=context.request.retry_count > 0)
        print(json.dumps({'search_form':mode}),flush=True)
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
        # The initial result already lists the current bill. Do not submit
        # "Show Tax Bill History": it adds a server round trip for older bills
        # that this latest-bill collector does not need.
        html = await page.content()
        if urlsplit(page.url).hostname != urlsplit(SITE).hostname:
            context.request.no_retry = True
            crawler.stop('Unexpected county redirect; manual inspection required')
            raise ValueError('County lookup left its allowed host')
        capture=save_capture(db,apn,page.url,html,'history')
        entries=save_listed_bills(db,apn,html,capture)
        latest_bill_keys=latest_annual_bill_keys(entries)
        # Retain the full initial listing and its raw installment rows. Older
        # history captures stay intact; only the latest listed annual detail is followed.
        with db:
            for entry in entries:
                if entry['key'] not in latest_bill_keys:
                    db.execute("UPDATE county_tax_listed_bills SET detail_status='not_selected' WHERE apn=? AND bill_key=? AND detail_status='pending'",
                               (apn,entry['key']))
        # Keep evidence even when a county bill has a format we cannot parse.
        issues=[]
        try:
            bills = parse_bills(html, apn,issues)
        except ValueError as error:
            if str(error)!='No complete annual tax bills in the county response': raise
            bills=[]
            issues.append({'fiscal_start':None,'reason':str(error)})
        with db:
            db.execute('DELETE FROM county_tax_parse_issues WHERE apn=?',(apn,))
            for issue in issues:
                db.execute('INSERT INTO county_tax_parse_issues VALUES(?,?,?,?)',(apn,issue['fiscal_start'],issue['reason'],capture))
            for bill in bills:
                db.execute('''INSERT INTO county_tax_bills VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(apn,fiscal_start)
                  DO UPDATE SET total_billed=excluded.total_billed,amount_paid=excluded.amount_paid,
                  fully_paid=excluded.fully_paid,installments_json=excluded.installments_json,
                  details_url=excluded.details_url,capture_id=excluded.capture_id''',
                  (apn,bill['fiscal_start'],bill['fiscal_end'],bill['total_billed'],bill['amount_paid'],
                   int(bill['fully_paid']),json.dumps(bill['installments']),bill['details_url'],capture))
            db.execute("UPDATE county_tax_targets SET status='history_complete',error=NULL WHERE apn=?",(apn,))
        for entry in entries:
            if entry['key'] not in latest_bill_keys: continue
            saved=db.execute('SELECT detail_status FROM county_tax_listed_bills WHERE apn=? AND bill_key=?',(apn,entry['key'])).fetchone()
            if saved['detail_status'] in ('complete','not_found','unavailable') or not entry['url']: continue
            # URL parameters are session-specific. Follow only URLs discovered
            # for this parcel in the current lookup, never old stored links.
            # Restore the cached form now; the detail replaces the forward
            # result entry, so the next Back goes directly to the GET form.
            if history_supported:
                await search_form()
            response=await page.goto(validate_detail_url(entry['url']),timeout=45000)
            await reject_challenge()
            if response.status in (401,403,429):
                context.request.no_retry=True;crawler.stop('County detail access denied; checkpoint retained')
                raise ValueError('Access denied on county bill detail')
            if response.status==404:
                with db: db.execute("UPDATE county_tax_listed_bills SET detail_status='not_found',detail_error='HTTP 404' WHERE apn=? AND bill_key=?",(apn,entry['key']))
                continue
            detail_html=await page.content()
            detail=save_capture(db,apn,page.url,detail_html,'bill_detail')
            info=page_information(detail_html)
            if not detail_matches_listing(apn, page_information(html)['text'], info['text'],
                                          entry['title'], response.status, page.url):
                with db: db.execute("UPDATE county_tax_listed_bills SET detail_status='failed',detail_error='Detail identity or response validation failed' WHERE apn=? AND bill_key=?",(apn,entry['key']))
                raise ValueError('County detail did not match the requested parcel and bill title')
            with db:
                db.execute("UPDATE county_tax_listed_bills SET detail_status='complete',detail_capture_id=?,detail_error=NULL WHERE apn=? AND bill_key=?",(detail,apn,entry['key']))
            print(json.dumps({'parcel':apn,'bill_detail':entry['title'],'capture_id':detail}),flush=True)
            await asyncio.sleep(1)  # Conservative pacing, not a readiness wait.
        with db:
            db.execute("UPDATE county_tax_targets SET status='complete',captured_at=?,error=NULL WHERE apn=?",(datetime.now(timezone.utc).isoformat(),apn))
        failures = 0
        save_progress()
        print(json.dumps({'parcel':apn,'bills':len(bills),**summary(db)}),flush=True)
        # Add modest jitter between parcels in addition to the crawl-wide rate cap.
        await asyncio.sleep(random.uniform(3.0,4.0))

    @crawler.error_handler
    async def retry(context,error):
        record_error(context,error,False)
        details=getattr(error,'details',None)
        status=getattr(error,'status_code',None)
        if isinstance(details,dict): status=status or details.get('target_http_status')
        if status in (401,403,429) or re.search(r'captcha|verify.*human|access.denied',str(error),re.I):
            context.request.no_retry=True
            crawler.stop('County access restriction; checkpoint retained')
            return
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
            db.execute('UPDATE county_tax_targets SET status=?,error=? WHERE apn=?',(state,str(error),context.request.user_data['apn']))
        reason=('Access restriction; queue retained' if blocked else
                f'More than {limit} consecutive failures; queue retained' if failures>limit else None)
        save_progress(reason)
        if reason: crawler.stop(reason)

    explicit_priority = import_priority_parcels(db,args.priority_file)
    ordered, prioritized = ordered_pending_targets(db,args.inventory,explicit_priority,args.county_inventory)
    seeds = [Request.from_url(SEARCH, unique_key='county-tax-full-v1:'+apn,user_data={'apn':apn})
             for apn in ordered]
    # Forefront is LIFO: insert in reverse so the geographic order stays first.
    # Updating existing pending requests preserves handled/failed queue entries.
    await queue.add_requests([
        Request.from_url(SEARCH,unique_key='county-tax-full-v1:'+apn,user_data={'apn':apn})
        for apn in reversed(prioritized)],forefront=True,
        wait_time_between_batches=timedelta(0),wait_for_all_requests_to_be_added=True)
    print(json.dumps({'queue_priority':'Santa Cruz city, Live Oak approximation, Capitola, Soquel, other areas; estimates within area',
                      'prioritized_pending':len(prioritized),'pending_total':len(ordered)}),flush=True)
    if args.repair_format_parcel:
        apn=normalize_apn(args.repair_format_parcel)
        row=db.execute('SELECT status,error FROM county_tax_targets WHERE apn=?',(apn,)).fetchone()
        if row and row['status']=='failed' and row['error']=='Annual bill does not contain exactly two identified installments':
            # Explicit, versioned reprocessing of a known parser failure only.
            # Never retries access denials or erases the previous error record.
            await queue.add_requests([Request.from_url(SEARCH,unique_key='county-tax-format-v2:'+apn,user_data={'apn':apn})],
                                     forefront=True,wait_for_all_requests_to_be_added=True)
    print(json.dumps({'session':session,'profile':args.profile,'source':SEARCH,'resume':summary(db)}),flush=True)
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(session_id=session,rel_base_url=args.base_url)
        page=await browser.new_page()
        stats = await crawler.run(seeds,purge_request_queue=False)
    print(json.dumps({'finished':stats.requests_finished,'failed':stats.requests_failed,**summary(db)}),flush=True)
    return 2 if stats.requests_failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--tax-database',type=Path,
                        help='Database for county-tax tables; use the inventory path to keep both datasets in one SQLite file')
    parser.add_argument('--base-url',default='http://127.0.0.1:17319/v1')
    parser.add_argument('--profile',choices=('Native','Datacenter'),default='Native',
                        help='REL browser profile; Datacenter explicitly uses the configured datacenter proxy')
    parser.add_argument('--session-file',type=Path,help='Persistent session state file (kept separate per profile)')
    parser.add_argument('--max-properties',default=12000,type=int)
    parser.add_argument('--rate',default=6,type=int,help='Parcel lookups per minute, serially')
    parser.add_argument('--max-consecutive-failures',default=10,type=int,help='Stop after more than this many consecutive failed resources')
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
        db = init_database(args.tax_database or args.output/'county-tax.sqlite3')
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
