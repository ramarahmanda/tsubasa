from tsubasa.models import Event
from tsubasa.redact import redact_event, redact_text


def test_jwt_redacted():
    jwt = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImQzZjQ3NmQyMmJj.eyJpc3MiOiJodHRwOi8vYXV0aGVudGlrLmxvY2FsaG9z.sig-part"
    out = redact_text(f"token was {jwt} in the response")
    assert "eyJ" not in out
    assert "[REDACTED:jwt]" in out


def test_credential_assignment_keeps_key_name():
    out = redact_text('api_key = "sk-live-abcdef1234567890"')
    assert "sk-live" not in out
    assert out.startswith("api_key = ")


def test_plain_text_untouched():
    s = "We chose Postgres because of JSONB support (see adr-use-postgres)."
    assert redact_text(s) == s


def test_event_fields_scrubbed():
    ev = Event(
        id="evt-1", type="note", ts="2026-07-15", title="AKIA1234567890ABCDEF leaked",
        summary="password: hunter2hunter2",
        derived_entities=[{"id": "x", "type": "service", "name": "svc",
                           "description": "uses AKIA1234567890ABCDEF",
                           "profile": {"key_facts": ["secret = supersecretvalue1"]}}],
    )
    redact_event(ev)
    assert "AKIA1234567890ABCDEF" not in ev.title
    assert "hunter2" not in ev.summary
    assert "AKIA" not in ev.derived_entities[0]["description"]
    assert "supersecretvalue1" not in ev.derived_entities[0]["profile"]["key_facts"][0]
