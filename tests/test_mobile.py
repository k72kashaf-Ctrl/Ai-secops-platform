"""Mobile-friendly view (Bonus C) is served by the API."""
from __future__ import annotations


def test_mobile_page_is_served_and_responsive(client):
    response = client.get("/mobile")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert 'name="viewport"' in body
    # Covers the four operator workflows and uses the existing JSON APIs.
    for tab in ("incidents", "quality", "briefing", "assistant"):
        assert f'data-tab="{tab}"' in body
    for path in ("/incidents", "/quality-issues", "/briefing", "/assistant/query"):
        assert path in body
    # Push-to-talk degrades gracefully when the Web Speech API is missing.
    assert "webkitSpeechRecognition" in body
