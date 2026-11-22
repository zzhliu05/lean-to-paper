import json
from pathlib import Path
import tempfile
import unittest
import shutil

from lean_to_paper.graph import catalog,expand,connect
from lean_to_paper.layers import build_layers
from lean_to_paper.repair import repair_examples

class AllDeclarationsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('rg'),'boundary repair requires ripgrep')
    def test_repair_removes_example_self_edge_and_preserves_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'src').mkdir()
            text='def term : Nat := 1\nexample : term = 1 := by rfl\n'
            (root/'src'/'Main.lean').write_text(text,encoding='utf-8')
            path=root/'graph.sqlite';catalog(root,path)
            db=connect(path)
            did=db.execute("SELECT id FROM declarations WHERE name='term'").fetchone()[0]
            db.execute('UPDATE declarations SET end=end+10 WHERE id=?',(did,))
            db.execute('INSERT INTO expanded VALUES (?)',(did,))
            db.execute('INSERT INTO edges VALUES (?,?,?,?,?,?)',(did,did,'proof','test_fake','term',2))
            db.commit();db.close()
            repair_examples(path,root/'out')
            db=connect(path)
            self.assertEqual(db.execute("SELECT id FROM declarations WHERE name='term'").fetchone()[0],did)
            self.assertEqual(db.execute('SELECT count(*) FROM edges').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM expanded').fetchone()[0],0)
            db.close()
            expand(path,[],all_declarations=True)
            db=connect(path)
            self.assertEqual(db.execute('SELECT count(*) FROM expanded').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM edges').fetchone()[0],0)
            db.close()

    def test_disconnected_declarations_are_expanded_and_layered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'src').mkdir()
            (root/'src'/'Main.lean').write_text('def helper := 1\ndef disconnected := helper\ndef root := 0\n',encoding='utf-8')
            path=root/'graph.sqlite';catalog(root,path)
            expand(path,['root'])
            with self.assertRaisesRegex(ValueError,'every declaration'):
                build_layers(path,root/'out',all_declarations=True)
            expand(path,[],limit=1,all_declarations=True)
            db=connect(path)
            state=json.loads(db.execute("SELECT value FROM metadata WHERE key='last_expansion'").fetchone()[0])
            self.assertFalse(state['candidate_closure_complete']);db.close()
            expand(path,[],all_declarations=True)
            build_layers(path,root/'out',all_declarations=True)
            rows=[json.loads(x) for x in (root/'out'/'candidate-layers.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({x['name']:x['candidate_structural_level'] for x in rows},
                {'helper':0,'disconnected':1,'root':0})
            summary=json.loads((root/'out'/'layer-summary.json').read_text(encoding='utf-8'))
            self.assertTrue(summary['all_declarations_layered'])
            self.assertEqual(summary['scope'],'entire_project')
