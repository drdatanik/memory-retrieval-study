"""Live smoke test: a handful of real requests that check each role works before any big step runs.

    python -m src.smoke              # key + account status, subject tool round trip, judge JSON, one distractor patient
    python -m src.smoke --long L5    # also: the CTX prompt at that level, sent twice (size, latency, caching)
    python -m src.smoke --gold       # the judge grades every gold answer (48 requests); all must score 3

About 5 requests by default (up to 8 if the generator's first patient fails validation), plus 2 with --long.
Nothing is written to the study's data folders. The key itself is never printed.
Exit code: 0 all passed, 1 something failed, 3 the free-model daily limit was hit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from src import config, llm

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = ROOT / "questions" / "questions.json"

SEARCH_TOOL = [{
    "name": "search_memory",
    "description": "Search the clinic's memory store. Returns matching memories with their IDs.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                     "required": ["query"], "additionalProperties": False},
}]


def _usage_line(r: llm.LLMResponse, latency: float) -> str:
    u = r.usage
    return (f"provider={r.provider} model={r.model} prompt={u['prompt_tokens']:,} cached={u['cached_tokens']:,} "
            f"out={u['completion_tokens']:,} (reasoning {u['reasoning_tokens']:,}) cost={u['cost_usd']} "
            f"latency={latency:.1f}s")


def check_account() -> bool:
    print(f"{config.OPENROUTER_KEY_ENV}: {'set' if os.environ.get(config.OPENROUTER_KEY_ENV) else 'NOT SET'}")
    if not os.environ.get(config.OPENROUTER_KEY_ENV):
        return False
    try:
        info = llm.key_info()
    except Exception as exc:
        print(f"  key status: FAIL {exc!r}")
        return False
    shown = {k: info.get(k) for k in ("is_free_tier", "limit", "limit_remaining", "usage", "usage_daily") if k in info}
    print(f"  key status: {shown}")
    print(f"  subject={config.SUBJECT}; {config.ROLES['subject']['model']}, judge {config.ROLES['judge']['model']}, "
          f"generator {config.ROLES['generator']['model']}")
    return True


def check_subject_tools(client) -> bool:
    print("subject: tool call round trip")
    messages = [
        {"role": "system", "content": "You answer questions about a clinic's patients. Use search_memory to look up "
                                      "records before answering. Today is 2026-10-12."},
        {"role": "user", "content": "Asked by: Doctor, Dr Test (D99). What was Asha Rao's latest HbA1c?"},
    ]
    t = time.monotonic()
    r = llm.chat(client, "subject", messages, tools=SEARCH_TOOL)
    print("  turn 1:", _usage_line(r, time.monotonic() - t))
    if not r.tool_calls:
        print(f"  FAIL: no tool call (finish_reason {r.finish_reason}, text {r.text[:120]!r})")
        return False
    call = r.tool_calls[0]
    print(f"  tool call: {call['name']}({call['raw_arguments']}) parse_error={call['parse_error']}; "
          f"reasoning_details returned: {'reasoning_details' in r.assistant_message}")
    for turn in range(2, 5):   # the real loop allows more; 3 follow-up turns is enough here
        messages.append(r.assistant_message)
        for c in r.tool_calls:
            messages.append({"role": "tool", "tool_call_id": c["id"], "content":
                             "[T01-m03] 2026-09-02 · Lab · Patient: Asha Rao (T01, 52 F) · Author: lab\nHbA1c 7.4%"})
        t = time.monotonic()
        r = llm.chat(client, "subject", messages, tools=SEARCH_TOOL)
        print(f"  turn {turn}:", _usage_line(r, time.monotonic() - t), f"finish={r.finish_reason}",
              f"tool_calls={[c['name'] + c['raw_arguments'] for c in r.tool_calls]}" if r.tool_calls else "")
        if not r.tool_calls:
            break
    ok = "7.4" in r.text
    print(f"  answer: {r.text.strip()[:160]!r} -> {'PASS' if ok else 'FAIL (7.4% not in answer)'}")
    return ok


def check_judge(client) -> bool:
    from src import judge

    print("judge: structured JSON on a real question, graded against its own gold answer")
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = questions["questions"] if isinstance(questions, dict) else questions
    q = next(x for x in questions if not x.get("abstention"))
    run = {"run_id": "smoke", "qid": q["qid"], "final_text": q["gold_answer"]}
    messages = judge.build_judge_messages(run, q, judge.patient_record_md(q))
    t = time.monotonic()
    r = llm.chat(client, "judge", messages, response_format=llm.json_schema_format("judgment", judge.JUDGE_SCHEMA))
    print("  ", _usage_line(r, time.monotonic() - t))
    try:
        parsed = judge.parse_judgment(r.text, q)
    except ValueError as exc:
        print(f"  FAIL: {exc}; text {r.text[:200]!r}")
        return False
    ok = parsed.get("correctness") == 3
    print(f"  {q['qid']}: correctness={parsed.get('correctness')} quality={parsed.get('quality')} "
          f"-> {'PASS' if ok else 'CHECK (gold answer should score 3)'}")
    if not ok:
        facts = q.get("required_facts") or []
        for f in parsed.get("required_facts") or []:
            i = f.get("index")
            text = facts[i - 1]["fact"] if isinstance(i, int) and 1 <= i <= len(facts) else "?"
            print(f"    fact {i}: {f.get('status'):<12} {text}")
        print(f"    rationale: {parsed.get('rationale')}")
    return ok


def check_generator(client) -> bool:
    from src import gen_distractors as gd

    print("generator: one distractor patient (validated, not saved)")
    ctx = gd.make_context(Path(tempfile.mkdtemp()))
    t = time.monotonic()
    res = gd.generate_patient(client, 1, ctx)
    n = len(res.record["memories"]) if res.record else 0
    print(f"   attempts={res.attempts} status={res.status} memories={n} latency={time.monotonic() - t:.1f}s"
          + (f" problem={res.problem}" if res.problem else ""))
    return res.status == "written"


def _judge_gold(client, q: dict) -> tuple[str, dict | None, str | None]:
    from src import judge

    run = {"run_id": f"gold-{q['qid']}", "qid": q["qid"], "final_text": q["gold_answer"]}
    messages = judge.build_judge_messages(run, q, judge.patient_record_md(q))
    try:
        r = llm.chat(client, "judge", messages, response_format=llm.json_schema_format("judgment", judge.JUDGE_SCHEMA))
        return q["qid"], judge.parse_judgment(r.text, q), None
    except llm.DailyLimitReached:
        raise
    except Exception as exc:
        return q["qid"], None, repr(exc)


def check_gold(client, workers: int = 4) -> bool:
    """Every gold answer must score 3 with the judge. Writes results/gold_check.json; resumes from it."""
    from concurrent.futures import ThreadPoolExecutor

    out = ROOT / "results" / "gold_check.json"
    done = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    todo = [q for q in questions if q["qid"] not in done or done[q["qid"]].get("error")]
    print(f"gold check: {len(todo)} to judge, {len(questions) - len(todo)} already done -> {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for qid, parsed, err in pool.map(lambda q: _judge_gold(client, q), todo):
                done[qid] = {"error": err} if err else parsed
                out.write_text(json.dumps(done, indent=2, ensure_ascii=False), encoding="utf-8")
    finally:
        out.write_text(json.dumps(done, indent=2, ensure_ascii=False), encoding="utf-8")
    by_q = {q["qid"]: q for q in questions}
    failing = []
    for qid in sorted(done):
        row = done[qid]
        if row.get("error") or row.get("correctness") != 3:
            failing.append(qid)
            if row.get("error"):
                print(f"  {qid}: ERROR {row['error']}")
                continue
            facts = by_q[qid].get("required_facts") or []
            print(f"  {qid}: correctness={row.get('correctness')}")
            for f in row.get("required_facts") or []:
                i = f.get("index")
                if f.get("status") != "present":
                    text = facts[i - 1]["fact"] if isinstance(i, int) and 1 <= i <= len(facts) else "?"
                    print(f"    fact {i}: {f.get('status'):<12} {text}")
            print(f"    rationale: {row.get('rationale')}")
    n = len(questions)
    print(f"  {n - len(failing)}/{n} gold answers score 3" + (f"; fix: {', '.join(failing)}" if failing else ""))
    return not failing and len(done) == n


def check_long(client, level: str) -> bool:
    from src import arms, world

    print(f"subject: CTX prompt at {level}, sent twice")
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = questions["questions"] if isinstance(questions, dict) else questions
    q = next(x for x in questions if x.get("pilot") and not x.get("abstention"))
    from src import prompts

    arm = arms.build_arm("CTX", world.load_level(level), q, None)
    messages = [{"role": "system", "content": llm.system_text(arm.system)},
                {"role": "user", "content": prompts.user_turn(q)}]
    ok = True
    for i in (1, 2):
        t = time.monotonic()
        r = llm.chat(client, "subject", messages)
        print(f"  call {i}:", _usage_line(r, time.monotonic() - t), f"finish={r.finish_reason}")
        ok = ok and r.finish_reason == "stop" and bool(r.text.strip())
    print(f"  answer: {r.text.strip()[:200]!r}")
    print(f"  {'PASS' if ok else 'FAIL'}; cached tokens on call 2 show whether this endpoint caches prompts")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A few live requests to check each model role works.")
    parser.add_argument("--long", metavar="LEVEL", choices=tuple(config.LEVEL_TARGETS), help="also send the CTX prompt at this level twice")
    parser.add_argument("--only", choices=("subject", "judge", "generator"), help="run just one role check")
    parser.add_argument("--gold", action="store_true", help="only the gold check: the judge grades every gold answer")
    args = parser.parse_args(argv)

    if not check_account():
        return 1
    client = llm.make_client()
    if args.gold:
        try:
            return 0 if check_gold(client) else 1
        except llm.DailyLimitReached as exc:
            print(f"Daily free-model request limit reached: {exc}. Rerun --gold tomorrow; it resumes.", file=sys.stderr)
            return 3
    checks = {"subject": check_subject_tools, "judge": check_judge, "generator": check_generator}
    if args.only:
        checks = {args.only: checks[args.only]}
    results = {}
    try:
        for name, fn in checks.items():
            try:
                results[name] = fn(client)
            except llm.DailyLimitReached:
                raise
            except Exception as exc:
                print(f"  FAIL: {exc!r}")
                results[name] = False
        if args.long:
            try:
                results[f"long {args.long}"] = check_long(client, args.long)
            except llm.DailyLimitReached:
                raise
            except Exception as exc:
                print(f"  FAIL: {exc!r}")
                results[f"long {args.long}"] = False
    except llm.DailyLimitReached as exc:
        print(f"Daily free-model request limit reached: {exc}", file=sys.stderr)
        return 3
    print("summary: " + ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
