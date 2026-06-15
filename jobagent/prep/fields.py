"""Field model + the guardrail that keeps screening answers away from autofill.

Two outputs per application:
  * prefilled_data:    {key: value}  — safe facts pulled straight from profile.
  * unanswered_fields: [ {key,label,kind,required,reason,suggested}, ... ]
                       — what the HUMAN must complete during review.

``NEVER_AUTOFILL_KINDS`` are categories the agent must never auto-answer. An
item of such a kind may carry a `suggested` value from stored state (e.g. the
salary note), but it still goes to the human to confirm — it is never written
into prefilled_data. ``assert_guardrails`` enforces this in code.
"""

from __future__ import annotations

# Categories the agent must never silently auto-fill (it would be fabricating an
# answer or submitting something irreversible like salary).
NEVER_AUTOFILL_KINDS = {
    "free_text_motivation",  # "why do you want to work here"
    "free_text",             # any open free-response
    "salary",                # salary / compensation expectations
    "work_auth",             # work-authorization / sponsorship specifics
    "demographic",           # EEO / voluntary self-id
}

# Keys that must always reach the human regardless of how fields were derived.
_FORBIDDEN_PREFILL_KEYS = {"why_company", "salary_expectation", "additional_info"}


class GuardrailViolation(RuntimeError):
    """Raised if prep ever tries to auto-answer something reserved for the human."""


def build_prefilled(
    profile: dict,
    resume_row,
    cover_row,
    rendered_cover_path: str | None,
) -> dict:
    """Safe form data drawn entirely from stored profile + selected materials."""
    data: dict = {}

    def put(key: str, value) -> None:
        if value:
            data[key] = value

    put("full_name", profile.get("full_name"))
    put("email", profile.get("email"))
    put("phone", profile.get("phone"))
    put("location", profile.get("location"))
    put("github_url", profile.get("github_url"))
    put("linkedin_url", profile.get("linkedin_url"))
    put("portfolio_url", profile.get("portfolio_url"))
    if resume_row is not None:
        put("resume_path", resume_row["file_path"])
    if rendered_cover_path:
        put("cover_letter_path", rendered_cover_path)
    elif cover_row is not None:
        put("cover_letter_path", cover_row["file_path"])
    return data


def default_unanswered(profile: dict) -> list[dict]:
    """The standard set of questions that always go to the human."""
    items: list[dict] = [
        {
            "key": "why_company",
            "label": "Why do you want to work here? / What interests you in this role?",
            "kind": "free_text_motivation",
            "required": True,
            "reason": "The agent never fabricates motivation (guardrail §2).",
            "suggested": None,
        },
        {
            "key": "salary_expectation",
            "label": "Salary expectations",
            "kind": "salary",
            "required": False,
            "reason": "Salary is surfaced from your notes but never auto-submitted.",
            "suggested": profile.get("salary_expectation_notes"),
        },
        {
            "key": "work_authorization",
            "label": "Work authorization / visa sponsorship question",
            "kind": "work_auth",
            "required": True,
            "reason": "Confirm your stored status against this role's exact question.",
            "suggested": profile.get("work_authorization"),
        },
        {
            "key": "additional_info",
            "label": "Anything else you'd like to add? (optional free text)",
            "kind": "free_text",
            "required": False,
            "reason": "Optional free response — your call.",
            "suggested": None,
        },
    ]
    labels = {
        "full_name": "Full name", "email": "Email", "phone": "Phone number",
        "location": "Location",
    }
    for key, label in labels.items():
        if not profile.get(key):
            items.append({
                "key": key, "label": label, "kind": "missing_profile",
                "required": key in ("full_name", "email"),
                "reason": "Not set in your profile.", "suggested": None,
            })
    return items


def assert_guardrails(prefilled: dict, unanswered: list[dict], require_core: bool = True) -> None:
    """Fail loudly if the never-autofill rule was violated.

    Universal invariants (always checked):
      1. No forbidden key may appear in prefilled_data.
      2. No unanswered item may carry an auto-filled value (only `suggested`).

    Default-path invariant (when require_core=True, i.e. not using a real ATS
    question list):
      3. The core screening categories must actually be queued for the human.
    """
    leaked = _FORBIDDEN_PREFILL_KEYS & set(prefilled)
    if leaked:
        raise GuardrailViolation(f"screening keys leaked into prefilled_data: {sorted(leaked)}")

    for item in unanswered:
        if item.get("kind") in NEVER_AUTOFILL_KINDS and item.get("value"):
            raise GuardrailViolation(
                f"unanswered field {item.get('key')!r} carries an auto-filled value"
            )

    if require_core:
        queued_kinds = {i["kind"] for i in unanswered}
        missing = {"free_text_motivation", "salary", "work_auth"} - queued_kinds
        if missing:
            raise GuardrailViolation(
                f"screening categories not queued for human: {sorted(missing)}"
            )
