"""Compare extracted detail text to the three manually inspected REL posts."""
import json
import sqlite3
from pathlib import Path
from collector import ROOT, atomic_json, now


checks = {
    '2108903544426266990': ('CarolineWren', 'What are the chances', 'So many coincidences!'),
    '2108909919105626224': ('SaraGonzalesTX', 'And there it is.', 'mental health facility.'),
    '2109329520247660715': ('nicksortor', '🚨 BREAKING EXCLUSIVE:', 'they can rely on.'),
}
db = sqlite3.connect(ROOT / 'data' / 'study.sqlite3')
results = []
for post_id, (author, beginning, ending) in checks.items():
    variants = [json.loads(row[0]) for row in db.execute('SELECT record FROM versions WHERE post_id=?', (post_id,))]
    valid = [r for r in variants if r['author_handle'].lower() == author.lower()
             and r['text_status'] == 'rendered_text' and r['text'].startswith(beginning)
             and r['text'].rstrip().endswith(ending) and r['publication_timestamp_utc'].endswith(('Z', '+00:00'))]
    if not valid:
        raise SystemExit(f'Pilot verification incomplete: {post_id}')
    r = valid[-1]
    results.append({'post_id': post_id, 'url': r['url'], 'capture': r['capture'], 'text_characters': len(r['text']),
                    'publication_timestamp_utc': r['publication_timestamp_utc'],
                    'validated_against': 'Manually opened REL detail page; author, complete beginning/end and UTC time attribute'})
state_path = ROOT / 'data' / 'checkpoint.json'
state = json.loads(state_path.read_text())
if state.get('active_run'):
    raise SystemExit('Pilot cycle still running; verify after completion')
state['pilot_verified'] = True
state['pilot_verified_at_utc'] = now()
atomic_json(state_path, state)
atomic_json(ROOT / 'pilot-validation.json', {'verified_at_utc': now(), 'posts': results,
            'limitations': ['Bounded timeline/history coverage', 'Unsupported post relationships stay unclassified',
                           'Non-text media is not transcribed', 'No post deletion inference']})
print(json.dumps(results, indent=2))
