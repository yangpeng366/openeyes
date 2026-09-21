"""Abstract: merge multiple trajectories into a DAG, generate natural-language description.

No vision needed. We work only on:
- step.action (click / type / hotkey / ...)
- element.control_type / name / automation_id
- step.intent (free text)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter, defaultdict
import json

from .event import Trajectory, UIAEvent, InputEvent


@dataclass
class ActionNode:
    """One abstracted action in the workflow DAG.

    key: dedup key (selector hash + action type)
    label: human-readable summary like 'Click on "Secure my connection" (Button)'
    count: how many trajectories hit this node (for confidence)
    sample_intents: optional free-text intent examples collected from recordings
    """
    key: str
    label: str
    action: str
    selector: str
    element_summary: Dict[str, Any]
    count: int = 1
    sample_intents: List[str] = field(default_factory=list)


@dataclass
class ActionEdge:
    """A directed edge between two nodes (with optional count)."""
    from_key: str
    to_key: str
    count: int = 1


@dataclass
class ActionGraph:
    nodes: List[ActionNode] = field(default_factory=list)
    edges: List[ActionEdge] = field(default_factory=list)
    workflow_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [
                {**n.__dict__, "sample_intents": n.sample_intents[:3]}
                for n in self.nodes
            ],
            "edges": [e.__dict__ for e in self.edges],
        }

    def to_dot(self) -> str:
        """Emit Graphviz DOT for visualisation."""
        lines = [f"digraph \"{self.workflow_name}\" {{", "  rankdir=LR;"]
        for n in self.nodes:
            label = n.label.replace("\"", "\\\"")
            lines.append(f'  "{n.key}" [label="{label}\\n(n={n.count})"];')
        for e in self.edges:
            label = f"n={e.count}"
            lines.append(f'  "{e.from_key}" -> "{e.to_key}" [label="{label}"];')
        lines.append("}")
        return "\n".join(lines)


def _node_key_for_step(step) -> Optional[str]:
    """Build a stable key for dedup; None if the step can't participate in the graph."""
    if isinstance(step, InputEvent):
        if step.action == "wait":
            return None   # wait is timing, not part of logical flow
        return f"hotkey:{step.combo}"
    if isinstance(step, UIAEvent):
        ed = step.element
        ctype = (ed.get("control_type") or "").strip() or "Control"
        sel = step.selector or f"hint={ed.get('hint')}"
        if step.action == "type":
            return f"type:{ctype}:{sel}:{hash((step.input_text or '')[:16]) & 0xffff}"
        return f"{step.action}:{ctype}:{sel}"
    return None


def _node_label_for_step(step) -> Tuple[str, Dict[str, Any]]:
    if isinstance(step, InputEvent):
        return f"Hotkey {step.combo}", {"kind": "hotkey", "combo": step.combo}
    ed = step.element
    name = (ed.get("name") or "").strip()
    ctype = (ed.get("control_type") or "").strip()
    if isinstance(step, UIAEvent) and step.action == "type":
        text = (step.input_text or "")
        if len(text) > 20:
            text = text[:17] + "..."
        label = f'Type "{text}" into {ctype or "field"} "{name}"'
        summary = {"kind": "type", "text": step.input_text, "control_type": ctype, "name": name}
        return label, summary
    label = f'{step.action.capitalize()} {ctype or "element"} "{name}"'
    summary = {
        "kind": step.action,
        "control_type": ctype,
        "name": name,
        "automation_id": ed.get("automation_id", ""),
        "selector": step.selector,
    }
    return label, summary


def merge_trajectories(trajectories: List[Trajectory], workflow_name: str = "") -> ActionGraph:
    """Build a DAG from one or more trajectories of the same workflow.

    Consecutive steps with the same key are merged. Edges track how often a
    transition occurred across recordings - useful to see both happy path and
    detours.
    """
    nodes_by_key: Dict[str, ActionNode] = {}
    edge_counts: Counter = Counter()
    edge_meta: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for traj in trajectories:
        prev_key: Optional[str] = None
        for step in traj.steps:
            key = _node_key_for_step(step)
            if key is None:
                continue
            label, summary = _node_label_for_step(step)
            if key in nodes_by_key:
                n = nodes_by_key[key]
                n.count += 1
                if isinstance(step, UIAEvent) and step.intent and step.intent not in n.sample_intents:
                    if len(n.sample_intents) < 5:
                        n.sample_intents.append(step.intent)
            else:
                n = ActionNode(
                    key=key,
                    label=label,
                    action=summary.get("kind", step.action),
                    selector=summary.get("selector", ""),
                    element_summary=summary,
                )
                if isinstance(step, UIAEvent) and step.intent:
                    n.sample_intents.append(step.intent)
                nodes_by_key[key] = n
            if prev_key is not None and prev_key != key:
                edge_counts[(prev_key, key)] += 1
                if isinstance(step, UIAEvent) and step.intent:
                    edge_meta[(prev_key, key)].append(step.intent)
            prev_key = key

    edges = [ActionEdge(from_key=fk, to_key=tk, count=c)
             for (fk, tk), c in edge_counts.most_common()]

    return ActionGraph(
        nodes=list(nodes_by_key.values()),
        edges=edges,
        workflow_name=workflow_name or (trajectories[0].name if trajectories else ""),
    )


def to_natural_language(trajectory: Trajectory) -> str:
    """Render one trajectory as plain English steps.

    Useful for LLM prompts and human review. Output is text only.
    """
    lines = [f"# {trajectory.name}", ""]
    if trajectory.description:
        lines += [trajectory.description, ""]
    lines.append(f"App: {trajectory.app_name or '(any)'}")
    lines.append(f"Steps: {len(trajectory.steps)}")
    lines.append("")
    for i, step in enumerate(trajectory.steps, 1):
        if isinstance(step, InputEvent):
            if step.action == "hotkey":
                intent = f" - {step.intent}" if step.intent else ""
                lines.append(f"{i}. Press hotkey `{step.combo}`{intent}")
            elif step.action == "wait":
                lines.append(f"{i}. Wait {step.duration_ms or 500} ms")
            else:
                lines.append(f"{i}. {step.action}")
            continue
        ed = step.element
        name = (ed.get("name") or "").strip()
        ctype = (ed.get("control_type") or "").strip()
        if step.action == "type":
            text = step.input_text or ""
            if len(text) > 60:
                text = text[:57] + "..."
            intent = f" - {step.intent}" if step.intent else ""
            lines.append(f'{i}. Type "{text}" into {ctype} "{name}"{intent}')
        elif step.action == "click" or step.action.endswith("_click"):
            intent = f" - {step.intent}" if step.intent else ""
            lines.append(f'{i}. {step.action.replace("_", " ").capitalize()} on {ctype} "{name}"{intent}')
        else:
            intent = f" - {step.intent}" if step.intent else ""
            lines.append(f'{i}. {step.action} on {ctype} "{name}"{intent}')
    lines.append("")
    return "\n".join(lines)


def graph_to_natural_language(graph: ActionGraph) -> str:
    """Render a DAG as a paragraph-form happy path + variants list."""
    if not graph.nodes:
        return "(empty workflow)"
    # Best path: highest-count edges, greedily
    out = [f"# {graph.workflow_name}", ""]
    out.append(f"Nodes: {len(graph.nodes)}, edges: {len(graph.edges)}")
    out.append("")
    out.append("## Nodes (by frequency)")
    for n in sorted(graph.nodes, key=lambda x: -x.count):
        intents = "; ".join(n.sample_intents[:2])
        suffix = f" - {intents}" if intents else ""
        out.append(f"- [{n.count}*] {n.label}{suffix}")
    out.append("")
    out.append("## Top transitions")
    for e in sorted(graph.edges, key=lambda x: -x.count)[:15]:
        f_label = next((n.label for n in graph.nodes if n.key == e.from_key), e.from_key)
        t_label = next((n.label for n in graph.nodes if n.key == e.to_key), e.to_key)
        out.append(f"- [{e.count}*] {f_label} -> {t_label}")
    out.append("")
    return "\n".join(out)