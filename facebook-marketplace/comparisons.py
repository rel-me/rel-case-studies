"""Dated, sourced price context; never treat another brand as the same product."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re

REFERENCES = Path(__file__).with_name('price_references.json')


def compare(row, *, references=None, now=None):
    refs = references if references is not None else json.loads(REFERENCES.read_text())
    title = row['title'].casefold()
    if not re.search(r'\bpronto\b', title) or re.search(r'\b(squared|2|two)\b|²', title):
        return 'Price comparison unavailable: no verified references for this model.'
    now = now or datetime.now(timezone.utc)
    checked = datetime.fromisoformat(refs['checked_at'])
    if not timedelta(0) <= now - checked <= timedelta(days=refs['max_age_days']):
        return 'Price comparison needs refresh: saved references are expired or not yet dated.'
    price_text = row.get('price', '')
    if not re.fullmatch(r'\$[\d,]+(?:\.\d{2})?', price_text):
        return 'Price comparison unavailable: asking price is not a USD amount.'
    price = float(price_text[1:].replace(',', ''))
    lines = [f"Comparable Pronto One asking prices (checked {checked.date()}; exact model/year unconfirmed):"]
    for ref in refs['references']:
        delta = price - ref['price']
        difference = f"${abs(delta):g} ({abs(delta)/ref['price']:.0%}) {'more' if delta > 0 else 'less'}" if delta else 'same price'
        kind = 'new online' if ref['channel'] == 'online-new' else 'used Marketplace'
        lines.append(f"{ref['area']}, {kind}: ${ref['price']:g}; this listing is {difference}. {ref['url']}")
    lines.append('Asking prices, not sold prices. Condition/accessories differ; tax, shipping and travel excluded.')
    return '\n'.join(lines)
