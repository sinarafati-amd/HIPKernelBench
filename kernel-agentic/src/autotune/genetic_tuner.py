from deap import base, creator, tools, algorithms
import random
creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
creator.create("Ind", list, fitness=creator.FitnessMin)

def eval_ind(ind):
    tile, vec, tpb = ind
    code   = generate_kernel(tile, vec, suggest_launch(tile*tile*4, tpb))
    stats  = compile_and_profile(code)
    return (stats["avg_us"],)

toolbox = base.Toolbox()
toolbox.register("tile", random.choice, [8,16,32])
toolbox.register("vec" , random.randint, 1, 4)
toolbox.register("tpb" , random.choice, [64,128,256])
toolbox.register("individual", tools.initCycle, creator.Ind,
                 (toolbox.tile, toolbox.vec, toolbox.tpb), n=1)
toolbox.register("population", tools.initRepeat, list, toolbox.individual)
toolbox.register("evaluate",   eval_ind)
toolbox.register("mate", tools.cxTwoPoint)
toolbox.register("mutate", tools.mutShuffleIndexes, indpb=0.3)
toolbox.register("select", tools.selTournament, tournsize=3)
pop, _ = algorithms.eaSimple(toolbox.population(20), toolbox,
                             cxpb=0.5, mutpb=0.3, ngen=10)
