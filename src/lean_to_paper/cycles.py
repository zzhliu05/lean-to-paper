"""Compact iterative SCC detection for positive integer declaration IDs."""
from array import array

def strongly_connected_components(pairs):
    sources,targets=array('I'),array('I')
    maximum=0
    for a,b in pairs:
        sources.append(a); targets.append(b); maximum=max(maximum,a,b)
    if not sources: return []
    size=maximum+2
    out_offsets=array('I',[0])*size
    in_offsets=array('I',[0])*size
    self_loops=bytearray(size)
    for a,b in zip(sources,targets):
        out_offsets[a+1]+=1; in_offsets[b+1]+=1
        if a==b: self_loops[a]=1
    for i in range(1,size):
        out_offsets[i]+=out_offsets[i-1]
        in_offsets[i]+=in_offsets[i-1]
    forward=array('I',[0])*len(sources)
    reverse=array('I',[0])*len(sources)
    out_cursor=array('I',out_offsets); in_cursor=array('I',in_offsets)
    for a,b in zip(sources,targets):
        forward[out_cursor[a]]=b; out_cursor[a]+=1
        reverse[in_cursor[b]]=a; in_cursor[b]+=1
    del sources,targets,out_cursor,in_cursor
    visited=bytearray(size); order=array('I')
    for root in range(maximum+1):
        if visited[root] or (out_offsets[root]==out_offsets[root+1] and in_offsets[root]==in_offsets[root+1]): continue
        visited[root]=1
        stack=[(root,out_offsets[root])]
        while stack:
            node,index=stack[-1]
            if index==out_offsets[node+1]:
                order.append(node); stack.pop(); continue
            stack[-1]=(node,index+1)
            child=forward[index]
            if not visited[child]:
                visited[child]=1; stack.append((child,out_offsets[child]))
    visited=bytearray(size); components=[]
    for root in reversed(order):
        if visited[root]: continue
        component=[]; stack=[root]; visited[root]=1
        while stack:
            node=stack.pop(); component.append(node)
            for i in range(in_offsets[node],in_offsets[node+1]):
                child=reverse[i]
                if not visited[child]: visited[child]=1; stack.append(child)
        if len(component)>1 or self_loops[root]: components.append(sorted(component))
    return components
