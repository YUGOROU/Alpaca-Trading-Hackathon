"""Load Alpaca Agent Skills (SKILL.md) as DECIDE-node context (README §5).

LangGraph has no native SKILL.md loader (that was DSH's feature), so "adding skills to
the agent" here means injecting the relevant SKILL.md text into the LLM's prompt as
domain reference — the model reads Alpaca's conventions when it makes its judgment.

Install skills into a folder and point AGENT_SKILLS_DIR at it, e.g.
    npx skills add alpacahq/alpaca-skills      # or clone the repo
Supported layouts: <root>/<name>/SKILL.md, <root>/<name>.md, or a bare <root>/SKILL.md.
"""
from __future__ import annotations

import os
from pathlib import Path


def discover_skills(root: str | Path) -> dict[str, Path]:
    """Map skill-name -> SKILL.md path under `root` (empty if none/root missing)."""
    root = Path(root)
    found: dict[str, Path] = {}
    if not root.exists():
        return found
    for p in root.rglob("SKILL.md"):
        name = p.parent.name if p.parent != root else "skill"
        found[name] = p
    for p in root.glob("*.md"):
        found.setdefault(p.stem, p)
    return found


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip()
    return text


def load_skill(path: str | Path) -> str:
    return _strip_frontmatter(Path(path).read_text(encoding="utf-8"))


def load_skills(
    root: str | Path | None = None,
    names: list[str] | None = None,
    max_chars: int = 6000,
) -> str:
    """Combine selected skills into one prompt-ready reference block (capped).

    `root` defaults to AGENT_SKILLS_DIR (or "skills"). `names` filters which to include;
    None includes all found. Returns "" when nothing is found (decider then runs skill-less).
    """
    root = root or os.getenv("AGENT_SKILLS_DIR", "skills")
    found = discover_skills(root)
    if names:
        found = {n: p for n, p in found.items() if n in names}
    if not found:
        return ""
    blocks = [f"## Skill: {name}\n{load_skill(path)}" for name, path in sorted(found.items())]
    return "\n\n".join(blocks)[:max_chars]
