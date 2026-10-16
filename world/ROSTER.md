# Core roster: the planted facts

This is the source of truth for the 20 core patients and 2 core doctors. World writers must
include **every fact listed under a patient, with the exact values and dates**, and must
**not** add anything listed under "MUST NOT appear". Everything else (extra visits, small
talk, vitals, routine findings) is free to invent, but must not contradict this file.

- Clinic: Sunrise Family Clinic, Bengaluru, India.
- History window: 2025-04-01 to 2026-10-11. "Today" is 2026-10-12. Future appointments may be dated after today.
- Units: HbA1c in %, LDL in mg/dL, eGFR in mL/min/1.73m², creatinine in mg/dL, TSH in mIU/L.
- Medicine frequencies: OD = once daily, BD = twice daily, HS = at bedtime, SOS = as needed.

## Doctors

### D01: Dr Anjali Mehta, Internal Medicine and Diabetology (main GP of the clinic)
Her notes are **terse SOAP** with abbreviations: `k/c/o`, `c/o`, `O/E`, `S:`, `O:`, `A:`,
`P:`, `BD/OD/HS/SOS`, `T2DM`, `HTN`, `R/V` (review). Her standing preferences (each one is a
`doctor_preference` memory):
1. Notes use the S/O/A/P headings, with the Plan as a numbered list.
2. Every note ends with an `Adv:` line (diet and exercise advice), then `R/V in N wks/months`.
3. HbA1c every 3 months if the last value is > 7%, otherwise every 6 months.
4. An annual dilated fundus (eye) exam for every patient with diabetes.
5. Telmisartan is her first-line antihypertensive in patients with diabetes.
6. She writes the generic name with the brand in brackets on the first mention, e.g. "sitagliptin (Januvia) 100 mg OD".

### D02: Dr Rohan Iyer, Cardiology (visiting consultant)
His notes are **narrative paragraphs in full sentences with no abbreviations**. His preferences
(each a `doctor_preference` memory):
1. He writes in full sentences and never abbreviates drug frequencies ("twice daily", not "BD").
2. Every note ends with the sentence "Plan discussed with the patient and family."
3. He states the NYHA class in every heart-failure note.
4. A high-intensity statin for every patient with coronary artery disease; LDL target < 55 mg/dL for clinical coronary artery disease, much higher LDL accepted without it.
5. An echocardiogram every 12 months for heart-failure patients.

---

## Patients

### P01: Sunita Rao, 58 F, primary D01. k/c/o T2DM (since 2019), HTN
- Telmisartan 40 mg OD throughout. Atorvastatin 10 mg OD throughout.
- Metformin 500 mg BD at the start of the window (2025-04-08) → **increased to 1000 mg BD on 2025-07-15** → **stopped 2026-04-14** because of persistent diarrhoea / GI upset → **sitagliptin (Januvia) 100 mg OD started 2026-04-14**.
- HbA1c: 2025-04-08 **8.9**; 2025-07-15 **8.1**; 2025-10-14 **7.6**; 2026-01-13 **7.4**; 2026-04-14 **7.9**; 2026-08-18 **7.1**.
- Dilated fundus exam 2025-05-20: mild non-proliferative diabetic retinopathy in both eyes; annual review advised. **No eye exam since.**
- Visits (D01): 2025-04-08, 2025-07-15, 2025-10-14, 2026-01-13, 2026-04-14, 2026-08-18.
- 2026-08-18 visit: GI symptoms resolved on sitagliptin; BP 132/84; continue the same; R/V in 3 months.
- Patient-reported: walks 20 min daily; her daughter's wedding is in December 2026 and she feels stressed; prefers to take all tablets in the morning.
- Appointment: 2026-11-17, 10:00, D01.

### P02: Arjun Nair, 45 M, primary D01. T2DM, newly diagnosed 2025-06-10
- Diagnosed 2025-06-10: HbA1c **9.4**, fasting glucose 186 mg/dL. Bank branch manager, sedentary.
- Metformin 500 mg BD from 2025-06-10, unchanged since.
- HbA1c: 2025-06-10 **9.4**; 2025-09-16 **7.2**; 2026-01-20 **6.8**; 2026-07-21 **6.6**.
- Weight 88 kg (2025-06) → 82 kg (2026-07).
- Dilated fundus exam 2026-02-12: normal, no retinopathy.
- Patient-reported: calls his diabetes "sugar"; asked whether he can still eat rice.
- Advice from D01 (2025-06-10, repeated 2026-01-20): cut white rice to 1 cup per meal; switch to brown rice or millets (ragi, jowar); walk 30 minutes, 5 days a week; lose 5 kg.
- 2026-07-21 visit: sugar well controlled; continue the same plan; repeat HbA1c in 6 months.
- Appointment: 2027-01-19, 09:00, D01.
- **MUST NOT appear:** any lipid profile or cholesterol result, or any discussion of cholesterol.

### P03: Kavita Jain, 52 F, primary D01. Hypothyroidism, prediabetes (NOT diabetic)
- HbA1c 2025-05-06 **6.1**; 2026-05-12 **6.0** (prediabetes; never on glucose-lowering medicine).
- Levothyroxine 50 mcg OD → **75 mcg OD from 2025-08-19**.
- TSH: 2025-05-06 **6.8**; 2025-08-19 **7.9**; 2025-11-18 **3.2**; 2026-05-12 **2.6**.
- Patient-reported: follows a Jain diet (no onion, garlic, potato or other root vegetables); does not eat after sunset; fasts for the 8 days of Paryushana (August–September) on boiled water only during the day.
- 2026-05-12, D01 gave a prediabetes diet plan: 3 meals plus 2 small snacks a day, including millets; avoid sweets; walk 30 minutes daily.
- Visits (D01): 2025-05-06, 2025-08-19, 2025-11-18, 2026-05-12.
- Appointment: **2026-10-12 (today), 10:30**, D01, routine thyroid follow-up.

### P04: Vikram Desai, 66 M, primary D01, also seen by D02. HTN, dyslipidaemia, ex-smoker
- Amlodipine 5 mg OD throughout. Atorvastatin 10 mg OD until 2026-01-09.
- 2025-11-04 (D01): chest tightness on climbing stairs for 3 weeks; ECG normal; **referred to cardiology (Dr Rohan Iyer, D02)**.
- 2026-01-09 (D02 consultation = the referral outcome): treadmill test **positive for inducible ischaemia** at 7 METs. Started **aspirin 75 mg OD** and **metoprolol succinate 25 mg OD**; **atorvastatin increased 10 → 40 mg OD**. Advised coronary angiography; **referred to the Narayana Health cath lab on 2026-01-09**.
- LDL: 2025-11-04 **142**; 2026-03-10 **88**.
- Visits (D01): 2025-05-13, 2025-11-04, 2026-03-10, 2026-09-08. The 2026-09-08 visit: no chest pain at rest, BP 128/78, stable.
- **MUST NOT appear:** any angiography result, report or date, or any reply from the cath lab (the angiography referral is an open loop). Any colonoscopy.

### P05: Fatima Sheikh, 49 F, primary D01. T2DM (since 2021), dyslipidaemia
- Metformin 1000 mg BD throughout.
- Glimepiride 1 mg OD → **2 mg OD on 2025-10-07** → **reduced to 1 mg, taken with the iftar meal, on 2026-02-03** ahead of Ramadan → **back to 2 mg OD before breakfast on 2026-04-07**.
- Atorvastatin 10 mg HS → **20 mg HS on 2026-04-07** (LDL 128).
- HbA1c: 2025-06-03 **8.0**; 2025-10-07 **8.7**; 2026-02-03 **8.2**; 2026-04-07 **8.1**; 2026-08-04 **8.4**.
- Dilated fundus exam 2026-01-15: no retinopathy.
- Patient-reported (2025-06-03): school teacher; keeps Ramadan fasts; had a low-sugar episode during Ramadan in March 2025.
- Appointment booked 2026-08-04 for **2026-10-21**, review with fasting lipid profile and HbA1c → **rescheduled by the patient on 2026-10-01 to 2026-10-28 at 09:00**. Fasting 10–12 hours needed (water allowed); take the morning glimepiride only after the blood test and breakfast.

### P06: Thomas Mathew, 71 M, primary D01. T2DM, CKD stage 3a, gout, HTN
- Metformin 1000 mg BD → **reduced to 500 mg BD on 2026-02-10** (eGFR 42).
- **Amlodipine 5 mg OD stopped 2025-12-09** (ankle oedema) → **telmisartan 40 mg OD started 2025-12-09**.
- Allopurinol 100 mg OD throughout.
- Colchicine 0.5 mg BD for an acute gout flare: 2026-06-16 to 2026-06-21 only, then stopped. Uric acid 8.9 mg/dL on 2026-06-16.
- eGFR: 2025-06-10 **49**; 2025-12-09 **45**; 2026-02-10 **42**; 2026-08-11 **44**.
- HbA1c: 2025-06-10 7.3; 2026-02-10 7.7; 2026-08-11 **7.5**.
- Dilated fundus exam 2025-08-05: early background retinopathy. **No eye exam since.**
- No anticoagulant or antiplatelet medicine, ever.
- Appointment: 2026-11-10, 11:00, D01, review with renal function test; no fasting needed.

### P07: Meera Pillai, 38 F, primary D01. Migraine, iron-deficiency anaemia
- Ferrous sulfate 200 mg OD **started 2025-07-08** (Hb 9.8) → **stopped 2026-05-12** (Hb 12.6, ferritin 48).
- Hb: 2025-07-08 **9.8**; 2025-10-14 **11.2**; 2026-05-12 **12.6**.
- **Propranolol 40 mg OD for migraine prevention, started 2026-02-17**; migraines down from 8 a month to 3 a month by 2026-06-23.
- Sumatriptan 50 mg SOS for migraine attacks.
- Patient-reported: software engineer; long screen time triggers migraines; 3 cups of coffee a day.
- Last visit: 2026-06-23.

### P08: Anil Kumar, 29 M, primary D01. Asthma
- Salbutamol inhaler SOS; budesonide-formoterol 200/6 inhaler BD since 2025-09-16.
- **Allergy: penicillin, anaphylaxis** (throat swelling and low blood pressure) after an amoxicillin injection in 2019. Recorded 2025-04-22.
- 2026-10-06 (D01): sore throat and fever for 2 days; O/E tonsillar exudate; rapid strep test sent; plan: review the result and decide on an antibiotic.

### P09: Deepa Krishnan, 55 F, primary D02 (also seen by D01). Paroxysmal AF, HTN
- Apixaban 5 mg twice daily from 2025-08-12. Bisoprolol 2.5 mg once daily. Telmisartan 40 mg once daily.
- **Allergy: sulfonamide antibiotics.** "Septran" (co-trimoxazole) in 2012 caused a widespread blistering rash needing hospital admission (suspected Stevens-Johnson syndrome). Recorded 2025-08-12.
- 2026-10-08 (D01): burning on passing urine for 3 days; urine culture sent; plan: start an antibiotic for a UTI.

### P10: Harish Gowda, 60 M, primary D02 (also seen by D01). HTN, asthma with NSAID sensitivity
- **Allergy / intolerance: NSAIDs.** Severe bronchospasm after ibuprofen in 2018 needing an emergency visit (aspirin-exacerbated respiratory disease). Recorded 2025-06-17.
- Amlodipine 10 mg once daily; montelukast 10 mg at bedtime; budesonide inhaler twice daily.
- 2026-07-14 (D01): knee osteoarthritis; advised **paracetamol 650 mg SOS** for knee pain, plus quadriceps exercises.

### P11: Joseph D'Souza, 68 M, primary D01. Permanent AF, HTN, COPD
- Warfarin 3 mg OD → **3.5 mg OD from 2025-09-16** (INR 1.7). INR target 2–3.
- INR: 2025-04-15 **2.4**; 2025-09-16 **1.7**; 2025-10-14 **2.5**; 2026-03-10 **2.8**; 2026-09-15 **2.6**.
- Metoprolol 50 mg OD; tiotropium inhaler OD.
- 2026-10-09 (D01): productive cough and fever; chest X-ray shows right lower zone consolidation; plan: start an antibiotic.

### P12: Neha Gupta, 33 F, primary D01. PCOS (NOT diabetic)
- Metformin 500 mg BD for PCOS from 2025-05-20. HbA1c 2025-05-20 **5.4** (normal).
- **Allergy: penicillin, mild.** An itchy rash with amoxicillin in childhood (around 1999); no swelling, no breathing difficulty. Recorded 2025-05-20.
- 2026-10-07 (D01): cough and sinus pain for 10 days; likely acute bacterial sinusitis; plan: antibiotic.

### P13: Lakshmi Venkatesh, 63 F, primary D01. T2DM, CKD stage 3b, HTN
- eGFR: 2025-05-13 **52**; 2025-09-09 **48**; 2026-01-13 **44**; 2026-05-12 **41**; 2026-09-08 **38**.
- Creatinine: 1.12, 1.20, 1.30, 1.40, 1.52 on the same dates.
- Urine albumin-creatinine ratio 2026-05-12: 180 mg/g.
- Metformin 1000 mg BD → **500 mg BD on 2026-01-13**. **Dapagliflozin 10 mg OD from 2025-09-09.** **Linagliptin 5 mg OD from 2026-05-12.** Telmisartan 40 mg OD throughout.
- HbA1c latest 2026-09-08 **7.2**.
- **Referral: nephrology (Dr Sunil Rao, St John's Hospital) on 2026-05-12** for falling eGFR.
- Dilated fundus exam 2026-03-17: no retinopathy.
- Patient-reported (app message, 2026-09-10): retired teacher; asks whether her "kidney numbers" are getting better.
- **MUST NOT appear:** any reply, appointment or report from nephrology.

### P14: Sanjay Patel, 57 M, primary D02. CAD with a stent after a heart attack (2024), dyslipidaemia, HTN
- LDL: 2025-04-22 **162** (on atorvastatin 40 mg, poor adherence); 2025-07-22 **118**; 2025-11-18 **96**; 2026-02-17 **91**; 2026-06-16 **74**.
- **2026-02-17: atorvastatin increased 40 → 80 mg once daily and ezetimibe 10 mg once daily added.** D02 notes the LDL target of < 55 mg/dL.
- Clopidogrel stopped 2025-06-10 (12 months after the stent). Aspirin 75 mg, ramipril 5 mg and metoprolol 50 mg, all once daily, throughout.
- Appointment: **2026-11-04, 10:00, D02**, with a fasting lipid profile. Fast 10–12 hours (water allowed); take the morning medicines after the blood test.

### P15: Ritu Banerjee, 47 F, primary D01. Hypothyroidism (Hashimoto's)
- Levothyroxine 50 → **75 mcg OD on 2025-04-29** → **88 mcg OD on 2025-07-29**.
- TSH: 2025-04-29 **8.2**; 2025-07-29 **5.1**; 2025-11-04 **2.4**; 2026-05-05 **3.9** (in the normal range).
- Patient-reported: takes levothyroxine with her morning tea. Counselled (2025-07-29) to take it on an empty stomach, 30–60 minutes before tea or breakfast.
- Last visit: 2026-05-05 (continue 88 mcg; repeat TSH in 6 months).

### P16: Imran Qureshi, 54 M, primary D01. T2DM
- Metformin 1000 mg BD throughout.
- Glimepiride 2 mg OD before breakfast → **2026-02-10: changed to 1 mg with the iftar meal for Ramadan** → **2026-04-14: back to 2 mg OD before breakfast**.
- HbA1c: 2025-05-06 **8.2**; 2025-11-11 **8.6**; 2026-04-14 **9.1**; 2026-08-11 **8.9**.
- **Referral: ophthalmology (Dr Kamath, Narayana Nethralaya) on 2025-12-09** for diabetic eye screening.
- Patient-reported: owns a restaurant; eats dinner late (around 11 pm); keeps Ramadan fasts.
- 2026-08-11 (D01) diet plan: 3 regular meals; breakfast within 1 hour of waking; glimepiride before breakfast; no sugary drinks.
- 2026-09-20 (app message): he wants to start 16:8 intermittent fasting he saw on YouTube, eating only between 12 pm and 8 pm and skipping breakfast.
- **MUST NOT appear:** any eye examination result, or any reply from ophthalmology.

### P17: Ramesh Sharma, 61 M, primary D01. T2DM, HTN. **Near-duplicate of P18.**
- Metformin 1000 mg BD; amlodipine 5 mg OD; atorvastatin 10 mg OD.
- HbA1c 2026-03-03 **7.4**.
- **Latest labs 2026-09-02:** HbA1c **7.8**; creatinine **1.1**; LDL **102**; fasting glucose **148**.
- **No known drug allergies** (recorded 2025-04-15).
- Dilated fundus exam 2026-04-21: no retinopathy.
- Appointment: 2026-12-02, 09:30, D01.
- Patient-reported: retired railway clerk in Jayanagar; his wife manages his tablets.

### P18: Rakesh Sharma, 64 M, primary D01. T2DM, CKD stage 3a. **Near-duplicate of P17.**
- **Insulin glargine started 2026-02-17 at 10 units HS → 14 units HS from 2026-05-19.** Metformin 500 mg BD. Telmisartan 40 mg OD.
- HbA1c 2026-05-19 **9.2**.
- **Latest labs 2026-08-18:** HbA1c **8.6**; creatinine **1.6**; LDL **131**; fasting glucose **172**.
- **Allergy: codeine** (severe vomiting, 2017). Recorded 2025-04-29.
- Dilated fundus exam 2025-06-10: mild non-proliferative retinopathy. **No eye exam since.**
- Appointment: 2026-10-20, 08:30, D01, fasting labs.
- Patient-reported: runs a hardware shop in Malleshwaram; his daughter comes to appointments with him.

### P19: Geeta Reddy, 70 F, primary D02. Heart failure with reduced ejection fraction (ischaemic), HTN
- Echocardiogram 2025-07-15: **EF 30%**, NYHA class III. Echocardiogram 2026-06-23: **EF 40%**, NYHA class II.
- Sacubitril/valsartan 24/26 mg twice daily from 2025-08-05 → **49/51 mg twice daily on 2025-11-18**. Bisoprolol 5 mg once daily; spironolactone 25 mg once daily; dapagliflozin 10 mg once daily; atorvastatin 20 mg once daily.
- Furosemide 40 mg once daily → **20 mg once daily on 2026-06-23**.
- Potassium 4.9 mmol/L on 2026-06-23.

### P20: Sameer Joshi, 41 M, primary D01. HTN (diagnosed 2025-06-17)
- Telmisartan 40 mg OD from 2025-06-17 → **telmisartan 40 mg + hydrochlorothiazide 12.5 mg OD (combination tablet) from 2025-09-23** (BP 148/96).
- Labs 2025-06-17: LDL 128; fasting glucose 98; creatinine 0.9. 2025-10-21: sodium 139, potassium 4.1.
- Visits (D01): 2025-06-17, 2025-09-23, 2025-10-21, 2026-04-14 (BP 132/86).
- Patient-reported: IT support engineer on night shifts (10 pm–7 am), sleeps 9 am–3 pm; vegetarian (eats dairy, no eggs). App message 2026-08-26: wants to start a keto diet to lose weight.
- **MUST NOT appear:** any HbA1c result, or any future appointment.
