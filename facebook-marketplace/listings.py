"""Extract only the rendered listing viewer, never stale search-page JSON."""
import math
import re
from urllib.parse import parse_qs, urlsplit
from bs4 import BeautifulSoup


def item_id(url):
    p = urlsplit(url)
    m = re.fullmatch(r'/marketplace/item/(\d+)/?', p.path)
    return m[1] if p.scheme == 'https' and p.hostname == 'www.facebook.com' and m else None


def miles(a, b, c, d):
    a, b, c, d = map(math.radians, (a, b, c, d))
    h = math.sin((c-a)/2)**2 + math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 3958.7613 * 2 * math.asin(min(1, math.sqrt(h)))


def parse_listing(html, url, config):
    ident = item_id(url)
    soup = BeautifulSoup(html, 'html.parser')
    viewer = soup.select_one('[role="dialog"][aria-label="Marketplace Listing Viewer"]')
    if not ident or not viewer:
        raise ValueError('Listing viewer missing: login, access challenge, or changed Facebook layout')
    title_node = viewer.select_one('h1')
    if not title_node:
        raise ValueError('Listing title missing')
    title = title_node.get_text(' ', strip=True)
    lines = list(viewer.stripped_strings)
    text = '\n'.join(lines)
    row = {'id': ident, 'url': f'https://www.facebook.com/marketplace/item/{ident}/',
           'title': title, 'price': next((s for s in lines if re.fullmatch(r'\$[\d,.]+', s)), 'Price unavailable'),
           'condition': None, 'distance_miles': None, 'reason': None}
    tokens = re.findall(r'\w+', title.casefold())
    if not all(w in tokens for w in re.findall(r'\w+', config['query'].casefold())):
        row['reason'] = 'Product keywords missing from title'
    # Intentionally conservative: bundled accessories may also need manual review.
    if re.search(r'\b(rental|rent|wanted|iso|cover|adapter|attachment|organizer|replacement|dog|pet)\b', title, re.I):
        row['reason'] = 'Accessory, rental, wanted, or pet listing'
    condition = next((lines[i+1] for i, s in enumerate(lines[:-1]) if s == 'Condition'), None)
    row['condition'] = condition
    if (condition or '').casefold() not in {'used - like new', 'used - good', 'used - fair'}:
        row['reason'] = 'Used condition not verified'
    if any(re.fullmatch(r'(?:sold|pending|sold out|no longer available|this listing is no longer available)', s, re.I) for s in lines):
        row['reason'] = 'Sold, pending, or unavailable'
    elif not any(n.get_text(' ', strip=True) == 'Message' or n.get('aria-label') == 'Message'
                 for n in viewer.select('[role="button"], button')):
        row['reason'] = 'Availability not verified (no Message action)'
    points = []
    for n in viewer.select('[style]'):
        for raw in re.findall(r'https://[^\s"\)]+', n['style']):
            p = urlsplit(raw)
            if p.path != '/static_map.php' or not (p.hostname or '').endswith('.fbcdn.net'):
                continue
            q = parse_qs(p.query)
            try:
                lat, lon = map(float, q['center'][0].split(','))
                circle = q.get('circle', [''])[0].split('|')[-1]
                # FB map circle is in meters, with a 'k' suffix for kilometers.
                uncertainty = float(circle[:-1])*1000 if circle.endswith('k') else float(circle)
                if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= uncertainty <= 100000):
                    continue
                points.append((lat, lon, uncertainty / 1609.344))
            except (KeyError, ValueError):
                continue
    if len(set(points)) != 1:
        row['reason'] = 'Approximate listing location missing or ambiguous'
    else:
        lat, lon, uncertainty = points[0]
        distance = miles(config['latitude'], config['longitude'], lat, lon)
        row.update(distance_miles=round(distance, 2), location_uncertainty_miles=round(uncertainty, 2))
        if distance + uncertainty > config['radius_miles']:
            row['reason'] = 'Outside radius or approximate location overlaps its boundary'
    row['matches'] = row['reason'] is None
    return row
