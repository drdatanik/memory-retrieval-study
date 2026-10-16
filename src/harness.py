"""Agent loop and CLI for the memory-retrieval study (SPEC section 4).

Model calls go through src.llm (OpenRouter, OpenAI-compatible chat completions). run_question() drives one
question through one arm and returns one run record (a JSON-serialisable dict). main() schedules runs for the
requested arms, levels and repeats, appends each record to a JSONL file as soon as it finishes, and skips runs
that already completed with status ok. When OpenRouter's daily free-model cap is hit, main() stops, records
nothing for the runs it cut short, and returns exit code 3; rerunning the same command resumes the study.

Run from the project root:
    python -m src.harness --arms CTX,EMB,MD --levels L1,L3,L5 --qset pilot --repeats 1 --out runs/pilot.jsonl --workers 4
    python -m src.harness --dry-run --arms CTX,EMB,MD --levels L1 --qset pilot
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from src import config, llm, prompts

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = ROOT / "questions" / "questions.json"
DEFAULT_OUT = ROOT / "runs" / "runs.jsonl"
DEFAULT_DRY_OUT = ROOT / "runs" / "dry_run.jsonl"

ARM_NAMES = ("CTX", "EMB", "MD", "ORACLE", "FLOOR")
USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "reasoning_tokens",
)
# OpenRouter finish_reason -> stop_reason recorded on each turn. A response with tool calls is always "tool_use".
STOP_REASON = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


# --------------------------------------------------------------------------- records


def make_run_id(arm_name: str, level: str, qid: str, repeat: int) -> str:
    return f"{arm_name}-{level}-{qid}-r{repeat}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _totals(turns: list[dict], tool_calls: list[dict]) -> dict:
    totals = {key: sum(t[key] for t in turns) for key in USAGE_KEYS}
    totals["prompt_tokens"] = (
        totals["input_tokens"]
        + totals["cache_creation_input_tokens"]
        + totals["cache_read_input_tokens"]
    )
    costs = [t["cost_usd"] for t in turns if t["cost_usd"] is not None]
    totals["cost_usd"] = sum(costs) if costs else None  # None only when no turn reported a cost
    totals["n_turns"] = len(turns)
    # Every tool call the model asked for and that was logged, including calls refused after the budget ran out.
    totals["n_tool_calls"] = len(tool_calls)
    return totals


def _new_record(arm_name: str, level: str, qid: str, repeat: int) -> dict:
    return {
        "run_id": make_run_id(arm_name, level, qid, repeat),
        "arm": arm_name,
        "level": level,
        "qid": qid,
        "repeat": repeat,
        "model": config.SUBJECT_MODEL,
        "effort": config.EFFORT,
        "provider": None,
        "temperature": config.ROLES["subject"]["temperature"],
        "started_at": _now_iso(),
        "status": "error",
        "final_text": "",
        "turns": [],
        "tool_calls": [],
        "totals": _totals([], []),
        "accessed_memory_ids": [],
        "latency_s": 0.0,
        "error": None,
    }


def _stop_reason(resp: llm.LLMResponse) -> str:
    if resp.tool_calls:  # some providers send finish_reason "stop" alongside tool calls
        return "tool_use"
    return STOP_REASON.get(resp.finish_reason, resp.finish_reason)


def _turn_record(resp: llm.LLMResponse, latency_s: float) -> dict:
    u = resp.usage
    return {
        # Uncached prompt tokens. The cached and cache-write parts are logged separately.
        "input_tokens": max(0, u["prompt_tokens"] - u["cached_tokens"] - u["cache_write_tokens"]),
        "cache_read_input_tokens": u["cached_tokens"],
        "cache_creation_input_tokens": u["cache_write_tokens"],
        "output_tokens": u["completion_tokens"],
        "reasoning_tokens": u["reasoning_tokens"],
        "cost_usd": u["cost_usd"],
        "provider": resp.provider,
        "finish_reason": resp.finish_reason,
        "stop_reason": _stop_reason(resp),
        "latency_s": round(latency_s, 3),
        "request_id": resp.request_id,
    }


def _cacheable_prefix_tokens(prompt_tokens: int, prev_prompt_tokens: int | None, user_text: str) -> int:
    """Prompt tokens a warm prefix cache would hold on this turn, estimated from the usage numbers.

    Turn 1: everything before the user turn (system prompt, store, tool definitions) is identical across questions
    at a level, so a warm prefix cache would hold it. The user turn is estimated at ~4 characters per token plus
    8 tokens of template overhead, an error of a few tokens.
    Turn k > 1: each request is the previous prompt plus appended messages, so the previous prompt is a cached prefix.
    """
    if prev_prompt_tokens is None:
        user_tokens = math.ceil(len(user_text) / 4) + 8
        return max(0, prompt_tokens - user_tokens)
    return min(prev_prompt_tokens, prompt_tokens)


# --------------------------------------------------------------------------- agent loop


def _dispatch(arm, call: dict, n_executed: int) -> tuple[str, list[str], bool, dict, bool]:
    """Decide what one tool call returns.

    Returns (content, accessed_ids, is_error, logged_input, executed). A call whose arguments did not parse is
    never executed and does not use the budget. A call past the budget gets BUDGET_EXHAUSTED without the executor.
    """
    if call["parse_error"] is not None:
        content = f"Error: could not parse tool arguments: {call['parse_error']}"
        return content, [], True, {"raw_arguments": call["raw_arguments"]}, False
    tool_input = dict(call["arguments"])
    if n_executed >= config.MAX_TOOL_CALLS:
        return prompts.BUDGET_EXHAUSTED, [], True, tool_input, False
    if arm.execute is None:
        return f"Error: no tools are available, so {call['name']!r} cannot run.", [], True, tool_input, False
    content, ids = arm.execute(call["name"], tool_input)
    return content, list(ids or []), False, tool_input, True


def run_question(client, arm, question: dict, level: str, repeat: int) -> dict:
    """Run one question through one arm and return the run record.

    Never raises for API or executor failures: they end the run with status "error". llm.DailyLimitReached is the
    exception: it propagates, so the caller can stop the batch, and no record is returned for that run.
    """
    rec = _new_record(arm.name, level, question["qid"], repeat)
    started = time.monotonic()
    turns: list[dict] = []
    tool_calls: list[dict] = []
    accessed: list[str] = []
    seen: set[str] = set()
    n_executed = 0
    last_text = ""
    provider: str | None = None
    prev_prompt_tokens: int | None = None
    user_text = prompts.user_turn(question)
    messages: list[dict] = [
        {"role": "system", "content": llm.system_text(arm.system)},
        {"role": "user", "content": user_text},
    ]
    status, error = "error", None

    try:
        while True:
            call_started = time.monotonic()
            resp = llm.chat(client, "subject", messages, tools=arm.tools)
            turn = _turn_record(resp, time.monotonic() - call_started)
            prompt_tokens = resp.usage["prompt_tokens"]
            turn["cacheable_prefix_tokens"] = _cacheable_prefix_tokens(prompt_tokens, prev_prompt_tokens, user_text)
            turns.append(turn)
            prev_prompt_tokens = prompt_tokens
            if resp.provider is not None:
                provider = resp.provider
            last_text = resp.text

            if resp.tool_calls:
                if len(turns) >= config.MAX_TURNS:
                    # The last allowed model call asked to continue; nothing more is sent.
                    status = "turn_limit"
                    break
                messages.append(resp.assistant_message)  # unchanged, so reasoning_details go back to the provider
                for call in resp.tool_calls:
                    content, ids, is_error, logged_input, executed = _dispatch(arm, call, n_executed)
                    if executed:
                        n_executed += 1
                    for memory_id in ids:
                        if memory_id not in seen:
                            seen.add(memory_id)
                            accessed.append(memory_id)
                    tool_calls.append(
                        {
                            "name": call["name"],
                            "input": logged_input,
                            "result_chars": len(content),
                            "accessed_ids": ids,
                            "is_error": is_error,
                        }
                    )
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                continue

            if resp.finish_reason == "stop":
                if resp.text.strip():
                    status = "ok"
                else:
                    status, error = "error", "stop with no text"
                break
            if resp.finish_reason == "length":
                status = "max_tokens"
                break
            if resp.finish_reason == "content_filter":
                status = "refusal"
                break
            status, error = "error", f"unexpected finish_reason: {resp.finish_reason!r}"
            break
    except llm.DailyLimitReached:
        raise  # a process-wide cap, not a result for this run: the caller stops and the run is redone later
    except Exception as exc:  # one bad run must not stop the batch
        status, error = "error", repr(exc)

    rec["status"] = status
    rec["error"] = error
    rec["final_text"] = last_text if status != "error" else ""
    rec["provider"] = provider
    rec["turns"] = turns
    rec["tool_calls"] = tool_calls
    rec["accessed_memory_ids"] = accessed
    rec["totals"] = _totals(turns, tool_calls)
    rec["latency_s"] = round(time.monotonic() - started, 3)
    return rec


# --------------------------------------------------------------------------- CLI helpers


def _load_questions(path: Path, qset: str) -> list[dict]:
    questions = json.loads(path.read_text(encoding="utf-8"))
    if qset == "pilot":
        questions = [q for q in questions if q.get("pilot") is True]
    return questions


def _load_done(path: Path, dry_run: bool) -> set[str]:
    """run_ids already finished with status ok, for this mode (real or dry run)."""
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line cut short by a crash
            if rec.get("status") == "ok" and bool(rec.get("dry_run")) == dry_run:
                done.add(rec["run_id"])
    return done


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as fh:
        fh.seek(-1, 2)
        return fh.read(1) == b"\n"


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


class _Sink:
    """Appends JSONL records under a lock, flushes each one, and prints one progress line per run."""

    def __init__(self, path: Path, total: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_newline = path.exists() and path.stat().st_size > 0 and not _ends_with_newline(path)
        self._fh = path.open("a", encoding="utf-8")
        if needs_newline:
            self._fh.write("\n")
            self._fh.flush()
        self._lock = threading.Lock()
        self._total = total
        self._finished = 0
        self.statuses: Counter = Counter()

    def write(self, rec: dict) -> None:
        line = json.dumps(rec, ensure_ascii=False, default=str)
        totals = rec.get("totals") or {}
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            self._finished += 1
            self.statuses[rec.get("status")] += 1
            note = ""
            if rec.get("status") == "error" and rec.get("error"):
                note = f"  error: {str(rec['error'])[:160]}"
            print(
                _ascii(
                    f"[{self._finished:>4}/{self._total}] {rec['run_id']:<28} {rec.get('status', ''):<11}"
                    f" prompt={totals.get('prompt_tokens', 0):>8} output={totals.get('output_tokens', 0):>6}"
                    f" {rec.get('latency_s', 0.0):7.1f}s{note}"
                ),
                flush=True,
            )

    def close(self) -> None:
        with self._lock:
            self._fh.close()


class _ArmCache:
    """Builds each arm once per (arm, level). ORACLE depends on the question, so it is keyed by qid too."""

    def __init__(self, build_arm, worlds: dict, emb_store):
        self._build_arm = build_arm
        self._worlds = worlds
        self._emb_store = emb_store
        self._arms: dict = {}
        self._lock = threading.Lock()

    def get(self, name: str, level: str, question: dict):
        key = (name, level, question["qid"]) if name == "ORACLE" else (name, level)
        with self._lock:
            arm = self._arms.get(key)
            if arm is None:
                arm = self._build_arm(name, self._worlds[level], question, self._emb_store)
                self._arms[key] = arm
            return arm


def _run_one(
    client, arms: _ArmCache, sink: _Sink, dry_run: bool, name: str, level: str, repeat: int, question: dict,
    stop: threading.Event,
) -> None:
    if stop.is_set():  # the daily cap was hit: this run is neither run nor recorded
        return
    try:
        arm = arms.get(name, level, question)
        rec = run_question(client, arm, question, level, repeat)
    except llm.DailyLimitReached:
        stop.set()  # nothing is written for this run; it is redone when the limit resets
        return
    except Exception as exc:  # e.g. an arm fails to build; record it and carry on
        rec = _new_record(name, level, question["qid"], repeat)
        rec["error"] = repr(exc)
    if dry_run:
        rec["dry_run"] = True
    sink.write(rec)


def _run_ctx_block(
    client, arms: _ArmCache, sink: _Sink, dry_run: bool, level: str, repeat: int, questions: list[dict],
    stop: threading.Event,
) -> None:
    """All CTX questions for one (level, repeat), run one after another so the cached store prefix stays warm."""
    for question in questions:
        if stop.is_set():
            return
        _run_one(client, arms, sink, dry_run, "CTX", level, repeat, question, stop)


def _parse_list(text: str, allowed: tuple, flag: str, parser: argparse.ArgumentParser, upper: bool = False) -> list[str]:
    items = [t.strip().upper() if upper else t.strip() for t in text.split(",") if t.strip()]
    bad = [t for t in items if t not in allowed]
    if bad:
        parser.error(f"{flag}: unknown {bad}; choose from {list(allowed)}")
    return list(dict.fromkeys(items))  # de-duplicate, keep order


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the memory-retrieval agent loop over arms, levels and questions.")
    parser.add_argument("--arms", default="CTX,EMB,MD", help="comma-separated: CTX, EMB, MD, ORACLE, FLOOR")
    parser.add_argument("--levels", default="L1,L2,L3,L4,L5", help="comma-separated levels, e.g. L1,L3,L5")
    parser.add_argument("--qset", choices=("pilot", "all"), default="all", help="pilot = questions with pilot true")
    parser.add_argument("--repeats", type=int, default=1, help="repeats per (arm, level, question)")
    parser.add_argument("--out", default=None, help="JSONL output (default runs/runs.jsonl, or runs/dry_run.jsonl with --dry-run)")
    parser.add_argument("--workers", type=int, default=4, help="thread pool size")
    parser.add_argument("--questions", default=str(QUESTIONS_PATH), help="questions JSON file")
    parser.add_argument("--dry-run", action="store_true", help="use tests.fake_openrouter.FakeOpenRouter; no API calls")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    arm_names = _parse_list(args.arms, ARM_NAMES, "--arms", parser, upper=True)
    levels = _parse_list(args.levels, tuple(config.LEVEL_TARGETS), "--levels", parser)
    if not arm_names or not levels:
        parser.error("--arms and --levels must each name at least one item")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    out = Path(args.out) if args.out else (DEFAULT_DRY_OUT if args.dry_run else DEFAULT_OUT)
    if not out.is_absolute():
        out = ROOT / out
    questions_path = Path(args.questions)
    if not questions_path.is_absolute():
        questions_path = ROOT / questions_path

    if not questions_path.exists():
        print(f"Questions file not found: {questions_path}", file=sys.stderr)
        return 1
    questions = _load_questions(questions_path, args.qset)
    if not questions:
        print(f"No questions in {questions_path} for --qset {args.qset}", file=sys.stderr)
        return 1

    # Created before the slow world and embedding builds, so a missing API key fails at once.
    if args.dry_run:
        from tests.fake_openrouter import FakeOpenRouter

        client = FakeOpenRouter()
    else:
        try:
            client = llm.make_client()
        except llm.LLMError as exc:
            print(f"Cannot make live model calls: {exc}", file=sys.stderr)
            return 1

    done = _load_done(out, dry_run=args.dry_run)

    # Imported here so run_question can be tested without the world, arm and embedding modules.
    from src import arms as arms_mod
    from src import embed_index, render, world

    worlds = {level: world.load_level(level) for level in levels}
    emb_store = None
    if "EMB" in arm_names:
        emb_store = embed_index.EmbeddingStore()
        for level in levels:  # build before any run starts: searches must not race with builds
            emb_store.build(render.render_units(worlds[level]))

    plan: list[tuple[str, str, int, dict]] = []
    skipped = 0
    for repeat in range(1, args.repeats + 1):
        for level in levels:
            for name in arm_names:
                for question in questions:
                    if make_run_id(name, level, question["qid"], repeat) in done:
                        skipped += 1
                        continue
                    plan.append((name, level, repeat, question))

    mode = "DRY RUN (FakeOpenRouter, no API calls)" if args.dry_run else "live"
    print(f"{mode}: {len(plan)} runs to do, {skipped} already done, writing to {out}", flush=True)
    if not plan:
        return 0

    sink = _Sink(out, total=len(plan))
    arm_cache = _ArmCache(arms_mod.build_arm, worlds, emb_store)
    stop = threading.Event()  # set when the daily free-model cap is hit; runs not yet started then return at once
    ctx_blocks: dict[tuple[str, int], list[dict]] = defaultdict(list)
    futures = []
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for name, level, repeat, question in plan:
                if name == "CTX":
                    ctx_blocks[(level, repeat)].append(question)
                else:
                    futures.append(
                        pool.submit(_run_one, client, arm_cache, sink, args.dry_run, name, level, repeat, question, stop)
                    )
            for (level, repeat), block in ctx_blocks.items():
                futures.append(
                    pool.submit(_run_ctx_block, client, arm_cache, sink, args.dry_run, level, repeat, block, stop)
                )
            for future in futures:
                future.result()  # surfaces any unexpected failure in the sink itself
    finally:
        sink.close()

    if stop.is_set():
        finished = sum(sink.statuses.values())
        print(
            f"Daily free-model request limit reached after {finished} finished runs; {len(plan) - finished} runs still to do."
            " Rerun the same command after the limit resets (00:00 UTC); finished runs are kept.",
            file=sys.stderr,
            flush=True,
        )
        return 3

    print("Finished: " + ", ".join(f"{k}={v}" for k, v in sorted(sink.statuses.items(), key=lambda kv: str(kv[0]))), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
