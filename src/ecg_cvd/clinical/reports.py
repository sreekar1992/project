"""Generate clinician-authored ECG report PDFs with a mandatory AI safety label."""
from __future__ import annotations

from io import BytesIO
import textwrap

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


def build_ecg_report_pdf(*, hospital: str, patient_name: str, empi_id: str, mrn: str,
                         encounter_number: str, accession_number: str, clinician: str,
                         ai_prediction: str | None, ai_score: float | None, model_version: str | None,
                         clinician_assessment: str | None, diagnosis: str | None,
                         prescription_summary: list[str], waveform_png: bytes | None = None) -> bytes:
    """Render a concise document with an optional source-waveform visualization.

    The waveform image is a visualization of the authorized stored source signal.
    It does not convert an AI result into a diagnosis, probability, or treatment
    recommendation.
    """
    output = BytesIO()
    pdf = Canvas(output, pagesize=A4)
    width, height = A4
    x, y = 18 * mm, height - 18 * mm
    pdf.setTitle("ECG research clinical-decision-support report")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(x, y, "ECG Clinical Decision-Support Report")
    y -= 9 * mm
    pdf.setFont("Helvetica", 9)
    for label, value in [
        ("Hospital", hospital), ("Patient", patient_name), ("EMPI", empi_id), ("MRN", mrn),
        ("Encounter", encounter_number), ("ECG accession", accession_number), ("Clinician", clinician),
    ]:
        pdf.drawString(x, y, f"{label}: {value or 'Not recorded'}")
        y -= 5.2 * mm

    if waveform_png:
        y -= 2 * mm
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(x, y, "ECG waveform — source-recording visualization")
        y -= 5.2 * mm
        pdf.setFont("Helvetica", 8)
        caption = ("Derived from the authorized stored ECG source signal for clinician review. "
                   "This image is not an AI diagnosis, disease probability, or treatment recommendation.")
        for line in textwrap.wrap(caption, 118):
            pdf.drawString(x, y, line)
            y -= 4.1 * mm
        try:
            image = ImageReader(BytesIO(waveform_png))
            source_width, source_height = image.getSize()
            if source_width <= 0 or source_height <= 0:
                raise ValueError("Waveform image has no drawable dimensions")
            max_width = width - (2 * x)
            max_height = 53 * mm
            scale = min(max_width / source_width, max_height / source_height)
            drawn_width, drawn_height = source_width * scale, source_height * scale
            pdf.drawImage(image, x, y - drawn_height, width=drawn_width, height=drawn_height,
                          preserveAspectRatio=True, mask="auto")
            y -= drawn_height + 5 * mm
        except Exception:
            # The PDF must stay usable if an optional visualization cannot be read.
            # Do not disclose storage or parser details in a patient-facing report.
            pdf.setFillColorRGB(0.60, 0.12, 0.08)
            pdf.drawString(x, y, "Waveform visualization was unavailable when this report was generated.")
            pdf.setFillColorRGB(0, 0, 0)
            y -= 5.2 * mm
    y -= 3 * mm
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, y, "AI-generated research/CDS observation")
    y -= 5.2 * mm
    pdf.setFont("Helvetica", 9)
    ai_text = f"Prediction: {ai_prediction or 'Not available'}"
    if ai_score is not None:
        ai_text += f" | Model score: {ai_score:.1%}"
    if model_version:
        ai_text += f" | Model version: {model_version}"
    for line in textwrap.wrap(ai_text, 105):
        pdf.drawString(x, y, line)
        y -= 4.6 * mm
    pdf.setFillColorRGB(0.60, 0.12, 0.08)
    for line in textwrap.wrap("This model-generated result supports qualified clinician review only. It is not an "
                               "autonomous diagnosis, disease probability, or treatment recommendation.", 105):
        pdf.drawString(x, y, line)
        y -= 4.6 * mm
    pdf.setFillColorRGB(0, 0, 0)
    y -= 3 * mm
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, y, "Clinician assessment")
    y -= 5.2 * mm
    pdf.setFont("Helvetica", 9)
    for line in textwrap.wrap(clinician_assessment or "Not yet recorded", 105):
        pdf.drawString(x, y, line)
        y -= 4.6 * mm
    y -= 2 * mm
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, y, "Clinician-entered diagnosis")
    y -= 5.2 * mm
    pdf.setFont("Helvetica", 9)
    for line in textwrap.wrap(diagnosis or "Not recorded", 105):
        pdf.drawString(x, y, line)
        y -= 4.6 * mm
    y -= 2 * mm
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, y, "Clinician-entered prescription record")
    y -= 5.2 * mm
    pdf.setFont("Helvetica", 9)
    if prescription_summary:
        for item in prescription_summary:
            for line in textwrap.wrap(f"• {item}", 105):
                pdf.drawString(x, y, line)
                y -= 4.6 * mm
    else:
        pdf.drawString(x, y, "No prescription record attached.")
    pdf.showPage()
    pdf.save()
    return output.getvalue()
