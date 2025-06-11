def fast_p(correct: bool, speedup: float, p: float = 1.0) -> bool:
    return correct and speedup >= p
