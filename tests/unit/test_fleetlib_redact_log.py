import io
import json

from ops.fleetlib import log, redact


def test_known_token_shapes_are_masked():
    samples = [
        "gsk_" + "a1B2c3D4e5" * 3,
        "sk-or-v1-" + "a1B2c3D4e5" * 3,
        "ghp_" + "a1B2c3D4e5" * 4,
        "123456789:" + "AAFa1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6",
        "hf_" + "a1B2c3D4e5" * 3,
        "AIza" + "a1B2c3D4e5" * 4,
    ]
    for s in samples:
        out = redact.redact_text("the key is %s ok" % s)
        assert s not in out and "[REDACTED]" in out


def test_assignments_and_bearer_are_masked():
    assert "hunter2hunter2" not in redact.redact_text("password=hunter2hunter2")
    assert "abcdefghijklmnop" not in redact.redact_text('{"api_key": "abcdefghijklmnop"}')
    assert "tok123456789abc" not in redact.redact_text("Authorization: Bearer tok123456789abc")


def test_plain_text_is_unchanged():
    text = "The fleet is paused. 3 tasks ready. Score 4.5 of 5."
    assert redact.redact_text(text) == text


def test_logger_redacts_nested_fields():
    out = io.StringIO()
    lg = log.get("test", stream=out)
    lg.info("event", detail={"token": "x", "msg": "ghp_" + "a1B2c3D4e5" * 4})
    line = json.loads(out.getvalue())
    assert line["service"] == "test" and line["event"] == "event"
    assert "ghp_" not in out.getvalue()


def test_exact_secret_is_masked_after_register():
    redact.register_secret("my-very-odd-secret-value")
    assert redact.redact_text("x my-very-odd-secret-value y") == "x [REDACTED] y"
