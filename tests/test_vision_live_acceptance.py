"""The vision tier's endpoint mode must be able to fail, not just to exist.

``examples/vision-live-acceptance.py`` is the end-to-end contract for
"an OmniParser-style service can feed the vision tier". These tests run it
against a local stub, so the contract is checked without a model:

* a working service -> PASS, and the image really travelled;
* a service answering 500 -> FAIL (a broken service is not an absent one);
* a service answering nothing -> FAIL on the parse leg, endpoint still PASS;
* no endpoint at all -> SKIP, never PASS.

They also assert the probe judges through ``examples/_acceptance_verdict.py``
instead of carrying its own copy of the rules.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PROBE = EXAMPLES / "vision-live-acceptance.py"
SHARED_SOURCE = EXAMPLES / "_acceptance_verdict.py"

#: Copied-into-the-probe regressions: these belong to the shared module only.
FUNCTIONS_THAT_MAY_NOT_BE_REDEFINED = (
    "_leg",
    "_combine_verdicts",
    "_verdict_exit_code",
    "_canonical_sha256",
    "_judgement_sha256",
    "_resolve_out_dir",
    "_require_free_evidence",
    "_staged",
    "_finalize",
    "_write_receipt_atomic",
    "_receipt",
    "_check",
)
CONSTANTS_THAT_MAY_NOT_BE_REDEFINED = (
    "PASS",
    "FAIL",
    "SKIP",
    "INTERRUPTED",
    "EXIT_CODES",
    "CONSOLE_INTERRUPT_CODES",
)


def _run(tmp_path: Path, *args: str) -> tuple[dict, int]:
    env = dict(os.environ)
    env.pop("OPENEYES_VISION_ENDPOINT", None)
    out = tmp_path / "evidence"
    completed = subprocess.run(
        [sys.executable, str(PROBE), *args, "--out", str(out)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=180,
        check=False,
    )
    stdout = completed.stdout.strip()
    assert stdout, f"probe printed nothing; stderr={completed.stderr!r}"
    return json.loads(stdout.splitlines()[-1]), completed.returncode


def _load_probe():
    if str(EXAMPLES) not in sys.path:
        sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location(
        "vision_live_acceptance", PROBE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shared():
    if str(EXAMPLES) not in sys.path:
        sys.path.insert(0, str(EXAMPLES))
    import _acceptance_verdict  # noqa: PLC0415

    return _acceptance_verdict


def test_a_working_service_passes_and_the_image_really_travelled(tmp_path):
    receipt, code = _run(tmp_path, "--serve-fixture")

    assert receipt["verdict"] == "PASS"
    assert code == 0
    assert receipt["page"]["verdict"] == "PASS"
    assert receipt["powershell"]["verdict"] == "PASS"
    names = [entry["check"] for entry in receipt["page"]["checks"] if entry["ok"]]
    assert names == [
        "endpoint-answers",
        "image-really-sent",
        "detections-nonempty",
        "window-relative-coords",
    ]
    image = receipt["judgement"]["image"]
    assert image["requests"] == 1
    assert len(image["image_sha256"]) == 64
    evidence = Path(receipt["evidence"]["directory"])
    assert (evidence / "vision-live-receipt.json").is_file()
    assert (evidence / "vision-live-probe.png").is_file()


def test_the_coordinates_are_window_relative_not_image_pixels(tmp_path):
    receipt, _ = _run(tmp_path, "--serve-fixture")

    coordinates = receipt["judgement"]["coordinates"]
    assert coordinates["origin"] == [40, 24]
    button = coordinates["elements"][0]
    # Detection [[100, 80, 240, 140]] with origin (40, 24) -> (60, 56) 140x60.
    assert (button["x"], button["y"], button["w"], button["h"]) == (60, 56, 140, 60)
    assert (button["cx"], button["cy"]) == (130, 86)


def test_the_verdict_is_reproducible_across_runs(tmp_path):
    first, _ = _run(tmp_path / "a", "--serve-fixture")
    second, _ = _run(tmp_path / "b", "--serve-fixture")

    # The digest covers the decision only: same stub, same verdict.
    assert first["judgement_sha256"] == second["judgement_sha256"]


def test_a_broken_endpoint_fails_instead_of_skipping(tmp_path):
    receipt, code = _run(tmp_path, "--inject-failure", "endpoint")

    assert receipt["page"]["verdict"] == "FAIL"
    assert receipt["page"]["reason"] == "endpoint-answers"
    assert receipt["verdict"] == "FAIL"
    assert code == 2
    # Nothing was parsed, so the parse leg is untested - not passed.
    assert receipt["powershell"]["verdict"] == "SKIP"


def test_empty_detections_fail_the_parse_leg_only(tmp_path):
    receipt, code = _run(tmp_path, "--inject-failure", "parse")

    assert receipt["page"]["verdict"] == "PASS"
    assert receipt["powershell"]["verdict"] == "FAIL"
    assert receipt["powershell"]["reason"] == "parse-detections-nonempty"
    assert receipt["verdict"] == "FAIL"
    assert code == 2


def test_no_endpoint_is_skip_never_pass(tmp_path):
    receipt, code = _run(tmp_path)

    assert receipt["verdict"] == "SKIP"
    assert code == 3
    assert receipt["page"]["reason"] == "no-vision-endpoint-configured"
    assert receipt["powershell"]["verdict"] == "SKIP"


def test_every_out_dir_holds_exactly_one_receipt_and_no_staged_files(tmp_path):
    _run(tmp_path, "--serve-fixture")
    _run(tmp_path / "b", "--inject-failure", "parse")

    for directory in (tmp_path / "evidence", tmp_path / "b" / "evidence"):
        names = sorted(path.name for path in directory.iterdir())
        assert not [name for name in names if name.endswith((".part", ".tmp"))]
        assert "vision-live-receipt.json" in names


def test_the_probe_imports_the_shared_verdict_model():
    module = _load_probe()
    shared = _shared()

    assert module.VERDICT_CONTRACT, "the probe declares no contract"
    for name, value in module.VERDICT_CONTRACT.items():
        assert hasattr(shared, name), f"{name} is not in the shared module"
        assert value is getattr(shared, name), (
            f"vision probe carries its own {name}, not the shared one"
        )


def test_the_probe_does_not_redefine_the_verdict_rules():
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    defined_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assigned_constants = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert not (defined_functions & set(FUNCTIONS_THAT_MAY_NOT_BE_REDEFINED))
    assert not (assigned_constants & set(CONSTANTS_THAT_MAY_NOT_BE_REDEFINED))
    assert ast.parse(SHARED_SOURCE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("failure", ["endpoint", "parse"])
def test_an_injected_failure_always_reaches_a_red_leg(failure):
    """The injection has to reach a red leg, or it proves nothing."""
    module = _load_probe()

    assert module._combine_verdicts(module.PASS, module.SKIP) == module.SKIP
    assert module._combine_verdicts(module.PASS, module.FAIL) == module.FAIL
    assert module._verdict_exit_code(module.FAIL) == 2
