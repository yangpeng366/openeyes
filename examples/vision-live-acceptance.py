"""End-to-end contract for the vision tier: HTTP endpoint -> ``Element[]``.

The vision backend already had two modes and unit tests for the *file* mode
(``tests/test_vision_backend.py``). Nothing exercised the **endpoint** mode:
the claim "an OmniParser service can feed the vision tier" had no test that
could fail. This probe is that test.

Three ways to run it, and each one has a verdict that is not "looks fine":

    python examples\\vision-live-acceptance.py --serve-fixture
        Run a local stub that answers the OmniParser contract. The probe
        asserts the image really travelled (sha256 of the posted base64 equals
        the sha256 of the PNG on disk) and that the detections came back as
        window-relative elements. This is the arithmetic contract.

    python examples\\vision-live-acceptance.py --endpoint http://127.0.0.1:8080/parse
        Same client path against a real service. Unknown detections, so it
        checks reachability + non-empty + window-relative sanity, not exact
        numbers. ``OPENEYES_VISION_ENDPOINT`` supplies the endpoint too.

    python examples\\vision-live-acceptance.py
        No endpoint anywhere: SKIP with the reason, exit 3. A machine without
        a model must never report PASS for this capability.

`--inject-failure endpoint|parse` makes one surface red for real (HTTP 500 /
empty detections), so "the probe can fail" is a demonstrated fact rather than
a reading of the code.

Verdict rules, inherited from ``examples/_acceptance_verdict.py``:

* endpoint configured but unreachable, 500, or answering garbage -> FAIL
  (that is a broken service, not an absent one);
* endpoint answers but the detections do not parse into elements -> FAIL;
* nothing configured at all -> SKIP.

The receipt keeps the shared two-slot shape. This probe has no CDP page and no
PowerShell leg, so the slots carry the two surfaces that can actually fail:
``page`` = the HTTP transport, ``powershell`` = the local parse of the answer.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

EXAMPLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent
for _path in (str(REPO_ROOT), str(EXAMPLES_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _acceptance_verdict import (  # noqa: E402, F401 - names are re-exported via VERDICT_CONTRACT
    EXIT_CODES,
    EXIT_INTERRUPTED,
    FAIL,
    INTERRUPTED,
    PASS,
    SKIP,
    OutDirError,
    PageCheckError,
    _check,
    _combine_verdicts,
    _configure_stdout,
    _default_out_dir,
    _discard,
    _file_sha256,
    _finalize,
    _leg,
    _out_dir_failure,
    _receipt,
    _require_free_evidence,
    _resolve_out_dir,
    _staged,
    _utc_now,
    _verdict_exit_code,
    _write_receipt_atomic,
)

from openeyes.backends.vision import (  # noqa: E402
    OmniParserBackend,
    VisionUnavailableError,
)

PROBE_NAME = "vision-live-acceptance"
RECEIPT_NAME = "vision-live-receipt.json"
IMAGE_NAME = "vision-live-probe.png"

#: The synthetic image. Small, deterministic, dependency-free (no Pillow).
IMAGE_SIZE = (96, 64)
#: Window top-left inside the image, so "image pixel - origin" is observable.
WINDOW_ORIGIN = (40, 24)

#: The detections the fixture stub answers with, in OmniParser v2 shape.
FIXTURE_DETECTIONS: list[list[Any]] = [
    [[100, 80, 240, 140], "button"],
    [[60, 40, 200, 76], "text"],
]
#: What those detections must become, in window-relative coordinates:
#: bbox = (x1 - 40, y1 - 24, w, h), center = bbox top-left + w/2, h/2.
EXPECTED_ELEMENTS = [
    {"control_type": "Button", "x": 60, "y": 56, "w": 140, "h": 60,
     "cx": 130, "cy": 86},
    {"control_type": "Text", "x": 20, "y": 16, "w": 140, "h": 36,
     "cx": 90, "cy": 34},
]

#: The names this probe takes from the shared verdict module. Tests assert
#: they are the same objects, so the rules cannot be forked into a copy.
VERDICT_CONTRACT = {
    name: globals()[name]
    for name in (
        "PASS",
        "FAIL",
        "SKIP",
        "INTERRUPTED",
        "EXIT_INTERRUPTED",
        "OutDirError",
        "PageCheckError",
        "_check",
        "_combine_verdicts",
        "_leg",
        "_receipt",
        "_resolve_out_dir",
        "_require_free_evidence",
        "_staged",
        "_finalize",
        "_write_receipt_atomic",
        "_verdict_exit_code",
        "_discard",
    )
}


# ===== synthetic image =====


def _png(width: int, height: int, rgb: tuple[int, int, int] = (28, 28, 28)) -> bytes:
    """A valid RGB PNG with no third-party dependency."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    stride = bytes(rgb) * width
    raw = b"".join(b"\x00" + stride for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _write_image(path: Path) -> Path:
    path.write_bytes(_png(*IMAGE_SIZE))
    return path


# ===== fixture service =====


class _FixtureState:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.received_sha256: str | None = None
        self.received_bytes: int | None = None
        self.requests = 0


def _make_handler(state: _FixtureState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep stdout clean
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib spelling
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            state.requests += 1
            try:
                payload = json.loads(body.decode("utf-8"))
                image = base64.b64decode(payload["image"], validate=True)
            except Exception:
                self._answer(400, {"error": "body is not {image: base64}"})
                return
            state.received_sha256 = hashlib.sha256(image).hexdigest()
            state.received_bytes = len(image)
            if state.mode == "error":
                self._answer(500, {"error": "injected-endpoint-failure"})
                return
            if state.mode == "empty":
                self._answer(200, [])
                return
            self._answer(200, FIXTURE_DETECTIONS)

        def _answer(self, status: int, payload: Any) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

    return Handler


def _start_fixture(mode: str) -> tuple[ThreadingHTTPServer, str, _FixtureState, threading.Thread]:
    state = _FixtureState(mode)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/parse", state, thread


# ===== checks =====


def _require_answers(elements: list, *, endpoint: str) -> dict:
    """The endpoint leg only claims "a service answered and decoded".

    An empty answer is a *service* that spoke and a *parse* that produced
    nothing, so emptiness is judged by the parse leg, not here; mixing the two
    would report a parse bug as an unreachable endpoint.

    The URL is deliberately *not* part of the answer: it carries an ephemeral
    port, and the judgement digest has to be stable across two runs.
    """
    del endpoint
    return {"elements": len(elements)}


def _require_image_travelled(
    state: _FixtureState, image_path: Path
) -> dict:
    """The bytes the service received must be the bytes on disk."""
    local = _file_sha256(image_path)
    if state.received_sha256 is None:
        raise AssertionError("the fixture never received an image")
    if state.received_sha256 != local:
        raise AssertionError(
            f"posted image sha256 {state.received_sha256} != file sha256 {local}"
        )
    return {
        "image_sha256": local,
        "bytes": state.received_bytes,
        "requests": state.requests,
    }


def _require_window_relative(elements: list) -> dict:
    """Detections are image pixels; elements must be window-relative."""
    if len(elements) != len(EXPECTED_ELEMENTS):
        raise AssertionError(
            f"expected {len(EXPECTED_ELEMENTS)} elements, got {len(elements)}"
        )
    measured = []
    for element, expected in zip(elements, EXPECTED_ELEMENTS, strict=True):
        actual = {
            "control_type": element.control_type,
            "x": element.bbox.x,
            "y": element.bbox.y,
            "w": element.bbox.w,
            "h": element.bbox.h,
            "cx": element.center.x,
            "cy": element.center.y,
        }
        if actual != expected:
            raise AssertionError(f"element mismatch: {actual} != {expected}")
        if actual["x"] < 0 or actual["y"] < 0:
            raise AssertionError(f"element still in image space: {actual}")
        measured.append(actual)
    return {"origin": list(WINDOW_ORIGIN), "elements": measured}


def _require_sane_elements(elements: list) -> dict:
    """Live endpoint: unknown detections, so only the invariants are checked."""
    for element in elements:
        if not element.control_type:
            raise AssertionError(f"element without control_type: {element!r}")
        if element.bbox.x < 0 or element.bbox.y < 0:
            raise AssertionError(
                f"bbox outside the window-relative space: {element.to_dict()}"
            )
    return {"elements": len(elements)}


# ===== main =====


def _endpoint_from(args) -> str | None:
    if args.endpoint:
        return args.endpoint
    env = os.environ.get("OPENEYES_VISION_ENDPOINT", "").strip()
    return env or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--endpoint",
        default=None,
        help="OmniParser-style service; falls back to OPENEYES_VISION_ENDPOINT",
    )
    parser.add_argument(
        "--serve-fixture",
        action="store_true",
        help="run a local stub that answers the contract (no model, no network)",
    )
    parser.add_argument(
        "--inject-failure",
        choices=("none", "endpoint", "parse"),
        default="none",
        help="make one surface red for real and report the verdict",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--out",
        default=None,
        help="evidence directory; default examples/out/<probe>-<utc>",
    )
    parser.add_argument("--force", action="store_true",
                        help="replace existing evidence in --out")
    args = parser.parse_args(argv)

    _configure_stdout()
    started_at = _utc_now()
    judgement: dict = {"injected_failure": args.inject_failure}
    checks: list[dict] = []

    try:
        out_dir = _resolve_out_dir(args.out or _default_out_dir(PROBE_NAME))
        _require_free_evidence(out_dir, [RECEIPT_NAME, IMAGE_NAME], force=args.force)
    except OutDirError as exc:
        return _out_dir_failure(PROBE_NAME, exc)

    receipt_path = out_dir / RECEIPT_NAME
    final_image = out_dir / IMAGE_NAME
    staged_image = _staged(final_image)
    image_path = _write_image(staged_image)

    # The stub's own vocabulary: "error" answers HTTP 500, "empty" answers [].
    serve_mode = {"endpoint": "error", "parse": "empty"}.get(
        args.inject_failure, "ok"
    )
    server = thread = state = None
    if args.serve_fixture or args.inject_failure in ("endpoint", "parse"):
        server, fixture_endpoint, state, thread = _start_fixture(serve_mode)
        endpoint = fixture_endpoint
        judgement["endpoint_source"] = "fixture"
    else:
        endpoint = _endpoint_from(args)
        judgement["endpoint_source"] = "flag" if args.endpoint else (
            "env" if endpoint else "none"
        )

    page = _leg(SKIP, reason="endpoint-leg-not-run")
    powershell = _leg(SKIP, reason="parse-leg-not-run")

    try:
        if endpoint is None:
            page = _leg(SKIP, reason="no-vision-endpoint-configured")
            powershell = _leg(SKIP, reason="nothing-to-parse")
            judgement["skip"] = (
                "no --endpoint, no OPENEYES_VISION_ENDPOINT, no --serve-fixture"
            )
        else:
            parser_backend = OmniParserBackend(
                endpoint=endpoint, timeout=args.timeout, origin=WINDOW_ORIGIN
            )
            try:
                with _check(checks, "endpoint-answers"):
                    elements = parser_backend.parse(str(image_path))
                    endpoint_detail = _require_answers(
                        elements, endpoint=endpoint
                    )
                judgement["endpoint"] = endpoint_detail
                checks[-1]["detail"] = endpoint_detail
            except (AssertionError, PageCheckError, VisionUnavailableError) as exc:
                if isinstance(exc, PageCheckError):
                    reason = exc.check
                elif isinstance(exc, VisionUnavailableError):
                    reason = "endpoint-unreachable-or-not-json"
                else:
                    reason = "endpoint-empty"
                page = _leg(FAIL, reason=reason, checks=checks, error=str(exc))
                elements = []
            else:
                page = _leg(PASS, checks=checks)

            if page["verdict"] == PASS:
                try:
                    with _check(checks, "image-really-sent"):
                        if state is not None:
                            travelled = _require_image_travelled(state, image_path)
                        else:
                            travelled = {"skipped": "live-endpoint-cannot-observe-body"}
                    judgement["image"] = travelled
                    checks[-1]["detail"] = travelled

                    with _check(checks, "detections-nonempty"):
                        if not elements:
                            raise AssertionError(
                                "the endpoint answered but no detection parsed "
                                "into an element"
                            )
                    checks[-1]["detail"] = {"elements": len(elements)}

                    with _check(checks, "window-relative-coords"):
                        if state is not None and serve_mode == "ok":
                            coords = _require_window_relative(elements)
                        else:
                            coords = _require_sane_elements(elements)
                    judgement["coordinates"] = coords
                    checks[-1]["detail"] = coords
                except (AssertionError, PageCheckError) as exc:
                    reason = (
                        f"parse-{exc.check}" if isinstance(exc, PageCheckError)
                        else "parse-invalid-detections"
                    )
                    powershell = _leg(FAIL, reason=reason, checks=checks,
                                      error=str(exc))
                else:
                    powershell = _leg(PASS, checks=checks)
            else:
                # A red endpoint leg makes the parse leg untested, not passed.
                powershell = _leg(SKIP, reason="endpoint-leg-failed")
                _discard(staged_image)

        if powershell["verdict"] == PASS:
            _finalize(staged_image, final_image)
        else:
            _discard(staged_image)

        receipt = _receipt(
            probe=PROBE_NAME,
            page=page,
            powershell=powershell,
            judgement=judgement,
            checks=checks,
            injected_failures={
                "page": args.inject_failure if args.inject_failure == "endpoint" else None,
                "powershell": args.inject_failure if args.inject_failure == "parse" else None,
            },
            run={
                "started_at": started_at,
                "finished_at": _utc_now(),
                "python": sys.version.split()[0],
                "timeout": args.timeout,
                "serve_fixture": bool(server is not None),
                "endpoint": endpoint,
            },
            evidence={
                "directory": str(out_dir),
                "receipt": str(receipt_path),
                "png": str(final_image) if final_image.is_file() else None,
            },
        )
        try:
            _write_receipt_atomic(receipt_path, receipt)
        except OSError as exc:
            print(json.dumps({
                "probe": PROBE_NAME,
                "verdict": FAIL,
                "reason": "receipt-write",
                "error": str(exc),
                "judgement": judgement,
            }, ensure_ascii=False))
            return EXIT_CODES[FAIL]
        print(json.dumps(receipt, ensure_ascii=False))
        return _verdict_exit_code(receipt["verdict"])
    except KeyboardInterrupt:
        _discard(staged_image, final_image,
                 receipt_path.with_name(receipt_path.name + ".tmp"))
        print(json.dumps({
            "probe": PROBE_NAME,
            "verdict": INTERRUPTED,
            "reason": "interrupted-before-receipt",
            "checks": checks,
            "evidence": {"directory": str(out_dir), "receipt": None, "png": None},
        }, ensure_ascii=False))
        return EXIT_INTERRUPTED
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
