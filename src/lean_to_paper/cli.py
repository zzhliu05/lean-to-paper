import argparse
import json
from .graph import catalog, connect, expand, export, stats
from .progress import watch, serve
from .layers import build_layers
from .repair import repair_examples
from . import writing
from . import explorer

MAIN_ROOTS=['riemannHypothesis','riemannZeta_eq_zero_re','LFunction_ne_zero_of_half_lt_re',
            'LFunction_eq_zero_re','LFunction_eq_zero_re_of_isPrimitive']

def main():
    parser=argparse.ArgumentParser(description='Lean to mathematical exposition: source candidate dependency graphs')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('catalog'); p.add_argument('--source',required=True); p.add_argument('--db',required=True)
    p=sub.add_parser('expand'); p.add_argument('--db',required=True); p.add_argument('--root',action='append'); p.add_argument('--limit',type=int); p.add_argument('--all',action='store_true')
    p=sub.add_parser('export'); p.add_argument('--db',required=True); p.add_argument('--out',required=True); p.add_argument('--depth',type=int,default=3); p.add_argument('--policy')
    p=sub.add_parser('stats'); p.add_argument('--db',required=True)
    p=sub.add_parser('show'); p.add_argument('--db',required=True); p.add_argument('name')
    p=sub.add_parser('watch'); p.add_argument('--db',required=True); p.add_argument('--interval',type=float,default=3); p.add_argument('--once',action='store_true')
    p=sub.add_parser('serve'); p.add_argument('--db',required=True); p.add_argument('--port',type=int,default=0)
    p=sub.add_parser('layers'); p.add_argument('--db',required=True); p.add_argument('--out',required=True); p.add_argument('--all',action='store_true')
    p=sub.add_parser('repair-examples'); p.add_argument('--db',required=True); p.add_argument('--out',required=True)
    p=sub.add_parser('writing-prepare'); p.add_argument('--db',required=True); p.add_argument('--layers',required=True); p.add_argument('--out',required=True)
    p=sub.add_parser('writing-import'); p.add_argument('--out',required=True); p.add_argument('--reviews',required=True)
    p=sub.add_parser('writing-export'); p.add_argument('--out',required=True)
    p=sub.add_parser('explorer-index'); p.add_argument('--db',required=True); p.add_argument('--layers',required=True); p.add_argument('--out',required=True)
    p=sub.add_parser('explore'); p.add_argument('--index',required=True); p.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    if args.command=='explorer-index': explorer.build_index(args.db,args.layers,args.out)
    elif args.command=='explore': explorer.serve(args.index,args.port)
    elif args.command=='catalog': catalog(args.source,args.db)
    elif args.command=='expand': expand(args.db,args.root or ([] if args.all else MAIN_ROOTS),args.limit,args.all)
    elif args.command=='export': export(args.db,args.out,args.depth,args.policy)
    elif args.command=='watch': watch(args.db,args.interval,args.once)
    elif args.command=='serve': serve(args.db,args.port)
    elif args.command=='layers': build_layers(args.db,args.out,args.all)
    elif args.command=='repair-examples': repair_examples(args.db,args.out)
    elif args.command=='writing-prepare': writing.prepare(args.db,args.layers,args.out)
    elif args.command=='writing-import': writing.import_reviews(args.out,args.reviews)
    elif args.command=='writing-export': writing.export(args.out)
    elif args.command=='stats':
        db=connect(args.db); print(json.dumps(stats(db),ensure_ascii=False,indent=2)); db.close()
    else:
        db=connect(args.db)
        declarations=[dict(r) for r in db.execute('SELECT d.*,m.path FROM declarations d JOIN modules m ON d.module_id=m.id WHERE d.name=?',(args.name,))]
        for d in declarations:
            d['references']=[dict(r) for r in db.execute('SELECT t.name,e.phase,e.evidence,e.token,e.line FROM edges e JOIN declarations t ON t.id=e.target WHERE e.source=?',(d['id'],))]
            d['unresolved']=[dict(r) for r in db.execute('SELECT * FROM unresolved WHERE source=?',(d['id'],))]
        print(json.dumps(declarations,ensure_ascii=False,indent=2)); db.close()

if __name__=='__main__': main()
