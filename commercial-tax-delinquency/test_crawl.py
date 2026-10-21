"""Checks against false parcel matches and false delinquency signals."""
from datetime import date
import unittest

from crawl import address_key, apn_key, listing_links, listing_record, parse_tax, discovery_priority, card_asking_price, enter_loopnet_search, business_priority


def account(address='6222 HWY 9, FELTON', year='2025 - 2026', paid='Not Paid',
            due='4/10/2026', include_history=True):
    header = f'<td class="TaxBillHeader">{year} Annual Tax Bill</td>'
    bill = f'''<table class="TaxBillContainer"><tr>{header}</tr>
    <tr><td>First Installment</td><td>{due}</td><td>{paid}</td><td>$100.00</td></tr>
    <tr><td>Second Installment</td><td>{due}</td><td>{paid}</td><td>$100.00</td></tr>
    <tr><td>Both Installment</td><td></td><td></td><td>$200.00</td></tr></table>'''
    return f'''<html><body><table><tr><td>Parcel/Account #</td><td>065-081-07</td></tr>
    <tr><td>Situs Address</td><td>{address}</td></tr></table>
    {bill if include_history else ''}</body></html>'''


class TaxMatchingTests(unittest.TestCase):
    def test_undashed_condo_apn_requires_a_labeled_property_cell(self):
        html = '<html><body><h1>Unit D</h1><p>12345678</p><table><tr><td class="feature-grid__data" data-fact-type="APN">04169101</td></tr></table></body></html>'
        record = listing_record(html, 'https://www.loopnet.com/Listing/9059-Soquel-Dr-Aptos-CA/38612707/')
        self.assertEqual(record['apn_hints'], ['04169101'])

    def parse(self, html, street='6222 Highway 9'):
        return parse_tax(html, street, '065-081-07-000', date(2026, 10, 17))

    def test_wrong_street_number_cannot_attach_taxes(self):
        result = self.parse(account(address='6221 HWY 9, FELTON'))
        self.assertFalse(result['identity_matched'])
        self.assertEqual(result['status'], 'identity_unverified')

    def test_units_and_ranges_are_not_discarded(self):
        self.assertNotEqual(address_key('6222 Highway 9 #2'), address_key('6222 HWY 9'))
        self.assertFalse(self.parse(account(), '6222-6224 Highway 9')['identity_matched'])

    def test_unknown_apn_suffix_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            apn_key('065-081-07-001')

    def test_future_unpaid_current_bill_is_not_overdue(self):
        result = self.parse(account(year='2026 - 2027', due='12/10/2026'))
        self.assertFalse(any(b['past_displayed_due_date'] for b in result['bills']))
        self.assertEqual(result['status'], 'unverified')  # No prior history checked.

    def test_prior_unpaid_requires_default_review_and_ignores_combined_total(self):
        result = self.parse(account())
        self.assertEqual(result['status'], 'prior_year_unpaid_needs_default_review')
        self.assertEqual(len(result['bills']), 2)
        self.assertTrue(all(b['prior_year'] and b['unpaid'] for b in result['bills']))

    def test_defaulted_past_due_row_is_unpaid_even_without_a_numeric_amount(self):
        html = account(paid='Past Due').replace('$100.00','Defaulted')
        result = self.parse(html)
        self.assertEqual(result['status'], 'prior_year_unpaid_needs_default_review')
        self.assertTrue(all(b['unpaid'] for b in result['bills']))

    def test_default_flag_prevents_false_clear_history(self):
        html = account(paid='4/9/2026') + '<!--Default message section--><div>Prior Year Taxes defaulted.</div><!--End of Default message section-->'
        self.assertEqual(self.parse(html)['status'], 'default_or_unknown_history_needs_review')

    def test_street_named_soquel_is_not_treated_as_soquel_locality(self):
        self.assertEqual(discovery_priority('https://www.loopnet.com/Listing/325-Soquel-Ave-Santa-Cruz-CA/42021749/'),3)

    def test_unknown_and_above_budget_cards_do_not_satisfy_price_limit(self):
        self.assertIsNone(card_asking_price('Office · Price Upon Request'))
        self.assertEqual(card_asking_price('Restaurant · $2,000,000 4,000 SF'),2000000)
        self.assertGreater(card_asking_price('Restaurant · $2,400,000 4,000 SF'),2000000)

    def test_multiple_rooms_do_not_imply_multiple_businesses(self):
        self.assertLess(business_priority('Medical Condo, 8 rooms'), business_priority('Shopping Center, 8 tenants'))

    def test_paid_prior_year_is_not_a_lead(self):
        result = self.parse(account(paid='4/9/2026'))
        self.assertEqual(result['status'], 'no_prior_year_arrears_shown')
        self.assertFalse(any(b['unpaid'] for b in result['bills']))

    def test_missing_bills_do_not_mean_current(self):
        self.assertEqual(self.parse(account(include_history=False))['status'], 'unverified')

    def test_discovery_deduplicates_ids_and_rejects_other_hosts(self):
        html = '''<a href="/Listing/a/123/">a</a><a href="/Listing/a/123/?x=1">a</a>
        <a href="https://evil.test/Listing/b/456/">b</a><a href="/property/a/123/">c</a>'''
        self.assertEqual(listing_links(html, 'https://www.loopnet.com/'),
                         {'123': 'https://www.loopnet.com/Listing/a/123/'})


class HomepageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_requires_sale_mode_and_location_before_submission(self):
        class Homepage:
            mode = None
            query = None
            submitted = False

            async def wait_for_selector(self, selector, **kwargs):
                pass

            async def click(self, selector):
                if selector == '[role="tab"][data-tab="forSale"]':
                    self.mode = 'sale'
                elif selector == 'form button.search-btn':
                    if self.mode != 'sale' or self.query != 'Santa Cruz County, CA':
                        raise AssertionError('Submission omitted sale mode or county')
                    self.submitted = True
                else:
                    raise AssertionError('Unexpected navigation control')

            async def fill(self, selector, value):
                if selector != 'textarea[aria-label="Search query"]':
                    raise AssertionError('Wrong search input')
                self.query = value

            async def goto(self, url, **kwargs):
                raise AssertionError('Homepage search must use native controls')

        page = Homepage()
        await enter_loopnet_search(page)
        self.assertTrue(page.submitted)


if __name__ == '__main__':
    unittest.main()
