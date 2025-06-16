import random, numpy as np
class EpsilonGreedy:
    def __init__(self, arms, eps=0.2):
        self.arms, self.eps = arms, eps
        self.n, self.v = [0]*len(arms), [0.0]*len(arms)

    def select(self):                       # arm index
        if random.random() < self.eps: return random.randrange(len(self.arms))
        return int(np.argmax(self.v))

    def update(self, arm, reward):
        self.n[arm] += 1
        self.v[arm] += (reward - self.v[arm]) / self.n[arm]