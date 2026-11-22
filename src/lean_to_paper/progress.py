"""Read-only live progress for a concurrently running graph expansion."""
import json
import pathlib
import sqlite3
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def snapshot(db):
    counts={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            for table in ('declarations','expanded','edges')}
    row=db.execute("SELECT value FROM metadata WHERE key='last_expansion'").fetchone()
    state=json.loads(row[0]) if row else {}
    counts['candidate_closure_complete']=state.get('candidate_closure_complete',False)
    counts['expansion_scope']=state.get('scope','root_closure')
    return counts

def watch(path,interval=3,once=False):
    if interval<=0: raise ValueError('Interval must be positive')
    uri=pathlib.Path(path).resolve().as_uri()+'?mode=ro'
    db=sqlite3.connect(uri,uri=True,timeout=10)
    previous=None
    try:
        while True:
            current=snapshot(db); now=time.monotonic()
            rate=(current['expanded']-previous[0])/(now-previous[1]) if previous else 0
            ratio=100*current['expanded']/max(1,current['declarations'])
            status='候选闭包已完成' if current['candidate_closure_complete'] else '展开中'
            print(f"已展开 {current['expanded']:,} / 全项目 {current['declarations']:,} 声明 ({ratio:.2f}% 目录覆盖率) | 候选引用 {current['edges']:,} | {rate:.0f} 声明/秒 | {status}",flush=True)
            if once or current['candidate_closure_complete']: break
            previous=(current['expanded'],now)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally: db.close()

def serve(path,port=0):
    uri=pathlib.Path(path).resolve().as_uri()+'?mode=ro'
    page=(pathlib.Path(__file__).parent/'progress.html').read_bytes()
    graph_page=pathlib.Path(path).resolve().parent/'main-preview.html'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/api/progress':
                with closing(sqlite3.connect(uri,uri=True,timeout=10)) as db:
                    db.execute('BEGIN')
                    payload=snapshot(db)
                payload['graph_ready']=graph_page.is_file()
                content=json.dumps(payload).encode('utf-8'); mime='application/json; charset=utf-8'
            elif self.path in ('/graph','/graph/'):
                if not graph_page.is_file():
                    self.send_error(503,'Graph export is not ready yet'); return
                content=graph_page.read_bytes(); mime='text/html; charset=utf-8'
            elif self.path in ('/','/index.html'):
                content=page; mime='text/html; charset=utf-8'
            else:
                self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type',mime)
            self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(content)))
            self.end_headers(); self.wfile.write(content)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Live progress: http://127.0.0.1:{server.server_port}/',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
