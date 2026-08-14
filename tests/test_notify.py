"""notify.send_email: the mail send, proven with a fake SMTP class instead of a socket.

The suite is not allowed to open a real connection any more than it is allowed to make a
real HTTP request — `smtplib.SMTP` is monkeypatched to a class that records what it would
have sent, the same shape the rest of this project fakes a provider (`FakeSearchApi`).
"""
from __future__ import annotations

import smtplib

import pytest

from propertyfinder.config import Settings
from propertyfinder.notify import email_configured, send_email


class FakeSMTP:
    """A stand-in for `smtplib.SMTP` that remembers instead of dialing out."""

    sent: list[tuple] = []

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, msg, to_addrs):
        FakeSMTP.sent.append((msg, to_addrs, self))


@pytest.fixture(autouse=True)
def _reset_and_patch(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)


def _configured_settings(**over) -> Settings:
    base = dict(
        smtp_host="smtp.example.com",
        smtp_username="bot",
        smtp_password="secret",
        alert_email_from="bot@example.com",
        alert_email_to="tyler@example.com, second@example.com",
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def test_unconfigured_smtp_is_not_sent(caplog):
    settings = Settings(_env_file=None)  # nothing filled in
    assert email_configured(settings) is False

    with caplog.at_level("INFO"):
        assert send_email(settings, "subject", "body") is False

    assert FakeSMTP.sent == []
    assert "not sent" in caplog.text


def test_configured_smtp_sends_through_the_fake_transport():
    settings = _configured_settings()
    assert email_configured(settings) is True

    assert send_email(settings, "Property Finder — Aug 14 · 2 deals", "the whole digest") is True

    assert len(FakeSMTP.sent) == 1
    msg, to_addrs, transport = FakeSMTP.sent[0]
    assert msg["Subject"] == "Property Finder — Aug 14 · 2 deals"
    assert msg["From"] == "bot@example.com"
    assert to_addrs == ["tyler@example.com", "second@example.com"]
    assert msg.get_content().strip() == "the whole digest"
    assert transport.started_tls is True  # smtp_tls defaults on
    assert transport.logged_in == ("bot", "secret")


def test_smtp_tls_and_login_are_skipped_when_asked_to_be():
    settings = _configured_settings(smtp_tls=False, smtp_username="")

    assert send_email(settings, "subject", "body") is True

    _, _, transport = FakeSMTP.sent[0]
    assert transport.started_tls is False
    assert transport.logged_in is None


def test_a_lone_recipient_needs_no_comma():
    settings = _configured_settings(alert_email_to="only@example.com")
    assert send_email(settings, "subject", "body") is True
    _, to_addrs, _ = FakeSMTP.sent[0]
    assert to_addrs == ["only@example.com"]
