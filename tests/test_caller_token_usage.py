import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "caller_usage", Path(__file__).parents[1] / "tools/codex_token_usage.py"
)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def event(total):
    return (
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "total_tokens": total,
                            "input_tokens": total - 10,
                            "cached_input_tokens": total - 20,
                            "output_tokens": 10,
                        }
                    },
                },
            }
        )
        + "\n"
    )


def test_checkpoint_pending_retry_ack_and_argument_copy(tmp_path):
    rollout, cursor = tmp_path / "selected.jsonl", tmp_path / "cursor.json"
    rollout.write_text(event(1000))
    usage = helper.CodexUsageCheckpoint(rollout, cursor)
    usage.baseline()
    arguments = {
        "action_context": {"action": "already chosen"},
        "target_current_a": 0.2,
    }
    assert usage.decorate(arguments) == arguments  # No new report, not zero.
    rollout.write_text(event(1000) + event(1200))
    first = usage.decorate(arguments)
    batch = first["action_context"]["token_usage"]
    assert batch["total_tokens"] == 200
    assert batch["input_tokens"] == 200 and batch["cached_input_tokens"] == 200
    assert batch["output_tokens"] == 0  # Actual counter delta, not absence.
    assert "token_usage" not in arguments["action_context"]
    rollout.write_text(event(1000) + event(1200) + event(1300))
    assert usage.decorate(arguments) == first  # Failed carrier keeps identical ID.
    usage.acknowledge(batch["usage_id"])
    second = usage.decorate(arguments)["action_context"]["token_usage"]
    assert second["total_tokens"] == 100 and second["usage_id"] != batch["usage_id"]
    with pytest.raises(FileExistsError):
        usage.baseline()


@pytest.mark.parametrize(
    "suffix",
    [
        '{"incomplete":',
        "{bad}\n",
        event(900),
        json.dumps(
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}}
        )
        + "\n",
    ],
)
def test_bad_or_reset_checkpoint_is_unavailable(tmp_path, suffix):
    rollout = tmp_path / "selected.jsonl"
    rollout.write_text(event(1000))
    usage = helper.CodexUsageCheckpoint(rollout, tmp_path / "cursor.json")
    usage.baseline()
    rollout.write_text(event(1000) + suffix)
    with pytest.raises(helper.UsageUnavailable):
        usage.decorate({"action_context": {}})


def test_no_usage_cannot_create_baseline(tmp_path):
    rollout = tmp_path / "selected.jsonl"
    rollout.write_text("{}\n")
    with pytest.raises(helper.UsageUnavailable):
        helper.CodexUsageCheckpoint(rollout, tmp_path / "cursor.json").baseline()
