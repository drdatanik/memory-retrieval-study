# Context vs embeddings for clinical memory: where is the crossover?

**Pre-registration.** Written 2026-10-12, before any data was generated or any model was run.
Anything changed after that date goes in the deviations log at the bottom, with a reason.

Derived from `memory-retrieval-experiment.md` (Nikhil Saldanha, Oct 2026). The changes from
that design, and why, are listed in section 9.

---

## 1. Question

An LLM agent (DeepSeek V4.1 Flash, open weights) answers doctor and patient questions from a
clinic's stored memory. As the store grows, **at what size does retrieval by embeddings match
or beat putting the whole store in the prompt** on:

1. answer quality,
2. tokens processed, and
3. cost, priced with and without prompt caching?

## 2. Subject model and fixed settings

| Setting | Value |
|---|---|
| Model | `deepseek/deepseek-v4.1-flash` via OpenRouter (open weights; 1,048,576-token context window) |
| Provider | pinned to DeepInfra (fp8), no fallbacks, so quantisation and caching don't vary between runs. Automatic prefix caching checked live: a repeated 40,509-token prompt read 40,448 tokens from cache |
| Reasoning | `effort: medium` (OpenRouter's unified reasoning parameter); reasoning details passed back unchanged across tool turns |
| Temperature | 0 |
| `max_tokens` | 16,000 |
| "Today" | 2026-10-12, stated in the system prompt |
| Asker | role (doctor/patient), ID and name, stated in the user turn |
| Tool budget | 8 tool calls. After that, every further call returns "budget exhausted, answer now". |
| Access control | none. The agent *can* read any patient. Leakage is measured, not prevented. Production systems must enforce isolation at the storage layer whatever this shows. |

The system prompt is identical across arms apart from one arm-specific block (how memory is
provided and how to use the tools), and those blocks are kept within ±10% of each other in length.

## 3. Arms

| Arm | What the model gets | Tools |
|---|---|---|
| **CTX** (full context) | The whole store in the system prompt, rendered as Markdown. The provider caches the prompt prefix automatically. | none |
| **EMB** (embeddings) | Nothing up front | `search_memory(query, k=8, patient_id?)` returns the top-k memories with ID, score and metadata header |
| **MD** (Claude Code style) | `INDEX.md` in the system prompt, one line per file | `read_file(path)`, `grep(pattern, path?)` |
| ORACLE (calibration) | Only the gold memories for the question | none |
| FLOOR (calibration) | No memory | none |

CTX and MD are rendered from the same Markdown files, so they hold identical content; EMB holds
the same memory units. Embeddings run locally with `BAAI/bge-base-en-v1.5` (fastembed), with
exact cosine search and one vector per memory unit.

**Caching changes the price, not what the model reads.** Each arm runs once, with the provider's
automatic prefix caching on (questions at a level run back to back, so the cache stays warm).
Cost is priced from the logged token counts with the price card in section 7, alongside the
actual charge OpenRouter reports. So that the scenarios don't depend on what the provider
happened to cache, every turn logs its cacheable prefix:
for the first turn, everything before the user turn (system prompt, store or index, tool
definitions), which is identical for every question at a level; for later turns, the whole
previous prompt. Pricing scenarios:

- **uncached:** every prompt token at the base rate;
- **cached-cold:** each question writes its first-turn prefix to the cache (one question per cache window); later turns of the same question read the previous prompt from the cache;
- **cached-warm:** steady traffic: every turn reads its cacheable prefix from the cache;
- **observed:** what the endpoint actually cached, as logged, and the **actual** OpenRouter charge.

## 4. Store sizes (the independent variable)

**Core:** 20 patients and 2 doctors (D01, D02), about 300 memory units over 18 months, with
planted traps (`world/ROSTER.md`). Every question is about the core.

**Distractors:** fictional patients under doctors D03–D06, added as nested sets: each level
contains all smaller levels. Distractors never mention core patients or D01/D02, so the gold
answers do not change across levels.

| Level | Target CTX prompt size | Why |
|---|---|---|
| L1 | core only (40,501 tokens) | a realistic single-clinic panel |
| L2 | ~100k | roughly doubling per level |
| L3 | ~200k | |
| L4 | ~400k | |
| L5 | ~800k | under the 1M context window, with room for the answer |

Sizes are measured locally with the subject model's own tokenizer (`deepseek-ai/DeepSeek-V4.1-Flash`).
Measured sizes (`world/levels.json`): L1 40,501; L2 99,513 (33 distractor patients); L3 198,497 (90);
L4 399,242 (209); L5 798,154 (436), out of 600 generated.

## 5. Questions

48 questions: 12 use cases × 4, using the use-case table in the source design. At least 6 can't
be answered from memory; for those the correct answer is "no record". Each question has:
asker, question text, gold answer, required facts (each linked to gold memory IDs), forbidden
facts (stale values, the other R. Sharma's values), an abstention flag and a safety-critical
flag. The questions are frozen before the pilot. The clinician (author) approves gold answers.

## 6. Metrics

**Primary: quality**, 0–3 per answer. 3 = all required facts, 2 = most, 1 = some, 0 = wrong
or none. **Leakage forces a 0.**

**Secondary**
- Hallucination, safety-critical miss, leakage and abstention pass (all binary).
- Tokens per question, summed over turns: uncached input, cache write, cache read and output (including thinking).
- Cost per question under each pricing scenario.
- Latency.
- For EMB and MD: recall and precision of gold memory IDs, wrong-patient memories in context, and number of tool calls.

**Judge:** `nvidia/nemotron-3-super-120b-a12b:free` (open weights) via OpenRouter, pinned to
NVIDIA's free endpoint, temperature 0, structured JSON output. It is a different model family
from the subject. It is blinded to arm and sees no tool traces. **Gold check:** before the pilot, the judge grades every
gold answer; each must score 3, or its required facts are fixed first.
It sees the question, the asker, the gold answer, the required and forbidden facts, the target
patient's full record (taken from the world-state, not from any arm) and the response, and
returns structured JSON. The clinician hand-scores a random 20% blind; Cohen's κ is reported,
and if κ < 0.6 the rubric is fixed before the judge is trusted.

## 7. Pricing used for cost

The subject is paid; the judge and distractor writer run on a free endpoint (actual charge $0,
cost modelled with the same model's paid card). Read from OpenRouter on 2026-10-12; re-check
before publishing. USD per million tokens:

| Role | Model / provider | Input | Output | Cached input read |
|---|---|---|---|---|
| Subject | DeepSeek V4.1 Flash / DeepInfra fp8 | 0.14 | 0.42 | 0.0042 |
| Judge (modelled) | `nvidia/nemotron-3-super-120b-a12b` | 0.08 | 0.45 | none listed (priced as input) |

Cache writes are priced at the input rate. There is no price step by prompt length.

## 8. Hypotheses (pre-registered)

- **H1 Tokens.** EMB and MD process fewer tokens per question than CTX at every level.
- **H2 Cost.** Uncached, EMB is cheaper than CTX at every level. With a warm cache (reads about 33× cheaper than uncached), CTX is cheaper than EMB at L1 and L2, and the cached-cost curves cross between L2 and L4.
- **H3 Quality.** At L1, CTX ≥ EMB. CTX quality falls as the store grows, and EMB falls less. If the quality curves cross, they cross at L3 or above.
- **H4 Leakage.** Leakage on the near-duplicate (R. Sharma) questions rises with store size in CTX and EMB.
- **H5 Markdown.** At L1, MD quality ≈ CTX quality, at fewer tokens.
- **Per use case** (descriptive only, n = 4 each): EMB does better on lay-language questions; CTX does better on cohort, trend and medication-reconciliation questions.

## 9. Changes from the source design

| Source design | Here | Why |
|---|---|---|
| Three retrieval layers at a fixed size | Store size is the variable; the scale arm became the whole design | The question is the crossover point |
| SQL arm | Dropped | Not part of the question |
| Markdown arm | Kept, in the form Claude Code uses (index in context, files read on demand) | It is the realistic "Claude memory" baseline |
| Model under test unspecified (Claude implied) | DeepSeek V4.1 Flash, open weights, via OpenRouter | Cost: about $5 for the study. The finding is about this model |
| Prompt caching off | Cost modelled from logged tokens and cacheable prefixes; priced three ways, plus what the endpoint actually cached | Caching changes price, not content; one run gives every price |
| Judge from another model family | NVIDIA Nemotron 3 Super (free), a different family from the subject | Kept. Also blinded, with a facts rubric, the gold check and clinician κ |
| Synthetic data from a different model | Core written by Claude Haiku; distractors by Nemotron 3 Super | The core, which every question targets, is from a different family than the subject. Clinician reviews 4 of the 20 core patients before freezing |
| pgvector / Supabase | Local exact cosine search | Exact search removes an approximation confound; nothing leaves the machine |

## 10. Analysis plan

1. Average repeats within each question first. Then, per level × arm: mean quality with a 95% bootstrap CI over questions, median tokens with IQR, and mean cost per scenario.
2. **Quality crossover.** Per question, d(L) = quality(EMB) − quality(CTX). Mean d per level with a bootstrap CI. The crossover is the store size at which mean d crosses 0, interpolated linearly in log(tokens), with a CI from a bootstrap over questions. If d never crosses, report "no crossover within 35k–800k".
3. **Cost crossover.** Fit tokens per question against store size per arm, apply the price card (section 7), and report the crossover size for each modelled pricing scenario, with the measured points overlaid.
4. Secondary tests at each level: Wilcoxon signed-rank for EMB vs CTX and MD vs CTX, Holm-corrected across levels × comparisons. Report effect sizes even when not significant.
5. Report raw counts of safety-critical misses and leaks per arm × level. Don't test them; any non-zero count is a finding.
6. Per use case: a heatmap of quality (use case × arm × level), labelled descriptive.
7. **Headline chart:** x = store size (log tokens); top panel = quality per arm with CIs; bottom panel = cost per question per arm and pricing scenario; vertical marker at the crossover.

## 11. Run order

1. Write the core world (20 patients, 2 doctors). Clinician reviews 4 patients. Freeze.
2. Write 48 questions with gold answers. Clinician approves. Freeze.
3. Render; run the fidelity check (every memory ID present exactly once in every rendering).
4. Generate distractors; size the levels with the subject's local tokenizer. Smoke-test each model role, including one L5-sized prompt.
5. **Pilot:** 12 questions (one per use case) × CTX/EMB/MD × L1, L3, L5 × 1 run each. Fix tool or prompt problems now, never during the main run.
6. **Main run:** 48 × 3 arms × 5 levels × 1 run each, plus ORACLE and FLOOR × 48 × 1. No repeats: at temperature 0 a rerun of the same question gives nearly the same answer, so the 48 questions, not repeats, carry the variance; the CIs are over questions.
7. Judge, then clinician hand-scoring and κ.
8. Analysis and charts.

## 12. What the write-up can and can't claim

**Can claim:** for these question types and store sizes, with DeepSeek V4.1 Flash, arm X gave quality Y at
Z tokens and W dollars, with these failure modes, and the curves cross (or don't) at size S.

**Can't claim:** that any of this is safe for real patients, that it holds for other models or
embedding models, or that it holds beyond the store sizes tested. All patients are synthetic.

---

## Deviations log

Changes to this pre-registration. All entries below were made **before any data was collected**.

| Date | Change | Reason |
|---|---|---|
| 2026-10-12 | Subject changed from Claude Haiku 5.5 to DeepSeek V4.1 Flash; judge from Claude Opus 5.5 to DeepSeek V4 Pro; both via OpenRouter with pinned providers. Temperature 0 restored. Levels sized with the DeepSeek tokenizer; L2 target 95k → 100k. H2 restated (no 100K price step on this model). Batch judging dropped (no batch API). | Author's decision on cost: estimated main run ~$14 instead of ~$58. |
| 2026-10-13 | All roles moved to OpenRouter free endpoints: subject Thinking Machines Inkling (`inkling:free`, NVFP4, 1M context), judge NVIDIA Nemotron 3 Super (`:free`), distractor generator Google Gemma 4 31B (`:free`). Levels sized with the Inkling tokenizer (L1 = 40,766 tokens). Cost is now modelled from logged tokens and a per-turn cacheable prefix, priced with each model's paid card; cached-cold redefined accordingly; the actual-charge crossover dropped (all $0). H2 restated for the new card. Main run 2 repeats → 1 (temperature 0; the daily request cap). Requests limited to 18 per minute; runs stop at the daily cap and resume the next day. The DeepSeek setup is kept in `src/config.py` as a fallback profile. | Author's decision: $0 model cost. Made before any data was collected. |
| 2026-10-13 | After the live smoke test: subject Inkling → NVIDIA Nemotron 3 Ultra (`:free`, 1M context), because Inkling's free endpoint refuses requests from outside its listed apps (403). Generator Gemma 4 31B → Nemotron 3 Super (`:free`), because Gemma's free endpoint has no JSON-schema output (404). Levels sized with the Nemotron 3 Ultra tokenizer (L1 = 45,339 tokens). Subject price card → Nemotron 3 Ultra's paid card; H2's cache ratio restated (5×). Judge is now the same family as the subject (rationale in §6). Gold check added: every gold answer must score 3 with the judge before the pilot. Judge rubric: equivalent wording and dose abbreviations count as present. Required facts realigned with their gold answers (questions/ALIGNMENT.md), because the judge scored q01's own gold answer below 3. | Found by the smoke test, before any data was collected. |
| 2026-10-13 | Judge and distractor generator fixed to Nemotron 3 Super (`:free`) whichever subject is used; the subject is a separate one-line choice in `src/config.py` (`SUBJECT`), to be settled before the pilot. One run per (arm, level, question), no repeats, in the pilot and the main run. | Author's decision on cost. Before any data was collected. |
| 2026-10-13 | Subject set to DeepSeek V4.1 Flash (paid, pinned to DeepSeek), with the judge and distractor writer staying on free Nemotron 3 Super, so judge and subject are again different families. Levels sized with the DeepSeek tokenizer (L1 = 40,472 tokens). Subject price card and H2 back to the DeepSeek values; runs scheduled outside DeepSeek's weekday peak-price hours. | Author's decision: about $5 and no daily cap for the subject. Before any data was collected. |
| 2026-10-13 | Subject provider DeepSeek first-party → DeepInfra fp8 (same model; caching verified live), because the account's privacy settings exclude endpoints that may train on prompts. Price card → DeepInfra (0.14 / 0.42 / 0.0042); H2's cache ratio ~33×. Generator reasoning capped at 2,000 tokens (uncapped it sometimes ran out of tokens; off, it ignored the memory count). Judge asks again, up to 3 times in all, when the free endpoint returns no JSON. | Found by the smoke test, before any data was collected. |
| 2026-10-13 | Clinician decisions: (1) style requirements dropped, so notes (use case 6, q21–q24) are graded on clinical content only; style facts and style-only forbidden statements removed. (2) Dr Iyer's LDL target of below 55 mg/dL applies to clinical coronary artery disease (D02-m04 now says so); q24's gold answer now steps up Mr Patel's lipid treatment, and q14 names the reason for his target. Gold check rerun: 48/48 score 3. Details in questions/ALIGNMENT.md. | Clinician review, before any data was collected. |
| 2026-10-13 | **Freeze.** The clinician approved the review pack (core world and all 48 questions). World, questions, prompts and tools are frozen from here; the pilot starts. | Run order step 5. |
| 2026-10-13 | **Pilot-stage fixes** (after the pilot, before the main run; prompts, tools and the subject are unchanged). Judge: cohort questions (q17–q20) now show it all 20 core patients' records, because correct mentions of non-target patients were being marked as hallucination; rubric adds that leaving out a detail that wouldn't change what the asker does isn't "partial". Facts trimmed of incidental details: q33 F1 (when the appointment was changed), q25 F3 (referral date), q37 F3 (paracetamol aside). Gold check rerun, pilot re-graded with the fixed judge. | The pilot exists to catch these. Details in questions/ALIGNMENT.md. |
| 2026-10-13 | **After the main run (post hoc, reported as such).** Abstention pass is now computed from the judge's correctness score (pass = 3, which the rubric gives only to a response that clearly says there is no record and invents nothing) instead of the judge's separate `abstained` flag. On the main run the flag was False for 46 of 101 responses the judge itself scored 3 for doing exactly that. Both definitions are reported. ORACLE and FLOOR were run at L1 only (they don't depend on store size). | The flag contradicted the judge's own scores. |
| 2026-10-17 | **Clinician κ check deferred.** The blind 20% hand-scoring sample was generated (`results/human_sample.csv`) but not scored, so no human–judge agreement is reported. Results are published with that stated as a limitation; the judge's checks are the gold check (48/48 scored 3) and the reading of its pilot grades. | Author's decision: the scoring sheet didn't give enough context to score reliably; to be revisited. |
