import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'scripts/export_data.py'
spec=importlib.util.spec_from_file_location('export_data',path)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class TaxEvidenceTests(unittest.TestCase):
    def test_assessment_and_generic_rate_never_become_a_payment(self):
        self.assertIsNone(module.tax_record({'taxAssessedValue':200000,'propertyTaxRate':1.1},'2026-10-16T00:00:00+00:00'))

    def test_latest_positive_tax_ignores_null_and_future_entries(self):
        facts={'taxHistory':[
            {'time':1791149429711,'taxPaid':None,'value':100000},
            {'time':1759613429711,'taxPaid':2000,'value':98000},
            {'time':1728077429711,'taxPaid':1800,'value':96000},
            {'time':1893456000000,'taxPaid':9000,'value':1000000}]}
        record=module.tax_record(facts,'2026-10-16T00:00:00+00:00')
        self.assertEqual(record['annual_tax'],2000)
        self.assertEqual(record['tax_year'],2025)
        self.assertEqual(record['tax_assessed_value'],98000)

    def test_rates_and_benchmarks_have_consistent_units(self):
        result=module.metrics(1000000,200000,3000)
        self.assertEqual(result['assessment_gap'],800000)
        self.assertEqual(result['assessment_ratio'],.2)
        self.assertEqual(result['effective_tax_rate'],.003)
        self.assertEqual(result['benchmark_gap'],7000)
        self.assertIsNone(module.metrics(1000000,None,None)['benchmark_gap'])
        self.assertLess(module.metrics(1000000,1200000,15000)['benchmark_gap'],0)


if __name__=='__main__': unittest.main()
