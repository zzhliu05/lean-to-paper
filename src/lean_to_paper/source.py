"""Offset-preserving lexical extraction. Does not elaborate Lean syntax."""
import bisect
import re

TOKEN = re.compile(r'--|/-|"')
COMMENT = re.compile(r'/-|-/')
STRING = re.compile(r'"(?:\\[\s\S]|[^"\\])*"')
IDENT_PART = r"(?:«[^»\n]+»|[^\W\d][\w'!?]*)"
IDENT = re.compile(IDENT_PART + r'(?:\.' + IDENT_PART + r')*', re.UNICODE)
DECL = re.compile(r'(?m)^[ \t]*(?:@\[[^\n]*?\][ \t]*)*(?P<mods>(?:(?:private|protected|noncomputable|unsafe|partial|nonrec)[ \t]+)*)(?P<kind>theorem|lemma|def|abbrev|axiom|opaque|instance|structure|class|inductive)\b[ \t]*(?P<name>' + IDENT_PART + r'(?:\.' + IDENT_PART + r')*)?')
COMMAND = re.compile(r'(?m)^[ \t]*(?P<cmd>namespace|section|end|open|import|example)\b[^\n]*')

def mask(text):
    chunks, pos = [], 0
    while (m := TOKEN.search(text, pos)):
        chunks.append(text[pos:m.start()])
        if m[0] == '--':
            end = text.find('\n', m.end())
            if end < 0: end = len(text)
        elif m[0] == '"':
            s = STRING.match(text, m.start())
            end = s.end() if s else len(text)
        else:
            depth, end = 1, m.end()
            while depth and (c := COMMENT.search(text, end)):
                depth += 1 if c[0] == '/-' else -1
                end = c.end()
            if depth: end = len(text)
        chunks.append(re.sub(r'[^\n]', ' ', text[m.start():end]))
        pos = end
    chunks.append(text[pos:])
    return ''.join(chunks)

def qualify(namespace, name):
    if name.startswith('_root_.'): return name[7:]
    return '.'.join(filter(None, (namespace, name)))

def body_offset(clean, start, end):
    """First top-level :=; binder defaults do not split the declaration."""
    stack = []
    i = start
    while i < end:
        c = clean[i]
        if c in '([{⦃': stack.append(c)
        elif c in ')]}⦄' and stack: stack.pop()
        elif not stack and clean.startswith(':=', i): return i + 2
        i += 1
    return end

def parse(text, module):
    clean = mask(text)
    newlines = [-1] + [m.start() for m in re.finditer('\n', clean)]
    declarations = list(DECL.finditer(clean))
    commands = list(COMMAND.finditer(clean))
    scope, opens, result, imports = [], [], [], []
    events = sorted([(m.start(), 'decl', m) for m in declarations] + [(m.start(), 'cmd', m) for m in commands])
    # Every source command also bounds the preceding declaration's text.
    boundaries = sorted({p for p, _, _ in events} | {len(text)})
    for pos, typ, m in events:
        if typ == 'cmd':
            cmd = m['cmd']
            args = m[0].strip()[len(cmd):].strip()
            if cmd in ('namespace', 'section'):
                ns = next((x[1] for x in reversed(scope) if x[0] == 'namespace'), '')
                if cmd == 'namespace': ns = qualify(ns, args.split()[0]) if args else ns
                scope.append((cmd, ns, len(opens)))
            elif cmd == 'end':
                if scope:
                    _, _, n = scope.pop()
                    del opens[n:]
            elif cmd == 'open' and not args.startswith('scoped '):
                # Complex open/renaming clauses remain a documented ambiguity.
                opens.extend(x[0] for x in IDENT.finditer(args.split('(')[0]))
            elif cmd == 'import': imports.extend(args.split())
            continue
        ns = next((x[1] for x in reversed(scope) if x[0] == 'namespace'), '')
        line = bisect.bisect_left(newlines, pos)
        name = m['name'] or f'@anonymous:{module}:{line}'
        end = boundaries[bisect.bisect_right(boundaries, pos)]
        split = body_offset(clean, m.end(), end)
        result.append(dict(name=qualify(ns, name), raw_name=name, namespace=ns,
            kind=m['kind'], private='private' in m['mods'].split(),
            line=line, start=pos, name_end=m.end(), end=end, body_start=split,
            opens=list(opens), header=text[pos:max(m.end(), split-2 if split<end else end)].strip()))
    return clean, result, imports

def shadowed_names(clean):
    """Conservative binder collection: over-suppression is preferable to false certainty."""
    names = set()
    for m in re.finditer(r'[({\[⦃]([^\n:(){}\[\]⦃⦄]*):', clean):
        names.update(t[0] for t in IDENT.finditer(m[1]))
    for m in re.finditer(r'\b(?:have|let|intro|intros|rintro|fun|∀|∃)\s+([^\n:=→↦]+)', clean):
        names.update(t[0] for t in IDENT.finditer(m[1].split('=>')[0]))
    return names
