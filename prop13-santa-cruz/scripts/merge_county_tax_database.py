"""Move the county-tax SQLite schema into the main Zillow database.

The original county-tax database is preserved as the migration source/backup.
County tables are prefixed to avoid collisions with Zillow's captures table.
"""
import argparse
from pathlib import Path
import re
import sqlite3


PREFIX = 'county_tax_'
TABLES = ('targets', 'captures', 'bills', 'errors', 'parse_issues',
          'listed_bills', 'page_search', 'settings', 'priority_parcels')
SHADOW_PREFIXES = ('page_search_config', 'page_search_content', 'page_search_data',
                   'page_search_docsize', 'page_search_idx')


def merge(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if source == destination:
        raise ValueError('Source and destination must be different database files')
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)
    dest = sqlite3.connect(destination, timeout=30)
    try:
        src_tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")}
        missing = set(TABLES) - src_tables
        if missing:
            raise ValueError('County tax database is missing tables: ' + ', '.join(sorted(missing)))
        existing = {row[0] for row in dest.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        collisions = {PREFIX + name for name in TABLES} & existing
        if collisions:
            raise ValueError('Prefixed county tax tables already exist: ' + ', '.join(sorted(collisions)))
        dest.execute('PRAGMA journal_mode=WAL')
        dest.execute('PRAGMA synchronous=FULL')
        dest.execute('ATTACH DATABASE ? AS tax_source', (str(source),))
        dest.execute('BEGIN IMMEDIATE')
        try:
            rows = src.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name").fetchall()
            for name, sql in rows:
                if name in SHADOW_PREFIXES or any(name.startswith(prefix) for prefix in SHADOW_PREFIXES):
                    continue  # FTS5 creates its own private shadow tables.
                if name not in TABLES:
                    raise ValueError(f'Unexpected county-tax table {name!r}; refusing a partial migration')
                replacement = PREFIX + name
                pattern = r'(?i)(CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)(["`\[]?)' + re.escape(name) + r'(["`\]]?)'
                renamed, count = re.subn(pattern, lambda match: match.group(1) + replacement, sql, count=1)
                if count != 1:
                    raise ValueError(f'Could not namespace the {name!r} table definition')
                dest.execute(renamed)
                cols = [row[1] for row in src.execute(f'PRAGMA table_info("{name}")')]
                column_sql = ','.join('"' + col.replace('"', '""') + '"' for col in cols)
                prefix = 'rowid,' if name == 'page_search' else ''
                dest.execute(f'INSERT INTO "{replacement}" ({prefix}{column_sql}) SELECT {prefix}{column_sql} FROM tax_source."{name}"')
            for name, sql in src.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"):
                renamed_name = PREFIX + name
                renamed_sql = re.sub(r'(?i)(CREATE\s+(?:UNIQUE\s+)?INDEX\s+)(?:IF\s+NOT\s+EXISTS\s+)?',
                                     lambda m: m.group(1) + 'IF NOT EXISTS ', sql, count=1)
                renamed_sql = re.sub(r'(?i)(CREATE\s+(?:UNIQUE\s+)?INDEX\s+)(["`\[]?)' + re.escape(name) + r'(["`\]]?)',
                                     lambda m: m.group(1) + renamed_name, renamed_sql, count=1)
                renamed_sql = re.sub(r'(?i)\bON\s+(["`\[]?)(' + '|'.join(map(re.escape, TABLES)) + r')(["`\]]?)',
                                     lambda m: 'ON ' + PREFIX + m.group(2), renamed_sql, count=1)
                dest.execute(renamed_sql)
            dest.execute('CREATE TABLE county_tax_migration(source TEXT PRIMARY KEY, migrated_at TEXT NOT NULL)')
            dest.execute('INSERT INTO county_tax_migration VALUES(?,strftime("%Y-%m-%dT%H:%M:%fZ","now"))', (str(source),))
            verification = {}
            for name in TABLES:
                old_count = src.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                new_count = dest.execute(f'SELECT count(*) FROM "{PREFIX + name}"').fetchone()[0]
                if old_count != new_count:
                    raise RuntimeError(f'Row count mismatch for {name}: {old_count} != {new_count}')
                verification[name] = new_count
            dest.commit()
            return verification
        except BaseException:
            dest.rollback()
            raise
        finally:
            dest.execute('DETACH DATABASE tax_source')
    finally:
        dest.close()
        src.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    print(merge(args.source, args.destination))
