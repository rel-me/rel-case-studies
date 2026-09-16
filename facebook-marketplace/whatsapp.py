"""WhatsApp group transport using the installed, linked wacli account."""
import json
import re
import subprocess


def command(*args):
    result = subprocess.run(['wacli', '--json', '--timeout', '45s', *args],
                            check=True, capture_output=True, text=True, timeout=55)
    envelope = json.loads(result.stdout)
    if envelope.get('success') is not True:
        raise RuntimeError('wacli did not report success')
    return envelope.get('data')


def resolve_group(name, pinned_jid=''):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Set whatsapp_group to the exact destination group name')
    if not command('doctor').get('authenticated'):
        raise RuntimeError('WhatsApp CLI is not linked. Run wacli auth, scan its QR with WhatsApp, then rerun.')
    command('groups', 'refresh')
    groups = command('groups', 'list', '--query', name, '--limit', '1000') or []
    if len(groups) >= 1000:
        raise RuntimeError('Group search may be truncated; refusing ambiguous destination')
    matches = [g for g in groups if g.get('Name') == name and
               re.fullmatch(r'\d+(?:-\d+)?@g\.us', g.get('JID', '')) and
               not g.get('IsParent') and
               (not g.get('LeftAt') or g['LeftAt'].startswith('0001-')) and
               (not pinned_jid or g['JID'] == pinned_jid)]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one joined WhatsApp group named {name!r}; found {len(matches)}. Set whatsapp_group_jid to disambiguate.')
    return matches[0]['JID']


def send(group_jid, body):
    if not re.fullmatch(r'\d+(?:-\d+)?@g\.us', group_jid):
        raise ValueError('Destination must be a WhatsApp group JID')
    # Pass page text literally, never through a shell or escape expansion.
    result = command('send', 'text', '--to', group_jid, '--message', body, '--no-preview')
    if not isinstance(result, dict) or result.get('sent') is not True or result.get('to') != group_jid or not result.get('id'):
        raise RuntimeError('WhatsApp send acknowledgement missing; check the group before retrying')
    return result
