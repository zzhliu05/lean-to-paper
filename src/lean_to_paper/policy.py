"""Article visibility is separate from dependency inclusion."""
import json
import pathlib

LEVELS={'school':0,'undergraduate_textbook':1,'graduate':2,'research':3}

def load_policy(path=None):
    if path is None:
        path=pathlib.Path(__file__).resolve().parents[2]/'policy.json'
    config=json.loads(pathlib.Path(path).read_text(encoding='utf-8')) if pathlib.Path(path).exists() else {}
    threshold=config.get('proof_omission_threshold','below_undergraduate_textbook')
    if threshold not in ('below_undergraduate_textbook','undergraduate_textbook_and_below'):
        raise ValueError(f'Unknown omission threshold: {threshold}')
    for name,level in config.get('overrides',{}).items():
        if level not in (*LEVELS,'unclassified'):
            raise ValueError(f'Unknown education level for {name}: {level}')
    return config

def annotate(node,policy):
    level=policy.get('overrides',{}).get(node['name'],'unclassified')
    inclusive=policy.get('proof_omission_threshold')=='undergraduate_textbook_and_below'
    omitted=level in LEVELS and LEVELS[level] <= (1 if inclusive else 0)
    node.update(education_level=level,article_action='omit_proof' if omitted else 'retain',retain_dependency_node=True)
    return node
