from collections import deque
import numpy as np
import pytest
from tablescan_local.components import connected_components


def reference(mask):
    unseen=set(map(tuple,np.argwhere(mask)));parts=[]
    while unseen:
        first=min(unseen);unseen.remove(first);todo=deque([first]);part=[first]
        while todo:
            y,x=todo.popleft()
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    p=(y+dy,x+dx)
                    if p in unseen:unseen.remove(p);todo.append(p);part.append(p)
        parts.append(part)
    return sorted((min(x for y,x in p),min(y for y,x in p),max(x for y,x in p)-min(x for y,x in p)+1,
                   max(y for y,x in p)-min(y for y,x in p)+1,len(p)) for p in parts)


@pytest.mark.parametrize('shape',[(1,1),(1,101),(101,1),(2,99),(99,2),(3,3),(19,21),(0,4),(4,0)])
def test_thin_empty_and_noncontiguous_masks(shape):
    rng=np.random.default_rng(2144)
    for density in (0,.1,.5,1):
        mask=(rng.random(shape)<density)[:,::-1]
        count,labels,stats,centroids=connected_components(mask)
        assert count==len(reference(mask))+1
        assert sorted(map(tuple,stats[1:].tolist()))==reference(mask)
        assert labels.shape==mask.shape
        assert np.count_nonzero(labels)==np.count_nonzero(mask)
