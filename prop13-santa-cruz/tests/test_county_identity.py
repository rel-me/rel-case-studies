"""Offline identity checks; no county traffic or terminal retries."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/crawl_county_taxes.py'
spec = importlib.util.spec_from_file_location('county_identity', path)
county = importlib.util.module_from_spec(spec)
spec.loader.exec_module(county)


class DetailIdentityTests(unittest.TestCase):
    listing = 'Parcel/Account # 010-621-10 (01019211) Situs Address Example'
    title = '2026 - 2027 Annual Tax Bill'
    detail = '2026 - 2027 Annual Tax Bill Parcel/Account # 010-192-11 Address Example'
    url = county.SITE + '/Taxbills/TaxBillDetails?TaxBillID=1&RevObjID=2'

    def matches(self, **changes):
        args = dict(apn='01062110', listing_text=self.listing, detail_text=self.detail,
                    title=self.title, status=200, url=self.url)
        args.update(changes)
        return county.detail_matches_listing(**args)

    def test_explicit_listing_alias_accepts_detail(self):
        self.assertTrue(self.matches())
        self.assertTrue(self.matches(listing_text='Parcel/Account # 01062110 ( 010-192-11 )'))

    def test_requested_parcel_still_accepts_without_alias(self):
        self.assertTrue(self.matches(detail_text=self.detail.replace('010-192-11','010-621-10'),
                                     listing_text='Parcel/Account # 010-621-10'))

    def test_unpaired_or_foreign_aliases_are_rejected(self):
        for listing in ['Parcel/Account # 010-621-10 Address 01019211',
                        'Parcel/Account # 999-999-99 (01019211)',
                        'Parcel/Account # 010-621-10 (99999999)',
                        'Parcel/Account # 010-621-10 (01019211 unrelated text)',
                        'Parcel/Account # 010-621-10 (123)',
                        'Parcel/Account # 010-621-10 (01019211) Parcel/Account # 999-999-99',
                        '']:
            with self.subTest(listing=listing):
                self.assertFalse(self.matches(listing_text=listing))

    def test_other_checks_remain_required(self):
        for changes in [dict(status=500), dict(status=403), dict(url='https://example.com/bill'),
                        dict(detail_text=self.detail.replace(self.title,'2025 - 2026 Annual Tax Bill')),
                        dict(detail_text=self.title),
                        dict(detail_text=self.detail.replace('010-192-11','999-999-99')),
                        dict(detail_text=self.detail.replace('010-192-11','123'))]:
            with self.subTest(changes=changes):
                self.assertFalse(self.matches(**changes))


if __name__ == '__main__':
    unittest.main()
