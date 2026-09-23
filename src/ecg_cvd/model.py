from __future__ import annotations
import torch
from torch import nn
import torch.nn.functional as F


class DepthwiseResidualAttention(nn.Module):
    def __init__(self, channels: int, stride: int = 1):
        super().__init__()
        self.dw = nn.Conv1d(channels, channels, 5, stride, 2, groups=channels, bias=False)
        self.pw = nn.Conv1d(channels, channels, 1, bias=False)
        self.bn1, self.bn2 = nn.BatchNorm1d(channels), nn.BatchNorm1d(channels)
        self.attention = nn.Sequential(nn.AdaptiveAvgPool1d(1), nn.Conv1d(channels, max(4, channels // 4), 1),
                                       nn.ReLU(), nn.Conv1d(max(4, channels // 4), channels, 1), nn.Sigmoid())
        # Match Conv1d's ceil-like output length when an odd-length waveform is downsampled.
        self.skip = nn.Identity() if stride == 1 else nn.AvgPool1d(stride, ceil_mode=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = F.relu(self.bn1(self.dw(x)))
        y = self.bn2(self.pw(y)) * self.attention(y)
        return F.relu(y + self.skip(x))


class RAMNV2(nn.Module):
    """Small 1-D residual-attention network inspired by MobileNetV2 principles."""
    def __init__(self, num_classes: int):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv1d(1, 32, 7, 2, 3, bias=False), nn.BatchNorm1d(32), nn.ReLU())
        self.blocks = nn.Sequential(DepthwiseResidualAttention(32), DepthwiseResidualAttention(32, 2),
                                    nn.Conv1d(32, 64, 1), nn.ReLU(), DepthwiseResidualAttention(64),
                                    DepthwiseResidualAttention(64, 2), DepthwiseResidualAttention(64))
        self.classifier = nn.Linear(64, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.blocks(self.stem(x))
        return self.classifier(x.mean(dim=-1))
