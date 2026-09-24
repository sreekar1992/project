import ChevronLeftOutlinedIcon from "@mui/icons-material/ChevronLeftOutlined";
import ChevronRightOutlinedIcon from "@mui/icons-material/ChevronRightOutlined";
import RestartAltOutlinedIcon from "@mui/icons-material/RestartAltOutlined";
import ZoomInOutlinedIcon from "@mui/icons-material/ZoomInOutlined";
import ZoomOutOutlinedIcon from "@mui/icons-material/ZoomOutOutlined";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { useEffect, useId, useMemo, useState } from "react";

export interface EcgWaveformViewerProps {
  /** Ordered ECG amplitude samples from an authorized recording. */
  samples: number[];
  /** Number of samples acquired per second. Must be greater than zero. */
  samplingRateHz: number;
  /** Visible heading and accessible name for this waveform. */
  title?: string;
}

const VIEWBOX_WIDTH = 1000;
const VIEWBOX_HEIGHT = 300;
const MARGIN = { top: 18, right: 20, bottom: 42, left: 68 };
const MAX_RENDERED_POINTS = 1600;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function formatSeconds(value: number): string {
  if (value < 1) return `${value.toFixed(2)} s`;
  if (value < 10) return `${value.toFixed(1)} s`;
  return `${value.toFixed(0)} s`;
}

function formatAmplitude(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude >= 100) return value.toFixed(0);
  if (magnitude >= 1) return value.toFixed(1);
  return value.toFixed(3);
}

/**
 * Displays an authorized ECG signal without requiring an external charting library.
 * Zoom and pan operate only on the supplied browser-memory samples; this component
 * never fetches, uploads, or stores waveform data.
 */
export function EcgWaveformViewer({ samples, samplingRateHz, title = "ECG waveform" }: EcgWaveformViewerProps) {
  // React's generated IDs may contain colons; normalize them for SVG fragment
  // references so the clip-path works consistently across browsers.
  const headingId = `ecg-waveform-${useId().replace(/:/g, "")}`;
  const descriptionId = `ecg-waveform-description-${useId().replace(/:/g, "")}`;
  const isSamplingRateValid = Number.isFinite(samplingRateHz) && samplingRateHz > 0;
  const totalSamples = samples.length;
  const totalDurationSeconds = isSamplingRateValid ? totalSamples / samplingRateHz : 0;
  const minimumWindowSamples = isSamplingRateValid
    ? Math.min(totalSamples, Math.max(1, Math.round(samplingRateHz * 0.25)))
    : 1;
  const defaultWindowSamples = isSamplingRateValid
    ? Math.min(totalSamples, Math.max(minimumWindowSamples, Math.round(samplingRateHz * 10)))
    : 1;
  const [windowSamples, setWindowSamples] = useState(defaultWindowSamples);
  const [startSample, setStartSample] = useState(0);

  useEffect(() => {
    setWindowSamples(defaultWindowSamples);
    setStartSample(0);
  }, [defaultWindowSamples, samplingRateHz, totalSamples]);

  const activeWindowSamples = clamp(windowSamples, minimumWindowSamples, Math.max(minimumWindowSamples, totalSamples));
  const maxStartSample = Math.max(0, totalSamples - activeWindowSamples);
  const visibleStartSample = clamp(startSample, 0, maxStartSample);
  const visibleEndSample = Math.min(totalSamples, visibleStartSample + activeWindowSamples);
  const visibleDurationSeconds = isSamplingRateValid ? (visibleEndSample - visibleStartSample) / samplingRateHz : 0;
  const visibleStartSeconds = isSamplingRateValid ? visibleStartSample / samplingRateHz : 0;
  const visibleEndSeconds = isSamplingRateValid ? visibleEndSample / samplingRateHz : 0;

  const chart = useMemo(() => {
    if (!isSamplingRateValid || !totalSamples || visibleEndSample <= visibleStartSample) return null;

    const values: number[] = [];
    for (let index = visibleStartSample; index < visibleEndSample; index += 1) {
      const value = samples[index];
      if (Number.isFinite(value)) values.push(value);
    }
    if (!values.length) return null;

    const rawMinimum = Math.min(...values);
    const rawMaximum = Math.max(...values);
    const rawRange = rawMaximum - rawMinimum;
    const padding = Math.max(rawRange * 0.1, Math.abs(rawMaximum) * 0.02, 0.001);
    const minimum = rawMinimum - padding;
    const maximum = rawMaximum + padding;
    const amplitudeRange = Math.max(maximum - minimum, 0.001);
    const plotWidth = VIEWBOX_WIDTH - MARGIN.left - MARGIN.right;
    const plotHeight = VIEWBOX_HEIGHT - MARGIN.top - MARGIN.bottom;
    const sampleCount = visibleEndSample - visibleStartSample;
    const stride = Math.max(1, Math.ceil(sampleCount / MAX_RENDERED_POINTS));
    const pointIndexes: number[] = [];
    for (let index = visibleStartSample; index < visibleEndSample; index += stride) pointIndexes.push(index);
    if (pointIndexes[pointIndexes.length - 1] !== visibleEndSample - 1) pointIndexes.push(visibleEndSample - 1);

    const finiteFallback = values[0];
    let previousValue = finiteFallback;
    const points = pointIndexes.map((index) => {
      const candidate = samples[index];
      const value = Number.isFinite(candidate) ? candidate : previousValue;
      previousValue = value;
      const x = MARGIN.left + ((index - visibleStartSample) / Math.max(1, sampleCount - 1)) * plotWidth;
      const y = MARGIN.top + ((maximum - value) / amplitudeRange) * plotHeight;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ");

    return { amplitudeRange, maximum, minimum, plotHeight, plotWidth, points };
  }, [isSamplingRateValid, samples, totalSamples, visibleEndSample, visibleStartSample]);

  const setZoom = (multiplier: number) => {
    setWindowSamples((current) => {
      const next = clamp(Math.round(current * multiplier), minimumWindowSamples, Math.max(minimumWindowSamples, totalSamples));
      setStartSample((currentStart) => clamp(currentStart + Math.round((current - next) / 2), 0, Math.max(0, totalSamples - next)));
      return next;
    });
  };

  const pan = (direction: -1 | 1) => {
    setStartSample((current) => clamp(current + direction * Math.max(1, Math.round(activeWindowSamples * 0.5)), 0, maxStartSample));
  };

  const resetView = () => {
    setWindowSamples(defaultWindowSamples);
    setStartSample(0);
  };

  if (!totalSamples) {
    return (
      <Box component="section" aria-labelledby={headingId} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, p: 2 }}>
        <Typography id={headingId} variant="subtitle1" fontWeight={700}>{title}</Typography>
        <Typography role="status" color="text.secondary" variant="body2" sx={{ mt: 0.5 }}>No waveform samples are available to display.</Typography>
      </Box>
    );
  }

  if (!isSamplingRateValid) {
    return (
      <Box component="section" aria-labelledby={headingId} sx={{ border: "1px solid", borderColor: "error.light", borderRadius: 2, p: 2 }}>
        <Typography id={headingId} variant="subtitle1" fontWeight={700}>{title}</Typography>
        <Typography role="alert" color="error.main" variant="body2" sx={{ mt: 0.5 }}>A valid sampling rate is required to plot time accurately.</Typography>
      </Box>
    );
  }

  const canZoomIn = activeWindowSamples > minimumWindowSamples;
  const canZoomOut = activeWindowSamples < totalSamples;
  const canPanBack = visibleStartSample > 0;
  const canPanForward = visibleStartSample < maxStartSample;
  const hasSignal = Boolean(chart);

  return (
    <Box component="section" aria-labelledby={headingId} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "hidden", bgcolor: "background.paper" }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" gap={1.5} sx={{ px: 2, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
        <Box>
          <Typography id={headingId} variant="subtitle1" fontWeight={700}>{title}</Typography>
          <Typography id={descriptionId} variant="caption" color="text.secondary" aria-live="polite">
            Showing {formatSeconds(visibleStartSeconds)}–{formatSeconds(visibleEndSeconds)} of {formatSeconds(totalDurationSeconds)} · {formatSeconds(visibleDurationSeconds)} window
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.25} aria-label="Waveform view controls">
          <Tooltip title="Pan earlier">
            <span><IconButton aria-label="Pan waveform earlier" size="small" onClick={() => pan(-1)} disabled={!canPanBack}><ChevronLeftOutlinedIcon /></IconButton></span>
          </Tooltip>
          <Tooltip title="Zoom out">
            <span><IconButton aria-label="Zoom out waveform" size="small" onClick={() => setZoom(2)} disabled={!canZoomOut}><ZoomOutOutlinedIcon /></IconButton></span>
          </Tooltip>
          <Tooltip title="Reset view">
            <span><IconButton aria-label="Reset waveform view" size="small" onClick={resetView} disabled={visibleStartSample === 0 && activeWindowSamples === defaultWindowSamples}><RestartAltOutlinedIcon /></IconButton></span>
          </Tooltip>
          <Tooltip title="Zoom in">
            <span><IconButton aria-label="Zoom in waveform" size="small" onClick={() => setZoom(0.5)} disabled={!canZoomIn}><ZoomInOutlinedIcon /></IconButton></span>
          </Tooltip>
          <Tooltip title="Pan later">
            <span><IconButton aria-label="Pan waveform later" size="small" onClick={() => pan(1)} disabled={!canPanForward}><ChevronRightOutlinedIcon /></IconButton></span>
          </Tooltip>
        </Stack>
      </Stack>

      <Box sx={{ px: { xs: 0.5, sm: 1 }, py: 1 }}>
        <svg
          role="img"
          aria-labelledby={`${headingId} ${descriptionId}`}
          viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
          width="100%"
          style={{ display: "block", minHeight: 210 }}
        >
          <defs>
            <clipPath id={`${headingId}-clip`}>
              <rect x={MARGIN.left} y={MARGIN.top} width={VIEWBOX_WIDTH - MARGIN.left - MARGIN.right} height={VIEWBOX_HEIGHT - MARGIN.top - MARGIN.bottom} />
            </clipPath>
          </defs>
          <rect width={VIEWBOX_WIDTH} height={VIEWBOX_HEIGHT} fill="#fbfcfc" />
          {chart && Array.from({ length: 6 }, (_, index) => {
            const y = MARGIN.top + (index / 5) * chart.plotHeight;
            const amplitude = chart.maximum - (index / 5) * chart.amplitudeRange;
            return (
              <g key={`horizontal-${index}`}>
                <line x1={MARGIN.left} x2={MARGIN.left + chart.plotWidth} y1={y} y2={y} stroke="#dbe8e4" strokeWidth="1" />
                <text x={MARGIN.left - 8} y={y + 4} textAnchor="end" fill="#58716a" fontSize="12">{formatAmplitude(amplitude)}</text>
              </g>
            );
          })}
          {chart && Array.from({ length: 6 }, (_, index) => {
            const x = MARGIN.left + (index / 5) * chart.plotWidth;
            const seconds = visibleStartSeconds + (index / 5) * visibleDurationSeconds;
            return (
              <g key={`vertical-${index}`}>
                <line x1={x} x2={x} y1={MARGIN.top} y2={MARGIN.top + chart.plotHeight} stroke="#dbe8e4" strokeWidth="1" />
                <text x={x} y={VIEWBOX_HEIGHT - 18} textAnchor="middle" fill="#58716a" fontSize="12">{formatSeconds(seconds)}</text>
              </g>
            );
          })}
          <line x1={MARGIN.left} x2={MARGIN.left + (chart?.plotWidth ?? VIEWBOX_WIDTH - MARGIN.left - MARGIN.right)} y1={MARGIN.top + (chart?.plotHeight ?? VIEWBOX_HEIGHT - MARGIN.top - MARGIN.bottom)} y2={MARGIN.top + (chart?.plotHeight ?? VIEWBOX_HEIGHT - MARGIN.top - MARGIN.bottom)} stroke="#607873" strokeWidth="1.25" />
          <line x1={MARGIN.left} x2={MARGIN.left} y1={MARGIN.top} y2={MARGIN.top + (chart?.plotHeight ?? VIEWBOX_HEIGHT - MARGIN.top - MARGIN.bottom)} stroke="#607873" strokeWidth="1.25" />
          {chart ? <polyline clipPath={`url(#${headingId}-clip)`} fill="none" points={chart.points} stroke="#007f91" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" /> : null}
          <text x={VIEWBOX_WIDTH / 2} y={VIEWBOX_HEIGHT - 2} textAnchor="middle" fill="#39514c" fontSize="13">Time (seconds)</text>
          <text x="16" y={VIEWBOX_HEIGHT / 2} textAnchor="middle" fill="#39514c" fontSize="13" transform={`rotate(-90 16 ${VIEWBOX_HEIGHT / 2})`}>Amplitude</text>
          {!hasSignal && <text x={VIEWBOX_WIDTH / 2} y={VIEWBOX_HEIGHT / 2} textAnchor="middle" fill="#7a4b00" fontSize="15">No finite amplitude samples are available in this time window.</text>}
        </svg>
      </Box>
    </Box>
  );
}
