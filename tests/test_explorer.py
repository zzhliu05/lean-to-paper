import hashlib
import gzip
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import runpy

from lean_to_paper.explorer import Explorer, build_index
from lean_to_paper.graph import catalog, expand, export
from lean_to_paper.layers import build_layers


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        raw = b'def base := 1\r\ntheorem claim : base = 1 := by rfl\r\n'
        (self.root / 'Test.lean').write_bytes(raw)
        self.graph = self.root / 'graph.sqlite'
        with closing(sqlite3.connect(self.graph)) as db:
            db.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);
                CREATE TABLE modules(id INTEGER,name TEXT,path TEXT,sha256 TEXT);
                CREATE TABLE declarations(id INTEGER,module_id INTEGER,name TEXT,kind TEXT,
                    line INTEGER,header TEXT,start INTEGER,end INTEGER);
                CREATE TABLE edges(source INTEGER,target INTEGER,phase TEXT);''')
            db.execute('INSERT INTO metadata VALUES(?,?)', ('source_root', json.dumps(str(self.root))))
            db.execute('INSERT INTO modules VALUES(1,?,?,?)', ('Test','Test.lean',hashlib.sha256(raw).hexdigest()))
            db.executemany('INSERT INTO declarations VALUES(?,1,?,?,?,?,?,?)', [
                (1,'base','def',1,'def base',0,15),
                (2,'claim','theorem',2,'theorem claim : base = 1',15,len(raw)),
                (3,'isolated_%','lemma',3,'lemma isolated_%',0,0),
                (4,'isolated_other','theorem',4,'theorem isolated_other',0,0)])
            db.executemany('INSERT INTO edges VALUES(?,?,?)',[(2,1,'statement'),(2,1,'proof'),(2,2,'proof')])
            db.commit()
        self.layers = self.root / 'layers.jsonl'
        self.layers.write_text(''.join(json.dumps(dict(id=i,candidate_structural_level=1 if i==2 else 0,
            component_id=i,cyclic_component_size=1 if i==2 else 0))+'\n' for i in range(1,5)))
        self.index = self.root / 'browser.sqlite'
        build_index(self.graph,self.layers,self.index)
        self.explorer = Explorer(self.index)

    def query(self,path,**params):
        return self.explorer.query(path,{k:[str(v)] for k,v in params.items()})

    def test_full_catalog_and_pagination_include_disconnected_nodes(self):
        self.assertEqual(self.explorer.metadata['declarations'],4)
        self.assertEqual(self.explorer.metadata['propositions'],3)
        first = self.query('/api/declarations',level=0,page_size=1)
        second = self.query('/api/declarations',level=0,page_size=1,page=2)
        self.assertEqual(first['total'],2)
        self.assertEqual({first['items'][0]['id'],second['items'][0]['id']},{3,4})
        self.assertEqual(self.query('/api/declarations',level=0,kind='all')['total'],3)
        self.assertEqual(self.query('/api/declarations',level=148)['total'],0)

    def test_literal_search_and_validation(self):
        result = self.query('/api/declarations',level=0,q='_%')
        self.assertEqual([x['id'] for x in result['items']],[3])
        self.assertEqual(self.query('/api/declarations',level=0,q="' OR 1=1 --")['total'],0)
        for params in [dict(page=0),dict(page_size=101),dict(level=-1),dict(kind='bad'),dict(q='a'*201)]:
            with self.assertRaises(ValueError): self.query('/api/declarations',**params)
        with self.assertRaises(LookupError): self.query('/api/node/999')

    def test_both_directions_group_all_evidence_and_cycles(self):
        d = self.query('/api/node/2/neighbors',direction='dependencies')
        self.assertEqual(d['total'],2)
        base = next(x for x in d['items'] if x['id']==1)
        self.assertEqual((base['phase_mask'],base['reference_records']),(3,2))
        self.assertEqual(self.query('/api/node/1')['in_degree'],1)
        self.assertEqual(self.query('/api/node/2')['cycle_size'],1)
        self.assertEqual(self.query('/api/node/1/neighbors',direction='dependents')['items'][0]['id'],2)
        self.assertEqual(self.query('/api/node/2/neighbors',direction='dependencies',q='def base')['total'],1)

    def test_source_preserves_crlf_offsets_and_detects_changes(self):
        result = self.query('/api/node/2/source')
        self.assertTrue(result['available'])
        self.assertTrue(result['text'].startswith('theorem claim'))
        self.assertEqual(result['first_line'],2)
        self.assertTrue(self.query('/api/node/2/source',full=1)['text'].startswith('def base'))
        (self.root / 'Test.lean').write_text('changed')
        self.assertFalse(self.query('/api/node/2/source')['available'])

    def test_invalid_source_path_and_incomplete_layers_rejected(self):
        with closing(sqlite3.connect(self.index)) as db:
            db.execute("UPDATE nodes SET path='../outside.lean' WHERE id=1")
            db.commit()
        self.assertFalse(self.query('/api/node/1/source')['available'])
        self.layers.write_text(self.layers.read_text().splitlines()[0]+'\n')
        with self.assertRaises(ValueError): build_index(self.graph,self.layers,self.root/'bad.sqlite')
        self.assertFalse((self.root/'bad.sqlite').exists())
        with self.assertRaises(FileExistsError): build_index(self.graph,self.layers,self.index)

    def test_static_export_keeps_all_nodes_both_directions_and_sources(self):
        exporter = runpy.run_path(str(Path(__file__).resolve().parents[1]/'tools'/'export_explorer_site.py'))
        output = self.root/'site'
        exporter['export_site'](self.index,output)
        manifest = json.loads((output/'data'/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['overview']['metadata']['declarations'],4)
        with gzip.open(output/'data/nodes/0.json.gz','rt',encoding='utf-8') as stream: nodes=json.load(stream)
        self.assertEqual(set(nodes),{'1','2','3','4'})
        self.assertEqual(nodes['2']['dependencies'][0],[1,3,2,'base'])
        self.assertEqual(nodes['1']['dependents'][0],[2,3,2,'claim'])
        with gzip.open(output/'data/sources/0.json.gz','rt',encoding='utf-8') as stream: sources=json.load(stream)
        self.assertEqual(sources['Test.lean'],(self.root/'Test.lean').read_bytes().decode())
        with gzip.open(output/'data/layers/0.json.gz','rt',encoding='utf-8') as stream: layer=json.load(stream)
        self.assertEqual({r[0] for r in layer},{1,3,4})
        hashes=json.loads((output/'data/checksums.json').read_text())
        for file,sha in hashes.items():
            self.assertEqual(hashlib.sha256((output/file).read_bytes()).hexdigest(),sha)
        self.assertIn('static-data.js',(output/'index.html').read_text(encoding='utf-8'))

    def test_release_rehydration_preserves_ids_and_example_boundaries(self):
        source=self.root/'input';(source/'src').mkdir(parents=True)
        text='def base := 1\ntheorem root : base = 1 := by rfl\nexample : True := by trivial\n'
        (source/'src'/'Main.lean').write_text(text,encoding='utf-8')
        graph=self.root/'original.sqlite';artifacts=self.root/'release'
        catalog(source,graph);expand(graph,[],None,True)
        export(graph,artifacts,3,None);build_layers(graph,artifacts,True)
        with closing(sqlite3.connect(graph)) as db:
            totals=dict(declarations=db.execute('SELECT count(*) FROM declarations').fetchone()[0],
                        edges=db.execute('SELECT count(*) FROM edges').fetchone()[0])
        (artifacts/'graph-data-manifest.json').write_text(json.dumps(dict(totals=totals)))
        helper=runpy.run_path(str(Path(__file__).resolve().parents[1]/'tools'/'rebuild_pages_index.py'))
        helper['rebuild'](artifacts,source,self.root/'rehydrated.sqlite')
        restored=Explorer(self.root/'rehydrated.sqlite')
        self.assertEqual(restored.metadata['declarations'],2)
        self.assertEqual(restored.query('/api/node/2',{})['name'],'root')
        self.assertNotIn('example',restored.query('/api/node/2/source',{})['text'])
        self.assertEqual(restored.query('/api/node/2/neighbors',{})['items'][0]['id'],1)


if __name__ == '__main__':
    unittest.main()
