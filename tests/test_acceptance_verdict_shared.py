"""The four browser acceptance probes must judge through one shared model.

``examples/_acceptance_verdict.py`` owns PASS/FAIL/SKIP, the exit-code table,
the PowerShell classifier, the judgement digest, the receipt shape and the
``--out`` / interruption contract. These tests assert that the probes import
those objects instead of carrying a copy: a probe that pastes ``EXIT_CODES``
back into itself - or defines its own ``_combine_verdicts`` - fails here even
if its own behaviour still looks right.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PROBES = (
    "browser-type-acceptance.py",
    "browser-shot-acceptance.py",
    "browser-scan-acceptance.py",
    "browser-click-acceptance.py",
)
SHARED_SOURCE = EXAMPLES / "_acceptance_verdict.py"

#: Copied-into-the-probe regressions: each of these must live in the shared
#: module only. Defining one in a probe is what this file is here to catch.
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
    "_classify_powershell",
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
#: The verdict model every probe must expose, by the shared module's names.
REQUIRED_CONTRACT = {
    "PASS",
    "FAIL",
    "SKIP",
    "INTERRUPTED",
    "EXIT_CODES",
    "EXIT_INTERRUPTED",
    "OutDirError",
    "PageCheckError",
    "_check",
    "_classify_powershell",
    "_combine_verdicts",
    "_discard",
    "_finalize",
    "_judgement_sha256",
    "_leg",
    "_receipt",
    "_require_free_evidence",
    "_resolve_out_dir",
    "_staged",
    "_verdict_exit_code",
    "_write_receipt_atomic",
}


def _shared():
    if str(EXAMPLES) not in sys.path:
        sys.path.insert(0, str(EXAMPLES))
    import _acceptance_verdict  # noqa: PLC0415

    return _acceptance_verdict


def _load(name: str):
    path = EXAMPLES / name
    module_name = name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tree(name: str) -> ast.Module:
    return ast.parse((EXAMPLES / name).read_text(encoding="utf-8"))


def _imported_from_shared(name: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(name)):
        if isinstance(node, ast.ImportFrom) and node.module == "_acceptance_verdict":
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("probe", PROBES)
def test_every_probe_imports_the_verdict_model(probe):
    imported = _imported_from_shared(probe)

    assert imported, f"{probe} does not import from _acceptance_verdict"
    assert imported >= REQUIRED_CONTRACT, sorted(REQUIRED_CONTRACT - imported)


@pytest.mark.parametrize("probe", PROBES)
def test_every_probe_exposes_the_shared_objects_by_identity(probe):
    shared = _shared()
    module = _load(probe)

    assert set(module.VERDICT_CONTRACT) >= REQUIRED_CONTRACT
    for name, value in module.VERDICT_CONTRACT.items():
        assert hasattr(shared, name), f"{name} is not in the shared module"
        assert value is getattr(shared, name), (
            f"{probe} carries its own {name}, not the shared one"
        )


@pytest.mark.parametrize("probe", PROBES)
def test_no_probe_redefines_the_verdict_rules(probe):
    """A pasted copy of a rule fails here even if the values still match."""
    tree = _tree(probe)
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


@pytest.mark.parametrize("probe", PROBES)
def test_every_probe_reads_the_same_exit_code_table(probe):
    shared = _shared()
    module = _load(probe)

    assert module.EXIT_CODES is shared.EXIT_CODES
    assert [module._verdict_exit_code(v) for v in ("PASS", "FAIL", "SKIP")] == [0, 2, 3]
    assert module.EXIT_INTERRUPTED == 130


@pytest.mark.parametrize("probe", PROBES)
def test_skip_is_never_promoted_to_pass_in_any_probe(probe):
    module = _load(probe)

    assert module._combine_verdicts(module.PASS, module.SKIP) == module.SKIP
    assert module._combine_verdicts(module.SKIP, module.SKIP) == module.SKIP
    assert module._combine_verdicts(module.SKIP, module.FAIL) == module.FAIL
    with pytest.raises(ValueError):
        module._combine_verdicts(module.PASS, "passed")


@pytest.mark.parametrize("probe", PROBES)
def test_receipt_shape_and_digest_are_identical_across_probes(probe):
    """The receipt is built by ``_receipt`` for every probe, so it cannot drift."""
    shared = _shared()
    module = _load(probe)
    page = module._leg(module.PASS, checks=[{"check": "tabs", "ok": True}])
    powershell = module._leg(
        module.PASS, executable="pwsh", returncode=0, stdout="200", stderr="",
        timed_out=False,
    )
    judgement = {"answer": 42}
    checks = [{"check": "tabs", "ok": True}]

    receipt = module._receipt(
        probe=module.PROBE_NAME,
        page=page,
        powershell=powershell,
        judgement=judgement,
        checks=checks,
        run={"started_at": "a", "finished_at": "b"},
        evidence={"directory": "d", "receipt": "r"},
        injected_failures={"page": None, "powershell": None},
    )

    assert set(receipt) == {
        "probe", "verdict", "exit_code", "page", "powershell", "judgement",
        "judgement_sha256", "injected_failures", "run", "evidence",
    }
    assert receipt["verdict"] == module.PASS
    assert receipt["exit_code"] == 0
    assert receipt["judgement_sha256"] == shared._judgement_sha256(
        probe=module.PROBE_NAME,
        verdict=module.PASS,
        page=module.PASS,
        powershell=module.PASS,
        checks=checks,
        judgement=judgement,
    )


def test_a_failing_leg_makes_the_receipt_fail_in_every_probe():
    for probe in PROBES:
        module = _load(probe)
        receipt = module._receipt(
            probe=module.PROBE_NAME,
            page=module._leg(module.FAIL, reason="page-tabs"),
            powershell=module._leg(module.PASS),
            judgement={},
            checks=[],
            run={},
            evidence={},
            injected_failures={"page": None, "powershell": None},
        )

        assert receipt["verdict"] == module.FAIL
        assert receipt["exit_code"] == 2


def test_the_shared_module_is_the_only_definition_of_the_table():
    """The shared module itself must still hold exactly one table."""
    tree = ast.parse(SHARED_SOURCE.read_text(encoding="utf-8"))
    tables = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "EXIT_CODES"
            for target in node.targets
        )
    ]

    assert len(tables) == 1
    value = tables[0].value
    assert isinstance(value, ast.Dict)
    keys = [key.id for key in value.keys if isinstance(key, ast.Name)]
    assert keys == ["PASS", "FAIL", "SKIP"]
    assert [ast.literal_eval(item) for item in value.values] == [0, 2, 3]


def test_injection_naming_the_powershell_leg_supplies_a_failing_command():
    shared = _shared()

    injected = shared.INJECTED_POWERSHELL_FAILURE
    assert shared._injected_ps_command("powershell", None) == injected
    assert shared._injected_ps_command("both", None) == injected
    assert shared._injected_ps_command("none", None) is None
    assert shared._injected_ps_command("page", None) is None
    assert "exit 1" in injected
    assert "200" not in injected


def test_an_explicit_ps_command_still_wins_over_the_injection():
    shared = _shared()

    assert shared._injected_ps_command("both", "Write-Output 200") == "Write-Output 200"


def test_the_injected_command_is_classified_fail_not_skip():
    """The injection has to reach a red leg, or it proves nothing."""
    shared = _shared()

    record = shared._run_powershell(shared.INJECTED_POWERSHELL_FAILURE, timeout=30)
    leg = shared._classify_powershell(record)

    assert record["returncode"] not in (0, None)
    assert leg["verdict"] == shared.FAIL


@pytest.mark.parametrize("probe", PROBES)
def test_every_probe_routes_the_injection_through_the_shared_resolver(probe):
    """A probe that ignores `--inject-failure powershell` hides its own red state."""
    assert "_injected_ps_command" in _imported_from_shared(probe)

    calls = [
        node
        for node in ast.walk(_tree(probe))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_injected_ps_command"
    ]
    assert calls, f"{probe} never calls _injected_ps_command"
    for call in calls:
        assert len(call.args) == 2


def test_powershell_pass_needs_the_exact_status_not_the_number_somewhere():
    """exit 0 + an error line that mentions 200 must still be FAIL."""
    shared = _shared()

    noisy = shared._classify_powershell(
        {"executable": "pwsh", "returncode": 0, "stdout": "Error 500: expected 200", "stderr": ""}
    )
    assert noisy["verdict"] == shared.FAIL
    assert noisy["reason"] == "powershell-unexpected-output"

    exact = shared._classify_powershell(
        {"executable": "pwsh", "returncode": 0, "stdout": "200", "stderr": ""}
    )
    assert exact["verdict"] == shared.PASS


def test_powershell_nonzero_exit_and_timeout_are_fail_not_skip():
    shared = _shared()

    failed = shared._classify_powershell(
        {"executable": "pwsh", "returncode": 1, "stdout": "200", "stderr": ""}
    )
    assert failed["verdict"] == shared.FAIL

    timed_out = shared._classify_powershell(
        {"executable": "pwsh", "returncode": None, "stdout": "", "stderr": "", "timed_out": True}
    )
    assert timed_out["verdict"] == shared.FAIL


def test_powershell_skip_is_reserved_for_an_unrunnable_environment():
    shared = _shared()

    leg = shared._classify_powershell({"unavailable": "no interpreter"})

    assert leg["verdict"] == shared.SKIP
    assert leg["reason"] == "no interpreter"