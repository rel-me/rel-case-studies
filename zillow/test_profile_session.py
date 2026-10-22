import unittest
from profile_session import ProfileSessionError, resolve_profile_session


class Api:
    def __init__(self, groups=None, sessions=None):
        self.groups = groups
        self.sessions = sessions or {}
        self.calls = []

    async def call(self, method, path, payload=None):
        self.calls.append((method,path,payload))
        if path == '/groups':
            if self.groups is None: raise ProfileSessionError({'id':'NOT_FOUND'})
            return {'groups':self.groups}
        if method == 'GET':
            if path.rsplit('/',1)[-1] not in self.sessions: raise ProfileSessionError({'id':'SESSION_NOT_FOUND'})
            return {'session':self.sessions[path.rsplit('/',1)[-1]]}
        if method == 'POST':
            if self.groups is None:
                return {'session':{'id':'Session9',**payload}}
            if 'group' in payload: raise AssertionError('New REL rejects the legacy group field')
            tab={'id':'Session9','group_id':'Group9'}
            self.groups.append({'id':'Group9','storage_id':'Session19','profile':payload['profile'],'sessions':[tab]})
            return {'session':tab}
        raise AssertionError((method,path))


def group(profile='Zillow',tabs=None):
    return {'id':'Group42','storage_id':'Session42','profile':profile,
            'sessions':tabs if tabs is not None else [{'id':'Session7','group_id':'Group42'}]}


class ProfileSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_tab_is_validated_against_group_profile(self):
        api=Api([group()],{'Session7':{'id':'Session7','group_id':'Group42'}})
        state=await resolve_profile_session(api,{'session_id':'Session7','profile':'Zillow'},'Zillow','crawl')
        self.assertEqual(state['session_id'],'Session7')
        api.groups[0]['profile']='Datacenter'
        with self.assertRaisesRegex(ValueError,'different Profile'):
            await resolve_profile_session(api,state,'Zillow','crawl')

    async def test_missing_session_leaves_group_selection_to_rel(self):
        for groups in [[],[group()],[group(),dict(group(),id='Group43')],
                       [group(tabs=[{'id':'Session7'},{'id':'Session8'}])]]:
            with self.subTest(groups=groups):
                api=Api(groups)
                state=await resolve_profile_session(api,{'session_id':'Gone','profile':'Zillow','group_id':'GoneGroup','consecutive_failures':4},'Zillow','crawl')
                self.assertEqual((state['session_id'],state['group_id']),('Session9','Group9'))
                self.assertEqual(state['consecutive_failures'],4)
                posts=[payload for method,_,payload in api.calls if method=='POST']
                self.assertEqual(posts,[{'profile':'Zillow','lifetime':{'type':'indefinite'}}])

    async def test_live_tab_in_different_group_is_rejected(self):
        api=Api([group()],{'Session7':{'id':'Session7','group_id':'Group42'}})
        with self.assertRaisesRegex(ValueError,'different group'):
            await resolve_profile_session(api,{'session_id':'Session7','profile':'Zillow','group_id':'Group99'},'Zillow','crawl')
        self.assertFalse(any(method=='POST' for method,_,_ in api.calls))

    async def test_new_profile_creation_omits_legacy_group_field(self):
        api=Api([])
        state=await resolve_profile_session(api,{},'Zillow','crawl')
        self.assertEqual(state['session_id'],'Session9')
        self.assertIn(('POST','/sessions',{'profile':'Zillow','lifetime':{'type':'indefinite'}}),api.calls)

    async def test_created_tab_is_selected_among_existing_tabs(self):
        class Reuse(Api):
            async def call(self, method, path, payload=None):
                if method == 'POST':
                    self.calls.append((method,path,payload))
                    tab={'id':'Session9','group_id':'Group42'}
                    self.groups[0]['sessions'].append(tab)
                    return {'session':tab}
                return await super().call(method,path,payload)
        api=Reuse([group()])
        state=await resolve_profile_session(api,{},'Zillow','crawl')
        self.assertEqual((state['session_id'],state['group_id']),('Session9','Group42'))

    async def test_created_tab_with_wrong_profile_is_rejected(self):
        class Wrong(Api):
            async def call(self, method, path, payload=None):
                data=await super().call(method,path,payload)
                if method == 'POST': self.groups[-1]['profile']='Datacenter'
                return data
        with self.assertRaisesRegex(ValueError,'configured Profile'):
            await resolve_profile_session(Wrong([]),{},'Zillow','crawl')

    async def test_legacy_creation_and_live_session_remain_supported(self):
        api=Api()
        state=await resolve_profile_session(api,{},'Zillow','crawl')
        self.assertEqual(state['session_id'],'Session9')
        self.assertEqual(api.calls[-1][2]['group'],'crawl')
        api=Api(sessions={'Session9':{'id':'Session9','profile':'Zillow','group':'crawl'}})
        self.assertEqual((await resolve_profile_session(api,state,'Zillow','crawl'))['session_id'],'Session9')
        self.assertEqual(len(api.calls),1)

    async def test_unexpected_service_error_does_not_create_session(self):
        class Broken(Api):
            async def call(self,*args): raise ProfileSessionError({'id':'UPSTREAM_UNAVAILABLE'})
        with self.assertRaisesRegex(ProfileSessionError,'UPSTREAM_UNAVAILABLE'):
            await resolve_profile_session(Broken(),{},'Zillow','crawl')
