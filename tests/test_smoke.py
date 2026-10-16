from src import judge, smoke
from src import gen_distractors as gd
from tests.fake_openrouter import FakeOpenRouter, text_resp, tool_resp


def test_subject_tool_round_trip_passes_and_fails():
    ok = FakeOpenRouter([tool_resp([("c1", "search_memory", {"query": "Asha Rao HbA1c"})]), text_resp("Her latest HbA1c was 7.4%.")])
    assert smoke.check_subject_tools(ok) is True
    no_tool = FakeOpenRouter([text_resp("I don't know.")])
    assert smoke.check_subject_tools(no_tool) is False


def test_judge_check_runs_on_dry_run_client():
    assert smoke.check_judge(judge._dry_run_client()) in (True, False)  # parses; score depends on the dry-run stub


def test_generator_check_runs_on_dry_run_client():
    assert smoke.check_generator(gd.dry_run_client()) is True


def test_main_without_key_fails_fast(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert smoke.main([]) == 1


def test_gold_check_writes_results_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    assert smoke.check_gold(judge._dry_run_client(), workers=2) in (True, False)
    import json
    rows = json.loads((tmp_path / "results" / "gold_check.json").read_text(encoding="utf-8"))
    assert len(rows) == len(json.loads(smoke.QUESTIONS_PATH.read_text(encoding="utf-8")))
