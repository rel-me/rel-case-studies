#!/usr/bin/env python3
"""Bounded LoopNet discovery and Santa Cruz tax lookup through REL only."""
import argparse
import asyncio
from datetime import datetime, timezone, date, timedelta
import hashlib
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

ROOT = Path(__file__).resolve().parent
SEARCH = 'https://www.loopnet.com/search/commercial-real-estate/santa-cruz-county-ca/for-sale/'
TAX = 'https://ttc.co.santa-cruz.ca.us/Taxbills/'
DETAIL = re.compile(r'^/Listing/[^/]+/(\d+)/$')
APN = re.compile(r'\b\d{3}-\d{3}-\d{2}(?:-\d{3})?\b')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def text(node):
    return ' '.join(node.text(separator=' ', strip=True).split()) if node else ''


def address_key(value):
    # Preserve unit numbers, fractions and ranges; no fuzzy number matching.
    aliases = {'HIGHWAY': 'HWY', 'ROAD': 'RD', 'STREET': 'ST', 'AVENUE': 'AVE',
               'BOULEVARD': 'BLVD', 'DRIVE': 'DR', 'LANE': 'LN', 'WAY': 'WY',
               'APT': 'UNIT', 'SUITE': 'UNIT', 'STE': 'UNIT'}
    tokens = re.sub(r'[^A-Z0-9/\-]+', ' ', value.upper().replace('#', ' UNIT ')).split()
    return ' '.join(aliases.get(t, t) for t in tokens)


def apn_key(raw):
    if re.fullmatch(r'\d{3}-\d{3}-\d{2}', raw):
        return raw.replace('-', '')
    if re.fullmatch(r'\d{8}', raw):
        return raw
    # A listing's 12-digit APN is a search hint, not an official parcel match.
    if re.fullmatch(r'\d{3}-\d{3}-\d{2}-000', raw):
        return raw.replace('-', '')[:8]
    raise ValueError('Unrecognized Santa Cruz APN; review rather than truncate: ' + raw)


def listing_links(html, base):
    links = {}
    for a in HTMLParser(html).css('a[href]'):
        url = urljoin(base, a.attributes['href']).split('?')[0].split('#')[0]
        p = urlsplit(url)
        match = DETAIL.fullmatch(p.path)
        if p.hostname == 'www.loopnet.com' and match:
            links[match[1]] = url
    return links


def listing_record(html, url):
    dom = HTMLParser(html)
    apns = set(APN.findall(text(dom.css_first('#taxes'))))
    # Condo templates can show eight undashed digits in a labeled APN cell.
    # Do not scan arbitrary eight-digit values elsewhere on the page.
    for cell in dom.css('td.feature-grid__data[data-fact-type="APN"]'):
        value = text(cell)
        if re.fullmatch(r'\d{8}', value):
            apns.add(value)
    main = dom.css_first('main') or dom.body or dom.root
    content = text(main)
    structured = {}
    for script in dom.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except (ValueError, TypeError):
            continue
        for record in data if isinstance(data, list) else [data]:
            if isinstance(record, dict) and 'RealEstateListing' in str(record.get('@type', '')):
                structured = record
    for node in dom.css('script, style, header, footer, nav'):
        node.decompose()
    content = text(dom.css_first('main') or dom.body or dom.root)
    facts = {p.get('name'):p.get('value') for p in structured.get('additionalProperty', []) if isinstance(p,dict) and p.get('name')}
    tenancy = str(facts.get('Tenancy', ''))
    operator_status = ('multiple_tenants_deprioritized' if 'Multiple' in tenancy else
                       'single_tenant' if 'Single' in tenancy else 'operator_suitability_unverified')
    summary = text(dom.css_first('section.description'))
    if re.search(r'five separate units|multiple tenants|multi-tenant', summary, re.I):
        operator_status = 'multiple_tenants_deprioritized'
    # Preserve evidence for agent review instead of guessing zoning or rural status.
    snippets = []
    for term in ('zoning', 'mixed use', 'mixed-use', 'flex', 'parcel', 'property type', 'address:'):
        for match in list(re.finditer(re.escape(term), content, re.I))[:6]:
            snippets.append(content[max(0, match.start()-50):match.end()+350])
    return dict(listing_id=DETAIL.fullmatch(urlsplit(url).path)[1], url=url,
                headline=text(dom.css_first('h1')), apn_hints=sorted(apns),
                address=structured.get('contentLocation', {}).get('address', {}),
                offers=structured.get('offers', []), modified_as_advertised=structured.get('dateModified'),
                property_facts=text(dom.css_first('#property-facts')),
                structured_property_facts=facts,
                single_business_status=operator_status,
                executive_summary=summary,
                investment_highlights=text(dom.css_first('section.highlights')),
                evidence_snippets=snippets, checked_at=stamp(), status='captured',
                county_status='unverified', rural_status='unverified', zoning_status='unverified')


def discovery_priority(url):
    """Prioritize outlying search hints, never treat a town name as rural proof."""
    value = url.lower()
    if any(area in value for area in ('bonny-doon', 'corralitos', 'davenport', 'highway-17', 'redwood-lodge', 'spring-hills', 'hughes-rd', '2536-freedom')):
        return 0
    if any(area in value for area in ('boulder-creek', 'ben-lomond', 'felton', 'brookdale')):
        return 1
    if any('-'+area+'-ca/' in value for area in ('watsonville', 'aptos', 'soquel', 'freedom', 'scotts-valley')):
        return 2
    return 3


def card_asking_price(card):
    match = re.search(r'·\s*\$([\d,]+)(?:\s|$)', card)
    return int(match[1].replace(',', '')) if match else None


def business_priority(card):
    # Room counts are intentionally not treated as business/tenant counts.
    if re.search(r'Multiple Property Types|Portfolio|Shopping Center', card, re.I):
        return 3
    if re.search(r'Restaurant|Auto Repair|Freestanding|Condo|Medical', card, re.I):
        return 0
    return 2 if 'Land' in card else 1


async def enter_loopnet_search(page):
    """Use the homepage form; never turn a discovered deep URL into a seed."""
    await page.wait_for_selector('[role="tab"][data-tab="forSale"]', timeout=20000)
    await page.click('[role="tab"][data-tab="forSale"]')
    await page.fill('textarea[aria-label="Search query"]', 'Santa Cruz County, CA')
    await page.wait_for_selector('form button.search-btn', timeout=10000)
    await page.click('form button.search-btn')


def parse_tax(html, expected_address, raw_apn, today=None):
    today = today or datetime.now(ZoneInfo('America/Los_Angeles')).date()
    dom = HTMLParser(html)
    parcel = situs = ''
    for row in dom.css('tr'):
        cells = row.css('td')
        if not cells:
            continue
        label = text(cells[0])
        if label == 'Parcel/Account #' and len(cells) > 1:
            parcel = text(cells[1])
        if label == 'Situs Address' and len(cells) > 1:
            situs = text(cells[1])
    # Compare the street component exactly after narrow, explicit aliases.
    official_street = situs.split(',')[0]
    matched = bool(parcel and situs and apn_key(parcel) == apn_key(raw_apn)
                   and address_key(official_street) == address_key(expected_address))
    bills = []
    default_block = re.search(r'<!--Default message section-->(.*?)<!--End of Default message section-->', html, re.S)
    default_message = text(HTMLParser(default_block[1]).root) if default_block else ''
    current_fy = today.year if today.month >= 7 else today.year-1
    for table in dom.css('table.TaxBillContainer'):
        header = text(table.css_first('.TaxBillHeader'))
        year = re.search(r'(\d{4})\s*-\s*(\d{4})', header)
        if not year:
            continue
        link = table.css_first('a[href]')
        for row in table.css('tr'):
            cells = row.css('td')
            if len(cells) != 4 or 'Installment' not in text(cells[0]):
                continue
            label, due, paid, amount = map(text, cells)
            if label not in {'First Installment', 'Second Installment'}:
                continue  # Do not count Both Installment totals again.
            try:
                due_date = datetime.strptime(due, '%m/%d/%Y').date()
            except ValueError:
                due_date = None
            unpaid = paid in {'Not Paid', 'Past Due'}
            try:
                datetime.strptime(paid, '%m/%d/%Y')
                payment_status = 'paid'
            except ValueError:
                payment_status = 'unpaid' if unpaid else 'unknown_or_defaulted'
            prior = int(year[1]) < current_fy
            overdue = unpaid and due_date is not None and due_date < today
            bills.append(dict(fiscal_year=f'{year[1]}-{year[2]}', bill_type=header,
                              installment=label, due_date=due, paid_date=paid, amount=amount,
                              unpaid=unpaid, prior_year=prior, past_displayed_due_date=overdue,
                              payment_status=payment_status,
                              detail_url=urljoin(TAX, link.attributes['href']) if link else None))
    old_unpaid = [b for b in bills if b['prior_year'] and b['unpaid']]
    unknown_prior = any(b['prior_year'] and b['payment_status']=='unknown_or_defaulted' for b in bills)
    return dict(official_apn=parcel, situs=situs, expected_street=expected_address,
                listing_apn_hint=raw_apn, apn_search_key=apn_key(raw_apn), identity_matched=matched,
                status=('identity_unverified' if not matched else
                        'prior_year_unpaid_needs_default_review' if old_unpaid else
                        'default_or_unknown_history_needs_review' if default_message or unknown_prior else
                        'no_prior_year_arrears_shown' if any(b['prior_year'] for b in bills) else 'unverified'),
                bills=bills, years_shown=sorted(set(b['fiscal_year'] for b in bills)),
                default_message=default_message,
                default_review='manual review required; no default balance inferred',
                checked_at=stamp(), source_url=TAX,
                qualification='not qualified automatically; rural setting, zoning, default/plan status require review')


async def discover(args):
    """One bounded Crawlee job; all internal navigation uses native site controls."""
    from crawlee import ConcurrencySettings
    from crawlee.configuration import Configuration
    from crawlee.storages import RequestQueue
    from crawlee.storage_clients import FileSystemStorageClient
    from crawlee.events import LocalEventManager
    from crawlee.statistics import Statistics
    from rel_crawlee import RelCrawler

    output = args.output
    checkpoint = output/'discovery.json'
    known = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    state = dict(profile='LoopNet', county='Santa Cruz', started_at=stamp(), failures=[],
                 listings=known.get('listings', {}), links_found={}, pages_this_run=[],
                 route='homepage -> For Sale -> search -> pagination -> listing click -> Back')
    config = Configuration(storage_dir=str(output/'queue'), purge_on_start=False)
    storage = FileSystemStorageClient()
    queue = await RequestQueue.open(name='root-'+uuid.uuid4().hex, configuration=config, storage_client=storage)
    options = {'session_id': args.loopnet_session} if args.loopnet_session else {'profile': 'LoopNet'}
    crawler = RelCrawler(**options, configuration=config, request_manager=queue, storage_client=storage,
                         event_manager=LocalEventManager(),
                         statistics=Statistics.with_default_state(persistence_enabled=False), max_requests_per_crawl=1,
                         max_request_retries=0, navigation_timeout=timedelta(seconds=60),
                         request_handler_timeout=timedelta(minutes=30),
                         concurrency_settings=ConcurrencySettings(min_concurrency=1, desired_concurrency=1, max_concurrency=1))

    async def ready_source(page):
        # Smart Search may briefly land on /usa/?ssti=... before its client
        # redirect. Capturing rebinds the adapter to the current visible page.
        for _ in range(30):
            await page.content()
            if re.fullmatch(r'/search/commercial-real-estate/santa-cruz-county-ca/for-sale/(?:[2-4]/)?', urlsplit(page.url).path):
                break
            await asyncio.sleep(0.25)
        await page.wait_for_selector('article a.placard-link', timeout=30000)
        if not re.fullmatch(r'/search/commercial-real-estate/santa-cruz-county-ca/for-sale/(?:[2-4]/)?', urlsplit(page.url).path):
            raise ValueError('Search did not resolve to the Santa Cruz County for-sale page')

    def capture(html, name, page):
        path = output/'captures'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        write(path.with_suffix('.metadata.json'), dict(url=page.url, checked_at=stamp(),
              session_id=page.session_id, profile='LoopNet', bytes=len(html.encode())))

    @crawler.router.default_handler
    async def handle(ctx):
        page = ctx.page
        state['session_id'] = page.session_id
        # The only direct LoopNet navigation is Crawlee's root seed. Each
        # internal transition is a native click in this same browser session.
        await enter_loopnet_search(page)
        await ready_source(page)
        page_number = 1
        for _ in range(4):
            html = await page.content()
            capture(html, f'loopnet-search-{page_number}.html', page)
            state['pages_this_run'].append(page.url)
            dom = HTMLParser(html)
            for article in dom.css('article'):
                for id_, url in listing_links(article.html, page.url).items():
                    state['links_found'].setdefault(id_, dict(url=url, discovery_url=page.url,
                          page_number=page_number, card_text=text(article)))
            write(checkpoint, state)
            if not dom.css_first('a[aria-label="Next page"]') or page_number == 4:
                break
            old = page.url
            await page.wait_for_selector('a[aria-label="Next page"]', timeout=10000)
            await page.click('a[aria-label="Next page"]')
            await ready_source(page)
            if page.url == old:
                raise ValueError('Pagination did not change the source URL')
            page_number += 1

        ordered = sorted(state['links_found'].items(), key=lambda item: (discovery_priority(item[1]['url']), business_priority(item[1]['card_text'])))
        selected = []
        for id_, item in ordered:
            # Clear residential/ag-only cards are outside commercial scope.
            if re.search(r'Apartment|Multifamily|Residential Land|Agricultural Land|Residential Income', item['card_text'], re.I):
                continue
            price = card_asking_price(item['card_text'])
            if price is None or price > args.max_price:
                continue
            if discovery_priority(item['url']) >= 3:
                continue  # Do not backfill with downtown/urban cards.
            if len(selected) >= args.max_listings:
                break
            selected.append((id_, item))
        for id_, item in selected:
            previous = state['listings'].get(id_, {})
            if previous.get('status') == 'captured' and (output/'captures'/f'loopnet-{id_}.html').exists():
                continue
            if previous.get('status') == 'failed':
                continue  # Explicit fresh output is needed to retry terminal failures.
            state['listings'][id_] = dict(**item, status='in_progress', attempts=previous.get('attempts',0)+1)
            write(checkpoint, state)
            target_page = item['page_number']
            if target_page != page_number:
                selector = f'a[aria-label="Go to page {target_page}"]'
                await page.wait_for_selector(selector, timeout=10000)
                await page.click(selector)
                await ready_source(page)
                page_number = target_page
            source_url = page.url
            selector = 'a[href="' + item['url'] + '"]'
            await page.wait_for_selector(selector, timeout=10000)
            await page.click(selector)
            await page.content()  # Rebind after asynchronous listing-template navigation.
            await page.wait_for_selector('h1', timeout=60000)
            match = DETAIL.fullmatch(urlsplit(page.url).path)
            if not match or match[1] != id_:
                raise ValueError('Native listing click did not reach the expected listing ID')
            html = await page.content()
            capture(html, f'loopnet-{id_}.html', page)
            state['listings'][id_].update(listing_record(html, page.url))
            write(checkpoint, state)
            print(f'Captured {id_}: {state["listings"][id_]["headline"]}', flush=True)
            await page.go_back()
            await ready_source(page)
            if page.url != source_url:
                raise ValueError('Back did not restore the expected results page')
        state['finished_at'] = stamp()
        write(checkpoint, state)

    @crawler.failed_request_handler
    async def failed(ctx, error):
        state['failures'].append(dict(url=ctx.request.url, error=str(error), at=stamp()))
        for item in state['listings'].values():
            if item.get('status') == 'in_progress':
                item.update(status='failed', error=str(error))
        write(checkpoint, state)
        crawler.stop('Native browsing failed; preserve checkpoint')

    await crawler.run(['https://www.loopnet.com/'])
    print(json.dumps({'pages':len(state['pages_this_run']), 'cards':len(state['links_found']),
                     'captured':sum(x.get('status')=='captured' for x in state['listings'].values()),
                     'failures':state['failures']}, indent=2))
    return 2 if state['failures'] else 0


async def taxes(args):
    from rel_playwright.async_api import async_playwright
    candidates = json.loads(args.input.read_text())
    if not isinstance(candidates, list) or len(candidates) > 25:
        raise ValueError('Input must be a list of at most 25 reviewed candidate parcels')
    results = []
    async with async_playwright() as playwright:
        options = {'session_id': args.tax_session} if args.tax_session else {'profile': 'Datacenter'}
        browser = await playwright.chromium.launch(**options)
        try:
            page = await browser.new_page()
            for candidate in candidates:
                # The discovery review must confirm county and record an address.
                if candidate.get('county') != 'Santa Cruz':
                    raise ValueError('Candidate county must be Santa Cruz')
                street = candidate['street_address']
                key = apn_key(candidate['apn_hint'])
                try:
                    response = await page.goto(TAX, timeout=60000)
                    if response.status >= 400:
                        raise RuntimeError(f'County HTTP {response.status}')
                    await page.wait_for_selector('#Parcel', timeout=15000)
                    await page.fill('#Parcel', key)
                    await page.click('input[value="Submit"]')
                    # Some valid accounts have no current annual bill. Their
                    # situs and history controls still need inspection.
                    await page.wait_for_selector('td.BigText', timeout=20000)
                    # The visible public history control is read-only, unlike Pay buttons.
                    html = await page.content()
                    history = HTMLParser(html).css_first('input[value="Show Tax Bill History"]')
                    if history:
                        await page.click('input[value="Show Tax Bill History"]')
                        await page.wait_for_selector('input[value="Show Current Property Tax Bills Only"]', timeout=20000)
                    html = await page.content()
                    result = parse_tax(html, street, candidate['apn_hint'])
                    result['listing_url'] = candidate.get('listing_url')
                    result['profile'] = 'Datacenter'
                    result['history_control_used'] = bool(history)
                    directory = args.output/'captures'
                    directory.mkdir(parents=True, exist_ok=True)
                    capture = directory/f'tax-{key}-{hashlib.sha256(result["checked_at"].encode()).hexdigest()[:8]}.html'
                    capture.write_text(html)
                    result['capture'] = str(capture.resolve())
                    results.append(result)
                    write(args.output/'tax-results.json', results)
                    print(f'{key}: {result["situs"]}: {result["status"]}', flush=True)
                except Exception as error:
                    results.append(dict(candidate=candidate, error=str(error), checked_at=stamp(), status='failed'))
                    write(args.output/'tax-results.json', results)
                    return 2  # Preserve partial work; do not retry a denied source.
        finally:
            await browser.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['discover','taxes'])
    parser.add_argument('--output', type=Path, default=ROOT/'output')
    parser.add_argument('--max-listings', type=int, default=25)
    parser.add_argument('--max-price', type=int, default=2000000)
    parser.add_argument('--loopnet-session', help='Borrow an existing session already using the LoopNet Profile')
    parser.add_argument('--tax-session', help='Borrow an existing session already using the Datacenter Profile')
    parser.add_argument('--input', type=Path, default=ROOT/'candidates.json')
    args = parser.parse_args()
    if not 1 <= args.max_listings <= 25:
        parser.error('--max-listings must be between 1 and 25')
    raise SystemExit(asyncio.run(discover(args) if args.phase == 'discover' else taxes(args)))


if __name__ == '__main__':
    main()
