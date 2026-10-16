# Gold answers and required facts: alignment pass

**Why.** The LLM judge grades answers fact by fact, and a perfect copy of a gold answer should satisfy every required fact. Many gold answers left out details that their facts state (doses, dates, values, reasons, symptom wording), so a copy of the gold could score 1 or 2 out of 3. Some facts also used abbreviations (OD, BD, HS) where the gold wrote the words out, and some facts carried incidental details (an old dose, a start date) that the gold does not need.

**What was done.** Every question was checked against the patient record in `world/core/<PATIENT_ID>.json`, not only the ones a heuristic flagged. For each fact, each element (drug, dose, frequency, date, value, reason, symptom wording) was compared with the gold answer:

- An element that answers what the question asks, or that is clinically decisive, was added to the gold answer in the gold's own style.
- An incidental element was removed from the fact, or the fact was reworded to the gold's wording.
- Abbreviations were written the way the gold writes them (once daily, twice a day, at bedtime).

Not changed: question text, `qid`, asker fields, `memory_ids`, `forbidden_facts`, the abstention, safety-critical and pilot flags, use-case fields, the number of facts, and their order. Backup of the previous file: `questions/questions.pre-align.json`.

**Totals.** 30 of 48 questions changed; 12 gold answers changed; 71 of 143 required facts reworded or trimmed. No fact was added or removed, and no `memory_ids` changed.

## Gold answer additions

- **q01**: Reason for stopping metformin is diarrhoea and GI upset (P01-m12, P01-m13).
- **q11**: The INR target 2.0 to 3.0 is the basis for calling 2.6 in range (P11-m06, P11-m11).
- **q14**: Formatting only: units added to the LDL values, which are the same as in the facts.
- **q21**: Euthyroid on 75 mcg with TSH 2.6 (P03-m10).
- **q22**: Formatting only: months written in full, as in the other gold answers.
- **q23**: Echo of 23 June 2026 improved from 30 per cent in July 2025 (P19-m04, P19-m10).
- **q30**: Intermediate eGFR values for the trend: 52 (May 2025), 48 (Sep 2025), 44 (Jan 2026), 41 (May 2026), 38 (8 Sep 2026) (P13-m02, m03, m06, m10, m14).
- **q38**: NSAID intolerance covers all NSAIDs and the tablet bought without prescription (P10-m01, P10-m02); paracetamol is SOS (P10-m10).
- **q39**: Furosemide is the water tablet (P19-m03, P19-m12).
- **q41**: Glimepiride 2 mg before breakfast and metformin 1000 mg BD (P16-m13, P16-m14); the 16:8 plan of eating 12 pm to 8 pm and skipping breakfast (P16-m16).
- **q43**: Vegetarian with no eggs (P20-m02).
- **q44**: Glimepiride was the drug during the March 2025 episode (P05-m01, P05-m03); the advice to come in below 70 mg/dL (P05-m09).

## Changed questions

### q01
- Gold, added or changed: "stopped metformin for persistent GI upset and started" becomes "stopped metformin for persistent diarrhoea and GI upset and started".
- **F1** before: "Metformin 1000 mg BD stopped on 2026-04-14 because of persistent diarrhoea and GI upset"
  after: "Metformin stopped on 2026-04-14 because of persistent diarrhoea and GI upset"
- **F2** before: "Sitagliptin (Januvia) 100 mg OD started on 2026-04-14 in place of metformin"
  after: "Sitagliptin (Januvia) 100 mg once daily started on 2026-04-14 in place of metformin"
- **F3** unchanged.
- **F4** before: "GI symptoms resolved on sitagliptin; no loose stools since (visit 2026-08-18)"
  after: "GI symptoms resolved on sitagliptin"

### q02
- Gold: unchanged.
- **F1** unchanged.
- **F2** before: "Aspirin 75 mg OD and metoprolol succinate 25 mg OD started and atorvastatin increased 10 to 40 mg OD on 2026-01-09"
  after: "Aspirin 75 mg once daily and metoprolol succinate 25 mg once daily started and atorvastatin increased from 10 mg to 40 mg once daily on 2026-01-09"
- **F3** unchanged.
- **F4** before: "Stable at the 2026-09-08 visit: no chest pain at rest; BP 128/78 mmHg on amlodipine 5 mg OD"
  after: "Stable at the 2026-09-08 visit: no chest pain at rest; BP 128/78 mmHg on amlodipine 5 mg once daily"

### q03
- Gold: unchanged.
- **F1** before: "Amlodipine 5 mg OD stopped and telmisartan 40 mg OD started on 2025-12-09 for ankle oedema"
  after: "Amlodipine stopped and telmisartan 40 mg OD started on 2025-12-09 for ankle oedema"
- **F2** before: "Metformin reduced from 1000 mg BD to 500 mg BD on 2026-02-10 because eGFR was 42"
  after: "Metformin reduced to 500 mg BD on 2026-02-10 because eGFR was 42"
- **F3** before: "Acute gout flare on 2026-06-16; colchicine 0.5 mg BD course 2026-06-16 to 2026-06-21, completed"
  after: "Acute gout flare on 2026-06-16, treated with a six-day colchicine course that finished on 2026-06-21"
- **F4** before: "eGFR 49 (2025-06-10), 45 (2025-12-09), 42 (2026-02-10) and 44 (2026-08-11)"
  after: "eGFR 49 (June 2025), 45 (December 2025), 42 (February 2026) and 44 (August 2026)"

### q05
- Gold: unchanged.
- **F1** before: "Sitagliptin (Januvia) 100 mg OD, started 2026-04-14 and continued 2026-08-18"
  after: "Sitagliptin (Januvia) 100 mg once daily"
- **F2** before: "Telmisartan 40 mg OD, continued 2026-08-18"
  after: "Telmisartan 40 mg once daily"
- **F3** before: "Atorvastatin 10 mg OD, continued 2026-08-18"
  after: "Atorvastatin 10 mg once daily"

### q06
- Gold: unchanged.
- **F1** before: "Metformin 500 mg BD (reduced on 2026-02-10 for eGFR 42), continued 2026-08-11"
  after: "Metformin 500 mg BD (reduced on 2026-02-10 for eGFR 42)"
- **F2** before: "Telmisartan 40 mg OD, started 2025-12-09 and continued 2026-08-11"
  after: "Telmisartan 40 mg OD, started 2025-12-09"
- **F3** unchanged.

### q07
- Gold: unchanged.
- **F1** before: "Propranolol (Inderal) 40 mg OD started 2026-02-17 and continued 2026-06-23"
  after: "Propranolol (Inderal) 40 mg once daily in the morning for migraine prevention"
- **F2** before: "Sumatriptan (Imitrex) 50 mg SOS at onset of an attack, max 2 doses in 24 hours"
  after: "Sumatriptan (Imitrex) 50 mg at the onset of an attack when needed, no more than 2 doses in 24 hours"
- **F3** before: "Ferrous sulfate 200 mg OD stopped 2026-05-12 (Hb 12.6 g/dL, ferritin 48 ng/mL); do not refill"
  after: "Ferrous sulfate 200 mg daily stopped on 2026-05-12 after her haemoglobin reached 12.6 g/dL; should not be refilled"

### q08
- Gold: unchanged.
- **F1** before: "Metformin 1000 mg BD, continued 2026-08-04"
  after: "Metformin 1000 mg BD"
- **F2** unchanged.
- **F3** before: "Atorvastatin increased to 20 mg HS on 2026-04-07"
  after: "Atorvastatin increased from 10 mg to 20 mg at bedtime on 2026-04-07"

### q11
- Gold, added or changed: "within range, and the 9 October 2026 plan" becomes "within the 2.0 to 3.0 target range, and the 9 October 2026 plan".
- **F1** before: "Warfarin 3.5 mg OD since 2025-09-16 for permanent AF, INR target 2.0 to 3.0"
  after: "Warfarin 3.5 mg once daily for permanent AF, INR target 2.0 to 3.0"
- **F2** unchanged.
- **F3** unchanged.

### q14
- Gold, added or changed: "to 118 (22 July 2025), 96 (18 November 2025), 91 (17 February 2026) and 74 mg/dL (16 June 2026)" becomes "to 118 mg/dL (22 July 2025), 96 mg/dL (18 November 2025), 91 mg/dL (17 February 2026) and 74 mg/dL (16 June 2026)".
- **F1** unchanged.
- **F2** unchanged.
- **F3** unchanged.
- **F4** before: "Atorvastatin increased from 40 mg to 80 mg OD and ezetimibe 10 mg OD added on 2026-02-17"
  after: "Atorvastatin increased from 40 to 80 mg once daily and ezetimibe 10 mg once daily added on 2026-02-17"

### q15
- Gold: unchanged.
- **F1** before: "TSH 8.2 mIU/L on 2025-04-29 on levothyroxine 50 mcg; increased to 75 mcg OD the same day"
  after: "TSH 8.2 mIU/L on 2025-04-29 on levothyroxine 50 mcg; raised to 75 mcg that day"
- **F2** before: "TSH 5.1 mIU/L on 2025-07-29, still raised; tablet taken with tea; increased to 88 mcg OD"
  after: "TSH 5.1 mIU/L on 2025-07-29, still raised; tablet taken with tea; dose raised to 88 mcg"
- **F3** before: "TSH 2.4 mIU/L on 2025-11-04 on 88 mcg OD, within range"
  after: "TSH 2.4 mIU/L on 2025-11-04 on 88 mcg, within range"
- **F4** before: "TSH 3.9 mIU/L on 2026-05-05 on 88 mcg OD, within range"
  after: "TSH 3.9 mIU/L on 2026-05-05 on 88 mcg, within range"

### q19
- Gold: unchanged.
- **F1** before: "Arjun Nair (P02): metformin 500 mg BD, continued 2026-07-21"
  after: "Arjun Nair (P02): on metformin"
- **F2** before: "Fatima Sheikh (P05): metformin 1000 mg BD, continued 2026-08-04"
  after: "Fatima Sheikh (P05): on metformin"
- **F3** before: "Thomas Mathew (P06): metformin 500 mg BD, continued 2026-08-11"
  after: "Thomas Mathew (P06): on metformin"
- **F4** before: "Neha Gupta (P12): metformin 500 mg BD for PCOS, continued 2026-10-07"
  after: "Neha Gupta (P12): on metformin for PCOS"
- **F5** before: "Lakshmi Venkatesh (P13): metformin 500 mg BD, continued 2026-09-08"
  after: "Lakshmi Venkatesh (P13): on metformin"
- **F6** before: "Imran Qureshi (P16): metformin 1000 mg BD, continued 2026-08-11"
  after: "Imran Qureshi (P16): on metformin"
- **F7** before: "Ramesh Sharma (P17): metformin 1000 mg BD, continued 2026-09-02"
  after: "Ramesh Sharma (P17): on metformin"
- **F8** before: "Rakesh Sharma (P18): metformin 500 mg BD, continued 2026-08-18"
  after: "Rakesh Sharma (P18): on metformin"

### q20
- Gold: unchanged.
- **F1** unchanged.
- **F2** before: "Lakshmi Venkatesh (P13): nephrology referral to Dr Sunil Rao, St John's, on 2026-05-12; still no reply on record at 2026-09-08"
  after: "Lakshmi Venkatesh (P13): nephrology referral to Dr Sunil Rao, St John's, on 2026-05-12; no reply on record"
- **F3** unchanged.

### q21
- Gold, added or changed: "A: (1) hypothyroidism on 75 mcg, last TSH 2.6 mIU/L (12 May 2026), now tired;" becomes "A: (1) hypothyroidism on 75 mcg, euthyroid with last TSH 2.6 mIU/L (12 May 2026), now tired;".
- **F1** before: "Writes notes under S/O/A/P headings with a numbered plan (one action per line) in standard abbreviations such as k/c/o, O/E, OD, BD and R/V"
  after: "Writes notes under S/O/A/P headings with a numbered plan (one action per line) in standard abbreviations such as k/c/o, OD and R/V"
- **F2** unchanged.
- **F3** unchanged.
- **F4** before: "On levothyroxine (Thyronorm) 75 mcg OD since 2025-08-19 for hypothyroidism"
  after: "On levothyroxine (Thyronorm) 75 mcg OD for hypothyroidism"
- **F5** unchanged.
- **F6** unchanged.

### q22
- Gold, added or changed: "HbA1c 7.8 % (2 Sep 2026)" becomes "HbA1c 7.8 % (2 September 2026)".
- Gold, added or changed: "last dilated eye exam 21 Apr 2026" becomes "last dilated eye exam 21 April 2026".
- **F1** before: "Writes notes under S/O/A/P headings with a numbered plan in standard abbreviations (k/c/o, O/E, BD, OD, R/V)"
  after: "Writes notes under S/O/A/P headings with a numbered plan in standard abbreviations (k/c/o, BD, OD, R/V)"
- **F2** unchanged.
- **F3** unchanged.
- **F4** before: "On metformin 1000 mg BD, amlodipine 5 mg OD and atorvastatin 10 mg OD (plan of 2026-09-02)"
  after: "On metformin 1000 mg BD, amlodipine 5 mg OD and atorvastatin 10 mg OD"
- **F5** unchanged.
- **F6** unchanged.

### q23
- Gold, added or changed: "ejection fraction is 40 per cent from the echocardiogram of 23 June 2026." becomes "ejection fraction is 40 per cent from the echocardiogram of 23 June 2026, improved from 30 per cent in July 2025.".
- **F1** unchanged.
- **F2** unchanged.
- **F3** unchanged.
- **F4** before: "Echocardiogram 2026-06-23: ejection fraction 40 %, improved from 30 % on 2025-07-15"
  after: "Echocardiogram on 2026-06-23: ejection fraction 40 per cent, improved from 30 per cent in July 2025"
- **F5** unchanged.

### q24
- Gold: unchanged.
- **F1** unchanged.
- **F2** unchanged.
- **F3** unchanged.
- **F4** unchanged.
- **F5** before: "Atorvastatin 80 mg once daily and ezetimibe 10 mg once daily since 2026-02-17"
  after: "Atorvastatin 80 mg once daily and ezetimibe 10 mg once daily"

### q25
- Gold: unchanged.
- **F1** unchanged.
- **F2** before: "Aspirin 75 mg OD and metoprolol succinate 25 mg OD started; atorvastatin increased to 40 mg OD"
  after: "Aspirin 75 mg once daily and metoprolol succinate 25 mg once daily started; atorvastatin increased from 10 mg to 40 mg once daily"
- **F3** unchanged.

### q29
- Gold: unchanged.
- **F1** before: "HbA1c 9.4 % on 2025-06-10 at diagnosis"
  after: "HbA1c 9.4 % in June 2025"
- **F2** before: "HbA1c 6.6 % on 2026-07-21, well controlled on metformin"
  after: "HbA1c 6.6 % in July 2026: your sugar control is good now"
- **F3** before: "Continue metformin 500 mg BD"
  after: "Keep taking metformin 500 mg twice a day"
- **F4** before: "Rice: one cup per meal, brown rice or millets; walk 30 min, 5 days a week"
  after: "Keep to one cup of rice per meal, using brown rice or millets, and keep walking 30 minutes, 5 days a week"

### q30
- Gold, added or changed: "has fallen from 52 in May 2025 to 38 on 8 September 2026," becomes "has fallen steadily from 52 in May 2025 to 48 in September 2025, 44 in January 2026, 41 in May 2026 and 38 on 8 September 2026,".
- **F1** before: "eGFR has fallen steadily: 52 (2025-05-13), 48 (2025-09-09), 44 (2026-01-13), 41 (2026-05-12), 38 (2026-09-08)"
  after: "eGFR has fallen steadily: 52 in May 2025, 48 in September 2025, 44 in January 2026, 41 in May 2026 and 38 on 8 September 2026"
- **F2** before: "Referred to nephrology, Dr Sunil Rao, St John's Hospital, on 2026-05-12"
  after: "Referred to nephrology, Dr Sunil Rao, St John's Hospital"

### q31
- Gold: unchanged.
- **F1** before: "Echocardiogram on 2025-07-15: ejection fraction 30 %"
  after: "Echocardiogram on 2025-07-15: ejection fraction 30 per cent"
- **F2** before: "Echocardiogram on 2026-06-23: ejection fraction 40 %, improved on current treatment"
  after: "Echocardiogram on 2026-06-23: ejection fraction 40 per cent, improved on current treatment"

### q33
- Gold: unchanged.
- **F1** before: "Review rescheduled from 2026-10-21 to 2026-10-28 at 09:00 with Dr Mehta, by phone on 2026-10-01"
  after: "Review rescheduled from 21 October to 28 October 2026 at 9:00 am with Dr Mehta, by phone on 1 October 2026"
- **F2** unchanged.
- **F3** unchanged.

### q37
- Gold: unchanged.
- **F1** before: "On warfarin 3.5 mg OD for permanent AF, INR target 2.0 to 3.0"
  after: "On warfarin for permanent AF"
- **F2** before: "Advised to avoid over-the-counter painkillers (NSAIDs) and herbal supplements without advice"
  after: "Advised to avoid over-the-counter painkillers (NSAIDs) without checking first"
- **F3** unchanged.

### q38
- Gold, added or changed: "so you must avoid it. For knee pain," becomes "so you must avoid it and all other NSAID painkillers. For knee pain,".
- Gold, added or changed: "Dr Mehta prescribed, no more than 3 tablets" becomes "Dr Mehta prescribed when needed, no more than 3 tablets".
- **F1** before: "Paracetamol (Crocin) 650 mg as needed for knee pain, max 3 tablets in 24 hours"
  after: "Paracetamol (Crocin) 650 mg when needed for knee pain, no more than 3 tablets in 24 hours"
- **F2** unchanged.

### q39
- Gold, added or changed: "your water tablet it can affect" becomes "your water tablet (furosemide) it can affect".
- **F1** before: "Heart failure with reduced ejection fraction; ejection fraction 40 % on 2026-06-23"
  after: "Heart failure with reduced ejection fraction"
- **F2** before: "Current medicines include sacubitril with valsartan 49/51 mg BD, spironolactone 25 mg OD and furosemide 20 mg OD (since 2026-06-23)"
  after: "Current medicines include sacubitril with valsartan, spironolactone and furosemide"
- **F3** unchanged.

### q40
- Gold: unchanged.
- **F1** before: "Propranolol (Inderal) 40 mg OD, started 2026-02-17 and continued 2026-06-23"
  after: "Propranolol (Inderal) 40 mg once a morning"
- **F2** before: "Sumatriptan (Imitrex) 50 mg SOS, max 2 doses in 24 hours"
  after: "Sumatriptan (Imitrex) taken only when a migraine starts"
- **F3** unchanged.

### q41
- Gold, added or changed: "This does not fit your current plan as it stands," becomes "Your planned 16:8 fasting, eating only between 12 pm and 8 pm and skipping breakfast, does not fit your current plan as it stands,".
- Gold, added or changed: "and you take glimepiride before breakfast; skipping" becomes "and you take glimepiride 2 mg before breakfast and metformin 1000 mg twice daily; skipping".
- **F1** before: "Diet plan on 2026-08-11: three regular meals a day, breakfast within 1 hour of waking; take glimepiride before breakfast"
  after: "Diet plan: three regular meals a day, breakfast within an hour of waking; take glimepiride before breakfast"
- **F2** before: "Glimepiride 2 mg OD before breakfast and metformin 1000 mg BD, continued 2026-08-11"
  after: "Glimepiride 2 mg before breakfast and metformin 1000 mg twice daily"
- **F3** before: "Patient plans 16:8 fasting from 2026-09-20: eating 12 pm to 8 pm and skipping breakfast"
  after: "Plans 16:8 fasting: eating only 12 pm to 8 pm and skipping breakfast"
- **F4** unchanged.

### q42
- Gold: unchanged.
- **F1** before: "Diet plan on 2026-05-12: 3 meals and 2 small snacks a day, include millets, no sweets, walk 30 min daily"
  after: "Diet plan: three meals and two small snacks a day with millets"
- **F2** before: "Paryushana 8-day fast: boiled water during the day, no eating after sunset (planned for August 2026)"
  after: "Paryushana fast: boiled water during the day, no eating after sunset"
- **F3** before: "Not on any glucose-lowering medicine; prediabetes with HbA1c 6.0 %"
  after: "Not on any sugar-lowering tablet"

### q43
- Gold, added or changed: "keep salt low on a vegetarian diet that works around your night shifts" becomes "keep salt low on a vegetarian diet with no eggs that works around your night shifts".
- **F1** before: "Telmisartan 40 mg with hydrochlorothiazide 12.5 mg OD (combination tablet) since 2025-09-23"
  after: "Telmisartan with hydrochlorothiazide, a water tablet"
- **F2** before: "Works night shifts (10 pm to 7 am) and sleeps in the day; vegetarian, eats dairy but no eggs"
  after: "Works night shifts and sleeps in the day; vegetarian with no eggs"
- **F3** before: "Keto is a poor fit without medical advice: on a diuretic (hydrochlorothiazide), so dehydration and electrolyte risk; vegetarian with no eggs limits keto food choices; discuss first"
  after: "Keto is a poor fit without medical advice: on a diuretic (hydrochlorothiazide), so dehydration and electrolyte risk; discuss first"

### q44
- Gold, added or changed: "You had a low sugar episode in Ramadan 2025, and in Ramadan 2026 your glimepiride was cut to 1 mg with iftar and your sugar was checked before iftar and suhoor, with no low sugar." becomes "You had a low sugar episode in Ramadan 2025 while on glimepiride. In Ramadan 2026 your glimepiride was cut to 1 mg with iftar, your sugar was checked before iftar and suhoor with advice to come in or call if it fell below 70 mg/dL, and you had no low sugar.".
- **F1** before: "Low sugar episode in Ramadan March 2025 on glimepiride (sweating, shakiness), resolved with sugar"
  after: "Low sugar episode in Ramadan March 2025 on glimepiride"
- **F2** unchanged.
- **F3** before: "Ramadan 2026 outcome: no hypoglycaemia; glimepiride back to 2 mg before breakfast on 2026-04-07"
  after: "Ramadan 2026 outcome: no hypoglycaemia"

### q48
- Gold: unchanged.
- **F1** before: "Insulin glargine (Lantus) 14 units at bedtime, increased on 2026-05-19 and continued 2026-08-18"
  after: "Insulin glargine (Lantus) 14 units at bedtime"
- **F2** before: "Metformin 500 mg BD, continued 2026-08-18"
  after: "Metformin 500 mg twice a day"
- **F3** before: "Telmisartan 40 mg OD, continued 2026-08-18"
  after: "Telmisartan 40 mg once a day"

## Checker result

Heuristic checker (`check_fact_gold.py`, numbers, dates and frequency tokens only) before this pass: 27 questions and 63 facts flagged. After: 0 of 48 questions and 0 of 143 facts flagged. The checker cannot see wording such as OD against once daily, or a missing symptom or reason, so every question was also checked by hand against the records, as described above.

## Needs clinician decision

1. **q24, fact 3 conflicts with the gold plan and with the record.** Fact 3 (from D02-m04) says lipids are reviewed and treatment is adjusted when LDL stays above the target of below 55 mg/dL. The gold answer, and the last note (P14-m13, 16 June 2026), keep atorvastatin 80 mg and ezetimibe 10 mg unchanged with LDL at 74 mg/dL. Left as it was. Either the gold should say that treatment is reviewed, or fact 3 should be reworded to match the plan.
2. **q21, fact 1, and q22 gold: plan format.** D01-m01 says the plan is a numbered list with one action per line. The gold writes the plan inline, as "(1) ...; (2) ...". The D01-m07 example note is also inline. Left as it was. Fact 1 of q21 still asks for one action per line, so the gold does not meet it. Decide whether to reformat the plans in q21 and q22, and which format the memory means.

## Checked and left unchanged

- q23 and q31: "next echocardiogram due June 2027" is not stated in any record. It follows from D02-m05 (echo every 12 months) after the echo of 23 June 2026, so it is consistent with the records.
- q38: "Combiflam contains ibuprofen" is general product knowledge, and no record mentions Combiflam.
- q40: "a gap of a few hours" between an antacid and propranolol is general advice; no record covers it.
- q35, fact 1: "renal function test" and the gold's "kidney blood test" mean the same thing.
- Dates in the other facts stay in ISO form (for example 2026-04-14). That is the file's convention and means the same as the gold's "14 April 2026".


## Follow-up after the gold check (2026-10-13)

The judge graded every gold answer; 44 of 48 scored 3. The other four had a non-numeric detail in a fact that the gold answer doesn't state, so the fact was trimmed:

- q23 fact 3: "NYHA class II recorded at the 2026-06-23 review" → "NYHA class II"
- q37 fact 1: "On warfarin for permanent AF" → "On warfarin"
- q39 fact 1: "Heart failure with reduced ejection fraction" → "Heart failure"
- q43 fact 2: "Works night shifts and sleeps in the day; vegetarian with no eggs" → "Works night shifts; vegetarian with no eggs"

## Clinician decisions (2026-10-13)

1. Style requirements dropped: notes are graded on clinical content, not on the doctor's format. Style facts and style-only forbidden statements were removed from q21–q24 (the question text still asks for the doctor's format, so answers may follow it, but it isn't graded).
2. Dr Iyer's LDL target of below 55 mg/dL applies to patients with clinical coronary artery disease; patients without it can be treated at much higher LDL levels. D02-m04 now says so. Mr Patel (P14) has coronary artery disease, so with LDL still 74 mg/dL his treatment needs stepping up: q24's gold answer and fact were changed accordingly, and q14's gold answer names the reason for the target.

D02-m04 before: "Dr Iyer prescribes a high-intensity statin for every patient with coronary artery disease. His LDL target is below 55 mg/dL, and he reviews the lipid profile and adjusts treatment when LDL stays above that target."

D02-m04 after: "Dr Iyer prescribes a high-intensity statin for every patient with coronary artery disease. For patients with clinical coronary artery disease his LDL target is below 55 mg/dL, and he reviews the lipid profile and adjusts treatment when LDL stays above that target. For patients without coronary artery disease he accepts much higher LDL levels before starting or intensifying lipid-lowering treatment."

- q21: removed style fact "Writes notes under S/O/A/P headings with a numbered plan (one action per line) in standard abbreviations such as k/c/o, OD and R/V"
- q21: removed style fact "Ends every note with an Adv: line of diet and exercise advice, then R/V in N months, even at routine reviews"
- q21: removed style fact "Writes the generic name first with the brand in brackets on first mention, e.g. levothyroxine (Thyronorm) 75 mcg OD"
- q22: removed style fact "Writes notes under S/O/A/P headings with a numbered plan in standard abbreviations (k/c/o, BD, OD, R/V)"
- q22: removed style fact "Ends with an Adv: line of diet and exercise advice, then R/V in N months"
- q23: removed style fact "Narrative paragraphs in full sentences with no abbreviations; drug frequencies written in words such as twice daily"
- q23: removed style fact "Ends with the sentence 'Plan discussed with the patient and family.' as the last line"
- q24: removed style fact "Narrative paragraphs in full sentences with no abbreviations; drug frequencies written out in words"
- q24: removed style fact "Ends with the sentence 'Plan discussed with the patient and family.'"
- q21: removed style forbidden statement "Writes the note as narrative paragraphs with no abbreviations, or ends with 'Plan discussed with the patient and family.' (Dr Iyer's style)"
- q21: removed style forbidden statement "Uses PRESENTING COMPLAINT, EXAMINATION, IMPRESSION and ARRANGEMENTS headings (Dr Natarajan's style)"
- q23: removed style forbidden statement "Uses S/O/A/P headings or the Adv: and R/V lines (Dr Mehta's style)"
- q23: removed style forbidden statement "Uses abbreviations such as BD or OD in the note text"
- q24: removed style forbidden statement "Uses S/O/A/P headings or abbreviations such as BD and OD (Dr Mehta's style)"
- q24 gold: "Mr Patel attends with no angina today, and his blood pressure is 126/78 mmHg. His low-density lipoprotein cholesterol was 74 mg/dL on 16 June 2026, the lowest result so far, but it remains above his target of below 55 mg/dL, so he continues atorvastatin 80 mg once daily and ezetimibe 10 mg once daily, alongside aspirin 75 mg once daily, ramipril 5 mg once daily and metoprolol 50 mg once daily. A fasting lipid profile is booked before his consultation on 4 November 2026. Plan discussed with the patient and family." → "Mr Patel attends with no angina today, and his blood pressure is 126/78 mmHg. He has coronary artery disease, with a heart attack and a stent in 2024, so his target low-density lipoprotein cholesterol is below 55 mg/dL. His last result was 74 mg/dL on 16 June 2026, the lowest so far but still above that target, so his lipid-lowering treatment needs to be stepped up beyond atorvastatin 80 mg once daily and ezetimibe 10 mg once daily, guided by the fasting lipid profile booked before his consultation on 4 November 2026. He continues aspirin 75 mg once daily, ramipril 5 mg once daily and metoprolol 50 mg once daily. Plan discussed with the patient and family."
- q24 fact (was 3, now 1): "LDL target below 55 mg/dL; lipids reviewed and treatment adjusted when LDL stays above that target" → "Has coronary artery disease, so his LDL target is below 55 mg/dL; with LDL still above it, lipid-lowering treatment needs to be stepped up"; memory_ids + P14-m02 (heart attack and stent, 2024)
- q24 forbidden: added "States his lipid-lowering treatment can stay unchanged"
- q14 gold: target sentence now says the < 55 mg/dL target applies because he has coronary artery disease

## Pilot-stage fixes (2026-10-13)

The pilot showed two grading problems. Cohort answers that correctly mentioned patients outside the target list were marked as hallucination, because the judge only saw the target patients' records. And the judge marked facts partial for leaving out details that don't change what the asker would do. Changes:

- q17: judge_patient_ids = all 20 core patients (the judge sees every record a correct answer may draw on)
- q18: judge_patient_ids = all 20 core patients (the judge sees every record a correct answer may draw on)
- q19: judge_patient_ids = all 20 core patients (the judge sees every record a correct answer may draw on)
- q20: judge_patient_ids = all 20 core patients (the judge sees every record a correct answer may draw on)
- q33 fact 1: "Review rescheduled from 21 October to 28 October 2026 at 9:00 am with Dr Mehta, by phone on 1 October 2026" → "Review rescheduled from 21 October to 28 October 2026 at 9:00 am with Dr Mehta"
- q25 fact 3: "Coronary angiography advised; referral to Narayana Health catheterisation laboratory on 2026-01-09" → "Coronary angiography advised, with referral to the Narayana Health catheterisation laboratory"
- q37 fact 3: "Do not take ibuprofen: on warfarin, so NSAIDs raise bleeding risk; check with the doctor (paracetamol is usually the safer option)" → "Do not take ibuprofen: on warfarin, so NSAIDs raise bleeding risk; check with the doctor"
- Judge rubric: omitting a detail that wouldn't change what the asker does is not 'partial'.
