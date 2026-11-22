"""Validate compressed static coverage, degrees, counts and checksums."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def load(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:return json.load(stream)


def validate(root):
    root=Path(root)
    manifest=json.loads((root/'data/manifest.json').read_text(encoding='utf-8'))
    expected=manifest['overview']['metadata']
    checksums=json.loads((root/'data/checksums.json').read_text(encoding='utf-8'))
    for path,sha in checksums.items():
        if hashlib.sha256((root/path).read_bytes()).hexdigest()!=sha:raise ValueError('Hash mismatch: '+path)
    seen=set();layer_seen=set();counts=Counter();props=0;outgoing=incoming=out_records=in_records=0
    for path in (root/'data/nodes').glob('*.gz'):
        for key,n in load(path).items():
            did=int(key)
            assert n['id']==did and did not in seen
            seen.add(did);counts[n['level']]+=1;props+=int(n['is_prop'])
            assert len(n['dependencies'])==n['out_degree']
            assert len(n['dependents'])==n['in_degree']
            for direction in ('dependencies','dependents'):
                assert n[direction]==sorted(n[direction],key=lambda x:(x[3],x[0]))
                assert len({x[0] for x in n[direction]})==len(n[direction])
            outgoing+=len(n['dependencies']);incoming+=len(n['dependents'])
            out_records+=sum(x[2] for x in n['dependencies']);in_records+=sum(x[2] for x in n['dependents'])
    for layer in manifest['overview']['layers']:
        rows=load(root/f"data/layers/{layer['level']}.json.gz")
        assert len(rows)==layer['declarations']==counts[layer['level']]
        assert sum(r[2] in ('theorem','lemma') for r in rows)==layer['propositions']
        assert rows==sorted(rows,key=lambda r:(r[1],r[0]))
        for row in rows:
            assert row[0] not in layer_seen and row[3]==layer['level']
            layer_seen.add(row[0])
    assert seen==layer_seen and len(seen)==expected['declarations']
    assert props==expected['propositions']
    assert outgoing==incoming==expected['dependency_pairs']
    assert out_records==in_records==expected['reference_records']
    sources=sum(len(load(path)) for path in (root/'data/sources').glob('*.gz'))
    assert sources==manifest['source_files']
    result=dict(declarations=len(seen),propositions=props,layers=len(counts),
                dependency_pairs=outgoing,reference_records=out_records,source_files=sources,
                checked_file_hashes=len(checksums),bytes=sum((root/p).stat().st_size for p in checksums))
    print(json.dumps(result),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args()
    validate(args.directory)
