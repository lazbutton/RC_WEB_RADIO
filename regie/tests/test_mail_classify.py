from unittest.mock import patch

import httpx

from regie.modules.mail.classify import (
    BRIEF_PROMPT,
    DRAFT_PROMPT,
    anthropic_error_message,
    anthropic_headers,
    brief_with_anthropic,
    can_auto_move,
    classify_batch_haiku,
    classify_with_anthropic,
    excerpt_body,
    heuristic_classification,
    heuristic_gate,
    parse_brief,
    parse_classification,
    parse_classification_batch,
    should_escalate,
    strip_html,
    useful_links,
    unwrap_url,
    with_research_links,
)


def test_brief_prompt_knows_campus_team():
    assert "Lou" in BRIEF_PROMPT
    assert "Erwann" in BRIEF_PROMPT
    assert "Viviane" in BRIEF_PROMPT
    assert "volontaire musique" in BRIEF_PROMPT
    assert "pas à Viviane à la place de Lou" in BRIEF_PROMPT
    assert "expéditeur du mail vers cette boîte" in BRIEF_PROMPT
    assert "même si Lou est en copie" in BRIEF_PROMPT
    assert "N'invente aucun lien" in BRIEF_PROMPT
    assert "Aucun fait hors du fil" in BRIEF_PROMPT
    assert "pièce absente" in BRIEF_PROMPT
    assert "page artiste" in BRIEF_PROMPT


def test_draft_prompt_does_not_sign():
    assert "Ne signe pas" in DRAFT_PROMPT
    assert "Signe" not in DRAFT_PROMPT


def test_useful_links_keeps_artist_site_drops_tracking():
    raw = (
        "Dossier : https://lespechus.fr/coins-sombres et Bandcamp "
        "https://pechus.bandcamp.com/album/demo. "
        "Unsub https://example.us18.list-manage.com/unsubscribe?u=abc "
        "pixel https://cdn.example.com/logo.png "
        "view https://mailchi.mp/xx/view-in-browser"
    )
    links = useful_links(raw)
    assert "https://lespechus.fr/coins-sombres" in links
    assert "https://pechus.bandcamp.com/album/demo" in links
    assert all("list-manage" not in url for url in links)
    assert all("view-in-browser" not in url for url in links)
    assert all("mailchi.mp" not in url for url in links)
    assert all(not url.endswith(".png") for url in links)


def test_unwrap_mailjet_then_keep_destination():
    wrapped = (
        "http://xpohy.mjt.lu/lnk/AAAA/4/652JM_MtHC2-evfweY7Hfg/"
        "aHR0cDovL2xlc2ZvdXNkZWJhc3Nhbi5vcmc"
    )
    assert unwrap_url(wrapped) == "http://lesfousdebassan.org"
    links = useful_links(f"Voir {wrapped}")
    assert links == ["http://lesfousdebassan.org"]


def test_strip_html_keeps_anchor_href():
    raw = '<p>Programme <a href="https://museesorleans.fr/programmes">en ligne</a></p>'
    out = strip_html(raw)
    assert "en ligne" in out
    assert "https://museesorleans.fr/programmes" in out
    assert "<a" not in out


def test_with_research_links_appends_missing_urls():
    summary = "Viviane te passe une idée invitée Pêchus."
    source = "Voir https://museesorleans.fr/programmes et https://lespechus.fr/"
    out = with_research_links(summary, source)
    assert "idée invitée" in out
    assert "https://museesorleans.fr/programmes" in out
    assert "https://lespechus.fr/" in out
    again = with_research_links(out, source)
    assert again.count("https://museesorleans.fr/programmes") == 1


def test_parse_brief_empty_draft_on_fyi():
    raw = '{"summary":"Viviane te transfère le dossier Alliage. Fil entre la comm et Viviane. Pour info.","draft":""}'
    out = parse_brief(raw)
    assert "Alliage" in out["summary"]
    assert out["draft"] == ""


def test_parse_brief_keeps_reply_when_asked():
    raw = '{"summary":"Viviane demande de confirmer jeudi à Bob.","draft":"Salut Viviane,\\nOk pour jeudi midi."}'
    out = parse_brief(raw)
    assert "jeudi" in out["summary"]
    assert "Ok pour jeudi" in out["draft"]


def test_parse_json_fenced():
    raw = '```json\n{"category":"todo","reason":"facture","summary":"Il faut payer la facture studio.","confidence":0.9}\n```'
    out = parse_classification(raw)
    assert out["category"] == "todo"
    assert out["reason"] == "facture"
    assert out["confidence"] == 0.9
    assert "facture studio" in out["summary"]


def test_parse_rejects_unknown_category():
    try:
        parse_classification('{"category":"banana","reason":"x","confidence":1}')
    except ValueError as exc:
        assert "catégorie" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_heuristic_newsletter():
    out = heuristic_classification(
        "News <n@x.test>",
        "Votre digest",
        "Cliquer pour unsubscribe",
        {"List-Unsubscribe": "<mailto:x@y>"},
    )
    assert out["category"] == "newsletters"
    assert out["summary"]
    assert out["confidence"] >= 0.72
    assert heuristic_gate("A <a@x>", "Sujet", "Bonjour", {}) is None


def test_strip_html_keeps_text():
    assert "Bonjour" in strip_html("<p>Bonjour <b>toi</b></p><script>alert(1)</script>")


def test_excerpt_body_peels_quoted_reply():
    raw = (
        "C’est possible mardi 29/09 à 12H30 Merci ! Viviane\n\n"
        "Le 15 sept. 2026 à 20:58, Abdelhake hy <spidyx@live.fr> a écrit :\n"
        "> Bonjour,\n> Merci\n"
    )
    out = excerpt_body(raw, None, 4000)
    assert "29/09" in out
    assert "spidyx" not in out.lower()
    assert "a écrit" not in out


def test_parse_nested_json_still_picks_object():
    raw = 'Voici : {"category":"waiting","reason":"on attend la fédé","confidence":0.7}'
    out = parse_classification(raw)
    assert out["category"] == "waiting"
    assert "fédé" in out["reason"]


def test_anthropic_headers_omit_workspace_when_empty():
    headers = anthropic_headers("sk-test", "")
    assert "anthropic-workspace-id" not in headers
    assert headers["x-api-key"] == "sk-test"


def test_anthropic_headers_include_workspace():
    headers = anthropic_headers("sk-test", "wrkspc_01JwQvzr7rXLA5AGx3HKfFUJ")
    assert headers["anthropic-workspace-id"] == "wrkspc_01JwQvzr7rXLA5AGx3HKfFUJ"


def test_workspace_error_is_actionable():
    response = httpx.Response(
        400,
        json={
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header",
            },
        },
    )
    message = anthropic_error_message(response)
    assert "ANTHROPIC_WORKSPACE_ID" in message
    assert "wrkspc_" in message


def test_credit_error_is_actionable():
    response = httpx.Response(
        400,
        json={
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.",
            },
        },
    )
    message = anthropic_error_message(response)
    assert "crédits" in message
    assert "Plans & Billing" in message


def test_classify_sends_workspace_header():
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": '{"category":"todo","reason":"facture","confidence":0.9}',
                    }
                ]
            },
        )

    with patch("regie.modules.mail.classify.httpx.post", side_effect=fake_post):
        out = classify_with_anthropic(
            api_key="sk-test",
            model="claude-sonnet-4-5",
            sender="a@b.test",
            subject="Facture",
            excerpt="Merci de payer",
            workspace_id="wrkspc_01ExampleWorkspaceIdxxxx",
        )
    assert out["category"] == "todo"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["anthropic-workspace-id"] == "wrkspc_01ExampleWorkspaceIdxxxx"


def test_can_auto_move_thresholds():
    assert not can_auto_move("todo", 0.99, True)
    assert not can_auto_move("waiting", 0.99, True)
    assert not can_auto_move("read", 0.99, False)
    assert can_auto_move("read", 0.80, True)
    assert not can_auto_move("read", 0.79, True)
    assert can_auto_move("newsletters", 0.72, True)
    assert can_auto_move("spam", 0.88, True)
    assert not can_auto_move("spam", 0.87, True)


def test_should_escalate_todo_and_action_words():
    assert should_escalate({"category": "todo", "confidence": 0.95})
    assert should_escalate({"category": "read", "confidence": 0.5})
    assert should_escalate({"category": "read", "confidence": 0.9}, "Peux-tu confirmer", "ok")
    assert not should_escalate({"category": "read", "confidence": 0.9}, "Agenda", "Info studio")


def test_parse_classification_batch_by_index():
    raw = '[{"i":1,"category":"spam","reason":"piège","confidence":0.9},{"i":0,"category":"read","reason":"info","confidence":0.8}]'
    out = parse_classification_batch(raw, 2)
    assert out[0]["category"] == "read"
    assert out[1]["category"] == "spam"


def _ok_text(text: str) -> httpx.Response:
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}], "usage": {}})


def test_scan_keeps_temperature_without_effort():
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _ok_text('[{"i":0,"category":"read","reason":"info","confidence":0.8}]')

    with patch("regie.modules.mail.classify.httpx.post", side_effect=fake_post):
        classify_batch_haiku(
            api_key="sk-test",
            model="claude-sonnet-4-5",
            mails=[{"sender": "a@b.test", "subject": "Agenda", "excerpt": "Studio"}],
        )
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["model"] == "claude-sonnet-4-5"
    assert body["temperature"] == 0
    assert "output_config" not in body


def test_brief_sends_high_effort_without_temperature():
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _ok_text('{"summary":"Lou te passe un fil.","draft":""}')

    with patch("regie.modules.mail.classify.httpx.post", side_effect=fake_post):
        brief_with_anthropic(
            api_key="sk-test",
            model="claude-sonnet-5",
            messages=[{"from": "Lou <lou@test>", "subject": "Fwd: Viviane", "excerpt": "idée invitée"}],
            subject="Fwd: Viviane",
            effort="high",
        )
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["model"] == "claude-sonnet-5"
    assert "temperature" not in body
    assert body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] >= 4000
