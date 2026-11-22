"""Complete, read-only candidate graph browser and reproducible derived index."""
from array import array
from contextlib import closing
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit
import uuid


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def build_index(graph, layers, output):
    """Copy every catalog node and every unique directed pair; never change graph."""
    graph, layers, output = map(lambda p: Path(p).resolve(), (graph, layers, output))
    if output.exists():
        raise FileExistsError(f'Index already exists: {output}; choose a new output path')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.building-' + uuid.uuid4().hex)
    source = readonly(graph)
    db = sqlite3.connect(temporary)
    try:
        count, maximum = source.execute('SELECT count(*),max(id) FROM declarations').fetchone()
        heights, components, sizes = (array('I', [0]) * (maximum + 1) for _ in range(3))
        seen = bytearray(maximum + 1)
        digest = hashlib.sha256()
        with layers.open('rb') as stream:
            for raw in stream:
                digest.update(raw)
                row = json.loads(raw)
                did = row['id']
                if did < 1 or did > maximum or seen[did]:
                    raise ValueError('Invalid or duplicate layer ID')
                seen[did] = 1
                heights[did] = row['candidate_structural_level']
                components[did] = row['component_id']
                sizes[did] = row['cyclic_component_size']
        if sum(seen) != count:
            raise ValueError('Layer file does not cover every declaration')
        print(f'Layer coverage checked: {count:,} declarations', flush=True)
        db.executescript('''
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE nodes(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,is_prop INTEGER,
                module TEXT,path TEXT,line INTEGER,header TEXT,level INTEGER,component_id INTEGER,
                cycle_size INTEGER,out_degree INTEGER DEFAULT 0,in_degree INTEGER DEFAULT 0,
                source_start INTEGER,source_end INTEGER,source_hash TEXT);
            CREATE TABLE links(source INTEGER,target INTEGER,phase_mask INTEGER,reference_records INTEGER,
                PRIMARY KEY(source,target)) WITHOUT ROWID;
        ''')
        query = '''SELECT d.id,d.name,d.kind,m.name,m.path,d.line,d.header,d.start,d.end,m.sha256
            FROM declarations d JOIN modules m ON m.id=d.module_id ORDER BY d.id'''
        batch = []
        for did, name, kind, module, path, line, header, start, end, sha in source.execute(query):
            if not seen[did]:
                raise ValueError(f'Missing layer for {did}')
            batch.append((did, name, kind, int(kind in ('theorem', 'lemma')), module, path,
                          line, header, heights[did], components[did], sizes[did], start, end, sha))
            if len(batch) >= 10000:
                db.executemany('INSERT INTO nodes(id,name,kind,is_prop,module,path,line,header,level,component_id,cycle_size,source_start,source_end,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                batch.clear()
        db.executemany('INSERT INTO nodes(id,name,kind,is_prop,module,path,line,header,level,component_id,cycle_size,source_start,source_end,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
        db.commit()
        print('Catalog copied; indexing all directed dependency pairs', flush=True)
        outgoing, incoming = (array('I', [0]) * (maximum + 1) for _ in range(2))
        batch, pairs, records = [], 0, 0
        for a, b, statement, body, n in source.execute('''SELECT source,target,
                max(phase='statement'),max(phase='proof'),count(*) FROM edges GROUP BY source,target'''):
            if not seen[a] or not seen[b]:
                raise ValueError('Dependency endpoint absent from catalog')
            batch.append((a, b, statement + 2 * body, n))
            outgoing[a] += 1
            incoming[b] += 1
            pairs += 1
            records += n
            if len(batch) >= 20000:
                db.executemany('INSERT INTO links VALUES(?,?,?,?)', batch)
                batch.clear()
            if pairs % 1000000 == 0:
                print(f'Dependency pairs copied: {pairs:,}', flush=True)
        db.executemany('INSERT INTO links VALUES(?,?,?,?)', batch)
        db.executemany('UPDATE nodes SET out_degree=?,in_degree=? WHERE id=?',
                       ((outgoing[i], incoming[i], i) for i in range(1, maximum + 1) if seen[i]))
        print('Creating search and reverse-dependency indexes', flush=True)
        db.executescript('''CREATE INDEX nodes_level_prop_name ON nodes(level,is_prop,name,id);
            CREATE INDEX nodes_level_name ON nodes(level,name,id);
            CREATE INDEX nodes_component ON nodes(component_id,id);
            CREATE INDEX links_target ON links(target,source);''')
        metadata = {r['key']: r['value'] for r in source.execute('SELECT * FROM metadata')}
        metadata.update(graph_db=str(graph), layers_sha256=digest.hexdigest(),
                        declarations=count, propositions=db.execute('SELECT sum(is_prop) FROM nodes').fetchone()[0],
                        dependency_pairs=pairs, reference_records=records, semantic_complete=False,
                        format_version=1)
        db.executemany('INSERT INTO metadata VALUES(?,?)',
                       ((k, json.dumps(v, ensure_ascii=False)) for k, v in metadata.items()))
        if db.execute('SELECT count(*) FROM nodes').fetchone()[0] != count:
            raise ValueError('Catalog count mismatch')
        if records != source.execute('SELECT count(*) FROM edges').fetchone()[0]:
            raise ValueError('Edge record count mismatch')
        db.execute('ANALYZE')
        db.commit()
        db.close()
        temporary.replace(output)
        print(json.dumps(dict(index=str(output), declarations=count, dependency_pairs=pairs,
                              reference_records=records), ensure_ascii=False), flush=True)
    finally:
        source.close()
        db.close()
        if temporary.exists():
            temporary.unlink()


def number(params, key, default, low=0, high=100000000):
    value = int(params.get(key, [str(default)])[0])
    if not low <= value <= high:
        raise ValueError(f'{key} must be between {low} and {high}')
    return value


def search(params, column='n.name'):
    query = params.get('q', [''])[0].strip()
    if len(query) > 200:
        raise ValueError('Search must be at most 200 characters')
    if not query:
        return '', []
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f" AND ({column} LIKE ? ESCAPE '\\' OR n.header LIKE ? ESCAPE '\\')", ['%' + escaped + '%'] * 2


class Explorer:
    def __init__(self, path):
        self.path = Path(path).resolve()
        with closing(readonly(path)) as db:
            self.metadata = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM metadata')}
            self.layers = [dict(r) for r in db.execute('''SELECT level,count(*) AS declarations,
                sum(is_prop) AS propositions FROM nodes GROUP BY level ORDER BY level''')]

    def overview(self):
        return dict(metadata=self.metadata, layers=self.layers)

    def query(self, path, params):
        if path == '/api/overview':
            return self.overview()
        with closing(readonly(self.path)) as db:
            page = number(params, 'page', 1, 1)
            page_size = number(params, 'page_size', 25, 1, 100)
            if path == '/api/declarations':
                level = number(params, 'level', 0)
                kind = params.get('kind', ['proposition'])[0]
                if kind not in ('proposition', 'all'):
                    raise ValueError('Unknown declaration kind filter')
                where, args = 'n.level=?', [level]
                if kind == 'proposition':
                    where += ' AND n.is_prop=1'
                clause, values = search(params)
                return self._page(db, 'nodes n', where + clause, args + values, page, page_size)
            parts = path.strip('/').split('/')
            if len(parts) not in (3, 4) or parts[:2] != ['api', 'node']:
                raise LookupError('Unknown endpoint')
            did = int(parts[2])
            node = db.execute('SELECT * FROM nodes WHERE id=?', (did,)).fetchone()
            if node is None:
                raise LookupError('Declaration not found')
            if len(parts) == 3:
                return dict(node)
            if parts[3] == 'source':
                return self.source(dict(node), params.get('full', ['0'])[0] == '1')
            if parts[3] != 'neighbors':
                raise LookupError('Unknown endpoint')
            direction = params.get('direction', ['dependencies'])[0]
            if direction == 'dependencies':
                join, where = 'n.id=l.target', 'l.source=?'
            elif direction == 'dependents':
                join, where = 'n.id=l.source', 'l.target=?'
            else:
                raise ValueError('Unknown dependency direction')
            clause, values = search(params)
            return self._page(db, 'links l JOIN nodes n ON ' + join, where + clause,
                              [did] + values, page, page_size, ',l.phase_mask,l.reference_records')

    @staticmethod
    def _page(db, tables, where, args, page, page_size, extra=''):
        total = db.execute(f'SELECT count(*) FROM {tables} WHERE {where}', args).fetchone()[0]
        rows = db.execute(f'''SELECT n.id,n.name,n.kind,n.level,n.module,n.line,n.header,
            n.cycle_size,n.in_degree,n.out_degree {extra} FROM {tables} WHERE {where}
            ORDER BY n.name,n.id LIMIT ? OFFSET ?''', args + [page_size, (page - 1) * page_size])
        return dict(total=total, page=page, page_size=page_size,
                    pages=max(1, (total + page_size - 1) // page_size), items=[dict(r) for r in rows])

    def source(self, node, full=False):
        root = self.metadata.get('source_root')
        # Original metadata values are JSON strings; accept both historical encodings.
        if isinstance(root, str) and root.startswith('"'):
            root = json.loads(root)
        if not root:
            return dict(available=False, reason='Source root is unavailable')
        root = Path(root).resolve()
        target = (root / node['path']).resolve()
        if not target.is_relative_to(root):
            return dict(available=False, reason='Source path outside configured root')
        try:
            stat = target.stat()
            raw, sha = read_source(str(target), stat.st_mtime_ns, stat.st_size)
            if sha != node['source_hash']:
                return dict(available=False, reason='Source hash changed since catalog creation')
            text = raw if full else raw[node['source_start']:node['source_end']]
            return dict(available=True, path=node['path'], first_line=1 if full else node['line'],
                        text=text, full=full, sha256=sha)
        except (OSError, UnicodeError) as exc:
            return dict(available=False, reason=str(exc))


@lru_cache(maxsize=8)
def read_source(path, modified, size):
    raw = Path(path).read_bytes()
    return raw.decode('utf-8-sig'), hashlib.sha256(raw).hexdigest()


def make_server(index, port=0):
    explorer = Explorer(index)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                if parsed.path in ('/', '/explorer'):
                    body = Path(__file__).with_name('explorer.html').read_bytes()
                    mime = 'text/html; charset=utf-8'
                elif parsed.path.startswith('/api/'):
                    body = json.dumps(explorer.query(parsed.path, parse_qs(parsed.query)), ensure_ascii=False).encode()
                    mime = 'application/json; charset=utf-8'
                elif parsed.path == '/favicon.ico':
                    self.send_response(204); self.end_headers(); return
                else:
                    raise LookupError('Not found')
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, LookupError) as exc:
                self.send_error(400 if isinstance(exc, ValueError) else 404, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(index, port=0):
    server = make_server(index, port)
    print(f'Complete candidate graph browser: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
