"""Assemble generated figures and exact results into a manuscript attachment PDF.

Use Python with reportlab installed, after running make_paper_results.py.
"""
from pathlib import Path
import json
from xml.sax.saxutils import escape
import zipfile
import reportlab

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Embed ReportLab's bundled font family so the PDF does not depend on viewer fonts.
font_dir=Path(reportlab.__file__).parent/"fonts"
for name,filename in [("ReportSans","Vera.ttf"),("ReportSans-Bold","VeraBd.ttf"),
                      ("ReportSans-Italic","VeraIt.ttf"),("ReportSans-BoldItalic","VeraBI.ttf")]:
    pdfmetrics.registerFont(TTFont(name,str(font_dir/filename)))
pdfmetrics.registerFontFamily("ReportSans",normal="ReportSans",bold="ReportSans-Bold",
                              italic="ReportSans-Italic",boldItalic="ReportSans-BoldItalic")

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"output/paper_results"
PDF=ROOT/"output/pdf/ecg_paper_results.pdf"
DATA=json.loads((OUT/"results.json").read_text())
MET=DATA["metrics"]
PDF.parent.mkdir(parents=True,exist_ok=True)
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name="ReportTitle",fontName="ReportSans-Bold",fontSize=23,leading=28,textColor=colors.HexColor("#213B56"),spaceAfter=14))
styles.add(ParagraphStyle(name="Kicker",fontName="ReportSans-Bold",fontSize=9,leading=12,textColor=colors.HexColor("#137E7A"),spaceAfter=8))
styles.add(ParagraphStyle(name="Copy",fontName="ReportSans",fontSize=10,leading=14,spaceAfter=10))
styles.add(ParagraphStyle(name="SmallCopy",fontName="ReportSans",fontSize=8.5,leading=12,spaceAfter=9))
styles.add(ParagraphStyle(name="FigureCaption",fontName="ReportSans",fontSize=9,leading=13,spaceBefore=12,spaceAfter=10))
styles["Heading2"].textColor=colors.HexColor("#213B56")
styles["Heading2"].fontName="ReportSans-Bold"
story=[]


def p(text,style="Copy"):
    return Paragraph(text,styles[style])


def table(rows,widths):
    result=Table(rows,colWidths=widths,repeatRows=1,hAlign="LEFT")
    result.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#213B56")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"ReportSans-Bold"),
        ("FONTNAME",(0,1),(-1,-1),"ReportSans"),
        ("FONTSIZE",(0,0),(-1,-1),9),
        ("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),7),
        ("LINEBELOW",(0,0),(-1,0),.4,colors.HexColor("#213B56")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F0F5F5")]),
        ("ALIGN",(1,1),(-1,-1),"RIGHT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    return result


def header(kicker,title):
    story.extend([p(kicker,"Kicker"),p(title,"ReportTitle")])


header("ECG RESEARCH / RESULTS ATTACHMENT","Prediction figures<br/>and validation results")
story.append(p("Computed from <b>artifacts_final/model.pt</b> using the local MLII MATLAB dataset. This package provides seven figures, exact result tables, and traceable validation predictions for manuscript preparation."))
rows=[["Measure","Result"],["Correct / validation fragments",f"{MET['correct']} / {MET['n_validation']}"],
      ["Accuracy",f"{MET['accuracy']:.2%}"],["Weighted precision",f"{MET['weighted_precision']:.2%}"],
      ["Weighted recall",f"{MET['weighted_recall']:.2%}"],["Weighted F1",f"{MET['weighted_f1']:.2%}"],
      ["Macro F1",f"{MET['macro_f1']:.2%}"],["Balanced accuracy",f"{MET['balanced_accuracy']:.2%}"],
      ["Macro one-versus-rest ROC AUC",f"{MET['macro_ovr_roc_auc']:.4f}"],
      ["Macro average precision",f"{MET['macro_average_precision']:.4f}"],["Trainable parameters",f"{MET['parameters']:,}"]]
story.extend([table(rows,[320,179]),Spacer(1,18),p("Interpretation to retain in the paper","Heading2"),
              p("These are <b>internal, fragment-level validation results with data leakage</b>. All 200 validation fragments have a filename-derived recording ID also present in training (44 shared recording IDs); three validation waveforms also exactly match training waveforms. The IDs are recording proxies, not verified patient identities. These scores do not establish performance on unseen recordings or patients."),
              p("The evaluated system is the implemented 1-D residual-attention baseline. The full proposed neuro-cardiac pipeline, treatment recommendation, and full ResilienceNet framework are not evaluated here. Figures include both correct predictions and failure cases.","SmallCopy"),PageBreak()])

header("TABLE 1 / CLASSIFICATION","Performance by class")
story.append(p("Class scores are percentages. Support n is the number of validation fragments. Specificity and continuous-score ranking metrics are supplied in the accompanying CSV table."))
rows=[["Class","n","Precision","Recall","F1"]]
for row in DATA["classes"]:
    rows.append([row["class"],str(row["validation_n"]),f"{row['precision']*100:.2f}",f"{row['recall']*100:.2f}",f"{row['f1']*100:.2f}"])
story.extend([table(rows,[175,54,90,90,90]),Spacer(1,16),
              p("Fusion and SVTA each have zero recall. Rare categories often have only two to four validation fragments, so seemingly perfect class scores are based on very small counts. The difference between weighted F1 (86.22%) and macro F1 (76.09%) shows the influence of class imbalance."),
              p("ROC AUC measures ranking and may remain high despite argmax misclassification. For example, Trigeminy has perfect one-versus-rest AUC and average precision on this split, while only one of its three examples is correctly classified.","SmallCopy"),PageBreak()])

for number,figure in enumerate(DATA["figures"],1):
    header(f"FIGURE {number:02d} / MANUSCRIPT GRAPHICS",escape(figure["title"]))
    path=OUT/"figures"/(figure["file"]+".png")
    width,height=ImageReader(str(path)).getSize()
    factor=min(499/width,485/height)
    story.append(Image(str(path),width=width*factor,height=height*factor,hAlign="CENTER"))
    story.append(p(f"<b>Figure {number}.</b> "+escape(figure["caption"]),"FigureCaption"))
    story.append(p("Standalone files: "+escape(figure["file"])+".png (400 dpi) and .svg (editable vector).","SmallCopy"))
    story.append(PageBreak())

header("METHODS / REPRODUCIBILITY","Evaluation details")
paragraphs=[
    ("Data and preprocessing", "The source provides 1,000 labelled 10-second MLII ECG fragments sampled at 360 Hz. This implementation applies fourth-order 0.5-45 Hz Butterworth filtering using zero-phase SOS filtering, median subtraction and scaled median absolute deviation normalization. Every fourth sample is retained to match the existing model's 90 Hz input. This subsampling design is retained for checkpoint compatibility; changing it requires retraining."),
    ("Split and checkpoint", "A stratified 80/20 fragment split is reconstructed with scikit-learn train_test_split and random_state=42 in the same sorted file order used by the original loader. The legacy checkpoint does not store split metadata, so the seed is inferred from the original default and confirmed by exact reproduction of saved accuracy and weighted metrics. No retraining was performed for these results."),
    ("Predictions and explanation", "Classification uses the maximum softmax score. Precision, recall and F1 are computed per class and summarized with macro and support-weighted averages. Undefined precision is assigned zero. One-versus-rest ROC AUC and average precision use continuous scores. Grad-CAM weights the final residual-block activations by the temporal average of the predicted-class gradient, applies ReLU, interpolates to the model-input grid, and scales each map to 0-1. These maps have not been clinically validated."),
    ("Adversarial test", "FGSM uses epsilon times the sign of the cross-entropy input gradient. The normalized inputs have no fixed amplitude range, so no extra clipping is applied. The entire sweep uses the same baseline checkpoint and validation set. At epsilon=0.05, accuracy is 59.5%, versus 87% for clean inputs. The earlier 47% estimate used extra clipping and is superseded. This test does not evaluate encryption security or certify robustness."),
]
for title,text in paragraphs:
    story.extend([p(title,"Heading2"),p(text,"SmallCopy")])
story.append(p("Files and reproducibility","Heading2"))
story.append(p("results.json records software versions and checkpoint SHA-256; split_manifest.csv records file hashes, indices and split membership. validation_predictions.csv includes every reference label, prediction and class score. cross_split_duplicates.csv identifies the three exactly duplicated waveform pairs. example_selection.csv specifies the figure selection rules. Learning curves are omitted because no per-epoch validation history was saved.","SmallCopy"))
story.append(p("References","Heading2"))
for reference in [
    'Plawiak, P. (2017). ECG signals (1000 fragments), Mendeley Data V3, CC BY 4.0. <link href="https://doi.org/10.17632/7dybx7wyfn.3">doi:10.17632/7dybx7wyfn.3</link>.',
    'Selvaraju et al. Grad-CAM: Visual Explanations from Deep Networks via Gradient-based Localization. <link href="https://arxiv.org/abs/1610.02391">arXiv:1610.02391</link>.',
    'Goodfellow, Shlens and Szegedy. Explaining and Harnessing Adversarial Examples. <link href="https://arxiv.org/abs/1412.6572">arXiv:1412.6572</link>.',
]: story.append(p(reference,"SmallCopy"))


def page_footer(canvas,doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D4DEDF")); canvas.line(48,43,A4[0]-48,43)
    canvas.setFont("ReportSans",8); canvas.setFillColor(colors.HexColor("#657883"))
    canvas.drawString(48,30,"ECG classification | Internal fragment-level validation")
    canvas.drawRightString(A4[0]-48,30,f"{doc.page}")
    canvas.restoreState()


doc=SimpleDocTemplate(str(PDF),pagesize=A4,rightMargin=48,leftMargin=48,topMargin=46,bottomMargin=56,
                      title="ECG prediction figures and validation results",author="ECG Workbench")
doc.build(story,onFirstPage=page_footer,onLaterPages=page_footer)
archive=ROOT/"output/ecg_paper_results.zip"
with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted(OUT.rglob("*")):
        if path.is_file(): bundle.write(path,Path("ecg_paper_results")/path.relative_to(OUT))
    bundle.write(PDF,"ecg_paper_results/ecg_paper_results.pdf")
    for script in ["make_paper_results.py","build_paper_pdf.py"]:
        bundle.write(ROOT/"scripts"/script,Path("ecg_paper_results/reproduce")/script)
print(f"Created {PDF}")
print(f"Created {archive}")
