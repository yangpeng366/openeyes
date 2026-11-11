"""Behavioural tests for the direct MCP stdio browser_shot acceptance probe.

The previous revision asserted that sentences existed in the probe source, so a
probe that captured for real, echoed a placeholder path, resolved the decoy tab,
or reported ``captured:false`` next to an ``error`` still printed
``passed: true``. These tests call the probe's own contract functions with the
payloads ``browser_shot`` serialises and with real PNG bytes on disk, and assert
the values that decide the verdict - signature, byte count, geometry, path.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-shot-acceptance.py"
TARGET_URL = "http://127.0.0.1:53100/acceptance-pages/target-a.html"
DECOY_URL = "http://127.0.0.1:53100/acceptance-pages/decoy.html"
SHOT_PATH = "shots/browser-shot-acceptance.png"
VIEWPORT = (1280, 800)


def _load_module():
    spec = importlib.util.spec_from_file_location("browser_shot_acceptance", PROBE)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _png_bytes(width: int = VIEWPORT[0], height: int = VIEWPORT[1]) -> bytes:
    """A real, self-consistent PNG: signature, IHDR, IDAT, IEND."""
    row = b"\x00" + b"\x2f\x6b\x8f" * width
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(row * height, 9))
        + _png_chunk(b"IEND", b"")
    )


def _dry_run_payload(**overrides) -> dict:
    payload = {
        "captured": False,
        "dry_run": True,
        "path": SHOT_PATH,
        "target_url": TARGET_URL,
    }
    payload.update(overrides)
    return payload


def _check_dry_run(payload: dict) -> dict:
    module = _load_module()
    return module._require_shot_dry_run(payload, out=SHOT_PATH, target_url=TARGET_URL)


def test_shot_dry_run_accepts_the_real_payload():
    accepted = _check_dry_run(_dry_run_payload())

    assert accepted["captured"] is False
    assert accepted["dry_run"] is True
    assert accepted["path"] == "shots/browser-shot-acceptance.png"
    assert accepted["target_url"] == TARGET_URL


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        pytest.param({"captured": True, "dry_run": False, "path": SHOT_PATH}, "not a dry-run", id="real-capture"),
        pytest.param({"captured": True, "path": SHOT_PATH}, "not a dry-run", id="captured-true"),
        pytest.param({"dry_run": False}, "not a dry-run", id="run-without-capture-flag"),
        pytest.param({"dry_run": None}, "not a dry-run", id="absent-dry-run-flag"),
        pytest.param({"captured": 0}, "not a dry-run", id="falsy-not-false"),
        pytest.param({"path": "shots/other.png"}, "wrong path", id="placeholder-path"),
        pytest.param({"path": None}, "wrong path", id="absent-path"),
        pytest.param({"target_url": DECOY_URL}, "wrong target", id="decoy-tab"),
        pytest.param({"target_url": None}, "wrong target", id="target-url-dropped"),
        pytest.param(
            {"error": "no page target matched url_contains='x'"},
            "failed instead of resolving a target",
            id="error-with-path-scored-as-success",
        ),
        pytest.param([{"captured": False}], "non-object payload", id="payload-list"),
    ],
)
def test_shot_dry_run_rejects_anything_but_the_resolved_dry_run(payload, detail):
    """Every case is a regression that used to print passed=true."""
    payload = payload if isinstance(payload, list) else _dry_run_payload(**payload)

    with pytest.raises(RuntimeError) as excinfo:
        _check_dry_run(payload)

    assert detail in str(excinfo.value)


def test_shot_fail_closed_returns_the_real_error_text():
    module = _load_module()
    error = (
        "no page target matched url_contains='missing-target'; "
        "available URLs: 'http://127.0.0.1:53100/acceptance-pages/target-a.html'"
    )

    assert module._require_fail_closed_shot({"error": error, "tool": "browser_shot"}) == error


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tool": "browser_shot"}, id="silent-success"),
        pytest.param({"error": "boom"}, id="wrong-error-text"),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "captured": True},
            id="claimed-capture",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "path": SHOT_PATH},
            id="leaked-path",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "target_url": TARGET_URL},
            id="leaked-target-url",
        ),
        pytest.param([_dry_run_payload()], id="payload-list"),
    ],
)
def test_shot_fail_closed_rejects_silent_success_and_leaked_outputs(payload):
    module = _load_module()

    with pytest.raises(RuntimeError):
        module._require_fail_closed_shot(payload)


def test_shot_capture_accepts_a_real_png_in_a_created_out_dir(tmp_path):
    module = _load_module()
    out = tmp_path / "shots" / "nested" / "capture.png"

    resolved = module._ensure_out_dir(str(out))
    resolved.write_bytes(_png_bytes())

    payload = {"captured": True, "dry_run": False, "path": str(resolved)}
    accepted = module._require_png_capture(payload, out=str(out), region=VIEWPORT)

    assert accepted["path"] == str(resolved)
    assert out.parent.is_dir(), "--out must create its missing directory"
    width, height, size = module._read_png(resolved)
    assert (width, height) == VIEWPORT
    assert size == len(_png_bytes()) > 0


def test_shot_capture_reports_the_bytes_that_are_actually_on_disk(tmp_path):
    module = _load_module()
    png = _png_bytes(640, 480)
    target = tmp_path / "capture.png"
    target.write_bytes(png)

    assert module._read_png(target) == (640, 480, len(png))
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        pytest.param(b"", "is empty", id="zero-bytes"),
        pytest.param(b"\xff\xd8\xff\xe0" + b"\x00" * 64, "is not a PNG", id="jpeg-bytes"),
        pytest.param(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "has no IHDR", id="magic-only"),
        pytest.param(_png_bytes(64, 64)[:-12], "is truncated", id="missing-iend"),
        pytest.param(_png_bytes(64, 64)[:8] + b"\x00\x00\x00\x0d" + b"XXXX" + b"\x00" * 40, "has no IHDR", id="wrong-chunk"),
    ],
)
def test_shot_capture_rejects_stub_and_corrupt_files(tmp_path, content, detail):
    """A created-but-empty or foreign file must never count as a screenshot."""
    module = _load_module()
    target = tmp_path / "capture.png"
    target.write_bytes(content)

    with pytest.raises(RuntimeError) as excinfo:
        module._require_png_capture(
            {"captured": True, "dry_run": False, "path": str(target)},
            out=str(target),
            region=VIEWPORT,
        )

    assert detail in str(excinfo.value)


def test_shot_capture_rejects_a_geometry_mismatch(tmp_path):
    module = _load_module()
    target = tmp_path / "capture.png"
    target.write_bytes(_png_bytes(800, 600))

    with pytest.raises(RuntimeError) as excinfo:
        module._require_png_capture(
            {"captured": True, "dry_run": False, "path": str(target)},
            out=str(target),
            region=VIEWPORT,
        )

    assert "800x600" in str(excinfo.value)
    assert "1280x800" in str(excinfo.value)


def test_shot_capture_rejects_a_missing_file_and_a_foreign_path(tmp_path):
    module = _load_module()
    written = tmp_path / "capture.png"
    written.write_bytes(_png_bytes())
    other = tmp_path / "elsewhere.png"

    with pytest.raises(RuntimeError) as missing:
        module._require_png_capture(
            {"captured": True, "dry_run": False, "path": str(other)},
            out=str(other),
            region=VIEWPORT,
        )
    assert "no capture at" in str(missing.value)

    with pytest.raises(RuntimeError) as foreign:
        module._require_png_capture(
            {"captured": True, "dry_run": False, "path": str(written)},
            out=str(other),
            region=VIEWPORT,
        )
    assert "outside the requested --out" in str(foreign.value)


def test_shot_capture_rejects_a_payload_that_did_not_capture(tmp_path):
    module = _load_module()
    target = tmp_path / "capture.png"
    target.write_bytes(_png_bytes())

    for payload in (
        {"captured": False, "dry_run": True, "path": str(target)},
        {"captured": True, "dry_run": True, "path": str(target)},
        {"captured": True, "dry_run": False, "path": str(target), "error": "boom"},
    ):
        with pytest.raises(RuntimeError):
            module._require_png_capture(payload, out=str(target), region=VIEWPORT)


def test_shot_out_dir_is_created_from_scratch(tmp_path):
    module = _load_module()
    out = tmp_path / "a" / "b" / "c" / "shot.png"
    assert not out.parent.exists()

    resolved = module._ensure_out_dir(str(out))

    assert resolved == out.resolve()
    assert out.parent.is_dir()
    assert not out.exists(), "creating the directory must not create the file"
    assert module._ensure_out_dir(str(out)) == resolved


def test_shot_dry_run_must_not_touch_the_out_file(tmp_path):
    module = _load_module()
    out = tmp_path / "shot.png"
    out.write_bytes(_png_bytes(320, 200))
    before = module._file_state(out)

    module._require_no_new_capture(out, before=before)

    with pytest.raises(RuntimeError) as created:
        module._require_no_new_capture(out, before=None)
    assert "wrote or touched" in str(created.value)

    out.write_bytes(_png_bytes(320, 240))
    with pytest.raises(RuntimeError) as rewritten:
        module._require_no_new_capture(out, before=before)
    assert "wrote or touched" in str(rewritten.value)

    absent = tmp_path / "never-written.png"
    module._require_no_new_capture(absent, before=None)
    with pytest.raises(RuntimeError):
        module._require_no_new_capture(absent, before=(10, 123))


def test_shot_tab_selection_returns_the_first_of_each_kind():
    module = _load_module()
    tabs = [
        {"url": TARGET_URL, "id": "t1"},
        {"url": DECOY_URL, "id": "d1"},
        {"url": TARGET_URL, "id": "t2"},
        {"url": "http://127.0.0.1:3080/session", "id": "unrelated"},
    ]

    acceptance_tabs, target_tab, decoy_tab = module._select_acceptance_tabs(tabs, "target-a")

    assert len(acceptance_tabs) == 3, "the unrelated dsh tab must not be counted"
    assert target_tab["id"] == "t1"
    assert decoy_tab["id"] == "d1"


@pytest.mark.parametrize(
    ("tabs", "detail"),
    [
        pytest.param([{"url": TARGET_URL, "id": "t1"}], "decoys=0", id="missing-decoy"),
        pytest.param([{"url": DECOY_URL, "id": "d1"}], "targets=0", id="missing-target"),
        pytest.param([], "targets=0", id="no-tabs"),
    ],
)
def test_shot_tab_selection_requires_each_side(tabs, detail):
    """Without both tabs the url_contains filter was never proven to discriminate."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._select_acceptance_tabs(tabs, "target-a")

    assert detail in str(excinfo.value)


def test_shot_arguments_keep_the_dry_run_default():
    module = _load_module()
    arguments = module._shot_arguments(9222, out=SHOT_PATH, url_contains="target-a")

    assert arguments == {"port": 9222, "out": SHOT_PATH, "url_contains": "target-a"}
    assert "dry_run" not in arguments


def test_shot_main_calls_the_contract_checks_it_documents():
    """Wiring only: main() must evaluate the extracted checks, not re-inline them."""
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    main_calls = {node.func.id for node in calls}
    strings = "".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    url_filters = {
        keyword.value.id if isinstance(keyword.value, ast.Name) else keyword.value.value
        for node in calls
        if node.func.id == "_shot_arguments"
        for keyword in node.keywords
        if keyword.arg == "url_contains"
    }

    assert {
        "_select_acceptance_tabs",
        "_shot_arguments",
        "_require_shot_dry_run",
        "_require_fail_closed_shot",
        "_require_no_new_capture",
        "_result_content",
    } <= main_calls
    assert "openeyes.mcp.server" in strings
    assert "notifications/initialized" in strings
    assert url_filters == {"TARGET_MARKER", "missing-target"}, url_filters
    assert "3080" not in strings, "the direct probe must not depend on the dsh web host"


def test_shot_acceptance_result_content_parses_single_text_payload():
    module = _load_module()
    result = {"content": [{"type": "text", "text": json.dumps(_dry_run_payload())}]}

    assert module._result_content(result) == _dry_run_payload()


def test_shot_acceptance_result_content_rejects_multi_part_payloads():
    module = _load_module()
    result = {
        "content": [
            {"type": "text", "text": "{}"},
            {"type": "text", "text": "{}"},
        ]
    }

    with pytest.raises(RuntimeError):
        module._result_content(result)


# ===== verdict, PowerShell leg, --out, staging (real PNG bytes only) =====


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        pytest.param(("PASS", "PASS"), "PASS", id="both-pass"),
        pytest.param(("FAIL", "PASS"), "FAIL", id="page-failed"),
        pytest.param(("PASS", "FAIL"), "FAIL", id="powershell-failed"),
        pytest.param(("FAIL", "FAIL"), "FAIL", id="both-failed"),
        pytest.param(("SKIP", "PASS"), "SKIP", id="skip-is-not-a-pass"),
        pytest.param(("SKIP", "FAIL"), "FAIL", id="fail-beats-skip"),
    ],
)
def test_combine_verdicts_never_promotes_a_skip_or_hides_a_fail(verdicts, expected):
    module = _load_module()

    assert module._combine_verdicts(*verdicts) == expected


def test_verdict_exit_codes_keep_pass_fail_and_skip_apart():
    module = _load_module()

    assert [module._verdict_exit_code("PASS"),
            module._verdict_exit_code("FAIL"),
            module._verdict_exit_code("SKIP")] == [0, 2, 3]
    with pytest.raises(ValueError):
        module._verdict_exit_code("green")


def test_powershell_leg_passes_on_the_real_200_output_and_fails_on_a_real_exit():
    module = _load_module()
    passed = module._classify_powershell({
        "returncode": 0, "stdout": "200\r\n", "stderr": "", "timed_out": False,
        "unavailable": None, "executable": "pwsh.exe",
    })
    failed = module._classify_powershell({
        "returncode": 1, "stdout": "", "timed_out": False, "unavailable": None,
        "stderr": "The term 'C:\\nope\\missing-openeyes.exe' is not recognized as a "
                  "name of a cmdlet, function, script file, or operable program.",
    })
    skipped = module._classify_powershell({
        "returncode": None, "stdout": "", "stderr": "", "timed_out": False,
        "unavailable": "pwsh-not-found",
    })

    assert passed["verdict"] == "PASS"
    assert failed["verdict"] == "FAIL"
    assert failed["reason"] == "powershell-exit-1"
    assert skipped["verdict"] == "SKIP"
    assert module._combine_verdicts(skipped["verdict"], "PASS") == "SKIP"


def test_judgement_digest_is_stable_across_runs_and_sensitive_to_the_capture(tmp_path):
    module = _load_module()
    checks = [
        {"check": "tabs", "ok": True, "detail": {"target_id": "AAA"}},
        {"check": "capture-png", "ok": True},
    ]
    judgement = {
        "viewport": {"width": 1280, "height": 800},
        "dry_run": {"captured": False, "dry_run": True, "path_is_requested": True},
        "error_kind": module.NO_PAGE_ERROR,
        "error_matched": True,
        "capture": {"width": 1280, "height": 800, "bytes": 4242, "sha256": "a" * 64},
    }

    first = module._judgement_sha256(
        probe=module.PROBE_NAME, verdict="PASS", page="PASS",
        powershell="PASS", checks=checks, judgement=judgement,
    )
    second = module._judgement_sha256(
        probe=module.PROBE_NAME, verdict="PASS", page="PASS",
        powershell="PASS",
        checks=[{"check": "tabs", "ok": True, "detail": {"target_id": "BBB"}},
                {"check": "capture-png", "ok": True}],
        judgement=dict(judgement),
    )
    other_pixels = module._judgement_sha256(
        probe=module.PROBE_NAME, verdict="PASS", page="PASS",
        powershell="PASS", checks=checks,
        judgement={**judgement, "capture": {**judgement["capture"], "sha256": "b" * 64}},
    )

    assert first == second
    assert first != other_pixels


def test_out_dir_creates_missing_parents_and_keeps_existing_files(tmp_path):
    module = _load_module()
    out = tmp_path / "round" / "one"
    out.mkdir(parents=True)
    keep = out / "earlier.png"
    keep.write_bytes(_png_bytes(64, 64))

    resolved = module._resolve_out_dir(str(out))

    assert resolved == out.resolve()
    assert keep.read_bytes() == _png_bytes(64, 64)
    assert sorted(path.name for path in out.iterdir()) == ["earlier.png"]


def test_out_dir_refuses_a_file_and_a_file_parent(tmp_path):
    module = _load_module()
    as_file = tmp_path / "evidence.png"
    as_file.write_bytes(_png_bytes(32, 32))
    parent = tmp_path / "blocked"
    parent.write_text("file, not a directory", encoding="utf-8")

    for out in (as_file, parent / "nested"):
        with pytest.raises(module.OutDirError):
            module._resolve_out_dir(str(out))


def test_shot_evidence_collision_needs_force(tmp_path):
    module = _load_module()
    out = module._resolve_out_dir(str(tmp_path / "evidence"))
    png = out / module.SHOT_NAME
    png.write_bytes(_png_bytes(320, 200))

    with pytest.raises(module.OutDirError) as excinfo:
        module._require_free_evidence(out, [module.RECEIPT_NAME, module.SHOT_NAME])
    assert "refusing to overwrite" in str(excinfo.value)

    module._require_free_evidence(
        out, [module.RECEIPT_NAME, module.SHOT_NAME], force=True
    )
    assert png.exists()


def test_receipt_write_is_atomic_and_bom_free(tmp_path):
    module = _load_module()
    target = tmp_path / module.RECEIPT_NAME
    payload = {"probe": module.PROBE_NAME, "verdict": "FAIL", "page": {"verdict": "FAIL"}}

    module._write_receipt_atomic(target, payload)

    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8")) == payload
    assert [path.name for path in tmp_path.iterdir()] == [module.RECEIPT_NAME]


def test_capture_is_staged_until_its_bytes_are_verified(tmp_path):
    """A killed run leaves ``.part`` - never a file named like evidence."""
    module = _load_module()
    final = tmp_path / module.SHOT_NAME
    staged = module._staged(final)
    staged.write_bytes(_png_bytes(320, 200))

    assert staged.name == "browser-shot.png.part"
    assert not final.exists()

    detail = module._capture_check(
        {"captured": True, "dry_run": False, "path": str(staged)},
        out=str(staged),
        region=(320, 200),
    )
    assert detail["sha256"] == module._file_sha256(staged)
    assert detail["bytes"] == len(_png_bytes(320, 200))

    module._finalize(staged, final)

    assert final.is_file()
    assert not staged.exists()


def test_capture_check_rejects_bytes_that_are_not_the_requested_region(tmp_path):
    module = _load_module()
    staged = tmp_path / "browser-shot.png.part"
    staged.write_bytes(_png_bytes(800, 600))

    with pytest.raises(RuntimeError) as excinfo:
        module._capture_check(
            {"captured": True, "dry_run": False, "path": str(staged)},
            out=str(staged),
            region=(1280, 800),
        )

    assert "800x600" in str(excinfo.value)


def test_tabs_check_turns_a_cdp_error_payload_into_a_failure():
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._tabs_check({"error": "HTTP Error 404: Not Found", "tool": "browser_tabs"},
                           "target-a")

    assert "no tab list" in str(excinfo.value)


def test_missing_judgement_stays_free_of_volatile_text():
    module = _load_module()
    error = ("no page target matched url_contains='missing-target'; available URLs: "
             "'http://127.0.0.1:53100/acceptance-pages/target-a.html'")

    judgement = module._missing_judgement(error)

    assert judgement == {"error_kind": module.NO_PAGE_ERROR, "error_matched": True}
    assert "53100" not in json.dumps(judgement)


def test_the_no_write_baseline_is_snapshotted_before_any_probe_call():
    """A dry run that writes anyway must be caught, so the baseline comes first."""
    source = PROBE.read_text(encoding="utf-8")
    main_source = source[source.index("def main("):]

    baseline = main_source.index("before = _file_state(final_png)")
    first_call = main_source.index('"name": "browser_shot"')

    assert baseline < first_call, (
        "the dry-run-wrote-nothing baseline is taken after a browser_shot call, "
        "which makes the check compare a state with itself"
    )