import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import inventory
import records
import zillow

HOME = 'https://www.zillow.com/homedetails/530-High-St-Santa-Cruz-CA-95060/123_zpid/'
LOOKUP = 'https://www.zillow.com/homes/530-HIGH-ST-santa-cruz,-ca_rb/'


def feature(oid=1, address='530 High St', code='020-SINGLE RESIDENCE', city='Yes'):
    return {'attributes': {'OBJECTID': oid, 'APN': str(oid), 'Address': address,
                          'UseCode': code, 'SCCityLMT': city, 'Zillow': LOOKUP,
                          'NumberOfUnits': '1'}, 'geometry': {'rings': []}}


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = records.database(Path(self.tmp.name) / 'test.db')
        self.addCleanup(self.db.close)
        inventory.ingest(self.db, [feature()], '2026-09-15', 'snapshot.json')

    def test_parcel_coverage_dedup_units_and_scope(self):
        features = [feature(), feature(2), feature(3, '530 High St #2'),
                    feature(4, code='150-GROCERY STORE'), feature(5, city='No')]
        result = inventory.ingest(self.db, features, '2026-09-16', 'next.json')
        self.assertEqual(result['address_status'], {'pending': 2})
        self.assertEqual(result['parcel_classification']['residential'], 3)
        self.assertEqual(inventory.address_key('530 High Street Apt 2'), '530 HIGH ST UNIT 2')
        self.assertNotEqual(inventory.address_key('530 High St'), inventory.address_key('530 High St #2'))
        self.assertEqual(len(zillow.inventory_seeds(self.db)), 2)

    def test_incomplete_download_rejected(self):
        with patch.object(inventory, 'query', side_effect=[{'objectIds': [1, 2]}, {'features': [feature()]}]):
            with self.assertRaisesRegex(ValueError, 'missing'):
                inventory.download(Path(self.tmp.name))

    async def test_matching_and_unit_mismatch(self):
        context = SimpleNamespace(request=SimpleNamespace(user_data={'inventory_key': '530 HIGH ST'}),
                                  add_requests=AsyncMock())
        facts = {'zpid': '123', 'address': {'streetAddress': '530 High St #2', 'city': 'Santa Cruz', 'state': 'CA'}}
        data = {'facts': facts}
        self.assertIsNone(await zillow.match_inventory(context, self.db, 1, data, HOME))
        self.assertEqual(self.db.execute('SELECT status FROM inventory_addresses').fetchone()[0], 'ambiguous')
        facts['address']['streetAddress'] = '530 High Street'
        parsed = records.parse('<title>Home</title>', HOME)
        with self.db:
            capture = records.save(self.db, LOOKUP, HOME, 200, '<title>Home</title>', parsed)
        self.assertEqual(await zillow.match_inventory(context, self.db, capture, data, HOME), '123')
        self.assertEqual(self.db.execute('SELECT status FROM inventory_addresses').fetchone()[0], 'matched')
        inventory.ingest(self.db, [feature()], '2026-09-16', 'next.json')
        self.assertEqual(zillow.inventory_seeds(self.db), [])

    async def test_search_enqueues_only_exact_candidate(self):
        context = SimpleNamespace(request=SimpleNamespace(user_data={'inventory_key': '530 HIGH ST'}),
                                  add_requests=AsyncMock())
        card = {'detailUrl': HOME, 'hdpData': {'homeInfo': {'streetAddress': '530 High St',
                'city': 'Santa Cruz', 'state': 'CA', 'zpid': 123}}}
        await zillow.match_inventory(context, self.db, 1, {'structured': [card]}, LOOKUP)
        context.add_requests.assert_awaited_once()
        self.assertEqual(context.add_requests.call_args.args[0][0].url, HOME)
        self.assertEqual(self.db.execute('SELECT status FROM inventory_addresses').fetchone()[0], 'resolving')


if __name__ == '__main__':
    unittest.main()
