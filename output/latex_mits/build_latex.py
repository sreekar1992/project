from __future__ import annotations

import re
import shutil
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DOCX = ROOT / "output/ieee_paper/MITS_26MPE05015_IEEE_Manuscript.docx"
SCREENSHOT = Path('/var/folders/yh/ry556nsj2_x_gh560cxy3_qm0000gn/T/codex-clipboard-e15d7d18-f9b7-4a58-88e9-d89981e955d6.png')

FIGURES = [
    ('fig01_workflow.png', 'Public-signal and protected-original paths. Recovery reads the encrypted original payload, not an inverse perturbation. The batch key envelope and authenticated manifest are also required.', 'fig:workflow'),
    ('fig02_confusion_matrix.png', 'Confusion matrix for 200 validation fragments; class IDs follow table~\\ref{tab:support}. Counts sum to 200, with 174 correct. Recording overlap and exact duplicates limit interpretation.', 'fig:confusion'),
    ('fig03_fgsm_curve.png', 'Baseline FGSM sensitivity on the reconstructed validation split. Epsilon is measured in normalized input units. This single-attack curve is not a robustness guarantee.', 'fig:fgsm'),
    ('fig04_camouflage_recovery.png', 'Illustrative training fragment 100m (2).mat: original NSR score 99.75\\%, public-decoy APB score 94.64\\%, and exact restored original. Waveforms use raw dataset units; the difference panel magnifies the alteration.', 'fig:recovery'),
]
TABLES = {
    14: ('Distinct roles of the implemented components', 'tab:roles'),
    21: ('Local dataset support and validation recall', 'tab:support'),
    28: ('Classifier stages for one input fragment', 'tab:stages'),
    56: ('Leakage-affected internal validation results', 'tab:metrics'),
    66: ('Normalized-input FGSM experiment', 'tab:fgsm'),
    71: ('Whole-dataset camouflage and recovery audit', 'tab:audit'),
}
SECTIONS = {
    5: ('section', 'Introduction'), 10: ('section', 'Related Work'),
    17: ('section', 'Dataset and Classification Baseline'),
    18: ('subsection', 'Data representation and class support'),
    24: ('subsection', 'Preprocessing and network architecture'),
    30: ('subsection', 'Training provenance and split audit'),
    33: ('section', 'Camouflage and Authenticated Recovery'),
    34: ('subsection', 'System workflow and scope'),
    39: ('subsection', 'Iterative raw waveform camouflage'),
    46: ('subsection', 'File container and key handling'),
    50: ('subsection', 'Separate FGSM sensitivity experiment'),
    52: ('section', 'Experimental Results'),
    53: ('subsection', 'Internal classification evaluation'),
    59: ('subsection', 'FGSM sensitivity of the baseline'),
    68: ('subsection', 'Whole-dataset camouflage and exact recovery'),
    75: ('subsection', 'Software verification and reproducibility'),
    80: ('section', 'Limitations and Responsible Use'),
    85: ('section', 'Conclusion'),
}
SKIP = {0, 1, 2, 3, 4, 13, 15, 20, 22, 27, 29, 36, 37, 41, 43,
        55, 57, 61, 62, 63, 64, 65, 67, 70, 72, 77, 78, 87, 95}
REFS = ['plawiak2017', 'moody2001', 'rizwan2025', 'goodfellow2015',
        'madry2018', 'dworkin2007', 'selvaraju2019']


def tex(s: str) -> str:
    s = s.replace('10⁻⁶', r'$10^{-6}$')
    s = s.replace('→', r'$\\rightarrow$')
    s = s.replace('“', '``').replace('”', "''")
    s = s.replace('’', "'").replace('‘', "'")
    s = s.replace('—', '---').replace('–', '--')
    s = s.replace('−', '-').replace('×', r'$\\times$')
    for a, b in [('\u00a0', ' '), ('%', r'\\%'), ('&', r'\\&'), ('#', r'\\#'), ('_', r'\\_')]:
        s = s.replace(a, b)
    for n, key in enumerate(REFS, 1):
        s = s.replace(f'[{n}]', rf'\\cite{{{key}}}')
    for roman, label in [('I', 'roles'), ('II', 'support'), ('III', 'stages'),
                         ('IV', 'metrics'), ('V', 'fgsm'), ('VI', 'audit')]:
        s = re.sub(rf'\bTable {roman}\b', rf'table~\\ref{{tab:{label}}}', s)
    for n, label in [(1, 'workflow'), (2, 'confusion'), (3, 'fgsm'), (4, 'recovery')]:
        s = re.sub(rf'\bFig\. {n}\b', rf'figure~\\ref{{fig:{label}}}', s)
    return s


def table(t: Table, caption: str, label: str) -> str:
    rows = [[tex(c.text.strip()) for c in row.cells] for row in t.rows]
    n = len(rows[0])
    spec = {
        'tab:roles': r'p{0.22\linewidth}p{0.30\linewidth}p{0.30\linewidth}',
        'tab:audit': r'p{0.61\linewidth}l',
    }.get(label, {2: 'll', 3: 'lcc', 4: 'lccc'}[n])
    out = [r'\\begin{table}', r'\\centering',
           rf'\\caption{{{caption}}}\\label{{{label}}}',
           r'\\small', rf'\\begin{{tabular}}{{{spec}}}', r'\\hline']
    for i, row in enumerate(rows):
        out.append(' & '.join(row) + r' \\')
        if i == 0:
            out.append(r'\\hline')
    out += [r'\\hline', r'\\end{tabular}', r'\\end{table}']
    return '\n'.join(out)


def figure(n: int) -> str:
    name, caption, label = FIGURES[n-1]
    return '\n'.join([r'\\begin{figure}', r'\\centering',
                      rf'\\includegraphics[width=0.88\\linewidth]{{figures/{name}}}',
                      rf'\\caption{{{caption}}}\\label{{{label}}}', r'\\end{figure}'])


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    figures = HERE / 'figures'
    figures.mkdir(exist_ok=True)
    for name, _, _ in FIGURES:
        shutil.copy2(ROOT / 'output/ieee_paper/figures' / name, figures / name)
    image = Image.open(SCREENSHOT).convert('RGB')
    image.crop((70, 5, 304, 255)).save(figures / 'mits_logo.png', optimize=True)

    document = Document(DOCX)
    parts = [r'''% Converted from MITS_26MPE05015_IEEE_Manuscript.docx.
% Uses the JPP LaTeX structure supplied by the user; requires jpp.cls.
% The logo is cropped from the university image supplied by the user.
\\documentclass{jpp}
\\usepackage[utf8]{inputenc}
\\usepackage[T1]{fontenc}
\\usepackage{graphicx}
\\usepackage{amsmath}
\\shorttitle{Adversarial ECG camouflage and recovery}
\\shortauthor{M. S. varma and S. dash}
\\title{\\includegraphics[height=1.45cm]{figures/mits_logo.png}\\\\[0.8ex]
Adversarial ECG Camouflage with Authenticated Recovery and Residual Attention Classification}
\\author{Muddisetty Sreekar varma\\aff{1} \\and Sachikanta dash\\aff{1}}
\\affiliation{\\aff{1}MITS -- Deemed to be University \\
(Madanapalle Institute of Technology \\& Science), Madanapalle, Andhra Pradesh, India}
\\begin{document}
\\maketitle
\\begin{abstract}
''', tex(Paragraph(document.element.body[3], document).text.removeprefix('Abstract—')),
            r'''\\end{abstract}
\\noindent\\textbf{Keywords:} ''' + tex(Paragraph(document.element.body[4], document).text.removeprefix('Index Terms—')) + '\n']

    for i, element in enumerate(document.element.body):
        if i in SKIP or i < 5 or i >= 87:
            continue
        if i in SECTIONS:
            level, heading = SECTIONS[i]
            parts.append(rf'\\{level}{{{heading}}}')
            continue
        if i in TABLES:
            caption, label = TABLES[i]
            parts.append(table(Table(element, document), caption, label))
            continue
        if i == 35:
            parts.append(tex(Paragraph(element, document).text))
            parts.append(figure(1))
            continue
        if i == 40:
            parts.append('Let $x$ denote the 3,600-sample raw waveform, $P$ the fixed preprocessing operation, and $f$ the classifier. Let $s$ be the scaled median absolute deviation of the band-passed original signal. The raw perturbation budget $b$ and nominal update size $\\alpha$ are defined by (\\ref{eq:budget}), where $\\epsilon_c$ is the camouflage setting and $T$ is the maximum iteration count. Defaults are $\\epsilon_c=0.5$ and $T=20$.')
            parts.append(r'\\begin{equation} b=\\epsilon_c s, \\qquad \\alpha=2b/T. \\label{eq:budget} \\end{equation}')
            continue
        if i == 42:
            parts.append("At each iteration the routine computes cross-entropy relative to the original predicted class, differentiates in the 900-sample normalized model-input domain, and linearly interpolates that gradient to 3,600 points. Denote the interpolated gradient by $U(g_t)$. The raw candidate update is (\\ref{eq:update}), where the projection clips each sample to the interval from $x-b$ to $x+b$.")
            parts.append(r'\\begin{equation} x_{t+1}=\\Pi_b\\!\\left(x_t+\\alpha\\,\\operatorname{sign}(U(g_t))\\right). \\label{eq:update} \\end{equation}')
            continue
        if i in (51, 60, 69):
            s = Paragraph(element, document).text
            if i == 51:
                s = s.replace('It adds  times', r'It adds $\\epsilon_a$ times').replace('  = 0.05', r' $\\epsilon_a=0.05$').replace('  = 0.5', r' $\\epsilon_c=0.5$')
            elif i == 60:
                s = s.replace('at  = 0.05', r'at $\\epsilon_a=0.05$')
                parts.append(figure(2))
                parts.append(figure(3))
            else:
                s = s.replace('uses  = 0.5', r'uses $\\epsilon_c=0.5$')
            parts.append(tex(s).replace(r'\\\\epsilon', r'\\epsilon'))
            continue
        if i == 76:
            parts.append(tex(Paragraph(element, document).text))
            parts.append(figure(4))
            continue
        if element.tag.endswith('}p'):
            s = Paragraph(element, document).text.strip()
            if s:
                parts.append(tex(s))

    parts.append(r'''\\begin{thebibliography}{7}
\\bibitem{plawiak2017} P. Plawiak, ``ECG signals (1000 fragments),'' Mendeley Data, V3, 2017, doi: 10.17632/7dybx7wyfn.3.
\\bibitem{moody2001} G. B. Moody and R. G. Mark, ``The impact of the MIT-BIH Arrhythmia Database,'' \\textit{IEEE Eng. Med. Biol. Mag.}, vol. 20, no. 3, pp. 45--50, 2001. Database: PhysioNet, ver. 1.0.0, 2005, doi: 10.13026/C2F305.
\\bibitem{rizwan2025} K. Rizwan, M. A. Habib, S. Raza, and M. Ahmad, ``RNAF: ResilienceNet Adversarial Framework Using Deep Reinforcement Learning for Adversarial Attacks on Digital Images,'' \\textit{IEEE Access}, vol. 13, pp. 201592--201610, 2025, doi: 10.1109/ACCESS.2025.3636942.
\\bibitem{goodfellow2015} I. J. Goodfellow, J. Shlens, and C. Szegedy, ``Explaining and Harnessing Adversarial Examples,'' in \\textit{Proc. ICLR}, 2015, arXiv:1412.6572.
\\bibitem{madry2018} A. Madry, A. Makelov, L. Schmidt, D. Tsipras, and A. Vladu, ``Towards Deep Learning Models Resistant to Adversarial Attacks,'' in \\textit{Proc. ICLR}, 2018, arXiv:1706.06083.
\\bibitem{dworkin2007} M. Dworkin, ``Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC,'' NIST SP 800-38D, Nov. 2007, doi: 10.6028/NIST.SP.800-38D.
\\bibitem{selvaraju2019} R. R. Selvaraju, M. Cogswell, A. Das, R. Vedantam, D. Parikh, and D. Batra, ``Grad-CAM: Visual Explanations from Deep Networks via Gradient-Based Localization,'' \\textit{Int. J. Comput. Vis.}, 2019, doi: 10.1007/s11263-019-01228-7.
\\end{thebibliography}
\\end{document}''')
    source = '\n\n'.join(parts).replace('\\\\', '\\')
    source = source.replace(' \\\n', ' \\\\\n')
    source = source.replace(r'\epsilon\_a', r'\epsilon_a')
    source = source.replace(r'\epsilon\_c', r'\epsilon_c')
    source = source.replace('table~\\ref', 'Table~\\ref')
    source = source.replace('figure~\\ref', 'Figure~\\ref')
    (HERE / 'mits_ecg_jpp.tex').write_text(source + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
