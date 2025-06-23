from .kernel_generator import KernelGenerator
import warnings

# Backward compatibility wrapper
class HIPGenerator(KernelGenerator):
    """
    Backward compatibility wrapper for HIPGenerator.
    This class is deprecated. Use KernelGenerator instead.
    """
    def __init__(self):
        warnings.warn(
            "HIPGenerator is deprecated. Use KernelGenerator instead.",
            DeprecationWarning,
            stacklevel=2
        )
        super().__init__(kernel_lang="hip")
