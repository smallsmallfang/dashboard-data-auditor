"""Only synthetic fixtures. No credentials, network, or business data."""
from __future__ import annotations
import copy
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit import Auditor, ConfigError, Masker, dec, export_report, safe_cell, within

KEY = b'synthetic-test-key-not-for-production-0000'


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = json.loads((ROOT / 'examples/demo_config.json').read_text())
        with (ROOT / 'examples/synthetic_source.csv').open(newline='') as f:
            reader = csv.DictReader(f)
            self.headers = reader.fieldnames
            self.rows = list(reader)
        for side in ('left', 'right'):
            self.cfg['datasets'][side]['path'] = side+'.csv'
            self.write(side, self.rows)

    def write(self, side, rows, headers=None):
        with (self.root / (side+'.csv')).open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers or self.headers)
            writer.writeheader()
            writer.writerows(rows)

    def run_audit(self):
        return Auditor(self.cfg, self.root, KEY).run()

    def codes(self, result):
        return {i['code'] for i in result['issues']}

    def test_equal_passes(self):
        r = self.run_audit()
        self.assertEqual(r['status'], '已配置检查通过')
        self.assertTrue(r['comparison_executed'])
        self.assertEqual(len(r['comparisons']), 25)

    def test_missing_group_is_not_zero(self):
        self.write('right', self.rows[:-1])
        r = self.run_audit()
        self.assertIn('MISSING_GROUP', self.codes(r))
        missing = [x for x in r['comparisons'] if x['status'] == 'MISSING']
        self.assertTrue(all(x['right'] == '' and x['difference'] == '' for x in missing))

    def test_equal_totals_do_not_hide_offsets(self):
        self.cfg.pop('profit_formula')
        rows = copy.deepcopy(self.rows)
        rows[0]['revenue'] = '205'
        rows[1]['revenue'] = '145'
        self.write('right', rows)
        r = self.run_audit()
        self.assertIn('VALUE_MISMATCH', self.codes(r))
        total = next(x for x in r['comparisons'] if x['key_token']=='TOTAL' and x['metric']=='revenue')
        self.assertEqual(total['status'], 'MATCH')
        self.assertEqual(r['status'], '不通过')

    def test_duplicate_blocks_aggregation(self):
        self.write('right', self.rows + [self.rows[0]])
        r = self.run_audit()
        self.assertIn('DUPLICATE_KEY', self.codes(r))
        self.assertFalse(r['comparison_executed'])
        self.assertEqual(r['comparisons'], [])

    def test_null_number_not_zero(self):
        rows = copy.deepcopy(self.rows)
        rows[0]['fees'] = ''
        self.write('right', rows)
        r = self.run_audit()
        self.assertIn('INVALID_NUMBER', self.codes(r))
        self.assertFalse(r['comparison_executed'])

    def test_nan_infinity_and_ambiguous_numbers_rejected(self):
        for s in ('NaN','Infinity','1,234','1.23.4','1e5','', '1_000', '1/0'):
            with self.subTest(s=s), self.assertRaises(ValueError):
                dec(s)

    def test_explicit_locale(self):
        self.assertEqual(dec('1\u00a0234,50', ',', ' '), Decimal('1234.50'))
        self.assertEqual(dec('-1,234.50','.',','), Decimal('-1234.50'))
        for s in ('12,34.50','1,234.5,0', '1,234,56'):
            with self.assertRaises(ValueError):
                dec(s,'.',',')

    def test_decimal_exact(self):
        self.assertEqual(dec('0.1')+dec('0.2'), dec('0.3'))
        self.assertTrue(within(dec('0.3'),dec('0.3'),{}))

    def test_tolerance_and_zero(self):
        rule={'abs_tolerance':'0.01','rel_tolerance':'0.01'}
        self.assertTrue(within(dec('100'),dec('101'),rule))
        self.assertFalse(within(dec('100'),dec('101.01'),rule))
        self.assertTrue(within(dec('0'),dec('0.01'),rule))
        self.assertFalse(within(dec('0'),dec('0.02'),rule))

    def test_scope_mismatch_blocks(self):
        self.cfg['datasets']['right']['scope']['basis']='settlement'
        r=self.run_audit()
        self.assertEqual(r['status'],'无法验收')
        self.assertIn('SCOPE_MISMATCH',self.codes(r))
        self.assertFalse(r['comparison_executed'])

    def test_missing_metadata_blocks(self):
        del self.cfg['datasets']['left']['scope']['cost_policy']
        r=self.run_audit()
        self.assertIn('SCOPE_MISSING',self.codes(r))
        self.assertFalse(r['comparison_executed'])

    def test_currency_mismatch_blocks(self):
        self.cfg['datasets']['right']['scope']['currency']='CNY'
        self.assertFalse(self.run_audit()['comparison_executed'])

    def test_snapshot_mismatch_blocks(self):
        self.cfg['datasets']['right']['scope']['snapshot_at']='2025-01-04T00:00:00Z'
        self.assertFalse(self.run_audit()['comparison_executed'])

    def test_empty_datasets_never_pass(self):
        self.write('left',[]);self.write('right',[])
        r=self.run_audit()
        self.assertIn('EMPTY_INPUT',self.codes(r))
        self.assertNotEqual(r['status'],'已配置检查通过')

    def test_out_of_range_date(self):
        rows=copy.deepcopy(self.rows);rows[0]['event_date']='2025-02-01'
        self.write('right',rows)
        self.assertIn('DATE_INVALID_OR_OUT_OF_SCOPE',self.codes(self.run_audit()))

    def test_null_dimension(self):
        rows=copy.deepcopy(self.rows);rows[0]['sku']=''
        self.write('right',rows)
        self.assertIn('NULL_KEY',self.codes(self.run_audit()))

    def test_noninteger_quantity(self):
        rows=copy.deepcopy(self.rows);rows[0]['quantity']='1.5'
        self.write('right',rows)
        self.assertIn('INVALID_NUMBER',self.codes(self.run_audit()))

    def test_formula_identifies_internal_error(self):
        rows=copy.deepcopy(self.rows);rows[0]['profit']='81'
        self.write('right',rows)
        self.assertIn('PROFIT_FORMULA_MISMATCH',self.codes(self.run_audit()))

    def test_formula_does_not_execute_expressions(self):
        self.cfg['profit_formula']['terms']['revenue']='__import__("os")'
        with self.assertRaises(ConfigError): self.run_audit()

    def test_profit_target_cannot_reference_itself(self):
        self.cfg['profit_formula']['terms']['profit']='1'
        with self.assertRaises(ConfigError): self.run_audit()

    def test_unique_grain_can_differ_from_comparison_grain(self):
        rows=copy.deepcopy(self.rows)
        extra=copy.deepcopy(rows[0]);extra['order_id']='DEMO_ORDER_005'
        rows.append(extra);self.write('left',rows)
        aggregated=copy.deepcopy(self.rows)
        for m in self.cfg['metrics']:
            aggregated[0][m]=str(dec(aggregated[0][m])*2)
        self.write('right',aggregated)
        self.cfg['datasets']['right']['unique_key']=['store','event_date','sku']
        self.assertEqual(self.run_audit()['status'],'已配置检查通过')

    def test_leading_zero_ids_remain_distinct(self):
        rows=copy.deepcopy(self.rows);extra=copy.deepcopy(rows[0]);extra['sku']='101'
        rows.append(extra);self.write('left',rows);self.write('right',rows)
        self.assertEqual(self.run_audit()['status'],'已配置检查通过')

    def test_masking_does_not_expose_raw_identifiers_or_links(self):
        rows=copy.deepcopy(self.rows)
        for r in rows: r['product_url']='https://example.invalid/private-product-DEMO'
        self.write('left',rows,self.headers+['product_url'])
        self.write('right',rows,self.headers+['product_url'])
        r=self.run_audit();dump=json.dumps(r,ensure_ascii=False)
        for value in ('DEMO_STORE_A','DEMO_ORDER_001','000101','private-product-DEMO',str(self.root)):
            self.assertNotIn(value,dump)
        store_tokens={x['dimensions']['store'] for x in r['comparisons'] if x['key_token']!='TOTAL' and x['metric']=='quantity'}
        self.assertEqual(len(store_tokens),2)

    def test_hmac_stability_and_key_separation(self):
        self.assertEqual(Masker(KEY).token('sku','000101'),Masker(KEY).token('sku','000101'))
        self.assertNotEqual(Masker(KEY).token('sku','000101'),Masker(b'x'*32).token('sku','000101'))
        with self.assertRaises(ConfigError): Masker(b'short')

    def test_csv_formula_injection(self):
        for s in ('=1+1','+CMD','-1','@SUM(A1:A2)','\t=1+1','   =1+1'):
            self.assertTrue(safe_cell(s).startswith("'"))

    def test_source_files_unchanged(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob('*.csv')}
        self.run_audit()
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob('*.csv')}
        self.assertEqual(before,after)

    def test_export_and_refuse_overwrite(self):
        r=self.run_audit();target=self.root/'out';export_report(r,target)
        self.assertEqual({p.name for p in target.iterdir()},{'report.json','report.md','differences.csv','issues.csv'})
        with self.assertRaises(FileExistsError): export_report(r,target)

    def test_bad_csv_shape(self):
        (self.root/'right.csv').write_text('a,b\n1,2,3\n')
        self.assertIn('INPUT_SCHEMA_INVALID',self.codes(self.run_audit()))

    def test_duplicate_header(self):
        text=(self.root/'right.csv').read_text()
        text=text.replace('store,event_date','store,store',1)
        (self.root/'right.csv').write_text(text)
        self.assertIn('INPUT_SCHEMA_INVALID',self.codes(self.run_audit()))

    def test_missing_file(self):
        (self.root/'right.csv').unlink()
        self.assertIn('INPUT_UNAVAILABLE',self.codes(self.run_audit()))

    def test_cli_exit_codes(self):
        config=self.root/'config.json';config.write_text(json.dumps(self.cfg))
        cmd=[sys.executable,str(ROOT/'scripts/audit.py'),'--config',str(config),'--out',str(self.root/'cli-report')]
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn(str(self.root),result.stdout+result.stderr)
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(result.returncode,3)

    def test_skill_frontmatter(self):
        content=(ROOT/'SKILL.md').read_text()
        self.assertTrue(content.startswith('---\n'))
        self.assertIn('name: dashboard-data-auditor',content.split('---')[1])
        self.assertLess(len(content.splitlines()),500)

if __name__=='__main__':
    unittest.main()
