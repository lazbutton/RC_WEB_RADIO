from inboxzero.sanitize import scrub_url, text


def test_drops_secret_and_login_urls():
    blob = (
        "Reset https://mail.example/reset?token=abc "
        "Invite https://app.example/join?invite=xyz "
        "Login https://example.org/wp-login.php "
        "Confirm https://site.test/path?confirm=1 "
        "OK https://lespechus.fr/page"
    )
    out = text(blob)
    assert out.count("[lien retiré]") == 4
    assert "https://lespechus.fr/page" in out
    assert "token=" not in out
    assert "invite=" not in out


def test_strips_tracking_keeps_url():
    url = "https://lespechus.fr/coins?utm_source=mail&utm_campaign=x&fbclid=123&c2id=9&ok=1"
    assert scrub_url(url) == "https://lespechus.fr/coins?ok=1"


def test_drops_long_tokenish_query():
    token = "a" * 32
    assert scrub_url(f"https://example.com/x?q={token}") is None
    assert "[lien retiré]" in text(f"voir https://example.com/x?q={token} merci")


def test_strips_tracking_pixels():
    out = text("Hello https://cdn.example.com/pixel.gif?x=1 world")
    assert "pixel.gif" not in out
    assert "[lien retiré]" not in out
    assert "Hello" in out
    assert "world" in out
