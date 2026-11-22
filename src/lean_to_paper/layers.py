"""Provisional longest-path layers on the condensed source candidate graph.

These are not Mathlib-anchored semantic levels. External and implicit dependencies
are missing; a candidate graph leaf is never asserted to be a mathematical base.
"""
from array import array
from collections import deque
import json
from pathlib import Path
import sqlite3

from .cycles import strongly_connected_components

def condensed_levels(node_ids, pairs_factory):
    """Return component representatives, finite structural heights, and cycles.

    Edges point from a declaration to a prerequisite. The factory must provide
    a fresh iterable each time. Repeated edges are allowed. Isolated nodes have
    structural height zero. SCCs are treated as single components.
    """
    node_ids=array('I',node_ids)
    maximum=max(node_ids,default=0)
    representative=array('I',range(maximum+1))
    cyclic=strongly_connected_components(pairs_factory())
    for component in cyclic:
        root=min(component)
        for node in component: representative[node]=root
    active=bytearray(maximum+1)
    for node in node_ids: active[representative[node]]=1
    remaining=array('I',[0])*(maximum+1)
    offsets=array('I',[0])*(maximum+2)
    for source,target in pairs_factory():
        a,b=representative[source],representative[target]
        if a!=b: remaining[a]+=1; offsets[b+1]+=1
    for i in range(1,len(offsets)): offsets[i]+=offsets[i-1]
    parents=array('I',[0])*offsets[-1]
    cursors=array('I',offsets)
    for source,target in pairs_factory():
        a,b=representative[source],representative[target]
        if a!=b: parents[cursors[b]]=a; cursors[b]+=1
    del cursors
    heights=array('I',[0])*(maximum+1)
    queue=deque(n for n in range(maximum+1) if active[n] and not remaining[n])
    visited=0
    while queue:
        node=queue.popleft(); visited+=1
        for i in range(offsets[node],offsets[node+1]):
            parent=parents[i]
            heights[parent]=max(heights[parent],heights[node]+1)
            remaining[parent]-=1
            if remaining[parent]==0: queue.append(parent)
    if visited!=sum(active): raise ValueError('Condensation graph unexpectedly contains a cycle')
    return representative,heights,cyclic

def build_layers(dbpath,outdir,all_declarations=False):
    db=sqlite3.connect(Path(dbpath).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        state=db.execute("SELECT value FROM metadata WHERE key='last_expansion'").fetchone()
        if not state or not json.loads(state[0]).get('candidate_closure_complete'):
            raise ValueError('Finish expanding the candidate closure before calculating layers')
        ids=[r[0] for r in db.execute('SELECT id FROM expanded')]
        total=db.execute('SELECT count(*) FROM declarations').fetchone()[0]
        if all_declarations and len(ids)!=total:
            raise ValueError('All-declaration layering requires expanding every declaration first (expand --all)')
        print(f'Layering {len(ids):,} expanded declarations',flush=True)
        reps,heights,cycles=condensed_levels(ids,lambda:db.execute('SELECT source,target FROM edges'))
        sizes={min(c):len(c) for c in cycles}
        out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
        histogram={}
        with (out/'candidate-layers.jsonl').open('w',encoding='utf-8') as f:
            for did,name,module,path,line in db.execute('''SELECT d.id,d.name,m.name,m.path,d.line
                FROM expanded x JOIN declarations d ON x.id=d.id JOIN modules m ON m.id=d.module_id
                ORDER BY d.id'''):
                component=reps[did];height=heights[component]
                histogram[height]=histogram.get(height,0)+1
                row=dict(id=did,name=name,module=module,path=path,line=line,
                    candidate_structural_level=height,component_id=component,
                    cyclic_component_size=sizes.get(component,0),in_candidate_cycle=component in sizes,
                    cycle_audit_status='requires_audit' if component in sizes else 'not_in_a_cyclic_component',mathlib_level=None,
                    mathlib_level_status='unknown_external_and_implicit_dependencies_missing')
                f.write(json.dumps(row,ensure_ascii=False)+'\n')
        summary=dict(scope='entire_project' if len(ids)==total else 'expanded_candidate_closure',nodes=len(ids),
            total_catalog_declarations=total,all_declarations_layered=len(ids)==total,
            definition='L(C)=0 at candidate component leaves; otherwise 1+max L(dependency component)',
            max_candidate_structural_level=max(histogram,default=0),
            histogram=dict(sorted(histogram.items())),cyclic_components=len(cycles),
            cyclic_nodes=sum(len(c) for c in cycles),
            cycle_policy='Condensed heights are structural only. Cyclic components require audit; condensation does not validate circular reasoning.',
            mathlib_levels_available=False,semantic_complete=False,
            warning='Candidate structure only, not mathematical difficulty or a bound on true Mathlib proof depth.')
        (out/'layer-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in summary.items() if k!='histogram'},ensure_ascii=False,indent=2),flush=True)
    finally:db.close()
