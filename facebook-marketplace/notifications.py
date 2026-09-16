"""Durable at-most-once handoff to WhatsApp groups; delivery is not observable."""
import json
import sqlite3
from datetime import datetime, timezone
from whatsapp import send
from comparisons import compare


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS whatsapp_alerts (listing_id TEXT, group_jid TEXT, status TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(listing_id, group_jid))')
    db.execute('CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, listing_json TEXT NOT NULL)')
    return db


def notify(db, row, config, *, enabled=False, sender=send):
    with db:
        db.execute('INSERT INTO observations(observed_at, listing_json) VALUES(?,?)', (now(), json.dumps(row)))
    if not row['matches']:
        return 'not_matched'
    group_jid = config.get('_whatsapp_group_jid', '')
    if enabled and not group_jid:
        raise ValueError('WhatsApp destination must be resolved before sending')
    if db.execute('SELECT 1 FROM whatsapp_alerts WHERE listing_id=? AND group_jid=?', (row['id'], group_jid)).fetchone():
        return 'already_attempted'
    body = (f"Nearby used {config['query']}: {row['title'][:120]} — {row['price']}\n"
            f"Approx. {row['distance_miles']:.1f} mi from Santa Cruz; {row['condition']}\n{row['url']}")
    body += '\n\n' + compare(row)
    if not enabled:
        print(json.dumps({'preview': body}, ensure_ascii=False), flush=True)
        return 'preview'
    with db:
        # Reserve BEFORE the external effect. A crash cannot trigger an automatic duplicate.
        db.execute('INSERT INTO whatsapp_alerts VALUES(?,?,?,?,?)', (row['id'], group_jid, 'uncertain', now(), body))
    try:
        sender(group_jid, body)
    except Exception as error:
        raise RuntimeError(f"WhatsApp handoff uncertain for listing {row['id']}; inspect the REL WhatsApp group before resetting this alert") from error
    with db:
        db.execute("UPDATE whatsapp_alerts SET status='submitted', updated_at=? WHERE listing_id=? AND group_jid=?", (now(), row['id'], group_jid))
    return 'submitted'
