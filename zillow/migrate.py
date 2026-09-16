#!/usr/bin/env python3
"""Offline, transactional data-quality migration. Never migrate an active crawl."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3

from selectolax.parser import HTMLParser
import records


def migrate(path):
    """Caller must own the output directory's run.lock for the entire call."""
    if not path.exists():
        return {'status': 'no database'}
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        columns = {r[1] for r in db.execute('PRAGMA table_info(observations)')}
        if 'asking_price' not in columns:
            if 'bathrooms_conflict' not in columns:
                raise RuntimeError('Unrecognized database schema; migration was not applied')
            return {'status': 'already migrated'}
        backup = path.with_name(path.name + '.before-quality-' +
                                datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.bak')
        with sqlite3.connect(backup) as destination:
            db.backup(destination)
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DROP VIEW IF EXISTS latest_homes')
            db.execute('ALTER TABLE observations RENAME COLUMN asking_price TO price')
            db.execute('ALTER TABLE homes ADD COLUMN lot_area_units TEXT')
            for column in ('rendered_bathrooms REAL', 'bathrooms_source TEXT', 'bathrooms_conflict INTEGER'):
                db.execute('ALTER TABLE observations ADD COLUMN ' + column)
            count = 0
            # Rebuild in capture order so homes retains its latest observation.
            # Preserve original property facts, adding only rendered evidence.
            for row in db.execute('''SELECT o.capture_id,o.zpid,o.facts_json,c.html
                    FROM observations o JOIN captures c ON c.id=o.capture_id
                    ORDER BY o.capture_id,o.zpid'''):
                facts = json.loads(row['facts_json'])
                if 'search_card' not in facts:
                    facts = records.with_rendered_fields(HTMLParser(row['html']), facts)
                url = db.execute('SELECT url FROM homes WHERE zpid=?', (row['zpid'],)).fetchone()[0]
                records.save_home(db, row['capture_id'], url, facts)
                count += 1
            schema = (records.ROOT / 'schema.sql').read_text()
            view = schema[schema.index('CREATE VIEW'):schema.index(';', schema.index('CREATE VIEW'))]
            db.execute(view)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('Database integrity check failed; migration rolled back')
        return {'status': 'migrated', 'observations': count, 'backup': str(backup)}
    finally:
        db.close()


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Crawler is running; migration deferred. Retry after it stops.') from None
        return migrate(output / 'zillow.sqlite3')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=records.ROOT / 'output')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output), indent=2))
    except (RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f'Migration failed: {error}\n')
