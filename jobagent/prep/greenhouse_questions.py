"""Optional: fetch a Greenhouse job's REAL application questions and classify them.

Greenhouse exposes the actual form fields:

    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true

We pre-fill the fields we can map to stored profile facts and route every
free-text / salary / work-auth / demographic / unmappable field to the human.
Pure ``classify_questions`` is I/O-free for easy testing.
"""

from __future__ import annotations

from typing import Mapping

from ..logging_setup import get_logger
from ..source.base import company_token
from ..source.http_client import PoliteClient

log = get_logger("prep.greenhouse")

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true"

_SALARY = ("salary", "compensation", "desired pay", "pay expectation")
_WORKAUTH = ("sponsor", "visa", "authorized to work", "work authorization", "right to work", "work permit")
_DEMOGRAPHIC = ("gender", "race", "ethnic", "hispanic", "latino", "veteran", "disability", "sexual orientation")
_MOTIVATION = ("why", "what interests", "interest you", "describe", "tell us", "motivat", "cover letter", "excite")


def fetch_questions(client: PoliteClient, company: Mapping, external_id: str) -> list[dict]:
    token = company_token(company)
    if not token or not external_id:
        return []
    data = client.get_json(API.format(token=token, job_id=external_id), sanctioned_api=True)
    return data.get("questions", []) or []


def _map_known(label: str, profile: dict) -> tuple[str, str] | None:
    name = (profile.get("full_name") or "").strip()
    first = name.split()[0] if name else ""
    last = name.split()[-1] if len(name.split()) > 1 else ""
    table: list[tuple[tuple[str, ...], str, str]] = [
        (("first name",), "first_name", first),
        (("last name", "surname", "family name"), "last_name", last),
        (("full name", "your name"), "full_name", name),
        (("email",), "email", profile.get("email") or ""),
        (("phone", "mobile"), "phone", profile.get("phone") or ""),
        (("linkedin",), "linkedin_url", profile.get("linkedin_url") or ""),
        (("github",), "github_url", profile.get("github_url") or ""),
        (("portfolio", "website", "personal site"), "portfolio_url", profile.get("portfolio_url") or ""),
        (("location", "city", "where are you"), "location", profile.get("location") or ""),
    ]
    for needles, key, value in table:
        if any(n in label for n in needles) and value:
            return key, value
    return None


def classify_questions(questions: list[dict], profile: dict) -> tuple[dict, list[dict]]:
    """Return (prefilled, unanswered) derived from the real form questions."""
    prefilled: dict = {}
    unanswered: list[dict] = []

    for q in questions:
        label = (q.get("label") or "").strip()
        label_l = label.lower()
        required = bool(q.get("required"))
        fields = q.get("fields") or [{}]
        ftype = (fields[0].get("type") or "").lower()
        key_base = fields[0].get("name") or label_l.replace(" ", "_") or "field"

        def ask(kind: str, suggested=None) -> None:
            unanswered.append({
                "key": key_base, "label": label or key_base, "kind": kind,
                "required": required, "reason": _reason(kind), "suggested": suggested,
            })

        if "resume" in label_l or label_l.strip() == "cv":
            pass  # the selected resume is attached separately by the runner
        elif "cover letter" in label_l:
            pass  # handled via the selected/rendered cover letter
        elif any(w in label_l for w in _SALARY):
            ask("salary", profile.get("salary_expectation_notes"))
        elif any(w in label_l for w in _WORKAUTH):
            ask("work_auth", profile.get("work_authorization"))
        elif any(w in label_l for w in _DEMOGRAPHIC):
            ask("demographic")
        elif ftype == "textarea" or any(w in label_l for w in _MOTIVATION):
            ask("free_text_motivation" if any(w in label_l for w in _MOTIVATION) else "free_text")
        else:
            mapped = _map_known(label_l, profile)
            if mapped:
                prefilled[mapped[0]] = mapped[1]
            elif ftype in ("multi_value_single_select", "multi_value_multi_select", "boolean") or required:
                # A choice/required field we cannot infer safely -> human decides.
                ask("unknown")
            # else: optional free input we can't map -> leave for the human to add ad hoc

    return prefilled, unanswered


def _reason(kind: str) -> str:
    return {
        "salary": "Salary is surfaced but never auto-submitted (guardrail §2).",
        "work_auth": "Confirm your stored status against this exact question.",
        "demographic": "Voluntary self-identification — your choice.",
        "free_text_motivation": "The agent never fabricates motivation (guardrail §2).",
        "free_text": "Open free response — your call.",
        "unknown": "Could not be answered from stored profile facts.",
    }.get(kind, "Needs a human.")
