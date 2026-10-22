"""Resolve saved crawler sessions across REL's group/tab API migration.

Only loopback management traffic runs here. REL assigns groups to new sessions;
ambiguous or mismatched ownership never triggers a replacement or route change.
"""
import asyncio
import json
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class ProfileSessionError(RuntimeError):
    def __init__(self, error):
        self.id = error.get('id')
        super().__init__(f"{self.id}: {error.get('message')}")


class ProfileSessionApi:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('REL management requires a loopback endpoint')
        self.base_url = base_url.rstrip('/')

    async def call(self, method, path, payload=None):
        def send():
            request = Request(self.base_url+path, method=method,
                              data=json.dumps(payload).encode() if payload is not None else None,
                              headers={'Content-Type':'application/json','Connection':'close'})
            try: response = urlopen(request, timeout=20)
            except HTTPError as error: response = error
            with response: envelope = json.load(response)
            if envelope.get('status') != 'ok':
                raise ProfileSessionError(envelope.get('error',{}))
            return envelope['data']
        return await asyncio.to_thread(send)


async def resolve_profile_session(api, state, profile, group):
    """Return updated checkpoint metadata, leaving persistence to the caller."""
    if state and (state.get('profile') != profile or state.get('group',group) != group):
        raise ValueError('Saved REL session belongs to a different Profile or crawler group')
    saved_id = state.get('session_id')
    session = None
    if saved_id:
        try: session = (await api.call('GET','/sessions/'+quote(saved_id,safe='')))['session']
        except Exception as error:
            if getattr(error,'id',None) != 'SESSION_NOT_FOUND': raise
        if session and 'group_id' not in session:
            if session.get('profile') != profile or session.get('group') != group:
                raise ValueError('Saved REL session belongs to a different Profile or crawler group')
            return dict(state,session_id=session['id'],profile=profile,group=group)
    try: groups = (await api.call('GET','/groups'))['groups']
    except Exception as error:
        # Older REL releases have the original session-only API.
        if getattr(error,'id',None) not in ('NOT_FOUND','ROUTE_NOT_FOUND','ENDPOINT_NOT_FOUND'): raise
        data = await api.call('POST','/sessions',{'profile':profile,'group':group,'lifetime':{'type':'indefinite'}})
        return dict(state,session_id=data['session']['id'],profile=profile,group=group)
    if session and state.get('group_id') and session.get('group_id') != state['group_id']:
        raise ValueError('Saved REL tab belongs to a different group')
    if session:
        owned = [g for g in groups if g.get('id') == session.get('group_id')]
        if len(owned) != 1 or owned[0].get('profile') != profile:
            raise ValueError('Saved REL group belongs to a different Profile or is missing')
    else:
        # REL chooses the group. Never adopt an unrelated tab or send a stale ID.
        created = (await api.call('POST','/sessions',{'profile':profile,'lifetime':{'type':'indefinite'}}))['session']
        groups = (await api.call('GET','/groups'))['groups']
        owned = [g for g in groups if g.get('id') == created.get('group_id') and g.get('profile') == profile]
        if len(owned) != 1: raise ValueError('Created session does not belong to the configured Profile')
        session = created
    owner = owned[0]
    tabs = owner.get('sessions',[])
    if session:
        tabs = [t for t in tabs if t.get('id') == session['id']]
    if len(tabs) != 1:
        raise ValueError('Saved REL group must have one unambiguous crawler tab')
    return dict(state,session_id=tabs[0]['id'],group_id=owner['id'],
                storage_id=owner.get('storage_id'),profile=profile,group=group)
