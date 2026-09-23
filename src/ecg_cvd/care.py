"""Source-linked education for research labels, never a treatment decision.

``reviewed_on`` records when the linked educational sources were checked. It is
not a claim of independent clinical review or validation of this application.
"""
from __future__ import annotations

import re


TITLE = "General care information — not a treatment recommendation"
DISCLAIMER = (
    "This research model output is not a diagnosis and cannot select treatment. "
    "A qualified clinician must review the ECG, symptoms and clinical context. "
    "Do not start, stop or change medicines based on this screen. "
    "This educational summary has not received independent clinical validation."
)
EMERGENCY = (
    "For an actual person with current chest pain, severe difficulty breathing, "
    "fainting or collapse, seek emergency medical help; do not wait for this app. "
    "If someone is unresponsive and not breathing normally or is only gasping, "
    "call local emergency services, start CPR and use an AED if available, "
    "following dispatcher/device instructions. A label on a stored dataset "
    "record does not establish a current emergency."
)

# Authoritative patient-education sources; no model score changes these texts.
_SOURCES = {
    "diagnosis": ("NHLBI: Arrhythmia diagnosis", "https://www.nhlbi.nih.gov/health/arrhythmias/diagnosis"),
    "treatment": ("NHLBI: Arrhythmia treatment", "https://www.nhlbi.nih.gov/health/arrhythmias/treatment"),
    "living": ("NHLBI: Living with arrhythmias", "https://www.nhlbi.nih.gov/health/arrhythmias/living-with"),
    "symptoms": ("NHLBI: Arrhythmia symptoms", "https://www.nhlbi.nih.gov/health/arrhythmias/symptoms"),
    "arrest": ("AHA: Recognizing cardiac arrest", "https://www.heart.org/en/health-topics/cardiac-arrest/about-cardiac-arrest"),
    "premature": ("AHA: Premature contractions (PACs and PVCs)", "https://www.heart.org/en/health-topics/arrhythmia/about-arrhythmia/premature-contractions-pacs-and-pvcs"),
    "afib": ("NHLBI: Atrial fibrillation treatment", "https://www.nhlbi.nih.gov/health/atrial-fibrillation/treatment"),
    "other": ("AHA: Atrial flutter and Wolff-Parkinson-White syndrome", "https://www.heart.org/en/health-topics/arrhythmia/about-arrhythmia/other-heart-rhythm-disorders"),
    "tachycardia": ("AHA: Supraventricular and ventricular tachycardia", "https://www.heart.org/en/health-topics/arrhythmia/about-arrhythmia/tachycardia--fast-heart-rate"),
    "conduction": ("AHA: Heart conduction disorders", "https://www.heart.org/en/health-topics/arrhythmia/about-arrhythmia/conduction-disorders"),
    "pacemaker": ("NHLBI: Living with a pacemaker", "https://www.nhlbi.nih.gov/health/pacemakers/living-with"),
}

# Each entry is (summary, general discussion points, category-specific sources).
_EDUCATION = {
    "nsr": (
        "The model assigned a normal sinus rhythm pattern to this short recording. "
        "This does not exclude heart disease, an intermittent arrhythmia or another illness.",
        (
            "Discuss persistent or new symptoms with a clinician even when this label is shown.",
            "A clinician may review a clinical ECG, medical history and longer rhythm monitoring if needed.",
            "Do not use this score as clearance to stop treatment or skip an assessment.",
        ),
        ("diagnosis",),
    ),
    "premature": (
        "Premature-beat labels describe early beats; bigeminy and trigeminy describe "
        "repeating patterns. Their significance depends on symptoms, frequency and "
        "underlying heart health, not this model score.",
        (
            "A clinician may check a clinical ECG, ambulatory monitor or heart imaging when appropriate.",
            "Discuss symptoms and possible triggers such as poor sleep, caffeine, alcohol or stimulant use.",
            "Only a clinician can decide whether observation or a specific treatment is appropriate.",
        ),
        ("premature",),
    ),
    "afib": (
        "If atrial fibrillation is clinically confirmed, care may address heart rate, "
        "rhythm and prevention of blood clots. These decisions require individual "
        "stroke and bleeding risk assessment.",
        (
            "Arrange clinician review of a diagnostic ECG and the person's symptoms and medical history.",
            "Discuss whether monitoring, risk-factor management, medicines or procedures are appropriate.",
            "Do not start blood-thinning or heart-rhythm medicines from a dataset prediction.",
        ),
        ("afib",),
    ),
    "afl": (
        "Atrial flutter is a fast rhythm arising in the upper heart chambers. It can "
        "coexist with atrial fibrillation; this research label alone cannot establish either condition.",
        (
            "Clinical confirmation and symptom assessment are needed before choosing care.",
            "A clinician can discuss rhythm management and whether stroke-risk evaluation is needed.",
            "New palpitations or lightheadedness warrant prompt contact with a healthcare professional.",
        ),
        ("other", "treatment"),
    ),
    "svta": (
        "Supraventricular tachyarrhythmia is a broad research label, not a precise "
        "clinical diagnosis. Evaluation identifies the actual rhythm and its cause.",
        (
            "A clinician may use a clinical ECG or longer monitoring to capture episodes.",
            "If confirmed, care can range from monitoring to clinician-selected medicines or ablation.",
            "Do not try neck massage or other rhythm-changing maneuvers based on this app.",
        ),
        ("tachycardia",),
    ),
    "wpw": (
        "A WPW pattern needs clinical ECG confirmation and individualized rhythm-risk "
        "assessment. A pattern label does not by itself establish symptomatic WPW syndrome.",
        (
            "Discuss the finding with a cardiologist or heart-rhythm specialist.",
            "Depending on confirmed findings, a specialist may discuss monitoring or catheter ablation.",
            "Medication decisions require specialist review; do not self-treat a fast heartbeat.",
        ),
        ("other",),
    ),
    "vt": (
        "If ventricular tachycardia is confirmed in a person, urgent medical assessment "
        "is needed because its severity can range from tolerated episodes to a life-threatening rhythm. "
        "This stored-record prediction does not establish an active episode.",
        (
            "Assessment depends on a clinical ECG, symptoms, circulation and underlying causes.",
            "Acute treatment and longer-term options are decisions for an emergency or cardiology team.",
            "Do not attempt to treat a ventricular rhythm using this research output.",
        ),
        ("tachycardia", "treatment"),
    ),
    "vfl": (
        "VFL is this dataset's ventricular flutter label, not the ventricular fibrillation "
        "label. If confirmed in a person, this ventricular rhythm needs urgent medical assessment. "
        "The app cannot assess circulation or establish an active episode.",
        (
            "An emergency or cardiology team must confirm and assess the current rhythm.",
            "Use actual symptoms and responsiveness, not a stored recording's score, to seek emergency help.",
            "No self-treatment or drug selection is supported by this model.",
        ),
        ("treatment", "arrest"),
    ),
    "ivr": (
        "An idioventricular rhythm label requires expert ECG interpretation. The model "
        "cannot determine its cause, clinical significance or whether treatment is needed.",
        (
            "A clinician should review the full ECG, symptoms, medicines and clinical circumstances.",
            "Further monitoring or tests depend on that assessment rather than this short dataset fragment.",
        ),
        ("diagnosis", "treatment"),
    ),
    "fusion": (
        "A fusion-beat label is a waveform category, not a standalone disease or a "
        "treatment indication. Interpretation depends on the surrounding rhythm and clinical context.",
        (
            "Ask a clinician to interpret the full ECG rather than an isolated model-labeled beat.",
            "Any further tests or treatment depend on the clinically confirmed underlying rhythm.",
        ),
        ("diagnosis",),
    ),
    "bundle": (
        "A bundle-branch-block label concerns electrical conduction. A clinician must "
        "confirm it and assess associated symptoms and heart conditions; management varies.",
        (
            "Discuss clinical ECG confirmation, relevant prior ECGs and follow-up with a clinician.",
            "Some confirmed cases are monitored; other cases need care for an underlying condition.",
            "This model cannot decide whether a device or other treatment is needed.",
        ),
        ("conduction",),
    ),
    "sdhb": (
        "Second-degree heart block has different subtypes with different implications. "
        "This label cannot distinguish the subtype or determine whether pacing is needed.",
        (
            "A clinically suspected heart block needs prompt medical assessment, particularly with symptoms.",
            "A clinician may review a clinical ECG, medicines and reversible causes; some types require pacing.",
            "Do not stop prescribed medicines without professional advice.",
        ),
        ("conduction", "living"),
    ),
    "pr": (
        "A pacemaker-rhythm pattern does not show whether a pacemaker is working "
        "properly and is not evidence of a new disease by itself.",
        (
            "For a person with a pacemaker, follow the device clinic's scheduled checks and care plan.",
            "Report new dizziness, fainting, breathlessness or suspected device problems promptly.",
            "Device programming or treatment changes require the responsible clinical team.",
        ),
        ("pacemaker",),
    ),
    "unknown": (
        "This label has no supported condition-specific care summary. A research "
        "prediction cannot determine a disease or choose treatment.",
        (
            "Have a qualified clinician review the ECG and clinical context if this concerns an actual person.",
            "Do not interpret an unknown or unsupported label as a reassuring result.",
        ),
        ("diagnosis",),
    ),
}

_CATEGORY = {
    "nsr": "nsr", "apb": "premature", "afl": "afl", "afib": "afib",
    "svta": "svta", "wpw": "wpw", "pvc": "premature", "bigeminy": "premature",
    "trigeminy": "premature", "vt": "vt", "ivr": "ivr", "vfl": "vfl",
    "fusion": "fusion", "lbbbb": "bundle", "rbbbb": "bundle", "sdhb": "sdhb", "pr": "pr",
}


def care_information(label: str) -> dict:
    """Return fresh JSON-safe education for a code or a numbered dataset label."""
    value = re.sub(r"^\d+\s+", "", str(label or "").strip()).casefold()
    summary, steps, sources = _EDUCATION[_CATEGORY.get(value, "unknown")]
    source_keys = dict.fromkeys((*sources, "symptoms", "arrest"))
    return {
        "title": TITLE,
        "disclaimer": DISCLAIMER,
        "summary": summary,
        "next_steps": list(steps),
        "emergency": EMERGENCY,
        "sources": [{"title": _SOURCES[key][0], "url": _SOURCES[key][1]} for key in source_keys],
        "reviewed_on": "2026-09-19",
    }
