"""Bounded session/proxy recovery through REL's public RPC v1 API.

No site-specific logic or proxy credentials belong in this module. Callers own
the queue and supply an optional, serializable retry payload.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from profile_session import resolve_profile_session
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class RelApiError(RuntimeError):
    def __init__(self, error):
        self.id = error.get('id')
        super().__init__(f"{self.id}: {error.get('message')}")


class RelSessionApi:
    """Session management only; website traffic remains in REL Chromium."""
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('REL session management requires a loopback HTTP endpoint')
        self.base_url = base_url.rstrip('/')

    async def call(self, method, path, payload=None):
        def send():
            request = Request(self.base_url + path, method=method,
                              data=json.dumps(payload).encode() if payload is not None else None,
                              headers={'Content-Type': 'application/json', 'Connection': 'close'})
            try:
                response = urlopen(request, timeout=15)
            except HTTPError as error:
                response = error
            with response:
                envelope = json.load(response)
            if envelope.get('status') != 'ok':
                raise RelApiError(envelope.get('error', {}))
            return envelope['data']
        return await asyncio.to_thread(send)


@dataclass(frozen=True)
class RotationRules:
    http_statuses: tuple = (502, 503, 504)
    skip_http_statuses: tuple = (404,)
    rotate_after_consecutive_skips: int = 3
    cooldown_seconds: float = 30
    max_rotations_per_run: int = 10
    max_consecutive_failures: int = 3
    retries_per_url: int = 1
    rotate_supported_proxy: bool = False

    def __post_init__(self):
        if any(type(status) is not int or not 400 <= status <= 599
               for status in (*self.http_statuses, *self.skip_http_statuses)):
            raise ValueError('http_statuses must contain HTTP error status integers')
        if not 0 <= self.cooldown_seconds <= 300:
            raise ValueError('cooldown_seconds must be in [0,300]')
        for field in ('max_rotations_per_run', 'max_consecutive_failures', 'retries_per_url',
                      'rotate_after_consecutive_skips'):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise ValueError(f'{field} must be a nonnegative integer')
        if type(self.rotate_supported_proxy) is not bool:
            raise ValueError('rotate_supported_proxy must be a boolean')

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text()))

    @staticmethod
    def target_status(error):
        # Branch on structured target status, never the REL transport's HTTP
        # status (a local PRO_REQUIRED error can also use HTTP 403).
        status = getattr(error, 'status_code', None)
        details = getattr(error, 'details', None)
        if status is None and isinstance(details, dict):
            status = details.get('target_http_status')
        return status

    def matches(self, error):
        return self.target_status(error) in self.http_statuses

    def skips(self, error):
        return self.target_status(error) in self.skip_http_statuses


class SessionRecovery:
    def __init__(self, state_file, profile, base_url, group, rules=None, api=None):
        self.state_file = Path(state_file)
        self.profile, self.base_url, self.group = profile, base_url, group
        self.rules = rules or RotationRules()
        self.api = api or RelSessionApi(base_url)
        self.rotations = 0
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        if self.state and (self.state['profile'] != profile or self.state['base_url'] != base_url):
            raise ValueError('Saved session belongs to a different Profile or REL endpoint')
        self.state.update(profile=profile, base_url=base_url)

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2) + '\n')
        temporary.replace(self.state_file)

    @property
    def pending(self):
        return self.state.get('pending_recovery')

    def successful_capture(self):
        if self.state.get('consecutive_failures') or self.state.get('consecutive_skips'):
            self.state['consecutive_failures'] = 0
            self.state['consecutive_skips'] = 0
            self.save()

    def schedule(self, error, retry_payload=None):
        if not self.rules.matches(error):
            return False
        return self._schedule(error, retry_payload)

    def skipped(self, error):
        """Skip the URL permanently; a streak can retire the suspect session."""
        self.state['consecutive_skips'] = self.state.get('consecutive_skips', 0) + 1
        self.save()
        threshold = self.rules.rotate_after_consecutive_skips
        if not threshold or self.state['consecutive_skips'] < threshold:
            return 'continue'
        return 'rotate' if self._schedule(error, None) else 'limit'

    def _schedule(self, error, retry_payload):
        failures = self.state.get('consecutive_failures', 0) + 1
        self.state['consecutive_failures'] = failures
        if (self.rotations >= self.rules.max_rotations_per_run
                or failures >= self.rules.max_consecutive_failures):
            self.save()
            return False
        self.state['pending_recovery'] = {'reason': str(error), 'retry': retry_payload}
        self.save()
        return True

    async def ensure(self):
        if self.state.get('replacement_pending'):
            await self.replace()
            return self.state['session_id']
        try:
            self.state = await resolve_profile_session(self.api, self.state, self.profile, self.group)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        self.state.setdefault('generation',0)
        self.save()
        return self.state['session_id']

    async def replace(self):
        """Invalidate old ID before effects; persist phases for process recovery."""
        if not self.state.get('replacement_pending'):
            old_id = self.state.get('session_id')
            if not old_id:
                raise RuntimeError('No managed session to replace')
            old = (await self.api.call('GET', '/sessions/' + quote(old_id, safe='')))['session']
            if old.get('group') != self.group or old.get('profile') != self.profile:
                raise RuntimeError('Refusing to replace a session outside the crawler group/Profile')
            options = {key: old[key] for key in ('proxy_alias', 'adblock_enabled',
                       'image_blocking_mode', 'image_size_limit_kb') if key in old}
            self.state.update(session_id=None, replacement_pending={
                'old_id': old_id, 'options': options, 'proxy_checked': False,
                'old_deleted': False, 'proxy_rotated': False,
                'replacement_name': self.group + '-' + uuid.uuid4().hex[:12]})
            self.save()
        pending = self.state['replacement_pending']
        alias = pending['options'].get('proxy_alias')
        if not pending['proxy_checked']:
            rotate = False
            if alias and self.rules.rotate_supported_proxy:
                proxy = (await self.api.call('GET', '/proxies/' + quote(alias, safe='')))['proxy']
                rotate = bool(proxy.get('oxylabs', {}).get('enabled'))
                if rotate:
                    sessions = (await self.api.call('GET', '/sessions'))['sessions']
                    if any(s.get('proxy_alias') == alias and s['id'] != pending['old_id'] for s in sessions):
                        raise RuntimeError('Refusing to rotate a proxy used by another REL session')
            pending.update(proxy_checked=True, rotate_proxy=rotate)
            self.save()
        if not pending['old_deleted']:
            try:
                await self.api.call('DELETE', '/sessions/' + quote(pending['old_id'], safe=''))
            except RelApiError as error:
                if error.id != 'SESSION_NOT_FOUND':
                    raise
            pending['old_deleted'] = True
            self.save()
        if pending['rotate_proxy'] and not pending['proxy_rotated']:
            sessions = (await self.api.call('GET', '/sessions'))['sessions']
            if any(s.get('proxy_alias') == alias for s in sessions):
                raise RuntimeError('Refusing to rotate a proxy used by another REL session')
            await self.api.call('POST', '/proxies/' + quote(alias, safe='') + '/rotate-session')
            pending['proxy_rotated'] = True
            self.save()
        # An interrupted POST may have created its session. Reconcile by a
        # unique persisted name before sending the same creation again.
        sessions = (await self.api.call('GET', '/sessions'))['sessions']
        found = next((s for s in sessions if s.get('name') == pending['replacement_name']), None)
        if found:
            if found.get('group') != self.group or found.get('profile') != self.profile:
                raise RuntimeError('Replacement session name belongs to another owner')
            new_id = found['id']
        else:
            data = await self.api.call('POST', '/sessions', {
                'name': pending['replacement_name'], 'profile': self.profile,
                'group': self.group, **pending['options'], 'lifetime': {'type': 'indefinite'}})
            new_id = data['session']['id']
        event = {'at': datetime.now(timezone.utc).isoformat(), 'old_id': pending['old_id'],
                 'session_id': new_id, 'proxy_alias': alias,
                 'proxy_rotated': pending['proxy_rotated'],
                 'reason': (self.pending or {}).get('reason', 'Explicit session recovery')}
        self.state.update(session_id=new_id, group=self.group,
                          generation=self.state.get('generation', 0) + 1, consecutive_skips=0)
        del self.state['replacement_pending']
        if self.pending:
            self.pending['rotated'] = True
        self.save()
        self.rotations += 1
        with self.state_file.with_name('session-rotations.jsonl').open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        print(f"REL session recovery: {pending['old_id']} -> {new_id}; "
              f"proxy={alias or 'direct'}; sticky session rotated={pending['proxy_rotated']}", flush=True)
        return new_id

    async def recover(self, enqueue):
        """enqueue must durably deduplicate the retry payload before returning."""
        if not self.pending:
            return
        if not self.pending.get('rotated'):
            await asyncio.sleep(self.rules.cooldown_seconds)
            await self.replace()
            self.pending['rotated'] = True
            self.save()
        if self.pending.get('retry') is not None:
            await enqueue(self.pending['retry'])
        del self.state['pending_recovery']
        self.save()
