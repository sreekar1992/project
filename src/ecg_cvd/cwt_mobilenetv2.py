"""Pure-PyTorch MobileNetV2 for experimental RGB ECG CWT images.

This module contains *only* the image classifier architecture.  It is a
separate research model and is intentionally not imported by the live
``RAMNV2`` clinical adapter.  A caller must create CWT images with the exact
wavelet, scales, resizing, RGB conversion, and normalization recorded in a
versioned training manifest before this model can be trained or evaluated.

Unlike the exploratory code supplied with the project, this implementation
does not depend on ``torchvision``, OpenCV, or PyWavelets.  It expects an
already-created RGB CWT image tensor in NCHW format and returns uncalibrated
logits.  It does not provide diagnosis, treatment selection, or a fallback
when weights are missing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

import torch
from torch import Tensor, nn


RGB_CHANNELS = 3
MINIMUM_IMAGE_DIMENSION = 32


def _positive_int(name: str, value: int, *, minimum: int = 1) -> int:
    """Validate an integer option without accepting booleans as integers."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = f"an integer >= {minimum}"
        raise ValueError(f"{name} must be {qualifier}.")
    return value


def _finite_positive(name: str, value: float) -> float:
    """Validate a finite, strictly positive floating-point option."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{name} must be a finite positive number.")
    return numeric


def _make_divisible(value: float, divisor: int, minimum: int | None = None) -> int:
    """Round a channel count while avoiding a reduction greater than 10 percent."""
    lower_bound = divisor if minimum is None else minimum
    rounded = max(lower_bound, int(value + divisor / 2) // divisor * divisor)
    if rounded < 0.9 * value:
        rounded += divisor
    return int(rounded)


@dataclass(frozen=True)
class CWTMobileNetV2Config:
    """Serializable architecture contract for an experimental CWT classifier.

    The geometry is intentionally part of the model contract.  It prevents a
    checkpoint trained on, for example, 224 x 224 scalograms from silently
    accepting a differently resized CWT representation.  The model accepts
    exactly three channels because a single CWT magnitude image is duplicated
    into RGB during the separately versioned CWT preprocessing step.
    """

    num_classes: int = 17
    image_height: int = 224
    image_width: int = 224
    width_multiplier: float = 1.0
    dropout: float = 0.2
    round_nearest: int = 8
    strict_input_geometry: bool = True

    def __post_init__(self) -> None:
        _positive_int("num_classes", self.num_classes)
        _positive_int("image_height", self.image_height, minimum=MINIMUM_IMAGE_DIMENSION)
        _positive_int("image_width", self.image_width, minimum=MINIMUM_IMAGE_DIMENSION)
        _finite_positive("width_multiplier", self.width_multiplier)
        _positive_int("round_nearest", self.round_nearest)
        if isinstance(self.dropout, bool) or not math.isfinite(float(self.dropout)):
            raise ValueError("dropout must be a finite number in [0, 1).")
        if not 0.0 <= float(self.dropout) < 1.0:
            raise ValueError("dropout must be a finite number in [0, 1).")
        if not isinstance(self.strict_input_geometry, bool):
            raise ValueError("strict_input_geometry must be a boolean.")

    @property
    def input_shape(self) -> tuple[int, int, int, int]:
        """A representative batch shape in NCHW layout."""
        return (1, RGB_CHANNELS, self.image_height, self.image_width)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable model configuration metadata."""
        return asdict(self)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CWTMobileNetV2Config":
        """Build a configuration from checkpoint/manifest architecture data."""
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unexpected = sorted(set(values) - allowed)
        if unexpected:
            raise ValueError(f"Unsupported CWTMobileNetV2 configuration keys: {unexpected}.")
        return cls(**dict(values))


class ConvNormActivation(nn.Sequential):
    """A bias-free convolution, batch normalization, and ReLU6 activation."""

    def __init__(self, in_channels: int, out_channels: int, *, stride: int = 1) -> None:
        super().__init__(
            nn.Conv2d(in_channels, out_channels, 3, stride, 1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU6(inplace=True),
        )


class InvertedResidual(nn.Module):
    """MobileNetV2 inverted residual block without external model libraries."""

    def __init__(self, in_channels: int, out_channels: int, stride: int, expand_ratio: int) -> None:
        super().__init__()
        if stride not in (1, 2):
            raise ValueError("InvertedResidual stride must be 1 or 2.")
        hidden_channels = int(round(in_channels * expand_ratio))
        layers: list[nn.Module] = []
        if expand_ratio != 1:
            layers.extend(
                (
                    nn.Conv2d(in_channels, hidden_channels, 1, 1, 0, bias=False),
                    nn.BatchNorm2d(hidden_channels),
                    nn.ReLU6(inplace=True),
                )
            )
        layers.extend(
            (
                nn.Conv2d(
                    hidden_channels,
                    hidden_channels,
                    3,
                    stride,
                    1,
                    groups=hidden_channels,
                    bias=False,
                ),
                nn.BatchNorm2d(hidden_channels),
                nn.ReLU6(inplace=True),
                nn.Conv2d(hidden_channels, out_channels, 1, 1, 0, bias=False),
                nn.BatchNorm2d(out_channels),
            )
        )
        self.block = nn.Sequential(*layers)
        self.use_residual = stride == 1 and in_channels == out_channels

    def forward(self, inputs: Tensor) -> Tensor:
        outputs = self.block(inputs)
        return inputs + outputs if self.use_residual else outputs


class CWTMobileNetV2(nn.Module):
    """MobileNetV2-style classifier for precomputed RGB CWT images.

    Parameters mirror :class:`CWTMobileNetV2Config`.  Inputs must be finite
    floating-point tensors in ``(batch, 3, image_height, image_width)`` NCHW
    layout by default.  The result is raw logits of shape ``(batch,
    num_classes)``; callers should use ``torch.softmax`` only when they need
    normalized *model scores*.  Those scores are not calibrated probabilities.

    ``strict_input_geometry=False`` is permitted only for controlled research
    experiments.  A saved model's manifest should normally preserve the
    default strict geometry to avoid preprocessing drift.
    """

    # ``(expansion ratio, output channels, repetitions, first-block stride)``
    # follows the canonical MobileNetV2 stage layout.  The module is built
    # locally rather than loading a generic-image pretrained model.
    INVERTED_RESIDUAL_SETTING: tuple[tuple[int, int, int, int], ...] = (
        (1, 16, 1, 1),
        (6, 24, 2, 2),
        (6, 32, 3, 2),
        (6, 64, 4, 2),
        (6, 96, 3, 1),
        (6, 160, 3, 2),
        (6, 320, 1, 1),
    )

    def __init__(
        self,
        num_classes: int = 17,
        *,
        image_height: int = 224,
        image_width: int = 224,
        width_multiplier: float = 1.0,
        dropout: float = 0.2,
        round_nearest: int = 8,
        strict_input_geometry: bool = True,
    ) -> None:
        super().__init__()
        self.config = CWTMobileNetV2Config(
            num_classes=num_classes,
            image_height=image_height,
            image_width=image_width,
            width_multiplier=width_multiplier,
            dropout=dropout,
            round_nearest=round_nearest,
            strict_input_geometry=strict_input_geometry,
        )

        input_channels = _make_divisible(32 * self.config.width_multiplier, self.config.round_nearest)
        last_channels = _make_divisible(
            1280 * max(1.0, self.config.width_multiplier), self.config.round_nearest
        )

        features: list[nn.Module] = [ConvNormActivation(RGB_CHANNELS, input_channels, stride=2)]
        current_channels = input_channels
        for expand_ratio, channels, repetitions, first_stride in self.INVERTED_RESIDUAL_SETTING:
            output_channels = _make_divisible(
                channels * self.config.width_multiplier,
                self.config.round_nearest,
            )
            for repeat_index in range(repetitions):
                stride = first_stride if repeat_index == 0 else 1
                features.append(
                    InvertedResidual(current_channels, output_channels, stride, expand_ratio)
                )
                current_channels = output_channels
        features.append(
            nn.Sequential(
                nn.Conv2d(current_channels, last_channels, 1, 1, 0, bias=False),
                nn.BatchNorm2d(last_channels),
                nn.ReLU6(inplace=True),
            )
        )

        self.features = nn.Sequential(*features)
        self.pool = nn.AdaptiveAvgPool2d((1, 1))
        self.classifier = nn.Sequential(
            nn.Dropout(p=self.config.dropout),
            nn.Linear(last_channels, self.config.num_classes),
        )
        self.feature_channels = last_channels
        self._initialize_weights()

    def _initialize_weights(self) -> None:
        """Use deterministic-safe standard initialization before training/loading."""
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, mode="fan_out")
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.BatchNorm2d):
                nn.init.ones_(module.weight)
                nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, 0, 0.01)
                nn.init.zeros_(module.bias)

    @classmethod
    def from_config(cls, config: CWTMobileNetV2Config | Mapping[str, Any]) -> "CWTMobileNetV2":
        """Construct an architecture from saved, validated configuration data."""
        resolved = (
            config
            if isinstance(config, CWTMobileNetV2Config)
            else CWTMobileNetV2Config.from_mapping(config)
        )
        return cls(**resolved.to_dict())

    def metadata(self) -> dict[str, Any]:
        """Return manifest-ready architecture details without clinical claims."""
        return {
            "architecture": "CWTMobileNetV2",
            "architecture_version": 1,
            "research_only": True,
            "input_tensor": {
                "layout": "NCHW",
                "shape": list(self.config.input_shape),
                "dtype": "float32",
                "channels": "RGB CWT image (three channels)",
            },
            "output": {
                "shape": [1, self.config.num_classes],
                "kind": "uncalibrated_logits",
            },
            "config": self.config.to_dict(),
            "requires_training_manifest": (
                "CWT wavelet/scales, image resizing, RGB conversion, normalization, "
                "label map, and evaluation metrics are required before inference."
            ),
        }

    def _validate_input(self, inputs: Tensor) -> None:
        if not isinstance(inputs, Tensor):
            raise TypeError("CWTMobileNetV2 inputs must be a torch.Tensor.")
        if inputs.ndim != 4:
            raise ValueError("CWTMobileNetV2 expects a four-dimensional NCHW tensor.")
        if inputs.shape[1] != RGB_CHANNELS:
            raise ValueError(
                f"CWTMobileNetV2 requires {RGB_CHANNELS}-channel RGB CWT images; "
                f"received {inputs.shape[1]} channels."
            )
        if inputs.shape[0] < 1:
            raise ValueError("CWTMobileNetV2 requires at least one image in each batch.")
        if not inputs.is_floating_point():
            raise TypeError("CWTMobileNetV2 inputs must use a floating-point dtype.")
        if self.config.strict_input_geometry and (
            inputs.shape[2] != self.config.image_height or inputs.shape[3] != self.config.image_width
        ):
            raise ValueError(
                "CWT image geometry does not match this model's saved contract: "
                f"expected ({self.config.image_height}, {self.config.image_width}), received "
                f"({inputs.shape[2]}, {inputs.shape[3]})."
            )
        if not bool(torch.isfinite(inputs).all()):
            raise ValueError("CWTMobileNetV2 inputs must contain only finite values.")

    def forward_features(self, inputs: Tensor) -> Tensor:
        """Return pooled CWT image features after strict input validation."""
        self._validate_input(inputs)
        return self.pool(self.features(inputs)).flatten(1)

    def forward(self, inputs: Tensor) -> Tensor:
        """Return uncalibrated 17-class (or configured-class) logits."""
        return self.classifier(self.forward_features(inputs))
