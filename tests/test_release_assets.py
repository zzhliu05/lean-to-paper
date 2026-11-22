import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import package_graph_release as package
from tools import restore_graph_release as restore

class ReleaseAssetsTests(unittest.TestCase):
    def test_split_restore_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'artifacts').mkdir();assets=root/'assets';assets.mkdir()
            original=os.urandom(4096)
            (root/'artifacts/candidate-nodes.jsonl').write_bytes(original)
            with patch.object(package,'PART_LIMIT',256),contextlib.redirect_stdout(io.StringIO()):
                item=package.package_one(root,assets,'candidate-nodes.jsonl')
            self.assertGreater(len(item['parts']),1)
            self.assertTrue(all(p['size']<=256 for p in item['parts']))
            (assets/'graph-data-manifest.json').write_text(json.dumps({'assets':[item]}),encoding='utf-8')
            target=root/'restored'
            argv=['restore','--assets',str(assets),'--root',str(target)]
            with patch('sys.argv',argv),contextlib.redirect_stdout(io.StringIO()):restore.main()
            self.assertEqual((target/'artifacts/candidate-nodes.jsonl').read_bytes(),original)
            with patch('sys.argv',argv),self.assertRaises(FileExistsError):restore.main()
            first=assets/item['parts'][0]['name'];first.write_bytes(b'corrupt')
            with patch('sys.argv',['restore','--assets',str(assets),'--root',str(root/'other')]),self.assertRaisesRegex(ValueError,'Bad asset'):restore.main()

    def test_manifest_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'graph-data-manifest.json').write_text(json.dumps({'assets':[{'source':'../escape','parts':[]}]}),encoding='utf-8')
            with patch('sys.argv',['restore','--assets',str(root),'--root',str(root/'out')]),self.assertRaisesRegex(ValueError,'Unsafe manifest'):restore.main()
