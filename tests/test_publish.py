"""Offline safety tests of release staging; never contact GitHub."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from publish_github import prepare

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.root=self.base/'release';self.root.mkdir()
        (self.root/'README.md').write_text('synthetic payload')
        self.hash=hashlib.sha256((self.root/'README.md').read_bytes()).hexdigest()
        self.manifest({'README.md':self.hash})
    def manifest(self,files):
        (self.root/'PUBLICATION_MANIFEST.json').write_text(json.dumps({'files':files}))
    def test_unlisted_business_file_excluded(self):
        (self.root/'business.csv').write_text('SYNTHETIC_SHOULD_NOT_BE_PUBLISHED')
        stage=prepare(self.root,self.base/'stage')
        self.assertEqual({p.name for p in stage.iterdir()},{'README.md','PUBLICATION_MANIFEST.json'})
    def test_changed_payload_rejected(self):
        (self.root/'README.md').write_text('changed content')
        with self.assertRaises(ValueError):prepare(self.root,self.base/'stage')
        self.assertFalse((self.base/'stage').exists())
    def test_path_escape_rejected(self):
        self.manifest({'../outside.txt':self.hash})
        with self.assertRaises(ValueError):prepare(self.root,self.base/'stage')
    def test_symlink_rejected(self):
        (self.root/'README.md').unlink()
        other=self.base/'other.md';other.write_text('synthetic payload')
        (self.root/'README.md').symlink_to(other)
        with self.assertRaises(ValueError):prepare(self.root,self.base/'stage')
    def test_existing_staging_directory_not_overwritten(self):
        stage=self.base/'stage';stage.mkdir()
        with self.assertRaises(FileExistsError):prepare(self.root,stage)

if __name__=='__main__':unittest.main()
