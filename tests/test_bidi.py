from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

from openeyes.backends import bidi
from openeyes.core.schema import BBox, Center, Element


def _message(message_id: int, result: dict) -> str:
    return json.dumps({"id": message_id, "type": "success", "result": result})


def test_connect_starts_bidi_session_and_selects_matching_context():
    fake_ws = MagicMock()
    fake_ws.recv.side_effect = [
        _message(1, {"sessionId": "session-1"}),
        _message(2, {"contexts": [
            {"context": "tab-a", "url": "https://other", "children": []},
            {"context": "tab-b", "url": "https://target", "children": []},
        ]}),
    ]
    with patch.object(bidi.websocket, "create_connection", return_value=fake_ws) as create:
        conn = bidi.connect(
            port=19222, url_contains="target", host_header="localhost:9222",
        )

    assert conn.session_id == "session-1"
    assert create.call_args.args == ("ws://localhost:19222/session",)
    assert create.call_args.kwargs["host"] == "localhost:9222"
    assert "subprotocols" not in create.call_args.kwargs
    assert conn.context_id == "tab-b"
    assert fake_ws.send.call_count == 2
    first = json.loads(fake_ws.send.call_args_list[0].args[0])
    assert first["method"] == "session.new"


def test_scan_dom_uses_selected_context_and_viewport_coordinates():
    conn = bidi.BidiConnection.__new__(bidi.BidiConnection)
    conn.context_id = "tab-b"
    conn.contexts = [
        {"context": "tab-a", "url": "https://other", "parent": None},
        {"context": "tab-b", "url": "https://target", "parent": None},
    ]
    conn.evaluate = MagicMock(return_value=[{
        "tag": "input", "role": "", "type": "text", "name": "手机号",
        "automation_id": "phone", "class_name": "field",
        "bbox": {"x": 10, "y": 20, "w": 100, "h": 30},
        "center": {"x": 60, "y": 35}, "enabled": True,
        "visible": True, "focused": False, "parent_chain": ["body"],
    }])
    conn._elements = []

    elements = bidi.scan_dom(conn)

    assert len(elements) == 1
    assert elements[0].backend == "bidi"
    assert elements[0].control_type == "Edit"
    assert elements[0].center == Center(60, 35)
    assert conn.evaluate.call_args.kwargs["context_id"] == "tab-b"


def test_focus_element_uses_viewport_point_not_unsafe_selector():
    conn = MagicMock()
    conn.evaluate.return_value = True
    element = Element(
        backend="bidi", control_type="Edit", name="手机号",
        bbox=BBox(10, 20, 100, 30), center=Center(60, 35),
        automation_id="data-testid-value",
    )

    assert bidi.focus_element(conn, element) is True
    expression = conn.evaluate.call_args.args[0]
    assert "elementFromPoint(x, y)" in expression
    assert expression.endswith("})(60, 35)")
    assert "querySelector" not in expression


def test_type_text_falls_back_to_bidi_key_actions_and_enter():
    conn = MagicMock()
    conn.insert_text.return_value = False
    bidi.BidiConnection.type_text(conn, "15928153396", press_enter=True)

    actions = conn.key_actions.call_args.args[0]
    assert actions[-2:] == [
        {"type": "keyDown", "value": "\uE007"},
        {"type": "keyUp", "value": "\uE007"},
    ]
    assert len(actions) == 2 * len("15928153396") + 2


def test_screenshot_decodes_png_and_returns_absolute_path(tmp_path):
    conn = MagicMock()
    conn.call.return_value = {"data": base64.b64encode(b"png-data").decode("ascii")}
    path = bidi.BidiConnection.screenshot(conn, str(tmp_path / "shot.png"))

    assert path.endswith("shot.png")
    assert (tmp_path / "shot.png").read_bytes() == b"png-data"


def test_failed_session_creation_closes_socket_and_preserves_remote_message():
    fake_ws = MagicMock()
    fake_ws.recv.return_value = json.dumps({
        "id": 1,
        "type": "error",
        "error": "session not created",
        "message": "Maximum number of active sessions",
    })
    with patch.object(bidi.websocket, "create_connection", return_value=fake_ws):
        try:
            bidi.BidiConnection("ws://localhost:9222/session")
        except bidi.BidiError as exc:
            assert "Maximum number of active sessions" in str(exc)
        else:
            raise AssertionError("expected BidiError")

    fake_ws.close.assert_called_once_with()


def test_close_ends_bidi_session_and_socket_once():
    conn = bidi.BidiConnection.__new__(bidi.BidiConnection)
    conn.session_id = "session-1"
    conn._closed = False
    conn._ws = MagicMock()
    conn.call = MagicMock(return_value={})

    conn.close()
    conn.close()

    conn.call.assert_called_once_with("session.end", {}, timeout=5.0)
    conn._ws.close.assert_called_once_with()
