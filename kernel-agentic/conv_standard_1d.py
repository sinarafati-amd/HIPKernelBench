import torch
import torch.nn as nn

class Model(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, 
                 kernel_size: int, stride: int = 1, padding: int = 0,
                 dilation: int = 1, groups: int = 1, bias: bool = False):
        super(Model, self).__init__()
        self.conv1d = nn.Conv1d(in_channels, out_channels, kernel_size, stride=stride,
                                padding=padding, dilation=dilation, groups=groups, bias=bias)
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv1d(x)

# Test code
batch_size = 16
in_channels = 3
out_channels = 64
kernel_size = 3
length = 512

def get_inputs():
    x = torch.randn(batch_size, in_channels, length)
    return [x]

def get_init_inputs():
    return [in_channels, out_channels, kernel_size]  # Provide in_channels, out_channels, kernel_size for initialization