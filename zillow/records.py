#!/usr/bin/env python3
"""Zillow URL scope, extraction, and SQLite records."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

ROOT = Path(__file__).resolve().parent
STARTS = ["https://www.zillow.com/santa-cruz-ca/",
          "https://www.zillow.com/santa-cruz-ca/sold/",
          "https://www.zillow.com/santa-cruz-ca/rentals/"]
HOME = re.compile(r"/homedetails/[^/]+/(\d+)_zpid/?$")
SEARCH = re.compile(r"/santa-cruz-ca/(?:sold/|rentals/)?(?:\d+_p/)?$")


def normalize(url, base="https://www.zillow.com"):
    try:
        p = urlsplit(urljoin(base, url))
        if p.scheme != "https" or p.hostname not in {"zillow.com", "www.zillow.com"} or p.port or p.username:
            return None
    except (TypeError, ValueError):
        return None
    if not (HOME.fullmatch(p.path) or SEARCH.fullmatch(p.path)):
        return None
    return urlunsplit(("https", "www.zillow.com", quote(p.path, safe="/%:@-._~!$&'()*+,;="),
                       quote(p.query, safe="%=&?/:@-._~!$'()*+,;"), ""))


def walk(value, depth=0):
    """Walk serialized page JSON, including Zillow's nested JSON strings."""
    if depth > 40:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, depth + 1)
    elif isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            yield from walk(json.loads(value), depth + 1)
        except (ValueError, RecursionError):
            pass


def with_rendered_fields(tree, facts):
    facts = dict(facts)
    fields = dict(facts.get('rendered_fields', {}))
    for field in ('price', 'primary-zestimate', 'rent-zestimate', 'bed-bath-sqft-facts'):
        nodes = tree.css(f'[data-testid="{field}"]')
        if len(nodes) == 1:
            fields[field] = nodes[0].text(separator=' ', strip=True)
    facts['rendered_fields'] = fields
    return facts


def bathroom_evidence(facts):
    text = facts.get('rendered_fields', {}).get('bed-bath-sqft-facts', '')
    match = re.search(r'([\d.]+)\s+baths?\b', text)
    displayed = float(match[1]) if match else None
    value = number(facts.get('bathrooms'))
    source = ('search_card' if 'search_card' in facts else
              'rendered' if facts.get('source_format') == 'detail_json_ld_and_rendered_fields'
              else 'property_json') if value is not None else None
    conflict = int(value != displayed) if value is not None and displayed is not None else None
    return displayed, source, conflict


def price(facts):
    value = number(facts.get('price'))
    displayed = facts.get('rendered_fields', {}).get('price', '').casefold()
    return value if value and displayed not in {'price unknown', 'not available'} else None


def detail_facts(tree, structured, zpid):
    records = list(walk(structured))
    direct = [v for v in records if str(v.get('zpid')) == zpid
              and isinstance(v.get('address'), dict)]
    if direct:
        return with_rendered_fields(tree, dict(max(direct, key=len), source_format='property_json'))
    # Client-side lightboxes keep the search page's Next.js state. The current
    # home's JSON-LD and rendered detail fields are the authoritative record.
    for listing in records:
        types = listing.get('@type', [])
        if 'RealEstateListing' not in types:
            continue
        link = normalize(listing.get('url', listing.get('@id', '')))
        if not link or not HOME.fullmatch(urlsplit(link).path) or HOME.fullmatch(urlsplit(link).path)[1] != zpid:
            continue
        offer = listing.get('offers', {})
        home = offer.get('itemOffered', {})
        address = home.get('address', {})
        facts = dict(zpid=zpid, address={
            'streetAddress': address.get('streetAddress'),
            'city': address.get('addressLocality'), 'state': address.get('addressRegion'),
            'zipcode': address.get('postalCode')},
            price=offer.get('price'), currency=offer.get('priceCurrency'),
            bedrooms=home.get('numberOfBedrooms'), homeType=home.get('@type'),
            livingArea=home.get('floorSize', {}).get('value'),
            latitude=home.get('geo', {}).get('latitude'),
            longitude=home.get('geo', {}).get('longitude'),
            source_format='detail_json_ld_and_rendered_fields', listing_json_ld=listing)
        fields = {}
        for field in ('primary-zestimate', 'rent-zestimate', 'zestimate-range',
                      'bed-bath-sqft-facts', 'facts-and-features-module'):
            nodes = tree.css(f'[data-testid="{field}"]')
            if len(nodes) == 1:
                fields[field] = nodes[0].text(separator=' ', strip=True)
        facts['rendered_fields'] = fields
        for field, key in [('primary-zestimate', 'zestimate'), ('rent-zestimate', 'rentZestimate')]:
            value = fields.get(field, '')
            match = re.fullmatch(r'\$([\d,]+(?:\.\d+)?)(?:/mo)?', value.strip())
            if match:
                facts[key] = float(match[1].replace(',', ''))
        match = re.search(r'([\d.]+)\s+baths?\b', fields.get('bed-bath-sqft-facts', ''))
        if match:
            facts['bathrooms'] = float(match[1])
        match = re.search(r'Year built:\s*(\d{4})', fields.get('facts-and-features-module', ''))
        if match:
            facts['yearBuilt'] = int(match[1])
        return with_rendered_fields(tree, facts)
    return None


def parse(html, url):
    tree = HTMLParser(html)
    structured = []
    for node in tree.css('script[type="application/json"], script[type="application/ld+json"]'):
        try:
            structured.append(json.loads(node.text()))
        except ValueError:
            continue
    metadata = [dict(n.attributes) for n in tree.css("meta")]
    canonical = tree.css_first('link[rel="canonical"]')
    links = []
    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "")
        normalized = normalize(href, url)
        if normalized:
            links.append((normalized, href))
    title = tree.css_first("title")
    title_text = title.text() if title else ""
    match = HOME.fullmatch(urlsplit(url).path)
    facts = detail_facts(tree, structured, match[1]) if match else None
    for node in tree.css("script, style, noscript"):
        node.decompose()
    text = tree.text(separator=" ", strip=True)
    return dict(title=title_text, canonical=canonical.attributes.get("href") if canonical else None,
                metadata=metadata, structured=structured, text=text, links=links, facts=facts)


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    columns = {row[1] for row in db.execute('PRAGMA table_info(observations)')}
    if 'asking_price' in columns:
        db.close()
        raise RuntimeError('Database needs migration: run ./run.sh migrate after the crawler stops')
    db.executescript((ROOT / "schema.sql").read_text())
    return db


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def save(db, requested, final, status, html, data):
    cur = db.execute("""INSERT INTO captures(requested_url,final_url,captured_at,http_status,title,
        canonical_url,sha256,html,text,metadata_json,structured_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (requested, final, datetime.now(timezone.utc).isoformat(), status, data['title'],
         data['canonical'], hashlib.sha256(html.encode()).hexdigest(), html, data['text'],
         json.dumps(data['metadata']), json.dumps(data['structured'])))
    return cur.lastrowid


def save_home(db, capture, url, facts):
    if not facts:
        raise ValueError("No structured property record matching the requested ZPID; capture retained")
    address = facts['address']
    if str(address.get('city', '')).casefold() != 'santa cruz' or address.get('state') != 'CA':
        raise ValueError("Property is outside the Santa Cruz, CA address scope")
    values = (str(facts['zpid']), url, address.get('streetAddress'), address.get('city'),
              address.get('state'), address.get('zipcode'), number(facts.get('latitude')),
              facts.get('longitude'), facts.get('homeType'), number(facts.get('bedrooms')),
              number(facts.get('bathrooms')), number(facts.get('livingArea')),
              number(facts.get('lotAreaValue')), number(facts.get('yearBuilt')), facts.get('lotAreaUnits'))
    columns = ['zpid','url','address','city','state','postal_code','latitude','longitude',
               'home_type','bedrooms','bathrooms','living_area','lot_area','year_built','lot_area_units']
    db.execute(f"INSERT INTO homes({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(zpid) DO UPDATE SET "
               + ','.join(f'{c}=excluded.{c}' for c in columns[1:]), values)
    zestimate = number(facts.get('zestimate'))
    db.execute("""INSERT INTO observations(capture_id,zpid,zestimate,rent_zestimate,price,
               home_status,zestimate_status,facts_json,rendered_bathrooms,bathrooms_source,bathrooms_conflict)
               VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(capture_id,zpid) DO UPDATE SET
               price=excluded.price, facts_json=excluded.facts_json,
               rendered_bathrooms=excluded.rendered_bathrooms,
               bathrooms_source=excluded.bathrooms_source,
               bathrooms_conflict=excluded.bathrooms_conflict""",
               (capture, values[0], zestimate, number(facts.get('rentZestimate')),
                price(facts), facts.get('homeStatus'),
                'available' if zestimate is not None else 'not_advertised', json.dumps(facts), *bathroom_evidence(facts)))


def save_search(db, capture, data):
    seen = set()
    for card in walk(data['structured']):
        info = card.get('hdpData', {}).get('homeInfo') if isinstance(card.get('hdpData'), dict) else None
        url = normalize(card.get('detailUrl', ''))
        if not info or not url or not HOME.fullmatch(urlsplit(url).path):
            continue
        zpid = str(info.get('zpid'))
        if zpid in seen or HOME.fullmatch(urlsplit(url).path)[1] != zpid:
            continue
        if str(info.get('city', '')).casefold() != 'santa cruz' or info.get('state') != 'CA':
            continue
        facts = dict(info, address={k: info.get(k) for k in
                     ('streetAddress', 'city', 'state', 'zipcode')}, search_card=card)
        save_home(db, capture, url, facts)
        seen.add(zpid)
    return len(seen)
