import hashlib,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ekb_bench import pinned_member

class SelectionIdentityTests(unittest.TestCase):
    def test_unchanged_manifest_cannot_authorize_edited_selection(self):
        with tempfile.TemporaryDirectory(prefix='ekb-selection-binding-') as directory:
            root=Path(directory).resolve();selection=root/'selection.json';selection.write_bytes(b'{"sample_uids":["original"]}')
            digest=hashlib.sha256(selection.read_bytes()).hexdigest()
            (root/'checksums.sha256').write_text(digest+'  selection.json\n',encoding='utf8')
            self.assertEqual(pinned_member(root,'selection.json'),selection)
            selection.write_bytes(b'{"sample_uids":["smaller-subset"]}')
            with self.assertRaises(ValueError):pinned_member(root,'selection.json')
    def test_unknown_member_cannot_be_selected(self):
        with tempfile.TemporaryDirectory(prefix='ekb-selection-binding-') as directory:
            root=Path(directory).resolve();(root/'checksums.sha256').write_text('',encoding='utf8');(root/'other.json').write_text('{}',encoding='utf8')
            with self.assertRaises(ValueError):pinned_member(root,'other.json')

if __name__=='__main__':unittest.main()
