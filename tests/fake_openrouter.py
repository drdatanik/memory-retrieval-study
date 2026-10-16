"""Offline stand-in for the OpenAI SDK client pointed at OpenRouter. Used by tests and --dry-run.

FakeOpenRouter(script=None | list | callable)
  .chat.completions.create(**kwargs) -> FakeCompletion (has .to_dict(), like the SDK object)
  .calls -> deep copies of every kwargs received
script: a list of response dicts (returned in order; an Exception instance is raised instead),
        or a callable (kwargs) -> response dict. With no script, a dry-run behaviour is used:
        if tools are offered and no tool has been called yet in this conversation, call the first
        tool once with a plausible input; otherwise answer "DRY RUN ANSWER" (or "{}" when a
        response_format is requested).
"""

from __future__ import annotations

import copy
import json
import itertools

_ids = itertools.count(1)

DEFAULT_USAGE = {"prompt_tokens": 1000, "completion_tokens": 20,
                 "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                 "completion_tokens_details": {"reasoning_tokens": 5}, "cost": 0.00016}


def usage(prompt=1000, completion=20, cached=0, cache_write=0, reasoning=5, cost=0.00016) -> dict:
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": cache_write},
            "completion_tokens_details": {"reasoning_tokens": reasoning}, "cost": cost}


def text_resp(text: str, usage_dict: dict | None = None, finish_reason: str = "stop", provider: str = "DeepSeek") -> dict:
    return {"id": f"gen-{next(_ids)}", "provider": provider, "model": "fake-model",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": text,
                                     "reasoning_details": [{"type": "reasoning.text", "text": "(fake)"}]}}],
            "usage": usage_dict or copy.deepcopy(DEFAULT_USAGE)}


def tool_resp(calls: list[tuple[str, str, dict | str]], usage_dict: dict | None = None, provider: str = "DeepSeek") -> dict:
    tool_calls = [{"id": cid, "type": "function",
                   "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
                  for cid, name, args in calls]
    return {"id": f"gen-{next(_ids)}", "provider": provider, "model": "fake-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": "", "tool_calls": tool_calls,
                                     "reasoning_details": [{"type": "reasoning.text", "text": "(fake)"}]}}],
            "usage": usage_dict or copy.deepcopy(DEFAULT_USAGE)}


class FakeCompletion:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return copy.deepcopy(self._data)


class FakeScriptExhausted(RuntimeError):
    pass


_DRY_INPUTS = {"search_memory": {"query": "test"}, "read_file": {"path": "INDEX.md"}, "grep": {"pattern": "test"}}


class _Completions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        owner = self._owner
        owner.calls.append(copy.deepcopy(kwargs))
        script = owner.script
        if script is None:
            data = owner._dry_run(kwargs)
        elif callable(script):
            data = script(kwargs)
        else:
            if owner._pos >= len(script):
                raise FakeScriptExhausted(f"script has {len(script)} responses; call {owner._pos + 1} requested")
            data = script[owner._pos]
            owner._pos += 1
        if isinstance(data, Exception):
            raise data
        return FakeCompletion(data)


class _Chat:
    def __init__(self, owner):
        self.completions = _Completions(owner)


class FakeOpenRouter:
    def __init__(self, script=None):
        self.script = script
        self.calls: list[dict] = []
        self._pos = 0
        self.chat = _Chat(self)

    @staticmethod
    def _dry_run(kwargs: dict) -> dict:
        messages = kwargs.get("messages") or []
        tools = kwargs.get("tools") or []
        already_called = any(m.get("role") == "tool" for m in messages)
        if tools and not already_called:
            name = tools[0]["function"]["name"]
            return tool_resp([(f"call_{next(_ids)}", name, _DRY_INPUTS.get(name, {}))])
        return text_resp("{}" if kwargs.get("response_format") else "DRY RUN ANSWER")
