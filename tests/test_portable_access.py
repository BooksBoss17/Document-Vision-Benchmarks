import hashlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

VENDOR=Path(__file__).resolve().parents[1]/'vendor/public-transcription'
sys.path.insert(0,str(VENDOR))
from portable_access import PortableAccess
from runtime_access import Access

class PortableDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ekb-bench-public-test-')
        self.base=Path(self.temp.name).resolve();self.root=self.base/'data';self.root.mkdir()
        self.bundle=self.base/'licensed';self.bundle.mkdir();self.index=self.bundle/'index.json'
        self.object=self.bundle/'object.bin';self.object.write_bytes(b'original licensed bytes')
        self.digest=hashlib.sha256(self.object.read_bytes()).hexdigest()
        self.row=dict(path='object.bin',sha256=self.digest,aliases=['C:\\historical\\source.bin'])
        self.save([self.row])
    def tearDown(self):self.temp.cleanup()
    def save(self,rows):self.index.write_text(json.dumps(dict(schema='ekb-licensed-dependency-index-v1',files=rows)),encoding='utf8')
    def test_actual_runtime_enters_without_developer_workspace(self):
        with patch.dict(os.environ,EKB_BENCH_DEPENDENCIES=str(self.index)):
            access=Access(self.root)
            self.assertEqual(access.locate('opaque historical ancestor',self.digest),self.object)
            access.verify()
            self.assertIn(str(VENDOR/'runtime_access.py'),access.frozen)
            self.assertIn(str(VENDOR/'portable_access.py'),access.frozen)
    def test_windows_selectors_are_portable_labels(self):
        a=PortableAccess(self.root,self.index)
        self.assertEqual(a.locate('C:/historical/source.bin'),self.object)
        self.assertEqual(a.locate('/linux/current/C:/historical/source.bin'),self.object)
    def test_unresolved_temp_path_spelling_preserves_file_identity(self):
        raw=Path(self.temp.name)
        a=PortableAccess(raw/'data',raw/'licensed/index.json')
        resolved=a.locate('original indexed object',self.digest)
        self.assertEqual(resolved,self.object)
        self.assertTrue(resolved.samefile(raw/'licensed/object.bin'))
        self.assertEqual(hashlib.sha256(resolved.read_bytes()).hexdigest(),self.digest)
        a.verify()
    def test_wrong_expected_digest_does_not_fall_back_to_alias(self):
        with self.assertRaises(FileNotFoundError):PortableAccess(self.root,self.index).locate(self.row['aliases'][0],'0'*64)
    def test_missing_dependency_is_explicit(self):
        with self.assertRaises(FileNotFoundError):PortableAccess(self.root,self.index).locate('missing original')
    def test_payload_tampering_rejected_before_use(self):
        a=PortableAccess(self.root,self.index);self.object.write_bytes(b'changed')
        with self.assertRaises(ValueError):a.locate('x',self.digest)
    def test_cached_payload_tampering_rejected_at_final_verify(self):
        a=PortableAccess(self.root,self.index);a.locate('x',self.digest);self.object.write_bytes(b'changed')
        with self.assertRaises(ValueError):a.verify()
    def test_index_mutation_rejected(self):
        a=PortableAccess(self.root,self.index);self.index.write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):a.verify()
    def test_traversal_absolute_and_drive_paths_rejected(self):
        for path in ['../object.bin','/object.bin','C:/object.bin','folder\\object.bin']:
            with self.subTest(path=path):
                self.save([{**self.row,'path':path}])
                with self.assertRaises(ValueError):PortableAccess(self.root,self.index)
    def test_ambiguous_casefold_selector_rejected(self):
        other=self.bundle/'other';other.write_bytes(b'other')
        self.save([self.row,dict(path='other',sha256=hashlib.sha256(b'other').hexdigest(),aliases=['c:/HISTORICAL/source.bin'])])
        with self.assertRaises(ValueError):PortableAccess(self.root,self.index)
    def test_duplicate_index_keys_rejected(self):
        self.index.write_text('{"schema":"x","schema":"ekb-licensed-dependency-index-v1","files":[]}',encoding='utf8')
        with self.assertRaises(ValueError):PortableAccess(self.root,self.index)

if __name__=='__main__':unittest.main()
