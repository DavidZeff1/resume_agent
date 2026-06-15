"""Phase 2: adapters, HTML parsing, token resolution, robots handling."""

from jobagent.source import greenhouse, lever, workable
from jobagent.source.base import company_token, html_to_text
from jobagent.source.html_generic import parse_html
from jobagent.source.registry import get_adapter, supported_ats
from tests.conftest import FakeClient


def test_html_to_text_keeps_sentences_blocks_break():
    out = html_to_text("<p>Build <b>APIs</b> in Python.</p><p>Own services.</p>")
    assert out == "Build APIs in Python.\nOwn services."


def test_greenhouse_unescapes_and_marks_sanctioned():
    c = FakeClient({"jobs": [{
        "id": 1, "title": "Backend Engineer",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": {"name": "Remote"},
        "content": "&lt;p&gt;Python &lt;b&gt;APIs&lt;/b&gt;.&lt;/p&gt;",
    }]})
    jobs = greenhouse.fetch(c, {"name": "Acme", "board_token": "acme"})
    assert jobs[0].description_text == "Python APIs."
    assert jobs[0].external_id == "1"
    assert c.calls[0][2] is True  # sanctioned API -> robots-exempt


def test_lever_parses_fields():
    c = FakeClient([{
        "id": "abc", "text": "Data Scientist",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
        "categories": {"location": "Tel Aviv"}, "descriptionPlain": "ML.",
    }])
    j = lever.fetch(c, {"name": "Acme", "board_token": "acme"})[0]
    assert (j.title, j.location, j.external_id) == ("Data Scientist", "Tel Aviv", "abc")


def test_workable_builds_location_and_url():
    c = FakeClient({"jobs": [{
        "title": "DevOps", "shortcode": "XYZ",
        "location": {"city": "Berlin", "country": "Germany"},
        "description": "<p>K8s</p>",
    }]})
    j = workable.fetch(c, {"name": "Acme", "board_token": "acme"})[0]
    assert j.location == "Berlin, Germany"
    assert j.url.endswith("/j/XYZ/")


def test_html_jsonld_parsing_resolves_relative_urls():
    html = (
        '<script type="application/ld+json">'
        '{"@type":"JobPosting","title":"ML Engineer","url":"/c/ml",'
        '"description":"<p>Train models</p>",'
        '"jobLocation":{"address":{"addressLocality":"NYC"}}}'
        "</script>"
    )
    j = parse_html(html, "https://example.com/careers")[0]
    assert j.url == "https://example.com/c/ml"
    assert j.location == "NYC"
    assert j.description_text == "Train models"


def test_html_anchor_fallback_filters_nonjobs():
    jobs = parse_html('<a href="/jobs/1">Senior Engineer</a><a href="/about">About</a>', "https://x.io")
    titles = {j.title for j in jobs}
    assert "Senior Engineer" in titles and "About" not in titles


def test_company_token_parsing():
    assert company_token({"board_token": "acme"}) == "acme"
    assert company_token({"board_url": "https://jobs.lever.co/acme"}) == "acme"
    assert company_token({"board_url": "boards.greenhouse.io/embed/job_board?for=acme"}) == "acme"


def test_registry_fallback_to_html():
    assert "greenhouse" in supported_ats() and "lever" in supported_ats()
    assert get_adapter("madeupats", has_board_url=True) is get_adapter("other")
