import json
import pathlib
import sqlite3
import tempfile
import unittest

from lean_to_paper.source import mask, parse
from lean_to_paper.graph import catalog, connect, expand, export
from lean_to_paper.cycles import strongly_connected_components
from lean_to_paper.policy import annotate

class SourceTests(unittest.TestCase):
    def test_anonymous_example_is_not_part_of_previous_declaration(self):
        text='def term : Nat := 1\nexample : term = 1 := by rfl\ndef other := term\n'
        clean,decls,_=parse(text,'Test')
        self.assertEqual([d['name'] for d in decls],['term','other'])
        first=clean[decls[0]['start']:decls[0]['end']]
        self.assertNotIn('example',first)
        self.assertNotIn('term',clean[decls[0]['name_end']:decls[0]['end']])
    def test_educational_policy_never_deletes_dependencies(self):
        policy={'overrides':{'basic':'school','calculus':'undergraduate_textbook'}}
        self.assertEqual(annotate({'name':'basic'},policy)['article_action'],'omit_proof')
        self.assertEqual(annotate({'name':'calculus'},policy)['article_action'],'retain')
        self.assertEqual(annotate({'name':'unknown'},policy)['education_level'],'unclassified')
        policy['proof_omission_threshold']='undergraduate_textbook_and_below'
        node=annotate({'name':'calculus'},policy)
        self.assertEqual(node['article_action'],'omit_proof')
        self.assertTrue(node['retain_dependency_node'])
    def test_iterative_cycles(self):
        self.assertEqual(strongly_connected_components([(1,2),(2,3)]),[])
        actual=strongly_connected_components([(1,2),(2,1),(3,3),(4,1)])
        self.assertEqual({tuple(x) for x in actual},{(1,2),(3,)})
    def test_nested_comments_and_strings(self):
        text='/- theorem fake /- nested -/ -/\ndef x := "sorry theorem fake" -- axiom bad\n'
        cleaned=mask(text)
        self.assertEqual(len(cleaned),len(text))
        self.assertEqual(cleaned.count('\n'),text.count('\n'))
        self.assertNotIn('fake',cleaned)
        self.assertEqual([d['name'] for d in parse(text,'A')[1]],['x'])

    def test_namespace_scope_and_binder_default(self):
        text='namespace A\nsection\nopen B\ndef f (x : Nat := 1) : Nat := x\nend\ndef g := f\nend A\ndef h := A.g\n'
        clean,decls,_=parse(text,'Test')
        self.assertEqual([d['name'] for d in decls],['A.f','A.g','h'])
        self.assertEqual(decls[0]['opens'],['B'])
        self.assertEqual(decls[1]['opens'],[])
        self.assertTrue(clean[decls[0]['body_start']:].startswith(' x'))

class IntegrationTests(unittest.TestCase):
    def test_interrupted_expansion_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); (root/'src').mkdir()
            (root/'src'/'Main.lean').write_text('def c := 1\ndef b := c\ndef root := b\n',encoding='utf-8')
            path=root/'graph.sqlite'
            catalog(root,path)
            expand(path,['root'],limit=1)
            db=connect(path)
            state=json.loads(db.execute("SELECT value FROM metadata WHERE key='last_expansion'").fetchone()[0])
            self.assertFalse(state['candidate_closure_complete'])
            db.close()
            expand(path,['root'])
            db=connect(path)
            self.assertEqual(db.execute('SELECT count(*) FROM expanded').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM edges').fetchone()[0],2)
            state=json.loads(db.execute("SELECT value FROM metadata WHERE key='last_expansion'").fetchone()[0])
            self.assertTrue(state['candidate_closure_complete'])
            db.close()

    def test_changed_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); (root/'src').mkdir()
            file=root/'src'/'Main.lean'
            file.write_text('def root := 1\n',encoding='utf-8')
            catalog(root,root/'graph.sqlite')
            file.write_text('def root := 2\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Source changed'):
                expand(root/'graph.sqlite',['root'])

    def test_graph_preserves_ambiguity_shadowing_and_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); (root/'src').mkdir()
            (root/'src'/'A.lean').write_text('def Alpha.x : Nat := 1\ndef Beta.x : Nat := 2\ndef ty : Type := Nat\ndef base : ty := 1\n',encoding='utf-8')
            (root/'src'/'Main.lean').write_text('import A\nopen Alpha Beta\ntheorem root (base : Nat) : ty := by\n  have h := Alpha.x\n  have z := x\n  exact base\n',encoding='utf-8')
            dbpath=root/'graph.sqlite'
            catalog(root,dbpath)
            expand(dbpath,['root'])
            db=connect(dbpath)
            edges=[tuple(r) for r in db.execute('SELECT t.name,e.phase FROM edges e JOIN declarations s ON e.source=s.id JOIN declarations t ON e.target=t.id WHERE s.name=\'root\'')]
            self.assertIn(('ty','statement'),edges)
            self.assertIn(('Alpha.x','proof'),edges)
            self.assertNotIn(('base','proof'),edges)
            self.assertEqual(db.execute("SELECT reason FROM unresolved WHERE token='x'").fetchone()[0],'ambiguous')
            summary=json.loads(db.execute("SELECT value FROM metadata WHERE key='last_expansion'").fetchone()[0])
            self.assertTrue(summary['candidate_closure_complete'])
            self.assertFalse(summary['semantic_complete'])
            db.close()
            export(dbpath,root/'out',depth=1)
            view=json.loads((root/'out'/'main-preview.json').read_text(encoding='utf-8'))
            self.assertEqual(view['direction'],'declaration -> referenced declaration')
            self.assertTrue(all(n['article_action']=='retain' for n in view['nodes']))
            with self.assertRaises(FileExistsError): catalog(root,dbpath)

if __name__=='__main__': unittest.main()
