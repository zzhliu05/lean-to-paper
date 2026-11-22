import unittest
from lean_to_paper.layers import condensed_levels

class LayerTests(unittest.TestCase):
    def calculate(self,nodes,edges):
        reps,heights,cycles=condensed_levels(nodes,lambda:iter(edges))
        return {n:heights[reps[n]] for n in nodes},cycles

    def test_uses_longest_path_not_shortest(self):
        levels,_=self.calculate([1,2,3,4],[(1,4),(1,2),(2,3),(3,4)])
        self.assertEqual(levels,{1:3,2:2,3:1,4:0})

    def test_condenses_cycles_instead_of_claiming_infinite_height(self):
        levels,cycles=self.calculate([1,2,3,4],[(1,2),(2,1),(2,3),(4,1)])
        self.assertEqual(levels,{1:1,2:1,3:0,4:2})
        self.assertEqual(cycles,[[1,2]])

    def test_duplicate_edges_and_self_loop(self):
        levels,cycles=self.calculate([1,2,3],[(1,2),(1,2),(2,2)])
        self.assertEqual(levels,{1:1,2:0,3:0})
        self.assertEqual(cycles,[[2]])

    def test_deep_chain_does_not_recurse(self):
        count=20000
        levels,_=self.calculate(list(range(1,count+1)),[(i,i+1) for i in range(1,count)])
        self.assertEqual(levels[1],count-1)
        self.assertEqual(levels[count],0)

    def test_empty_graph(self):
        self.assertEqual(self.calculate([],[]),({},[]))
