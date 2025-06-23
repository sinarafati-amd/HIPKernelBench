"""
DEAP GA – evolves kernel tunables.
"""

from __future__ import annotations
import random, itertools
from deap import base, creator, tools
from typing import Dict, Any

RANGE = {
    "block_size"  : [64,128,256,512,1024],
    "vector_width": [1,2,4,8],
    "tile_m"      : [8,16,32,64],
    "tile_n"      : [8,16,32,64],
}

_KEYS = tuple(RANGE.keys())          # fixed order for genomes
creator.create("FitnessMax", base.Fitness, weights=(1.0,))
creator.create("Individual", list,   fitness=creator.FitnessMax)

def _random_ind():
    return creator.Individual([random.choice(RANGE[k]) for k in _KEYS])

toolbox = base.Toolbox()
toolbox.register("individual", _random_ind)
toolbox.register("population", tools.initRepeat, list, toolbox.individual)
toolbox.register("mate", tools.cxUniform, indpb=0.5)
toolbox.register("mutate", tools.mutUniformInt, low=0, up=len(RANGE["block_size"])-1, indpb=0.2)
toolbox.register("select", tools.selTournament, tournsize=3)

class GeneticOpt:
    def __init__(self, space: Dict[str,List[Any]], pop_size=16, ngen=20):
        # build your toolbox.RANGE dynamically
        from deap import base, creator, tools
        creator.create("FitnessMax", base.Fitness, weights=(1.0,))
        creator.create("Individual", list,   fitness=creator.FitnessMax)
        self.keys   = list(space.keys())
        self.range  = space
        toolbox      = base.Toolbox()
        toolbox.register("individual", 
                         lambda: creator.Individual([random.choice(self.range[k]) 
                                                     for k in self.keys]))
        toolbox.register("population", tools.initRepeat, list, toolbox.individual)
        toolbox.register("mate", tools.cxUniform, indpb=0.5)
        toolbox.register("mutate", tools.mutUniformInt, 
                          low=0, up=len(self.range[self.keys[0]])-1, indpb=0.2)
        toolbox.register("select", tools.selTournament, tournsize=3)
        self.pop     = toolbox.population(n=pop_size)
        self.toolbox = toolbox
        self.ngen    = ngen
        self.cur_gen = 0
        self.cursor  = 0
    # ----------------------------------------------
    def next_params(self) -> Dict[str,Any] | None:
        if self.cur_gen >= self.ngen:
            return None
        if self.cursor >= len(self.pop):
            # done evaluating gen → evolve
            self._evolve()
            self.cursor = 0
        ind = self.pop[self.cursor]
        self.cursor += 1
        return dict(zip(_KEYS, ind)) | {"_ind": ind}
    # ----------------------------------------------
    def update(self, handle, speedup: float):
        ind = handle     # here handle is the Individual itself
        ind.fitness.values = (speedup,)
    # ----------------------------------------------
    def _evolve(self):
        offspring = toolbox.select(self.pop, len(self.pop))
        offspring = list(map(toolbox.clone, offspring))
        for c1,c2 in zip(offspring[::2], offspring[1::2]):
            if random.random() < 0.5:
                toolbox.mate(c1, c2)
                del c1.fitness.values, c2.fitness.values
        for mut in offspring:
            if random.random() < 0.3:
                toolbox.mutate(mut)
                del mut.fitness.values
        self.pop = offspring
        self.cur_gen += 1
