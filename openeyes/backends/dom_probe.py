"""One DOM probe shared by every structured browser backend.

The CDP and WebDriver BiDi backends must agree on what an "interactive
element" is, otherwise a caller switching protocols would see a different
page model. The probe therefore lives here once, with a coordinate space
placeholder each backend fills in.

Coordinate spaces:
    ``viewport``  - CSS pixels relative to the visible viewport. This is what
                    WebDriver BiDi ``input.performActions`` expects.
    ``document``  - CSS pixels relative to the page origin, including scroll.
                    This is what the CDP backend has always reported.
"""
from __future__ import annotations

from openeyes.core.schema import BBox, Center, Element, ElementState

#: JS payload: walk the DOM, return JSON array. Single top-level expression
#: so it can be serialised directly by either protocol.
DOM_JS_TEMPLATE = """(function() {
  var INTERACTIVE = "a[href],button,input,textarea,select,[contenteditable]," + "[tabindex],[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],summary,label";
  var COORD_SPACE = "__COORD_SPACE__";
  function nameOf(el) {
    var al = el.getAttribute("aria-label");
    if (al && al.trim()) return al.trim();
    var ph = el.getAttribute("placeholder");
    if (ph && ph.trim()) return ph.trim();
    var tt = el.getAttribute("title");
    if (tt && tt.trim()) return tt.trim();
    var al2 = el.getAttribute("alt");
    if (al2 && al2.trim()) return al2.trim();
    var txt = (el.innerText || el.textContent || "").trim();
    if (txt) return txt.replace(/\\s+/g, " ").slice(0, 200);
    var n = el.getAttribute("name");
    if (n) return n;
    return el.id || "";
  }
  function roleOf(el) { return el.getAttribute("role") || ""; }
  function textOf(el) { var t = (el.innerText || el.textContent || "").trim(); return t ? t.replace(/\\s+/g, " ").slice(0, 200) : ""; }
  function parentTypes(el) { var out = [], p = el; while (p && p !== document.body && out.length < 6) { p = p.parentElement; if (p) out.push(p.tagName.toLowerCase()); } return out.reverse(); }
  function selectorOf(el) { if (el.id) return "#" + el.id; if (el.getAttribute("data-testid")) return "[data-testid=" + JSON.stringify(el.getAttribute("data-testid")) + "]"; if (el.getAttribute("name")) return el.tagName.toLowerCase() + "[name=" + JSON.stringify(el.getAttribute("name")) + "]"; return el.tagName.toLowerCase(); }
  var nodes = document.querySelectorAll(INTERACTIVE);
  var out = [];
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (!el.isConnected) continue;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") continue;
    var cx = r.left + (COORD_SPACE === "document" ? window.scrollX : 0);
    var cy = r.top + (COORD_SPACE === "document" ? window.scrollY : 0);
    out.push({
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      type: el.getAttribute("type") || "",
      name: nameOf(el),
      text: textOf(el),
      automation_id: el.id || el.getAttribute("data-testid") || "",
      class_name: el.className && typeof el.className === "string" ? el.className : "",
      selector: selectorOf(el),
      bbox: { x: Math.round(cx), y: Math.round(cy), w: Math.round(r.width), h: Math.round(r.height) },
      center: { x: Math.round(cx + r.width / 2), y: Math.round(cy + r.height / 2) },
      parent_chain: parentTypes(el),
      enabled: !el.disabled && !el.getAttribute("aria-disabled"),
      visible: true,
      focused: el === document.activeElement,
      href: el.getAttribute("href") || "",
      value: el.value !== undefined ? String(el.value).slice(0, 80) : "",
    });
  }
  return out;
})()"""


def dom_js(coord_space: str = "viewport") -> str:
    """Render the probe for one coordinate space."""
    if coord_space not in ("viewport", "document"):
        raise ValueError("coord_space must be viewport or document")
    return DOM_JS_TEMPLATE.replace("__COORD_SPACE__", coord_space)


def control_type_map(raw: dict) -> str:
    """Same labels as the JS probe - keeps tests independent of JS."""
    tag = raw.get("tag", "")
    role = raw.get("role", "")
    type_ = (raw.get("type") or "").lower()
    if tag == "a" or role == "link":
        return "Hyperlink"
    if tag in ("button", "summary") or role == "button":
        return "Button"
    if tag == "select":
        return "ComboBox"
    if tag == "textarea":
        return "Edit"
    if tag == "input":
        if type_ in ("submit", "button"):
            return "Button"
        if type_ == "checkbox":
            return "CheckBox"
        if type_ == "radio":
            return "RadioButton"
        return "Edit"
    if role == "checkbox":
        return "CheckBox"
    if role == "radio":
        return "RadioButton"
    if role == "tab":
        return "Tab"
    if role == "menuitem":
        return "MenuItem"
    return raw.get("control_type") or (tag.title() if tag else "Unknown")


def make_element(raw: dict, backend: str = "cdp") -> Element:
    """Build one backend-agnostic element from a raw probe record."""
    bbox = BBox(**raw["bbox"])
    center = Center(**raw["center"])
    return Element(
        backend=backend,
        control_type=raw.get("control_type") or raw["tag"].title(),
        name=raw.get("name", ""),
        bbox=bbox,
        center=center,
        automation_id=raw.get("automation_id", ""),
        class_name=raw.get("class_name", ""),
        score=1.0,
        interactive=True,
        state=ElementState(
            enabled=raw.get("enabled", True),
            visible=raw.get("visible", True),
            focused=raw.get("focused", False),
        ),
        parent_chain=raw.get("parent_chain", []),
    )
