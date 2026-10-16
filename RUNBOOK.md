# Runbook

Run these from this folder, using `.venv\Scripts\python.exe` as `python`. Every step resumes
where it stopped, so a crashed or capped step can simply be rerun with the same command.

**Models.** The model under test is DeepSeek V4.1 Flash (paid, served by DeepInfra). The grader and the distractor
writer are NVIDIA Nemotron 3 Super on OpenRouter's free endpoint.

**Prerequisites**
- `OPENROUTER_API_KEY` set as a user environment variable, then the terminal (or the Claude app) restarted.
- At least **$10 of credit bought, all time**, on the OpenRouter account. It pays for DeepSeek and lifts
  the free-model cap from 50 to 1,000 requests a day (the free steps need about 1,650).
- The key's own credit limit (openrouter.ai → Keys) must not be $0; set it to about $15 or leave it blank.
- If free requests fail with "No endpoints found matching your data policy", allow free endpoints in
  openrouter.ai → Settings → Privacy. All data in this study is synthetic.

**Limits and timing**
- Free model (grader, distractors): 20 requests a minute (the code holds itself to 18) and 1,000 a
  day per account. At the daily cap a step stops with exit code 3 and keeps everything finished;
  rerun the same command the next day (the cap resets at 00:00 UTC, 05:30 IST).
- DeepSeek (subject, via DeepInfra): no cap, flat prices.
- Run one step at a time.

| # | Step | Command | Free requests | DeepSeek cost (est.) |
|---|---|---|---|---|
| 0 | Tests (offline) | `python -m pytest -q` | 0 | 0 |
| 1 | Smoke test: key, subject tools, judge JSON, one distractor patient | `python -m src.smoke` | ~3 | <$0.01 |
| 1b | Gold check: the judge grades every gold answer; all must score 3 | `python -m src.smoke --gold` | 48 | 0 |
| 2 | Generate distractor patients | `python -m src.gen_distractors --n 600 --workers 8` | ~700 | 0 |
| 3 | Size the 5 levels (local tokenizer, no API) | `python -m src.size_levels` | 0 | 0 |
| 4 | Fidelity check at the largest level | `python -m src.fidelity --level L5` | 0 | 0 |
| 5 | Smoke test the 800k-token prompt (shows whether caching works) | `python -m src.smoke --only subject --long L5` | 0 | ~$0.13 |
| 6 | **Pilot:** 12 questions × CTX/EMB/MD × L1, L3, L5 | `python -m src.harness --arms CTX,EMB,MD --levels L1,L3,L5 --qset pilot --repeats 1 --out runs/pilot.jsonl` | 0 | ~$0.50 (≤$2.50) |
| 7 | Judge the pilot | `python -m src.judge --runs runs/pilot.jsonl --out results/judgments_pilot.jsonl` | ~110 | 0 |
| 8 | Analyse the pilot | `python -m src.analyze --runs runs/pilot.jsonl --judgments results/judgments_pilot.jsonl --out results/pilot/` | 0 | 0 |
| — | **Stop.** Review the pilot. Fix tools or prompts only here, and log any change in DESIGN.md. Freeze. | | | |
| 9 | Main run | `python -m src.harness --arms CTX,EMB,MD,ORACLE,FLOOR --levels L1,L2,L3,L4,L5 --qset all --repeats 1 --out runs/main.jsonl` | 0 | ~$3 (≤$15) |
| 10 | Judge | `python -m src.judge --runs runs/main.jsonl --out results/judgments_main.jsonl` | ~820 | 0 |
| 11 | Human sample for κ | `python -m src.judge --runs runs/main.jsonl --out results/judgments_main.jsonl --sample-for-human 0.2 --seed 7` | 0 | 0 |
| 12 | κ, after hand-scoring | `python -m src.judge --runs runs/main.jsonl --out results/judgments_main.jsonl --kappa results/human_sample_scored.csv --seed 7` | 0 | 0 |
| 13 | Analysis | `python -m src.analyze --runs runs/main.jsonl --judgments results/judgments_main.jsonl --out results/` | 0 | 0 |

About $4 in all if DeepSeek's caching works, and up to about $18 if it doesn't (the ≤ figures).
Step 5 shows which: on its second call `cached` should be close to the full prompt size.
Free requests total about 1,650, so two days: day 1, steps 1–8; day 2, steps 9–10.

**Notes**
- The grader's cost is modelled (it runs free): logged tokens × its paid card (DESIGN.md §7).
  The subject's cost is the actual OpenRouter charge, alongside the modelled pricing scenarios.
- Embedding the full store locally takes about 35 minutes on this CPU (once; cached in `runs/emb_cache`).
- The model under test is `SUBJECT` in `src/config.py` (`"deepseek-v4.1-flash"`; the free
  `"nemotron-3-ultra-free"` is kept as an alternative). Changing it means rerunning `size_levels` and
  logging it in DESIGN.md, before the pilot, not after.
