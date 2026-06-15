"""Cover-letter rendering and document output.

Templates are rendered in a *sandboxed* Jinja2 environment (we are filling
human-authored templates, so the sandbox is a safety belt). The rendered letter
is written both as .txt (for the review preview) and .docx (ready to attach).
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

TRACK_LABELS = {
    "backend": "backend engineering",
    "frontend": "frontend engineering",
    "fullstack": "full-stack engineering",
    "devops": "DevOps / platform engineering",
    "data_scientist": "data science",
    "data_analyst": "data analytics",
    "ml_engineer": "machine-learning engineering",
    "general": "software engineering",
}


def track_label(track: str | None) -> str:
    if not track:
        return "software engineering"
    return TRACK_LABELS.get(track, track.replace("_", " "))


def render_template(template_path: str, context: dict) -> str:
    """Render a Jinja2 cover-letter template with the given context."""
    text = Path(template_path).read_text(encoding="utf-8")
    env = SandboxedEnvironment(
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,  # surface a missing slot instead of silently blanking
    )
    return env.from_string(text).render(**context).strip() + "\n"


def write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def write_docx(path: Path, text: str) -> str:
    """Write `text` to a .docx (paragraphs split on blank lines)."""
    from docx import Document

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for block in text.split("\n\n"):
        block = block.strip()
        if block:
            doc.add_paragraph(block)
    doc.save(str(path))
    return str(path)
