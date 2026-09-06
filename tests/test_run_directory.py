import json
import re

from dispenser_conditioning_mcp import run_directory
from dispenser_conditioning_mcp.recording_service import RecordingService


def test_new_storage_names_preserve_live_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(run_directory, "RUNS_DIRECTORY", tmp_path)
    for mode in ("hardware", "simulation"):
        path = run_directory.new_run_directory(mode)
        assert path.parent == tmp_path
        assert re.fullmatch(
            rf"\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}}Z_{mode}_[0-9a-f]{{8}}",
            path.name,
        )
        assert not path.exists()
    service = RecordingService()
    assert "_hardware_" in service.directory.name
    metadata = json.loads((service.directory / "metadata.json").read_text())
    assert metadata["session_kind"] == "live"
