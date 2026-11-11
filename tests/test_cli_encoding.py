import json,os,subprocess,sys,unittest
from pathlib import Path

class OutputEncodingTests(unittest.TestCase):
    def test_chinese_catalog_works_in_non_utf8_redirected_environment(self):
        root=Path(__file__).resolve().parents[1]
        env={**os.environ,'PYTHONIOENCODING':'cp1252','PYTHONUTF8':'0'}
        p=subprocess.run([sys.executable,'-B',str(root/'ekb_bench.py'),'list'],env=env,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr.decode('utf8',errors='replace'))
        rows=json.loads(p.stdout.decode('utf8'))
        self.assertEqual(len(rows),5)
        self.assertTrue(any('转录' in x['name'] for x in rows))

if __name__=='__main__':unittest.main()
