"""Quality-gated, experimental digitization of simple ECG chart JPEGs.

This module deliberately supports only a narrow input class: a clean,
single-trace ECG chart rendered on a mostly neutral background.  It is *not*
an ECG-photo, paper-scan, or general image interpretation system.  The
resulting 10-second numerical signal is a research derivative whose units,
calibration, lead identity, and diagnostic fidelity are unknown.

The strict gates below are important: it is safer to reject an ambiguous
image than to turn a photograph, dashboard screenshot, or non-ECG graphic
into an apparently analysable waveform.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .security import APIError


# The RAMNV2 adapter is fixed to 10 seconds at 360 Hz.  This conversion does
# not infer a sampling rate from pixels; it creates the adapter's required
# research representation only after a trace has passed the image gates.
OUTPUT_SAMPLES = 3600
OUTPUT_SAMPLING_RATE_HZ = 360.0

MIN_WIDTH = 320
MIN_HEIGHT = 160
MAX_PROCESSING_WIDTH = 2_400
MAX_PROCESSING_HEIGHT = 1_400
MIN_TRACE_SPAN_FRACTION = 0.55
MIN_TRACE_COVERAGE = 0.62
MIN_VERTICAL_RANGE_PX = 8.0


@dataclass(frozen=True)
class DigitizedTrace:
    """A non-clinically-validated waveform recovered from a simple chart."""

    waveform: np.ndarray
    quality: dict[str, Any]


def _reject() -> APIError:
    return APIError(
        "ECG_IMAGE_DIGITIZATION_REJECTED",
        "The JPEG does not contain a sufficiently isolated, simple ECG chart trace for experimental digitization. "
        "Use a validated .mat or .csv waveform, or upload a clearer single-trace chart.",
        422,
    )


def _hue(rgb: np.ndarray, maximum: np.ndarray, delta: np.ndarray) -> np.ndarray:
    """Return HSV hue in [0, 1), without a dependency on OpenCV."""
    hue = np.zeros_like(maximum, dtype=np.float32)
    nonzero = delta > 1e-5
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    red = nonzero & (maximum == r)
    green = nonzero & (maximum == g)
    blue = nonzero & (maximum == b)
    hue[red] = np.mod((g[red] - b[red]) / delta[red], 6.0)
    hue[green] = ((b[green] - r[green]) / delta[green]) + 2.0
    hue[blue] = ((r[blue] - g[blue]) / delta[blue]) + 4.0
    return hue / 6.0


def _fill_short_gaps(active: np.ndarray, maximum_gap: int) -> np.ndarray:
    """Join anti-aliased/occluded portions of one otherwise continuous trace."""
    output = active.copy()
    starts = np.flatnonzero(np.diff(np.r_[False, ~active, False].astype(np.int8)) == 1)
    ends = np.flatnonzero(np.diff(np.r_[False, ~active, False].astype(np.int8)) == -1)
    for start, end in zip(starts, ends):
        if start > 0 and end < active.size and end - start <= maximum_gap:
            output[start:end] = True
    return output


def _longest_span(active: np.ndarray) -> tuple[int, int] | None:
    starts = np.flatnonzero(np.diff(np.r_[False, active, False].astype(np.int8)) == 1)
    ends = np.flatnonzero(np.diff(np.r_[False, active, False].astype(np.int8)) == -1)
    if not len(starts):
        return None
    index = int(np.argmax(ends - starts))
    return int(starts[index]), int(ends[index])


def _candidate_from_mask(mask: np.ndarray, *, method: str) -> tuple[float, np.ndarray, dict[str, Any]] | None:
    """Extract and score a left-to-right trace from a binary colour mask.

    A chart line is expected to be narrow in most columns, vary vertically,
    and occupy a large contiguous horizontal span.  These checks rule out
    coloured panels, broad backgrounds, axes, and small legend fragments.
    """
    height, width = mask.shape
    # Exclude the image border where chart frames, browser chrome, and axis
    # labels commonly sit.  The gates deliberately favour clean exports.
    y_margin = max(4, round(height * 0.035))
    x_margin = max(4, round(width * 0.015))
    region = mask[y_margin:height - y_margin, x_margin:width - x_margin]
    if region.size == 0:
        return None
    local_height, local_width = region.shape
    counts = region.sum(axis=0)
    # A continuous coloured background or filled chart has too many selected
    # pixels per column to be a trace.  Steep QRS complexes are still allowed.
    max_column_pixels = max(18, int(local_height * 0.22))
    active = (counts > 0) & (counts <= max_column_pixels)
    active = _fill_short_gaps(active, max(2, int(local_width * 0.012)))
    span = _longest_span(active)
    if span is None:
        return None
    start, end = span
    span_width = end - start
    span_fraction = span_width / local_width
    if span_fraction < MIN_TRACE_SPAN_FRACTION:
        return None

    raw_active = (counts > 0) & (counts <= max_column_pixels)
    coverage = float(raw_active[start:end].mean()) if span_width else 0.0
    if coverage < MIN_TRACE_COVERAGE:
        return None

    xs = np.flatnonzero(raw_active[start:end]) + start
    if xs.size < max(32, int(local_width * 0.25)):
        return None
    # Median y remains stable for a multi-pixel anti-aliased line; a handful
    # of vertical QRS pixels do not distort the resampled signal materially.
    ys = np.asarray([
        np.median(np.flatnonzero(region[:, column])) for column in xs
    ], dtype=np.float32)
    vertical_range = float(np.percentile(ys, 95) - np.percentile(ys, 5))
    if vertical_range < MIN_VERTICAL_RANGE_PX:
        return None
    median_thickness = float(np.median(counts[xs]))
    if median_thickness > max(10.0, local_height * 0.05):
        return None

    # A basic complexity gate rejects a horizontal rule or nearly straight
    # graphic.  It intentionally does not call this a physiological check.
    coarse = np.interp(np.linspace(float(xs[0]), float(xs[-1]), 180), xs, ys)
    derivative = np.diff(coarse)
    significant = np.abs(derivative) > max(0.20, np.std(derivative) * 0.20)
    signs = np.sign(derivative[significant])
    direction_changes = int(np.count_nonzero(np.diff(signs) != 0)) if signs.size > 1 else 0
    if direction_changes < 3:
        return None

    normalized_x = np.linspace(float(xs[0]), float(xs[-1]), OUTPUT_SAMPLES)
    trace_y = np.interp(normalized_x, xs, ys)
    # Pixel y grows down the image.  RAMNV2 preprocessing is scale invariant
    # (median/MAD), but we use a stable robust normalization before serializing.
    center = float(np.median(trace_y))
    scale = float(np.median(np.abs(trace_y - center)))
    if not np.isfinite(scale) or scale < 0.35:
        return None
    waveform = ((center - trace_y) / scale).astype(np.float32)
    if not np.isfinite(waveform).all() or float(np.ptp(waveform)) < 0.25:
        return None

    gap_fraction = 1.0 - coverage
    quality = {
        "method": method,
        "trace_x_span_fraction": round(span_fraction, 4),
        "trace_column_coverage": round(coverage, 4),
        "interpolated_column_fraction": round(gap_fraction, 4),
        "trace_vertical_range_px": round(vertical_range, 3),
        "median_trace_thickness_px": round(median_thickness, 3),
        "direction_changes": direction_changes,
    }
    # Prefer continuous, narrow traces that occupy a usable part of the chart.
    score = coverage * span_fraction * min(1.0, vertical_range / 30.0) / max(1.0, median_thickness)
    return score, waveform, quality


def _colour_candidates(rgb: np.ndarray) -> list[np.ndarray]:
    """Return masks for the strongest saturated hues in a neutral chart."""
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    delta = maximum - minimum
    saturation = delta / np.maximum(maximum, 1e-5)
    hue = _hue(rgb, maximum, delta)
    # A teal/blue/red trace remains saturated after JPEG compression; neutral
    # grid lines and black axes do not enter this branch.
    eligible = (saturation >= 0.20) & (maximum >= 0.14) & (maximum <= 0.98)
    if int(eligible.sum()) < 50:
        return []
    bins = np.floor(hue[eligible] * 36).astype(np.int16) % 36
    counts = np.bincount(bins, minlength=36)
    candidates: list[np.ndarray] = []
    for bin_id in np.argsort(counts)[::-1][:6]:
        if counts[bin_id] < 30:
            continue
        centre = (float(bin_id) + 0.5) / 36.0
        # Keep a +/- 25 degree neighbourhood to retain JPEG anti-aliasing.
        distance = np.abs(((hue - centre + 0.5) % 1.0) - 0.5)
        candidates.append(eligible & (distance <= (25.0 / 360.0)))
    return candidates


def digitize_ecg_jpeg(payload: bytes) -> DigitizedTrace:
    """Recover an experimental 3,600-sample trace from a clean JPEG chart.

    ``APIError`` is deliberately raised for inputs that do not meet the narrow
    quality envelope.  Callers must retain the original JPEG as the source
    artifact and must never label the output as a calibrated ECG acquisition.
    """
    try:
        with Image.open(BytesIO(payload)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise _reject() from exc
    original_width, original_height = image.size
    if original_width < MIN_WIDTH or original_height < MIN_HEIGHT:
        raise _reject()
    if original_width > MAX_PROCESSING_WIDTH or original_height > MAX_PROCESSING_HEIGHT:
        image.thumbnail((MAX_PROCESSING_WIDTH, MAX_PROCESSING_HEIGHT), Image.Resampling.LANCZOS)
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise _reject()

    scored: list[tuple[float, np.ndarray, dict[str, Any]]] = []
    for mask in _colour_candidates(rgb):
        candidate = _candidate_from_mask(mask, method="saturated_colour_trace")
        if candidate is not None:
            scored.append(candidate)

    # Some clean reports use a black trace. This conservative fallback only
    # considers the chart interior, keeping dark page chrome/axis labels out.
    if not scored:
        luminance = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
        candidate = _candidate_from_mask(luminance < 0.42, method="dark_trace_fallback")
        if candidate is not None:
            scored.append(candidate)
    if not scored:
        raise _reject()

    _score, waveform, quality = max(scored, key=lambda item: item[0])
    height, width = rgb.shape[:2]
    quality.update({
        "source_image_width": original_width,
        "source_image_height": original_height,
        "processed_image_width": int(width),
        "processed_image_height": int(height),
        "output_samples": OUTPUT_SAMPLES,
        "sampling_rate_hz": OUTPUT_SAMPLING_RATE_HZ,
        "quality_gate": "PASSED_EXPERIMENTAL_SIMPLE_TRACE_ONLY",
    })
    return DigitizedTrace(waveform=waveform, quality=quality)
