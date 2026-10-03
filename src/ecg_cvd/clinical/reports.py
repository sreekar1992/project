"""Polished clinician-authored ECG research report PDFs.

The report renderer deliberately keeps model output visually distinct from
clinician-entered content. It is a presentation layer only: it does not turn
the research model score into a diagnosis, treatment plan, or prescription.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)


# The palette is intentionally conservative for print, screen readers, and
# grayscale copies. A hospital name is data, not a supplied logo, so the
# header uses a neutral vector ECG mark instead of inventing a hospital brand.
NAVY = colors.HexColor("#083B66")
BLUE = colors.HexColor("#0B679C")
TEAL = colors.HexColor("#0C8A94")
INK = colors.HexColor("#183447")
MUTED = colors.HexColor("#5C7380")
PALE_BLUE = colors.HexColor("#EEF6FB")
PALE_TEAL = colors.HexColor("#EDF8F7")
PALE_AMBER = colors.HexColor("#FFF7E8")
BORDER = colors.HexColor("#C9DCE4")
ROW_ALT = colors.HexColor("#F7FAFC")


def _display(value: object | None, fallback: str = "Not recorded") -> str:
    """Return safe, compact text for a report field."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _paragraph_text(value: object | None, fallback: str = "Not recorded") -> str:
    """Escape user-entered text before it reaches ReportLab paragraph markup."""
    return escape(_display(value, fallback)).replace("\n", "<br/>")


def _styles() -> dict[str, ParagraphStyle]:
    """Create local styles without mutating ReportLab's global style sheet."""
    return {
        "eyebrow": ParagraphStyle(
            "report-eyebrow", fontName="Helvetica-Bold", fontSize=7.2, leading=9,
            textColor=BLUE, spaceAfter=1.6 * mm, tracking=0.7,
        ),
        "intro": ParagraphStyle(
            "report-intro", fontName="Helvetica", fontSize=8.6, leading=12,
            textColor=MUTED, spaceAfter=3.2 * mm, wordWrap="CJK",
        ),
        "section": ParagraphStyle(
            "report-section", fontName="Helvetica-Bold", fontSize=11.2, leading=14,
            textColor=INK, spaceBefore=1.5 * mm, spaceAfter=0,
        ),
        "body": ParagraphStyle(
            "report-body", fontName="Helvetica", fontSize=8.7, leading=11.6,
            textColor=INK, wordWrap="CJK",
        ),
        "body-strong": ParagraphStyle(
            "report-body-strong", fontName="Helvetica-Bold", fontSize=9.4, leading=12.2,
            textColor=INK, wordWrap="CJK",
        ),
        "caption": ParagraphStyle(
            "report-caption", fontName="Helvetica", fontSize=7.7, leading=10.2,
            textColor=MUTED, wordWrap="CJK",
        ),
        "safety": ParagraphStyle(
            "report-safety", fontName="Helvetica-Bold", fontSize=8.1, leading=11,
            textColor=colors.HexColor("#8A2C14"), wordWrap="CJK",
        ),
        "table-head": ParagraphStyle(
            "report-table-head", fontName="Helvetica-Bold", fontSize=7.1, leading=8.6,
            textColor=colors.white,
        ),
        "table-cell": ParagraphStyle(
            "report-table-cell", fontName="Helvetica", fontSize=7.3, leading=9.2,
            # Keep clinician-entered words intact in the narrow printed table
            # columns. ReportLab's CJK mode may split a word character by
            # character, which is especially distracting in prescription text.
            textColor=INK,
        ),
        "table-empty": ParagraphStyle(
            "report-table-empty", fontName="Helvetica-Oblique", fontSize=8, leading=10.5,
            textColor=MUTED, wordWrap="CJK",
        ),
    }


def _draw_ecg_mark(canvas: Any, x: float, y: float) -> None:
    """Draw a small vector hospital/ECG mark in the blue report banner."""
    canvas.saveState()
    canvas.setFillColor(colors.white)
    canvas.roundRect(x, y, 21 * mm, 18 * mm, 3.4 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(1.35)
    left = x + 3.2 * mm
    center = y + 9 * mm
    points = [
        (left, center), (left + 3.2 * mm, center), (left + 5.1 * mm, center + 2.6 * mm),
        (left + 7.6 * mm, center - 4.8 * mm), (left + 10.1 * mm, center + 6.1 * mm),
        (left + 12.4 * mm, center), (left + 15 * mm, center),
    ]
    path = canvas.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    canvas.drawPath(path, stroke=1, fill=0)
    canvas.restoreState()


def _draw_header_and_footer(canvas: Any, doc: BaseDocTemplate, hospital: str) -> None:
    """Render a consistent hospital-style banner and confidentiality footer."""
    width, height = A4
    header_height = 37 * mm
    header_bottom = height - header_height
    canvas.saveState()
    if doc.page == 1:
        canvas.setTitle("ECG clinical decision-support report")

    canvas.setFillColor(NAVY)
    canvas.rect(0, header_bottom, width, header_height, fill=1, stroke=0)
    canvas.setFillColor(BLUE)
    canvas.rect(width * 0.58, header_bottom, width * 0.42, header_height, fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.rect(width * 0.77, header_bottom, width * 0.23, header_height, fill=1, stroke=0)
    canvas.setStrokeColor(colors.Color(1, 1, 1, alpha=0.58))
    canvas.setLineWidth(0.8)
    pulse_y = header_bottom + 10.5 * mm
    pulse_x = width - 68 * mm
    pulse = [
        (pulse_x, pulse_y), (pulse_x + 8 * mm, pulse_y), (pulse_x + 11 * mm, pulse_y + 4 * mm),
        (pulse_x + 14 * mm, pulse_y - 7 * mm), (pulse_x + 18 * mm, pulse_y + 9 * mm),
        (pulse_x + 22 * mm, pulse_y), (pulse_x + 34 * mm, pulse_y),
    ]
    path = canvas.beginPath()
    path.moveTo(*pulse[0])
    for point in pulse[1:]:
        path.lineTo(*point)
    canvas.drawPath(path, stroke=1, fill=0)

    _draw_ecg_mark(canvas, 16 * mm, header_bottom + 9.4 * mm)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 15.2)
    canvas.drawString(43 * mm, header_bottom + 23.5 * mm, "ECG Clinical Decision-Support Report")
    canvas.setFont("Helvetica", 8.4)
    canvas.drawString(43 * mm, header_bottom + 15.9 * mm, _display(hospital, "Hospital not recorded")[:78])
    canvas.setFont("Helvetica-Bold", 7.1)
    canvas.drawRightString(width - 16 * mm, header_bottom + 27 * mm, "RESEARCH-ONLY")
    canvas.setFont("Helvetica", 6.9)
    canvas.drawRightString(width - 16 * mm, header_bottom + 19.3 * mm, "Qualified clinician review required")

    footer_y = 13 * mm
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.45)
    canvas.line(16 * mm, footer_y + 3.7 * mm, width - 16 * mm, footer_y + 3.7 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 6.7)
    canvas.drawString(16 * mm, footer_y, "Confidential clinical record - authorized users only")
    canvas.drawRightString(width - 16 * mm, footer_y, f"Page {doc.page} - research/CDS, clinician review required")
    canvas.restoreState()


def _section_heading(title: str, styles: dict[str, ParagraphStyle], content_width: float) -> Table:
    return Table(
        [[Paragraph(escape(title), styles["section"])]],
        colWidths=[content_width],
        style=TableStyle([
            ("LINEBEFORE", (0, 0), (0, 0), 2.8, TEAL),
            ("LINEBELOW", (0, 0), (0, 0), 0.45, BORDER),
            ("LEFTPADDING", (0, 0), (0, 0), 3.1 * mm),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("TOPPADDING", (0, 0), (0, 0), 1.5 * mm),
            ("BOTTOMPADDING", (0, 0), (0, 0), 1.7 * mm),
        ]),
    )


def _field(label: str, value: object | None, styles: dict[str, ParagraphStyle]) -> Paragraph:
    label_text = escape(label.upper())
    value_text = _paragraph_text(value)
    return Paragraph(
        f'<font name="Helvetica-Bold" size="6.6" color="#517080">{label_text}</font>'
        f'<br/><font name="Helvetica-Bold" size="9.1" color="#183447">{value_text}</font>',
        styles["body"],
    )


def _identity_card(*, patient_name: str, empi_id: str, mrn: str, encounter_number: str,
                   accession_number: str, clinician: str, styles: dict[str, ParagraphStyle],
                   content_width: float) -> Table:
    data = [
        [_field("Patient", patient_name, styles), _field("MRN", mrn, styles)],
        [_field("EMPI", empi_id, styles), _field("ECG accession", accession_number, styles)],
        [_field("Encounter", encounter_number, styles), _field("Report clinician", clinician, styles)],
    ]
    return Table(
        data,
        colWidths=[content_width / 2, content_width / 2],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
            ("BOX", (0, 0), (-1, -1), 0.55, BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.8 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.8 * mm),
        ]),
    )


def _waveform_card(waveform_png: bytes | None, styles: dict[str, ParagraphStyle],
                   content_width: float) -> Table:
    caption = Paragraph(
        "Authorized source-recording visualization for clinician review. This image is not an AI diagnosis, "
        "disease probability, or treatment recommendation.",
        styles["caption"],
    )
    table_style = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.55, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.35, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]
    if not waveform_png:
        unavailable = Paragraph("No source waveform visualization was available when this report was generated.",
                                styles["table-empty"])
        return Table([[caption], [unavailable]], colWidths=[content_width], style=TableStyle(table_style))
    try:
        reader = ImageReader(BytesIO(waveform_png))
        source_width, source_height = reader.getSize()
        if source_width <= 0 or source_height <= 0:
            raise ValueError("image has no drawable dimensions")
        max_width = content_width - 8 * mm
        max_height = 57 * mm
        scale = min(max_width / source_width, max_height / source_height)
        image = Image(BytesIO(waveform_png), width=source_width * scale, height=source_height * scale)
        table_style.append(("ALIGN", (0, 1), (0, 1), "CENTER"))
        return Table([[caption], [image]], colWidths=[content_width], style=TableStyle(table_style))
    except Exception:
        unavailable = Paragraph("Waveform visualization was unavailable when this report was generated.",
                                styles["table-empty"])
        return Table([[caption], [unavailable]], colWidths=[content_width], style=TableStyle(table_style))


def _ai_observation(*, prediction: str | None, score: float | None, model_version: str | None,
                    styles: dict[str, ParagraphStyle], content_width: float) -> Table:
    details = f"<b>Prediction:</b> {_paragraph_text(prediction, 'Not available')}"
    if score is not None:
        details += f"&nbsp;&nbsp; <b>Uncalibrated model score:</b> {score:.1%}"
    if model_version:
        details += f"&nbsp;&nbsp; <b>Model version:</b> {_paragraph_text(model_version)}"
    safety = (
        "This model-generated result supports qualified clinician review only. It is not an autonomous diagnosis, "
        "disease probability, or treatment recommendation."
    )
    return Table([
        [Paragraph("AI-GENERATED RESEARCH/CDS OBSERVATION", styles["eyebrow"])],
        [Paragraph(details, styles["body-strong"])],
        [Paragraph(safety, styles["safety"])],
    ], colWidths=[content_width], style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, 1), PALE_TEAL),
        ("BACKGROUND", (0, 2), (-1, 2), PALE_AMBER),
        ("BOX", (0, 0), (-1, -1), 0.55, BORDER),
        ("LINEABOVE", (0, 2), (-1, 2), 0.45, colors.HexColor("#E5C98D")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.7 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.7 * mm),
    ]))


def _clinical_panel(*, assessment: str | None, diagnosis: str | None,
                    styles: dict[str, ParagraphStyle], content_width: float) -> Table:
    return Table([
        [Paragraph("CLINICIAN ASSESSMENT", styles["eyebrow"])],
        [Paragraph(_paragraph_text(assessment, "Not yet recorded"), styles["body"])],
        [Paragraph("CLINICIAN-ENTERED DIAGNOSIS", styles["eyebrow"])],
        [Paragraph(_paragraph_text(diagnosis), styles["body"])],
    ], colWidths=[content_width], style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFB")),
        ("BOX", (0, 0), (-1, -1), 0.55, BORDER),
        ("LINEABOVE", (0, 2), (-1, 2), 0.35, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.7 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.7 * mm),
    ]))


def _prescription_table(items: list[dict[str, str | None]], styles: dict[str, ParagraphStyle],
                        content_width: float) -> Table:
    headers = ["Medicine", "Dose", "Route", "Frequency", "Duration", "Instructions"]
    data: list[list[Paragraph]] = [[Paragraph(escape(header.upper()), styles["table-head"]) for header in headers]]
    if items:
        for item in items:
            data.append([
                Paragraph(_paragraph_text(item.get("medicine")), styles["table-cell"]),
                Paragraph(_paragraph_text(item.get("dose"), "-"), styles["table-cell"]),
                Paragraph(_paragraph_text(item.get("route"), "-"), styles["table-cell"]),
                Paragraph(_paragraph_text(item.get("frequency"), "-"), styles["table-cell"]),
                Paragraph(_paragraph_text(item.get("duration"), "-"), styles["table-cell"]),
                Paragraph(_paragraph_text(item.get("instructions"), "-"), styles["table-cell"]),
            ])
    else:
        data.append([Paragraph("No clinician-entered prescription record attached.", styles["table-empty"]),
                     Paragraph("", styles["table-empty"]), Paragraph("", styles["table-empty"]),
                     Paragraph("", styles["table-empty"]), Paragraph("", styles["table-empty"]),
                     Paragraph("", styles["table-empty"])])
    # Reserve enough space for the structured fields before allowing the
    # free-text instructions column to absorb the remaining width. In
    # particular, a route such as "sublingual" must not be squeezed into a
    # character-by-character wrap.
    widths = [24 * mm, 26 * mm, 22 * mm, 24 * mm, 26 * mm, content_width - 122 * mm]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.55, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.25 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.25 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.1 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.1 * mm),
    ]
    if items:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]))
    else:
        style.extend([("SPAN", (0, 1), (-1, 1)), ("BACKGROUND", (0, 1), (-1, 1), ROW_ALT)])
    return Table(data, colWidths=widths, repeatRows=1, splitByRow=1, style=TableStyle(style))


def build_ecg_report_pdf(*, hospital: str, patient_name: str, empi_id: str, mrn: str,
                         encounter_number: str, accession_number: str, clinician: str,
                         ai_prediction: str | None, ai_score: float | None, model_version: str | None,
                         clinician_assessment: str | None, diagnosis: str | None,
                         prescription_items: list[dict[str, str | None]],
                         waveform_png: bytes | None = None) -> bytes:
    """Render a printable, research-only clinical review PDF.

    ``prescription_items`` is clinician-authored structured data. The model is
    never used to populate it. The optional waveform image is a visualization
    of the authorized stored signal, not a raster model input.
    """
    output = BytesIO()
    width, height = A4
    left_margin = 16 * mm
    bottom_margin = 18 * mm
    top_margin = 44 * mm
    content_width = width - (2 * left_margin)
    frame = Frame(
        left_margin, bottom_margin, content_width, height - top_margin - bottom_margin,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="clinical-report-content",
    )
    doc = BaseDocTemplate(
        output, pagesize=A4, leftMargin=left_margin, rightMargin=left_margin,
        topMargin=top_margin, bottomMargin=bottom_margin,
        title="ECG clinical decision-support report",
    )
    doc.addPageTemplates([PageTemplate(
        id="clinical-report", frames=[frame],
        onPage=lambda canvas, document: _draw_header_and_footer(canvas, document, hospital),
        # ``onPageEnd`` makes the banner/footer resilient when a flowable is
        # moved to a new page by ReportLab's splitter.  The drawing occurs
        # outside the content frame, so rendering it at both lifecycle points
        # is visually identical on page one and guarantees it on later pages.
        onPageEnd=lambda canvas, document: _draw_header_and_footer(canvas, document, hospital),
    )])
    styles = _styles()
    story: list[Any] = [
        Spacer(1, 1 * mm),
        Paragraph("AUTHORIZED CLINICAL REVIEW RECORD", styles["eyebrow"]),
        Paragraph(
            "This report combines the authorized source-recording visualization, a research-only model observation, "
            "and clinician-entered documentation. It is intended for qualified clinician review.",
            styles["intro"],
        ),
        _identity_card(
            patient_name=patient_name, empi_id=empi_id, mrn=mrn, encounter_number=encounter_number,
            accession_number=accession_number, clinician=clinician, styles=styles, content_width=content_width,
        ),
        Spacer(1, 4 * mm),
        _section_heading("ECG waveform - source-recording visualization", styles, content_width),
        Spacer(1, 2 * mm),
        _waveform_card(waveform_png, styles, content_width),
        Spacer(1, 4 * mm),
        _section_heading("AI research/CDS observation", styles, content_width),
        Spacer(1, 2 * mm),
        _ai_observation(
            prediction=ai_prediction, score=ai_score, model_version=model_version,
            styles=styles, content_width=content_width,
        ),
        Spacer(1, 4 * mm),
        KeepTogether([
            _section_heading("Clinical review", styles, content_width),
            Spacer(1, 2 * mm),
            _clinical_panel(
                assessment=clinician_assessment, diagnosis=diagnosis,
                styles=styles, content_width=content_width,
            ),
        ]),
        Spacer(1, 4 * mm),
        _section_heading("Clinician-entered prescription record", styles, content_width),
        Spacer(1, 1.7 * mm),
        Paragraph(
            "The following record is entered by a clinician. It is not generated, selected, or recommended by the AI model.",
            styles["caption"],
        ),
        Spacer(1, 2 * mm),
        _prescription_table(prescription_items, styles, content_width),
        Spacer(1, 3 * mm),
        Table([[Paragraph(
            "Research-use notice: RAMNV2 output is an uncalibrated research score. It does not replace independent "
            "clinical assessment, diagnosis, treatment planning, or emergency evaluation.",
            styles["caption"],
        )]], colWidths=[content_width], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
            ("BOX", (0, 0), (-1, -1), 0.45, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3.5 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ])),
    ]
    doc.build(story)
    return output.getvalue()
