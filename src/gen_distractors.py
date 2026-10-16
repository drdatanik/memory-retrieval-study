"""Generate fictional distractor patients (X0001, X0002, ...) with the generator role (OpenRouter, via src.llm).

    python -m src.gen_distractors --n 600 --workers 8 [--start 1] [--dry-run]

Each patient is a deterministic skeleton (name, age, sex, primary doctor, conditions, memory
count) drawn from random.Random(SEED + index). One generator call per patient writes the
memories. Every result is validated (window, types, authors, hard rules) and retried up to
MAX_RETRIES times before it is logged and skipped. Runs are resumable: an ID whose file already
exists is skipped. --dry-run uses a fake client and writes to runs/dry_run/distractors, so it can
never leave placeholder files in world/distractors.

When OpenRouter's free-model daily request cap is hit, the batch stops: the patient it hit writes nothing and
is not counted as failed, patients not yet started make no API call, and the process exits 3. Rerun the same
command after 00:00 UTC; the patients already written are skipped.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src import config, llm

ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT / "world" / "core"
DISTRACTOR_DIR = ROOT / "world" / "distractors"
DRY_RUN_DIR = ROOT / "runs" / "dry_run" / "distractors"
ROSTER_PATH = ROOT / "world" / "ROSTER.md"

SEED = 20261012          # config.py has no seed; skeletons use random.Random(SEED + index)
MAX_RETRIES = 3          # retries after the first attempt, so at most 4 generator calls per patient
EXIT_DAILY_LIMIT = 3     # main's exit code when the free-model daily request cap stopped the batch
MEMORY_COUNT = (12, 18)  # skeleton draws from this range; the validator accepts the same band
AGE_RANGE = (18, 90)

MEMORY_TYPES = (
    "visit_note",
    "prescription",
    "allergy",
    "lab_result",
    "investigation",
    "referral",
    "referral_outcome",
    "patient_context",
    "appointment",
)
PATIENT_AUTHORS = ("patient", "lab", "front_desk")

MEMORIES_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "type": {"type": "string", "enum": list(MEMORY_TYPES)},
                    "author": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["date", "type", "author", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["memories"],
    "additionalProperties": False,
}

GEN_SYSTEM = """You write synthetic clinical records for one fictional patient of Sunrise Family Clinic, a family practice in Bengaluru, India. The records are test data for a study of how an assistant answers questions from a clinic's stored memory. Nobody real is described.

Window: every memory date is between 2025-04-01 and 2026-10-11 inclusive. The one exception is an appointment memory, which is dated the day it was booked or changed; its text may give an appointment date after 2026-10-11.

One memory is one atomic record: one visit, one prescription change, one lab report (which can hold several values drawn together), one allergy, one investigation, one referral or referral outcome, one piece of patient-reported context, or one appointment booking or change.

Types (use exactly these strings):
- visit_note: the treating doctor's note of a consultation, in that doctor's style
- prescription: prescription lines (generic name, dose, frequency, duration), one line per drug
- allergy: an allergy or intolerance, with the reaction
- lab_result: a lab report with values, units and reference ranges
- investigation: an ECG, echo, treadmill test, X-ray, eye exam or similar, with the finding
- referral: a referral letter to a specialist or hospital
- referral_outcome: the specialist's reply or the outcome of a referral
- patient_context: an intake note, or a quoted message from the patient through the clinic app
- appointment: a booking or change of an appointment

Authors (use exactly these strings):
- the treating doctor's ID, as given in the request, for visit notes, prescriptions, doctor-reported investigations, referrals and clinical decisions
- "patient" for patient-reported context and app messages
- "lab" for lab reports
- "front_desk" for appointments
Do not use any other doctor ID.

Writing rules:
- Each text is 30 to 150 words. Prescriptions, lab reports and appointments are shorter.
- Write each text as the real clinic record would read. Do not write the patient's name or ID; the record header carries them.
- Keep the course realistic and internally consistent: a medicine that was stopped is not taken later unless it is restarted; doses change after labs, side effects or intercurrent illness; lab series show plausible trends and are repeated at sensible intervals; a review date written in a visit note is followed by a matching appointment record.
- Use the conditions and tests given in the request, plus ordinary related ones.

Hard rules. A record that breaks any of these is discarded:
- Never mention Dr Anjali Mehta, Dr Rohan Iyer, D01 or D02, in any form.
- Never name or number any other patient of the clinic, and never write a patient ID that starts with P.
- Return only the JSON object requested."""

# --- Condition catalogue -----------------------------------------------------------------------
# Typical medicines and tests per outpatient condition. "sex" and "min_age" are optional limits.
CONDITIONS = [
    {"name": "Type 2 diabetes",
     "meds": ["metformin 500 mg BD", "glimepiride 1 mg OD before breakfast", "sitagliptin 100 mg OD", "empagliflozin 10 mg OD"],
     "tests": ["HbA1c (%)", "fasting plasma glucose (mg/dL)", "urine albumin-creatinine ratio", "dilated fundus exam"]},
    {"name": "Hypertension",
     "meds": ["amlodipine 5 mg OD", "telmisartan 40 mg OD", "losartan 50 mg OD", "hydrochlorothiazide 12.5 mg OD"],
     "tests": ["serum creatinine and eGFR", "serum potassium", "ECG"]},
    {"name": "Asthma",
     "meds": ["salbutamol metered-dose inhaler SOS", "budesonide-formoterol 200/6 inhaler BD", "montelukast 10 mg HS"],
     "tests": ["spirometry with reversibility", "Asthma Control Test score"]},
    {"name": "COPD",
     "meds": ["tiotropium inhaler OD", "salbutamol inhaler SOS", "budesonide-formoterol 200/6 inhaler BD"],
     "tests": ["spirometry (FEV1, FEV1/FVC)", "chest X-ray", "SpO2 at rest"], "min_age": 40},
    {"name": "Hypothyroidism",
     "meds": ["levothyroxine 50 mcg OD", "levothyroxine 75 mcg OD", "levothyroxine 100 mcg OD"],
     "tests": ["TSH (mIU/L)", "free T4"]},
    {"name": "Chronic kidney disease",
     "meds": ["telmisartan 40 mg OD", "dapagliflozin 10 mg OD", "furosemide 20 mg OD", "sodium bicarbonate 500 mg BD"],
     "tests": ["serum creatinine (mg/dL)", "eGFR (mL/min/1.73m2)", "urine albumin-creatinine ratio", "haemoglobin"],
     "min_age": 40},
    {"name": "Coronary artery disease",
     "meds": ["aspirin 75 mg OD", "atorvastatin 40 mg HS", "metoprolol succinate 25 mg OD", "isosorbide mononitrate 20 mg OD", "sublingual GTN SOS"],
     "tests": ["fasting lipid profile (LDL, mg/dL)", "ECG", "treadmill test", "echocardiogram"], "min_age": 45},
    {"name": "Atrial fibrillation",
     "meds": ["apixaban 5 mg BD", "bisoprolol 2.5 mg OD", "warfarin (INR target 2 to 3)"],
     "tests": ["ECG", "INR (if on warfarin)", "serum creatinine", "TSH"], "min_age": 50},
    {"name": "Heart failure with reduced ejection fraction",
     "meds": ["sacubitril/valsartan 24/26 mg BD", "bisoprolol 2.5 mg OD", "spironolactone 25 mg OD", "furosemide 40 mg OD", "dapagliflozin 10 mg OD"],
     "tests": ["echocardiogram (EF %, NYHA class)", "serum potassium", "serum creatinine", "NT-proBNP"], "min_age": 50},
    {"name": "Osteoarthritis of the knee",
     "meds": ["paracetamol 650 mg SOS", "topical diclofenac gel", "calcium with vitamin D3 OD"],
     "tests": ["weight-bearing X-ray of both knees", "serum vitamin D"], "min_age": 45},
    {"name": "Gout",
     "meds": ["allopurinol 100 mg OD", "febuxostat 40 mg OD", "colchicine 0.5 mg BD during flares"],
     "tests": ["serum uric acid (mg/dL)", "serum creatinine"], "min_age": 35},
    {"name": "Migraine",
     "meds": ["propranolol 40 mg OD for prevention", "sumatriptan 50 mg SOS", "topiramate 25 mg HS", "naproxen 500 mg SOS"],
     "tests": ["neurological examination", "CT head (normal)"]},
    {"name": "Depression",
     "meds": ["sertraline 50 mg OD", "escitalopram 10 mg OD", "mirtazapine 15 mg HS"],
     "tests": ["PHQ-9 score", "TSH", "vitamin B12"]},
    {"name": "Generalised anxiety disorder",
     "meds": ["escitalopram 5 mg OD", "buspirone 5 mg BD"],
     "tests": ["GAD-7 score", "TSH", "haemoglobin"]},
    {"name": "Gastro-oesophageal reflux",
     "meds": ["pantoprazole 40 mg OD before breakfast", "antacid gel SOS", "domperidone 10 mg BD before meals"],
     "tests": ["H. pylori stool antigen", "upper GI endoscopy (if alarm symptoms)"]},
    {"name": "Irritable bowel syndrome",
     "meds": ["mebeverine 135 mg BD", "ispaghula husk 5 g OD", "loperamide 2 mg SOS"],
     "tests": ["complete blood count", "stool routine", "tTG-IgA (coeliac screen)"]},
    {"name": "Iron-deficiency anaemia",
     "meds": ["ferrous sulfate 200 mg OD", "ferrous sulfate 200 mg on alternate days"],
     "tests": ["haemoglobin (g/dL)", "serum ferritin (ng/mL)", "MCV (fL)"]},
    {"name": "Polycystic ovary syndrome",
     "meds": ["metformin 500 mg BD", "combined oral contraceptive pill OD"],
     "tests": ["serum testosterone", "LH:FSH ratio", "HbA1c", "pelvic ultrasound"], "sex": "F", "min_age": 18},
    {"name": "Psoriasis",
     "meds": ["clobetasol 0.05% cream BD topically", "calcipotriol ointment OD topically", "methotrexate 7.5 mg once weekly with folic acid 5 mg OD"],
     "tests": ["liver function test", "complete blood count", "serum creatinine"]},
    {"name": "Benign prostatic hyperplasia",
     "meds": ["tamsulosin 0.4 mg HS", "finasteride 5 mg OD"],
     "tests": ["PSA (ng/mL)", "urine routine", "ultrasound KUB with post-void residual"], "sex": "M", "min_age": 45},
    {"name": "Osteoporosis",
     "meds": ["alendronate 70 mg once weekly", "calcium 500 mg BD with vitamin D3"],
     "tests": ["DEXA scan (T-score)", "serum calcium", "serum 25-OH vitamin D"], "min_age": 50},
    {"name": "Rheumatoid arthritis",
     "meds": ["methotrexate 10 mg once weekly", "folic acid 5 mg once weekly", "hydroxychloroquine 200 mg OD", "prednisolone 5 mg OD"],
     "tests": ["ESR", "CRP", "rheumatoid factor", "anti-CCP", "liver function test"]},
    {"name": "Epilepsy",
     "meds": ["levetiracetam 500 mg BD", "lamotrigine 50 mg BD"],
     "tests": ["EEG", "serum sodium", "serum creatinine"]},
    {"name": "Allergic rhinitis",
     "meds": ["cetirizine 10 mg HS", "fluticasone nasal spray, one spray each nostril OD", "montelukast 10 mg HS"],
     "tests": ["skin prick test", "total serum IgE"]},
    {"name": "Obesity",
     "meds": ["orlistat 120 mg with each main meal"],
     "tests": ["fasting lipid profile", "fasting glucose", "HbA1c", "liver function test", "waist circumference"]},
    {"name": "Non-alcoholic fatty liver disease",
     "meds": ["silymarin 140 mg TDS", "rosuvastatin 5 mg OD"],
     "tests": ["liver function test (ALT, AST)", "abdominal ultrasound", "platelet count", "fasting lipid profile"]},
    {"name": "Vitamin D deficiency",
     "meds": ["cholecalciferol 60000 IU once weekly for 8 weeks, then 1000 IU OD"],
     "tests": ["serum 25-hydroxyvitamin D (ng/mL)", "serum calcium", "serum phosphate"]},
    {"name": "Vitamin B12 deficiency",
     "meds": ["methylcobalamin 1500 mcg OD"],
     "tests": ["serum vitamin B12 (pg/mL)", "MCV (fL)", "haemoglobin"]},
    {"name": "Insomnia",
     "meds": ["melatonin 3 mg HS", "trazodone 50 mg HS"],
     "tests": ["Insomnia Severity Index score", "TSH", "haemoglobin"]},
    {"name": "Sciatica",
     "meds": ["pregabalin 75 mg BD", "diclofenac 50 mg BD after food", "methylcobalamin 1500 mcg OD"],
     "tests": ["straight leg raise test", "MRI lumbar spine", "X-ray lumbar spine"]},
    {"name": "Dyslipidaemia",
     "meds": ["atorvastatin 10 mg HS", "rosuvastatin 10 mg OD", "fenofibrate 145 mg OD"],
     "tests": ["fasting lipid profile (LDL, HDL, triglycerides)", "liver function test", "creatine kinase"]},
    {"name": "Chronic urticaria",
     "meds": ["levocetirizine 5 mg OD", "cetirizine 10 mg HS"],
     "tests": ["complete blood count with eosinophils", "thyroid function test"]},
    {"name": "Benign positional vertigo",
     "meds": ["betahistine 16 mg BD", "meclizine 25 mg SOS"],
     "tests": ["Dix-Hallpike test", "audiometry"]},
    {"name": "Glaucoma",
     "meds": ["timolol 0.5% eye drops BD", "latanoprost 0.005% eye drops HS"],
     "tests": ["intraocular pressure (mmHg)", "visual field test", "optic disc assessment"], "min_age": 40},
]

OCCUPATIONS = [
    "retired schoolteacher", "bank clerk", "shop owner", "auto-rickshaw driver", "software engineer",
    "homemaker", "retired railway employee", "tailor", "pharmacy assistant", "government office clerk",
    "nurse at a private hospital", "coffee estate supervisor", "small-scale contractor", "school bus driver",
    "college lecturer", "vegetable vendor", "cab driver", "accountant", "electrician", "hotel manager",
    "postal worker", "retired textile mill worker", "IT support technician", "florist", "lab technician",
    "driver for a delivery company", "insurance agent", "jewellery shop assistant",
]

LOCALITIES = [
    "Yelahanka", "Basavanagudi", "Indiranagar", "Koramangala", "Rajajinagar", "Hebbal", "Whitefield",
    "HSR Layout", "Banashankari", "JP Nagar", "Vijayanagar", "BTM Layout", "Shivajinagar", "Frazer Town",
    "Peenya", "Bommanahalli", "Sadashivanagar", "Kengeri", "Malleshwaram", "Hennur", "Marathahalli",
]

SEX_WORD = {"F": "female", "M": "male"}

# --- Name lists: region-coupled first names and surnames -----------------------------------------
FIRST_NAMES = {
    "Kannada": {
        "M": ["Basavaraj", "Shivanna", "Manjunath", "Mahesh", "Prakash", "Nagaraj", "Lokesh", "Chandrashekar"],
        "F": ["Lalitha", "Savitha", "Shobha", "Pushpa", "Sumathi", "Vijayalakshmi", "Rukmini", "Padma"],
    },
    "Tamil": {
        "M": ["Murugan", "Senthil", "Arumugam", "Selvam", "Ravichandran", "Balasubramanian", "Kannan", "Sundaram"],
        "F": ["Selvi", "Malathi", "Amudha", "Thangam", "Revathi", "Vasanthi", "Kamala", "Meenakshi"],
    },
    "Telugu": {
        "M": ["Srinivas", "Narasimha", "Prasad", "Chandra", "Ravindra", "Rajesh", "Venkateswarlu", "Subbarao"],
        "F": ["Padmavathi", "Sudha", "Anitha", "Sarala", "Radha", "Jyothi", "Bhagyalakshmi", "Kalpana"],
    },
    "Malayalam": {
        "M": ["Rajan", "Gopinath", "Sreekumar", "Vijayan", "Anoop", "Ajith", "Biju", "Unnikrishnan"],
        "F": ["Latha", "Sreelekha", "Remya", "Sindhu", "Ammini", "Sajitha", "Indu", "Vidya"],
    },
    "Hindi": {
        "M": ["Amit", "Deepak", "Manoj", "Vikas", "Sanjeev", "Ashok", "Naveen", "Ajay"],
        "F": ["Pooja", "Neelam", "Sarita", "Usha", "Madhu", "Kiran", "Shalini", "Geeta"],
    },
    "Marathi": {
        "M": ["Sachin", "Ganesh", "Nitin", "Pravin", "Dilip", "Vilas", "Sudhir", "Milind"],
        "F": ["Sunanda", "Manisha", "Smita", "Varsha", "Rohini", "Vaishali", "Jayashree", "Archana"],
    },
    "Bengali": {
        "M": ["Subhash", "Sourav", "Partha", "Tapas", "Debashish", "Ranjan", "Kaushik", "Arindam"],
        "F": ["Mousumi", "Sharmila", "Ratna", "Kakoli", "Sukanya", "Shampa", "Sujata", "Bani"],
    },
    "Gujarati": {
        "M": ["Hitesh", "Jignesh", "Bhavesh", "Hasmukh", "Mehul", "Paresh", "Nilesh", "Kirit"],
        "F": ["Hetal", "Jyoti", "Bhavna", "Nisha", "Kinjal", "Falguni", "Ila", "Dipti"],
    },
    "Punjabi": {
        "M": ["Gurpreet", "Harjit", "Jaswinder", "Manpreet", "Sukhdev", "Balwinder", "Harpal", "Ranjit"],
        "F": ["Simran", "Jaspreet", "Parminder", "Kulwinder", "Gurdeep", "Rupinder", "Manjeet", "Navjot"],
    },
    "Muslim": {
        "M": ["Mohammed", "Rahim", "Farooq", "Javed", "Nasir", "Asif", "Shabbir", "Imtiaz"],
        "F": ["Nasreen", "Shabana", "Farzana", "Rukhsana", "Yasmeen", "Razia", "Shaheen", "Zubeda"],
    },
    "Christian": {
        "M": ["Abraham", "Stephen", "Philip", "Jacob", "Daniel", "George", "Alwyn", "Benjamin"],
        "F": ["Mary", "Annie", "Elsy", "Jessy", "Sheeba", "Sherly", "Lilly", "Rosamma"],
    },
}

SURNAMES = {
    "Kannada": ["Gowda", "Shetty", "Patil", "Naik", "Murthy", "Bhat", "Hiremath", "Kamath", "Shastry", "Nayak", "Rao", "Achar"],
    "Tamil": ["Subramanian", "Raman", "Velu", "Pandian", "Chellappa", "Ramasamy", "Krishnaswamy", "Annamalai", "Balan", "Iyer", "Kannan", "Sundaram"],
    "Telugu": ["Reddy", "Naidu", "Chowdary", "Kotha", "Gorantla", "Pulipati", "Bollineni", "Mallela", "Vemula", "Jonnalagadda", "Settipalli", "Kamineni"],
    "Malayalam": ["Nambiar", "Kurup", "Varma", "Panicker", "Warrier", "Namboodiri", "Achari", "Kaimal", "Chandran", "Unnithan", "Karunakaran", "Nair"],
    "Hindi": ["Verma", "Gupta", "Agarwal", "Mishra", "Tiwari", "Dubey", "Chauhan", "Saxena", "Pandey", "Bhatia", "Malhotra", "Kapoor", "Sharma"],
    "Marathi": ["Deshpande", "Joshi", "Phadke", "Pawar", "Ghorpade", "Bhosale", "Jadhav", "Gaikwad", "Sawant", "Mhatre", "Salunkhe", "Chavan", "Kadam"],
    "Bengali": ["Chatterjee", "Mukherjee", "Ghosh", "Bose", "Dutta", "Sen", "Roy", "Dasgupta", "Chakraborty", "Sarkar", "Mitra", "Banerjee"],
    "Gujarati": ["Shah", "Parikh", "Modi", "Trivedi", "Solanki", "Thakkar", "Patel", "Desai", "Bhatt", "Vyas", "Raval", "Dave"],
    "Punjabi": ["Singh", "Dhillon", "Gill", "Sandhu", "Brar", "Bajwa", "Chadha", "Sethi", "Grewal", "Sidhu", "Ahluwalia", "Bains"],
    "Muslim": ["Khan", "Ahmed", "Sheikh", "Syed", "Qureshi", "Ansari", "Siddiqui", "Hussain", "Pasha", "Mirza", "Baig", "Mulla"],
    "Christian": ["D'Souza", "Fernandes", "Pereira", "Mathew", "Thomas", "Varghese", "Abraham", "Philip", "Mendonca", "Pinto", "Lobo", "Rodrigues"],
}

REGIONS = tuple(FIRST_NAMES)

# --- Core names and hard-rule patterns -----------------------------------------------------------
ROSTER_NAME_RE = re.compile(r"^### (P\d{2}): ([^,\n]+),", re.M)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Each entry is (compiled pattern, reason). Case-insensitive where it names someone.
FORBIDDEN_PATTERNS = [
    (re.compile(r"\banjali\s+mehta\b", re.I), "names Dr Anjali Mehta"),
    (re.compile(r"\brohan\s+iyer\b", re.I), "names Dr Rohan Iyer"),
    (re.compile(r"\bDr\.?\s+(?:Anjali|Mehta|Rohan|Iyer)\b", re.I), "refers to a core doctor"),
    (re.compile(r"\bD0[12]\b"), "mentions core doctor ID D01 or D02"),
    (re.compile(r"\bP\d{2}\b"), "mentions a core patient ID (P nn)"),
]


def norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def core_patient_names() -> list[str]:
    """Full names of the core patients: world/core/P*.json if present, plus ROSTER.md (union)."""
    names: dict[str, str] = {}
    for path in sorted(CORE_DIR.glob("P*.json")):
        names[path.stem] = json.loads(path.read_text(encoding="utf-8"))["name"]
    roster = ROSTER_PATH.read_text(encoding="utf-8")
    for pid, name in ROSTER_NAME_RE.findall(roster):
        names.setdefault(pid, name.strip())
    if not names:
        raise RuntimeError("no core patient names found in world/core or world/ROSTER.md")
    return [names[pid] for pid in sorted(names)]


@functools.lru_cache(maxsize=None)
def _name_regex(name: str) -> re.Pattern:
    parts = [re.escape(p) for p in name.split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.I)


def text_violations(text: str, core_names: list[str]) -> list[str]:
    """Hard-rule problems in one memory text. An empty list means the text is clean."""
    problems = [reason for rx, reason in FORBIDDEN_PATTERNS if rx.search(text)]
    problems += [f"names core patient {name}" for name in core_names if _name_regex(name).search(text)]
    return problems


def in_window(value) -> bool:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return False
    return date.fromisoformat(config.WINDOW_START) <= d <= date.fromisoformat(config.WINDOW_END)


def validate_memories(memories, primary_doctor: str, core_names: list[str]) -> list[str]:
    """Return a list of problems with a generated memory list. Empty means valid."""
    if not isinstance(memories, list):
        return ["memories is not a list"]
    problems: list[str] = []
    lo, hi = MEMORY_COUNT
    if not lo <= len(memories) <= hi:
        problems.append(f"{len(memories)} memories, expected {lo} to {hi}")
    allowed_authors = {primary_doctor, *PATIENT_AUTHORS}
    for i, m in enumerate(memories, 1):
        if not isinstance(m, dict):
            problems.append(f"memory {i}: not an object")
            continue
        if not in_window(m.get("date")):
            problems.append(f"memory {i}: date {m.get('date')!r} outside the window")
        if m.get("type") not in MEMORY_TYPES:
            problems.append(f"memory {i}: type {m.get('type')!r} not allowed")
        if m.get("author") not in allowed_authors:
            problems.append(f"memory {i}: author {m.get('author')!r} not allowed")
        text = m.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"memory {i}: empty text")
            continue
        problems += [f"memory {i}: {why}" for why in text_violations(text, core_names)]
    return problems


def finalize_memories(patient_id: str, memories: list[dict]) -> list[dict]:
    """Sort by date (stable) and assign memory IDs X0001-m01, X0001-m02, ..."""
    ordered = sorted(memories, key=lambda m: m["date"])
    return [
        {
            "memory_id": f"{patient_id}-m{i:02d}",
            "date": m["date"],
            "type": m["type"],
            "author": m["author"],
            "text": m["text"].strip(),
        }
        for i, m in enumerate(ordered, 1)
    ]


# --- Skeletons -------------------------------------------------------------------------------------
def draw_skeleton(index: int, core_norm: frozenset) -> dict:
    """Deterministic patient skeleton for index. Names matching a core patient are re-drawn."""
    if index < 1:
        raise ValueError("index must be 1 or more")
    rng = random.Random(SEED + index)
    while True:
        region = rng.choice(REGIONS)
        sex = rng.choice(("F", "M"))
        first = rng.choice(FIRST_NAMES[region][sex])
        surname = rng.choice(SURNAMES[region])
        name = f"{first} {surname}"
        if norm_name(name) not in core_norm:
            break
    age = rng.randint(*AGE_RANGE)
    eligible = [
        c for c in CONDITIONS
        if c.get("sex") in (None, sex) and age >= c.get("min_age", 0)
    ]
    conditions = rng.sample(eligible, rng.randint(1, 3))
    return {
        "patient_id": f"X{index:04d}",
        "name": name,
        "age": age,
        "sex": sex,
        "primary_doctor": config.DISTRACTOR_DOCTORS[(index - 1) % len(config.DISTRACTOR_DOCTORS)],
        "conditions": conditions,
        "n_memories": rng.randint(*MEMORY_COUNT),
        "occupation": rng.choice(OCCUPATIONS),
        "locality": rng.choice(LOCALITIES),
    }


def skeleton_prompt(skel: dict, doctor: dict) -> str:
    lines = [
        "Write the clinic record history for one patient, as JSON.",
        "",
        "Patient (the name and ID go in the record header, so do not write them in any text):",
        f"- Age {skel['age']}, {SEX_WORD[skel['sex']]}. Occupation: {skel['occupation']}. "
        f"Lives in {skel['locality']}, Bengaluru.",
        f"- Treating doctor: {doctor['name']} ({doctor['doctor_id']}), {doctor['specialty']}. "
        f'Author every clinician entry as "{doctor["doctor_id"]}".',
        "- Conditions, with typical medicines and tests (vary the details to suit this patient):",
    ]
    for c in skel["conditions"]:
        lines.append(f"  - {c['name']}. Medicines: {'; '.join(c['meds'])}. Tests: {'; '.join(c['tests'])}.")
    lines += [
        "",
        f"Write exactly {skel['n_memories']} memories, in date order, spread over 2025-04-01 to 2026-10-11.",
        "Show a realistic course: visits every few months, medicines started, changed or stopped as the "
        "labs and symptoms change, labs repeated, and at least one appointment booking or change.",
        "Return the JSON object only.",
    ]
    return "\n".join(lines)


def load_distractor_doctors() -> dict[str, dict]:
    doctors = {}
    for did in config.DISTRACTOR_DOCTORS:
        doctors[did] = json.loads((CORE_DIR / f"{did}.json").read_text(encoding="utf-8"))
    return doctors


def system_for_doctor(doctor: dict) -> str:
    """GEN_SYSTEM plus the treating doctor's documentation style, summarised from their preferences."""
    prefs = [m["text"] for m in doctor.get("memories", []) if m.get("type") == "doctor_preference"]
    if not prefs:
        return GEN_SYSTEM
    bullets = "\n".join(f"- {' '.join(p.split())}" for p in prefs)
    return (
        f"{GEN_SYSTEM}\n\n## Treating doctor's documentation style\n"
        f"{doctor['name']} ({doctor['doctor_id']}), {doctor['specialty']}. "
        f"Write this doctor's clinician entries in this style:\n{bullets}"
    )


# --- Generation ------------------------------------------------------------------------------------
@dataclass
class GenContext:
    core_names: list[str]
    core_norm: frozenset
    doctors: dict
    systems: dict
    out_dir: Path


def make_context(out_dir: Path) -> GenContext:
    core_names = core_patient_names()
    doctors = load_distractor_doctors()
    return GenContext(
        core_names=core_names,
        core_norm=frozenset(norm_name(n) for n in core_names),
        doctors=doctors,
        systems={did: system_for_doctor(doc) for did, doc in doctors.items()},
        out_dir=out_dir,
    )


@dataclass
class PatientResult:
    patient_id: str
    status: str                      # "written" | "failed"
    record: dict | None = None
    turns: list = field(default_factory=list)
    attempts: int = 0
    problem: str | None = None


def parse_memories(text: str) -> list[dict]:
    """Decode the JSON object the generator returned and return its memories list."""
    payload = json.loads(text)  # json.JSONDecodeError is a ValueError
    if not isinstance(payload, dict) or "memories" not in payload:
        raise ValueError("JSON has no 'memories' key")
    return payload["memories"]


def build_record(skel: dict, memories: list[dict]) -> dict:
    return {
        "patient_id": skel["patient_id"],
        "name": skel["name"],
        "age": skel["age"],
        "sex": skel["sex"],
        "primary_doctor": skel["primary_doctor"],
        "conditions": [c["name"] for c in skel["conditions"]],
        "memories": memories,
    }


def generate_patient(client, index: int, ctx: GenContext) -> PatientResult:
    """One patient: up to 1 + MAX_RETRIES generator calls, validated after each.

    Raises llm.DailyLimitReached at once. That is not a failed attempt: the caller stops the batch.
    """
    skel = draw_skeleton(index, ctx.core_norm)
    pid = skel["patient_id"]
    doctor = ctx.doctors[skel["primary_doctor"]]
    system = ctx.systems[doctor["doctor_id"]]
    user_text = skeleton_prompt(skel, doctor)
    turns: list[dict] = []
    problem = "no attempt made"
    attempts = 0
    for attempts in range(1, MAX_RETRIES + 2):
        try:
            r = llm.chat(
                client, "generator",
                [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
                response_format=llm.json_schema_format("memories", MEMORIES_SCHEMA),
            )
        except llm.DailyLimitReached:
            raise
        except Exception as exc:  # API errors that survived llm.chat's retries, or no choice in the response
            problem = f"API error: {exc!r}"
            if 400 <= (getattr(exc, "status_code", None) or 0) < 500:
                break  # a 4xx (bad parameters, no endpoint, forbidden) won't change on retry
            continue
        turns.append(r.usage)
        if r.finish_reason == "content_filter":
            problem = "content_filter finish"
            continue
        if not r.text.strip():
            problem = f"empty text (finish_reason {r.finish_reason})"
            continue
        try:
            raw = parse_memories(r.text)
        except ValueError as exc:  # includes json.JSONDecodeError
            problem = f"bad output: {exc}"
            continue
        problems = validate_memories(raw, skel["primary_doctor"], ctx.core_names)
        if problems:
            problem = "; ".join(problems[:5])
            continue
        record = build_record(skel, finalize_memories(pid, raw))
        return PatientResult(pid, "written", record, turns, attempts, None)
    return PatientResult(pid, "failed", None, turns, attempts, problem)


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _job(client, index: int, ctx: GenContext, stop: threading.Event) -> PatientResult | None:
    """One patient. Returns None when nothing was written or failed: the batch was already stopped, or this
    patient hit the daily cap (it is generated again after the reset)."""
    if stop.is_set():  # the daily cap was already hit: a job that has not started makes no API call
        return None
    pid = f"X{index:04d}"
    try:
        res = generate_patient(client, index, ctx)
    except llm.DailyLimitReached:
        stop.set()
        return None
    except Exception as exc:  # unexpected: log and move on
        return PatientResult(pid, "failed", None, [], 0, f"unexpected error: {exc!r}")
    if res.record is not None:
        try:
            write_json_atomic(ctx.out_dir / f"{pid}.json", res.record)
        except OSError as exc:
            res.status, res.problem = "failed", f"write error: {exc!r}"
    return res


def _ascii(text: str) -> str:
    """Console-safe text: model-echoed values can hold characters a Windows console cannot print."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def usage_totals(turns: list[dict]) -> dict:
    """Token and cost totals over the usage dicts from llm.chat. Cost is None-safe: a call with no
    usage.cost adds nothing and is not counted as reported. Input is prompt tokens minus cached tokens."""
    totals = {"input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0,
              "reasoning_tokens": 0, "cost_usd": 0.0, "cost_reported": 0}
    for u in turns:
        cached = u.get("cached_tokens") or 0
        totals["input_tokens"] += max((u.get("prompt_tokens") or 0) - cached, 0)
        totals["cache_read_tokens"] += cached
        totals["output_tokens"] += u.get("completion_tokens") or 0
        totals["reasoning_tokens"] += u.get("reasoning_tokens") or 0
        if u.get("cost_usd") is not None:
            totals["cost_usd"] += u["cost_usd"]
            totals["cost_reported"] += 1
    return totals


@dataclass
class RunSummary:
    requested: int
    skipped: list[str]
    written: list[str]
    failed: list[tuple[str, str]]
    turns: list[dict]
    daily_limit_hit: bool = False
    pending: list[str] = field(default_factory=list)   # to generate and neither written nor failed (daily cap)


def generate_all(client, indices: list[int], out_dir: Path, workers: int) -> RunSummary:
    ctx = make_context(out_dir)
    skipped = [f"X{i:04d}" for i in indices if (out_dir / f"X{i:04d}.json").exists()]
    todo = [i for i in indices if not (out_dir / f"X{i:04d}.json").exists()]
    print(f"{len(indices)} requested, {len(skipped)} already present, {len(todo)} to generate "
          f"with {workers} workers -> {out_dir}", flush=True)

    written: list[str] = []
    failed: list[tuple[str, str]] = []
    turns: list[dict] = []
    stop = threading.Event()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_job, client, i, ctx, stop) for i in todo]
        for fut in as_completed(futures):
            res = fut.result()
            if res is None:  # stopped by the daily cap: not written, not failed
                continue
            done += 1
            turns.extend(res.turns)
            if res.status == "written":
                written.append(res.patient_id)
                line = f"{len(res.record['memories'])} memories, {res.attempts} attempt(s)"
                print(f"[{done}/{len(todo)}] {res.patient_id} written: {line}", flush=True)
            else:
                failed.append((res.patient_id, res.problem or "unknown"))
                print(f"[{done}/{len(todo)}] {res.patient_id} FAILED after {res.attempts} attempt(s): "
                      f"{_ascii(res.problem or 'unknown')}", file=sys.stderr, flush=True)
    finished = set(written) | {pid for pid, _ in failed}
    pending = [f"X{i:04d}" for i in todo if f"X{i:04d}" not in finished]
    return RunSummary(len(indices), skipped, sorted(written), sorted(failed), turns,
                      daily_limit_hit=stop.is_set(), pending=pending)


def print_summary(summary: RunSummary, dry_run: bool) -> None:
    t = usage_totals(summary.turns)
    n_calls = len(summary.turns)
    all_tokens = t["input_tokens"] + t["cache_read_tokens"] + t["output_tokens"]
    print("")
    print("=== distractor generation summary ===")
    if dry_run:
        print("DRY RUN: fake client, no API calls.")
    print(f"requested {summary.requested}: written {len(summary.written)}, "
          f"already present {len(summary.skipped)}, failed {len(summary.failed)}")
    print(f"generator calls: {n_calls}")
    print(f"usage tokens: input {t['input_tokens']}, cache read {t['cache_read_tokens']}, "
          f"output {t['output_tokens']} (of which reasoning {t['reasoning_tokens']}; total {all_tokens})")
    if t["cost_reported"] == n_calls:
        print(f"cost: ${t['cost_usd']:.4f} (OpenRouter usage.cost, summed over {n_calls} calls)")
    else:
        print(f"cost: ${t['cost_usd']:.4f} (OpenRouter usage.cost, summed over {t['cost_reported']} of "
              f"{n_calls} calls; the others reported no cost)")
    if summary.failed:
        print("failed (re-run the same command to retry; finished files are skipped):")
        for pid, reason in summary.failed:
            print(f"  {pid}: {_ascii(reason)}")


def dry_run_client():
    """Offline OpenRouter stand-in for --dry-run: every call returns one fixed, valid memories payload."""
    from tests.fake_openrouter import FakeOpenRouter, text_resp  # test helper; never imported on a live run

    payload = json.dumps({"memories": DRY_RUN_MEMORIES}, ensure_ascii=False)
    return FakeOpenRouter(lambda kwargs: text_resp(payload))


# Authors are patient / lab / front_desk only, so the payload is valid for every distractor doctor.
DRY_RUN_MEMORIES = [
    {"date": "2025-04-10", "type": "patient_context", "author": "patient",
     "text": "Patient-reported via the clinic app: asks for a routine review. Reports lower energy in the "
             "afternoons for about a month. No chest pain, breathlessness or fever. Says the usual tablets "
             "have been taken without missed doses."},
    {"date": "2025-04-10", "type": "lab_result", "author": "lab",
     "text": "Complete blood count, clinic lab. Haemoglobin 13.1 g/dL (ref 12.0 to 15.0). White cell count "
             "6.8 x10^9/L (ref 4.0 to 11.0). Platelets 245 x10^9/L (ref 150 to 400). No value flagged."},
    {"date": "2025-04-11", "type": "appointment", "author": "front_desk",
     "text": "Review booked for 2025-07-11 at 10:30. Booked by phone at the front desk."},
    {"date": "2025-06-02", "type": "lab_result", "author": "lab",
     "text": "Fasting blood glucose 104 mg/dL (ref 70 to 99, above range). Serum creatinine 0.9 mg/dL "
             "(ref 0.6 to 1.1). Fasting sample collected at 08:15 at the clinic lab."},
    {"date": "2025-07-09", "type": "appointment", "author": "front_desk",
     "text": "Appointment changed from 2025-07-11 to 2025-07-18 at 10:00 at the patient's request. "
             "Change made at the front desk on 2025-07-09."},
    {"date": "2025-07-18", "type": "patient_context", "author": "patient",
     "text": "Patient-reported via the clinic app: started a short daily walk after dinner, about 20 minutes. "
             "Feels less tired than before. Asks whether the walk is enough exercise."},
    {"date": "2025-09-22", "type": "lab_result", "author": "lab",
     "text": "Lipid profile, fasting sample. Total cholesterol 214 mg/dL (ref below 200, above range). "
             "LDL 138 mg/dL (ref below 100). HDL 48 mg/dL (ref above 40). Triglycerides 162 mg/dL "
             "(ref below 150)."},
    {"date": "2025-10-03", "type": "appointment", "author": "front_desk",
     "text": "Review booked for 2026-01-08 at 09:30. Booked at the front desk in person."},
    {"date": "2025-11-19", "type": "patient_context", "author": "patient",
     "text": "Patient-reported via the clinic app: mild cough for four days after a cold, no fever, no "
             "shortness of breath. Asks whether to come in before the booked review."},
    {"date": "2026-01-08", "type": "lab_result", "author": "lab",
     "text": "HbA1c 6.4 % (ref below 5.7; 5.7 to 6.4 is the prediabetes range). Fasting blood glucose "
             "98 mg/dL (ref 70 to 99)."},
    {"date": "2026-03-14", "type": "appointment", "author": "front_desk",
     "text": "Review booked for 2026-06-13 at 10:00. Booked at the front desk through the clinic app."},
    {"date": "2026-05-27", "type": "lab_result", "author": "lab",
     "text": "Thyroid function. TSH 3.1 mIU/L (ref 0.4 to 4.0). Free T4 1.2 ng/dL (ref 0.8 to 1.8). "
             "Both within reference range."},
    {"date": "2026-09-16", "type": "patient_context", "author": "patient",
     "text": "Patient-reported via the clinic app: asks whether the morning and evening tablets can be "
             "taken together to save time. Says there have been no side effects this month."},
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate fictional distractor patients via the OpenRouter API (generator role).")
    parser.add_argument("--n", type=int, default=600, help="number of patients to generate (default 600)")
    parser.add_argument("--workers", type=int, default=8, help="parallel generator calls (default 8)")
    parser.add_argument("--start", type=int, default=1, help="first index, giving X{start:04d} (default 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fake client, no API calls; writes to runs/dry_run/distractors")
    args = parser.parse_args(argv)
    if args.n < 1 or args.start < 1 or args.workers < 1:
        parser.error("--n, --start and --workers must be 1 or more")

    if args.dry_run:
        client, out_dir = dry_run_client(), DRY_RUN_DIR
    else:
        client, out_dir = llm.make_client(), DISTRACTOR_DIR
    indices = list(range(args.start, args.start + args.n))
    summary = generate_all(client, indices, out_dir, args.workers)
    print_summary(summary, dry_run=args.dry_run)
    if summary.daily_limit_hit:
        print(f"Daily free-model request limit reached; {len(summary.written)} written this run, "
              f"{len(summary.pending)} still to generate. Rerun the same command after 00:00 UTC; "
              f"patients already written are skipped.", file=sys.stderr)
        return EXIT_DAILY_LIMIT
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
