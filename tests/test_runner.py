import json

from pipeline import runner


def test_runner_rejects_non_whitelisted_and_runs_allowed(vault):
    d = runner._dirs(vault)
    (d["inbox"] / "a.json").write_text(json.dumps({"id": "a", "args": ["rm", "-rf", "/"]}), encoding="utf-8")
    (d["inbox"] / "b.json").write_text(json.dumps({"id": "b", "args": ["catalog"]}), encoding="utf-8")
    runner.main(vault, once=True)
    assert json.loads((d["done"] / "a.json").read_text())["rc"] == 2
    assert "ODMÍTNUTO" in (d["logs"] / "a.log").read_text(encoding="utf-8")
    assert (d["done"] / "b.json").exists()
    assert not list(d["inbox"].glob("*.json"))
    assert (d["base"] / "heartbeat.json").exists()
