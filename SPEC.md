# Build spec (for every builder)

Read `DESIGN.md` for the why. This file is the what: data formats and module contracts.
Deviate from it only if it is impossible to follow, and say so in your final report.

Project root: the repository root (called ROOT below).
Python 3.10, venv at `ROOT\.venv` (run `ROOT\.venv\Scripts\python.exe`). Installed: anthropic 1.13,
fastembed 0.9, numpy, pandas, scipy, matplotlib, pytest. **Add no other dependencies.**
Run modules as `python -m src.<module>` from ROOT. In code, resolve paths from
`ROOT = Path(__file__).resolve().parent.parent`. Constants live in `src/config.py` and the
system-prompt text in `src/prompts.py`; import them and don't redefine them.

---

## 1. World data

### Patient file: `world/core/P01.json` (core) or `world/distractors/X0001.json` (distractor)
```json
{
  "patient_id": "P01",
  "name": "Sunita Rao",
  "age": 58,
  "sex": "F",
  "primary_doctor": "D01",
  "conditions": ["Type 2 diabetes", "Hypertension"],
  "memories": [
    {
      "memory_id": "P01-m01",
      "date": "2025-04-08",
      "type": "visit_note",
      "author": "D01",
      "text": "S: k/c/o T2DM x 6 yrs, HTN. c/o ..."
    }
  ]
}
```

### Doctor file: `world/core/D01.json`
```json
{
  "doctor_id": "D01",
  "name": "Dr Anjali Mehta",
  "specialty": "Internal Medicine and Diabetology",
  "memories": [
    {"memory_id": "D01-m01", "date": "2025-04-01", "type": "doctor_preference", "author": "D01", "text": "..."}
  ]
}
```

### Rules
- `memory_id` = `{owner_id}-m{NN}` (two digits, `m01`, `m02`, ...), numbered in date order.
- `date`: ISO `YYYY-MM-DD`, between 2025-04-01 and 2026-10-11. **Exception:** an `appointment` memory is dated the day it was booked or changed, and its text gives the appointment date, which may be in the future.
- `type`, one of:
  `visit_note`, `prescription`, `allergy`, `lab_result`, `investigation` (ECG, echo, treadmill test, X-ray, eye exam), `referral`, `referral_outcome`, `patient_context`, `doctor_preference`, `appointment`.
- `author`: a doctor ID (`D01`–`D06`) for clinician entries, `"patient"` for patient-reported context and app messages, `"lab"` for lab reports, `"front_desk"` for appointments.
- One memory = one atomic record: one visit, one prescription change, one lab report (which may hold several values from the same draw), one allergy, one referral, one appointment booking or change.
- `text` is what the record itself says. Write it as a real clinic record would read: visit notes in the treating doctor's style (see `world/ROSTER.md`), prescriptions as Rx lines, labs as report lines with units and reference ranges, patient context as a short intake note or quoted app message. 30–150 words per memory; prescriptions and labs are shorter.
- Core patients have 12–20 memories each; doctors have 5–7.

### Levels: `world/levels.json` (written later by `size_levels.py`)
```json
{"L1": {"distractors": [], "ctx_tokens": 35123}, "L2": {"distractors": ["X0001", "..."], "ctx_tokens": 95001}}
```
Levels are nested: each level's distractor list is a prefix of the next level's list. Doctors
D01–D06 are included at every level.

---

## 2. Questions: `questions/questions.json`
```json
[{
  "qid": "q01",
  "use_case": 1,
  "use_case_name": "Pre-visit summary",
  "asker_role": "doctor",
  "asker_id": "D01",
  "asker_name": "Dr Anjali Mehta",
  "target_patient_ids": ["P01"],
  "question": "What's changed for Mrs Rao since her April visit?",
  "gold_answer": "...",
  "required_facts": [{"fact": "Metformin stopped on 2026-04-14 because of GI upset", "memory_ids": ["P01-m12"]}],
  "forbidden_facts": ["States she is still on metformin"],
  "abstention": false,
  "safety_critical": false,
  "pilot": true
}]
```
`gold_memory_ids` = the union of `required_facts[*].memory_ids`. For cohort questions,
`target_patient_ids` lists every patient in the gold answer.

---

## 3. Modules and their contracts

### `src/world.py`
```python
def load_core() -> tuple[list[dict], list[dict]]          # (patients P01-P20, doctors D01-D06)
def load_distractors(ids: list[str]) -> list[dict]
def load_level(level: str) -> dict                       # {"patients": [...], "doctors": [...], "level": "L1"}
                                                         # if levels.json is missing, "L1" = core only; other levels raise
def all_memories(world: dict) -> list[dict]              # flat list; each memory gets added keys owner_id, owner_kind ("patient"/"doctor")
```

### `src/render.py`. Every arm renders from these functions (this is the fidelity control)
```python
def slug(name: str) -> str                               # "Sunita Rao" -> "sunita-rao"; "Dr Anjali Mehta" -> "anjali-mehta"
def patient_path(p) -> str                               # "patients/P01-sunita-rao.md"
def doctor_path(d) -> str                                # "doctors/D01-anjali-mehta.md"
def render_md(world) -> dict[str, str]                   # {relative_path: file_content}, includes "INDEX.md"
def render_index(world) -> str                           # same text as render_md(world)["INDEX.md"]
def render_ctx(world) -> str                             # the full store for the CTX arm
def render_units(world) -> list[dict]                    # one unit per memory, for EMB
def render_memories_md(memories: list[dict], world) -> str  # the given memories only, as CTX-style sections (ORACLE arm)
```
**Patient file** (`render_md`):
```
# Sunita Rao (P01)
58 F · Primary doctor: Dr Anjali Mehta (D01) · Conditions: Type 2 diabetes, Hypertension

## 2025-04-08 · Visit note · Dr Anjali Mehta (D01) <!-- m:P01-m01 -->
<text>

## 2025-04-08 · Lab result · Lab <!-- m:P01-m02 -->
<text>
```
**Doctor file:** `# Dr Anjali Mehta (D01): Internal Medicine and Diabetology`, then one
`## <date> · Doctor preference · Dr Anjali Mehta (D01) <!-- m:D01-m01 -->` section per memory.
Type labels: Visit note, Prescription, Allergy, Lab result, Investigation, Referral, Referral
outcome, Patient-reported, Doctor preference, Appointment. Author labels: the doctor's
name + ID, "Patient", "Lab", "Front desk". Memories appear in date order, then by ID.

**File order** (in the index and in CTX): doctors by ID first, then patients sorted by
`(slug(name), patient_id)`. Alphabetical order scatters the core patients among the
distractors, so the core isn't always at the top of the prompt.

**`INDEX.md`:**
```
# Record index: Sunrise Family Clinic
- doctors/D01-anjali-mehta.md: Dr Anjali Mehta (D01), Internal Medicine and Diabetology; documentation preferences
- patients/P01-sunita-rao.md: Sunita Rao (P01), 58 F, primary D01; Type 2 diabetes, Hypertension
```
**`render_ctx`:**
```
<memory_store>
<file path="doctors/D01-anjali-mehta.md">
...file content...
</file>
...
</memory_store>
```
**`render_units`:** a list of
`{"memory_id", "owner_id", "patient_id" (None for doctors), "doctor_id" (the patient's primary doctor, or the doctor themself), "date", "type", "text"}`.
`text` = a header line + `\n` + the memory text. Header for a patient memory:
`[P01-m01] 2025-04-08 · Visit note · Patient: Sunita Rao (P01, 58 F) · Author: Dr Anjali Mehta (D01)`.
Header for a doctor memory: `[D01-m01] 2025-04-01 · Doctor preference · Doctor: Dr Anjali Mehta (D01)`.

### `src/fidelity.py`
`check(world) -> list[str]` returns problems; `python -m src.fidelity --level L1` prints them
and exits non-zero if there are any. Checks: every memory ID appears **exactly once** in
`render_ctx`, exactly once across the `render_md` files (counting `<!-- m:ID -->` markers),
and exactly once in `render_units`; memory text is byte-identical in all three; IDs match
`^[PXD]\d{2,4}-m\d{2}$`; dates are valid and in the window; types and authors are valid; no
duplicate patient IDs.

### `src/embed_index.py`
```python
class EmbeddingStore:
    def __init__(self, cache_dir: Path = ROOT / "runs" / "emb_cache", model_name: str = config.EMBED_MODEL): ...
    def build(self, units: list[dict]) -> None   # embeds only units not already cached (key = memory_id + sha1(text)); saves .npy + ids json
    def search(self, query: str, k: int = 8, patient_id: str | None = None,
               allowed_ids: set[str] | None = None) -> list[dict]   # [{"memory_id", "score", "text"}], highest score first
```
Use `fastembed.TextEmbedding(model_name)`: `.embed()` for documents and `.query_embed()` for
queries. L2-normalise, and score by dot product (exact search, no approximation).
`allowed_ids` restricts the candidates to the current level's memory IDs. `patient_id`
filters to that patient's units (doctor units are excluded when filtering by patient).

### `src/tools.py`: tool definitions and executors
```python
EMB_TOOLS: list[dict]          # [search_memory]
MD_TOOLS: list[dict]           # [read_file, grep]
class EmbExecutor:  def __init__(self, store: EmbeddingStore, allowed_ids: set[str]) ; def __call__(self, name, tool_input) -> tuple[str, list[str]]
class MdExecutor:   def __init__(self, files: dict[str, str]) ; def __call__(self, name, tool_input) -> tuple[str, list[str]]
```
Each executor returns `(result_text, accessed_memory_ids)`.
- `search_memory(query: str, k: int = 8, patient_id: str = optional)`: k is clamped to 1–20. The result is the hits' `text` fields, each followed by `(relevance 0.873)`, separated by `\n---\n`; `"No matching records."` if there are none. Accessed = the hit IDs.
- `read_file(path: str)`: exact key into `files` (strip a leading `./` or `/`); otherwise `"Error: no such file: <path>. Check INDEX.md."`. Accessed = every `<!-- m:ID -->` in the file.
- `grep(pattern: str, path: str = optional)`: case-insensitive Python regex, or a literal search if the regex is invalid. Searches every file (or just `path` if given, which may be a file or a directory prefix like `patients/`). Output lines are `path:lineno: line`, at most 50, then `... N more matches not shown` if there are more; `"No matches."` if none. Accessed = for each matching line, the memory whose `<!-- m:ID -->` heading is nearest above it.
- Tool `description` texts: EMB 60–90 words; MD's two tools 30–45 words each. Each schema has `"additionalProperties": false` and a `required` list.

### `src/arms.py`
```python
@dataclass
class Arm:
    name: str                      # CTX | EMB | MD | ORACLE | FLOOR
    system: list[dict]             # Anthropic system text blocks; the LAST block carries {"cache_control": {"type": "ephemeral"}}
    tools: list[dict] | None
    execute: Callable[[str, dict], tuple[str, list[str]]] | None

def build_arm(name: str, world: dict, question: dict, emb_store: EmbeddingStore | None = None) -> Arm
```
`system` = `[{"type":"text","text": prompts.BASE}, {"type":"text","text": prompts.ARM_<NAME> + payload, "cache_control": {"type":"ephemeral"}}]`,
where the payload is `render_ctx(world)` for CTX, `render_index(world)` for MD,
`render_memories_md(gold memories)` for ORACLE, and empty for EMB and FLOOR. Build the EMB
executor with `allowed_ids` = the memory IDs in this world.

### `src/pricing.py`
```python
def turn_cost(turn: dict, scenario: str) -> float   # USD; turn has input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens
def run_cost(turns: list[dict], scenario: str) -> float
SCENARIOS = ("observed", "uncached", "cached_cold", "cached_warm")
```
Per turn: `prompt = input + cache_write + cache_read`. The rate card is `config.PRICE_SMALL` if
`prompt <= config.PRICE_BOUNDARY_TOKENS`, otherwise `config.PRICE_LARGE`. With `ri`/`ro` the
input/output $ per MTok:
- observed = `input*ri + cache_write*1.25*ri + cache_read*0.1*ri + output*ro`
- uncached = `prompt*ri + output*ro`
- cached_warm = `input*ri + (cache_write + cache_read)*0.1*ri + output*ro`
- cached_cold = `input*ri + (cache_write + cache_read)*1.25*ri + output*ro`

All divided by 1e6.

### `src/harness.py`: the agent loop (see section 4)
### `src/judge.py`, `src/analyze.py`: see their builder briefs.
### `src/gen_distractors.py`, `src/size_levels.py`: see their builder brief.

---

## 4. Agent loop (harness)

> **Updated 2026-10-12:** the API is now OpenRouter (OpenAI-compatible) through `src/llm.py`,
> which is the only module that talks to the API. The Anthropic-specific call details below are
> superseded by `llm.chat(client, role, messages, tools, response_format)`. Usage is mapped as
> input = prompt − cached − cache_write, cache_read = cached, cache_creation = cache_write,
> output = completion (which includes reasoning). The run record format and loop rules are unchanged.
>
> **Updated 2026-10-13:** the registered profile is `config.PROFILE = "free"` (OpenRouter free
> endpoints). `llm.chat` retries transient errors itself (the SDK's retries are off), holds each
> process to `config.REQUESTS_PER_MINUTE`, and raises `llm.DailyLimitReached` on the free-model
> daily cap; the harness, judge and generator then stop without recording the interrupted item and
> exit with code 3. Each turn record adds `cacheable_prefix_tokens` (turn 1: prompt tokens minus an
> estimate of the user turn; later turns: the previous turn's prompt tokens), which `src/pricing.py`
> uses for the modelled cached-warm and cached-cold prices. `python -m src.smoke` checks each role
> live with a handful of requests.

```python
def run_question(client, arm: Arm, question: dict, level: str, repeat: int) -> dict
```
- The user turn is `prompts.user_turn(question)`.
- Each request: `client.messages.stream(model=config.SUBJECT_MODEL, max_tokens=config.MAX_TOKENS, output_config={"effort": config.EFFORT}, system=arm.system, messages=messages, cache_control={"type": "ephemeral"}, **({"tools": arm.tools} if arm.tools else {}))` used as a context manager, then `resp = stream.get_final_message()`.
  **Never** pass `temperature`, `top_p`, `top_k` or `thinking`; Haiku 5.5 rejects them or doesn't need them.
- Read content blocks **by `.type`**: the response may start with `thinking` blocks.
- When `stop_reason == "tool_use"`: append `{"role": "assistant", "content": resp.content}` **unchanged** (thinking blocks included), run every `tool_use` block, and append **one** user message holding all the `tool_result` blocks (`{"type": "tool_result", "tool_use_id": ..., "content": text}`).
- The tool budget is `config.MAX_TOOL_CALLS` (8). Once spent, each further tool call gets the result `prompts.BUDGET_EXHAUSTED` with `"is_error": True`, and the executor is not called. **Do not** change `tools`, `tool_choice` or `system` mid-run (that would break the cache and the thinking-block checks).
- The hard stop is `config.MAX_TURNS` (12) model calls, giving status `"turn_limit"`.
- Statuses: `ok` (end_turn with text), `refusal`, `max_tokens`, `turn_limit`, `error` (exception; store `repr`).
- Record every turn: `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens` (None → 0), `stop_reason`, `latency_s`, `request_id` (`resp._request_id`).
- Record every tool call: `name`, `input`, `result_chars`, `accessed_ids`, `is_error`.
- The run record (one JSON line):
```json
{"run_id": "CTX-L1-q01-r1", "arm": "CTX", "level": "L1", "qid": "q01", "repeat": 1,
 "model": "claude-haiku-5-5", "effort": "medium", "started_at": "ISO", "status": "ok",
 "final_text": "...", "turns": [...], "tool_calls": [...],
 "totals": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0, "prompt_tokens": 0, "n_turns": 0, "n_tool_calls": 0},
 "accessed_memory_ids": [], "latency_s": 0.0, "error": null}
```
**CLI:** `python -m src.harness --arms CTX,EMB,MD --levels L1,L3,L5 --qset pilot|all --repeats 1 --out runs/pilot.jsonl --workers 4`
- **Resumable:** skip any `run_id` already in `--out` with status `ok`.
- **CTX ordering:** for each (level, repeat), run that level's CTX questions **sequentially in one thread**, so the cached store prefix stays warm. Different levels may run in parallel. EMB and MD runs may go fully in parallel (`--workers`).
- Use `anthropic.Anthropic(max_retries=6)`. Append each record as soon as it finishes (thread-safe), so a crash loses nothing.
- **Dry run:** `--dry-run` uses `FakeClient` from `tests/fakes.py` and makes no API calls.
