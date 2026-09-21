from regie.modules.mail.threads import (
    cluster_groups,
    forward_context,
    item_needs_brief,
    looks_forwarded,
    reply_parent,
    short_summary,
    thread_key,
    thread_title,
    visible_body,
    weak_summary,
)


def test_looks_forwarded_strips_reply_prefix():
    assert looks_forwarded("Re: Fwd: Interview Les Pêchus")
    assert looks_forwarded("Fwd: idée invitée")
    assert not looks_forwarded("Re: dispo jeudi", "Salut, tu es libre jeudi ?")


def test_thread_key_strips_prefixes():
    assert thread_key("Re: Fwd: Interview Les Pêchus") == thread_key("Interview Les Pêchus")
    assert thread_key("TR: concert") == thread_key("Re: concert")
    assert thread_key("Fw: Re: Fw: studio") == thread_key("studio")
    assert thread_title("Re: Fwd: Hello") == "Hello"
    assert thread_title("") == "(sans objet)"


def test_short_summary_takes_two_sentences():
    text = "Bonjour. Il faut préparer l’interview jeudi. Merci beaucoup. Autre chose."
    out = short_summary(text)
    assert "interview" in out.lower()
    assert "Autre chose" not in out


VIVIANE_FIRST = (
    "Bonjour ! Je vous propose de venir en parler à l’antenne, dans notre émission Ecoute ta Fac, "
    "à 12h, mercredi 23 sept ou merc 30 sept. Qu'en pensez-vous ? Seriez-vous dispo ? Bonne "
    "journée, Viviane > Le 7 sept. 2026 à 12:58, Abdelhake hy <spidyx@live.fr> a écrit : > > "
    "Bonjour, > > Suite à la conversation que nous avons eu lors de la rentrée en fête hier, "
    "je vous envoi cette mail. > Je suis SpidyX, rappeur indépendant originaire d'Orléans."
)

VIVIANE_REPLY = (
    "C’est possible mardi 29/09 à 12H30 Merci pour la réponse rapide ! Viviane > Le 15 sept. "
    "2026 à 20:58, Abdelhake hy <spidyx@live.fr> a écrit : > > Bonjour, > > Merci pour votre "
    "réponse et votre invitation. > > Pour le mercredi 23 ça ne sera malheureusement pas "
    "possible > > From: Viviane Berreur <viviane.berreur@orleans.radiocampus.org> > Sent: "
    "Tuesday, 15 September 2026 20:13:47 > To: Abdelhake hy <spidyx@live.fr>; "
    "volontairelocal@orleans.radiocampus.org; Volontaire Musique <volontairemusique@orleans.radiocampus.org>"
)


def test_visible_body_keeps_new_reply_drops_quote_and_cc():
    out = visible_body(VIVIANE_REPLY)
    assert "29/09" in out
    assert "réponse rapide" in out
    assert "spidyx" not in out.lower()
    assert "volontairelocal" not in out.lower()
    assert "From:" not in out
    assert "a écrit" not in out


def test_visible_body_keeps_first_message_drops_nested_quote():
    out = visible_body(VIVIANE_FIRST)
    assert "Ecoute ta Fac" in out
    assert "Viviane" in out
    assert "spidyx" not in out.lower()
    assert "Timbaland" not in out
    assert "a écrit" not in out


def test_visible_body_strips_outlook_original_and_cc():
    raw = (
        "C'est possible mardi.\n\n"
        "-----Original Message-----\n"
        "From: Abdelhake hy <spidyx@live.fr>\n"
        "Sent: Tuesday, 15 September 2026\n"
        "To: Viviane; volontairelocal@orleans.radiocampus.org\n"
        "Cc: Volontaire Musique\n"
        "Subject: Re: Timbaland\n\n"
        "Bonjour,\nMerci pour l'invitation.\n"
    )
    out = visible_body(raw)
    assert out.startswith("C'est possible")
    assert "volontairelocal" not in out.lower()
    assert "Timbaland" not in out
    assert "From:" not in out


def test_visible_body_strips_english_wrote_and_signature():
    raw = "Thanks, I'll be there.\n\n-- \nErwann Cochery\nChargé d'actions\n\nOn Mon, 15 Sep 2026 at 20:58, Bob <bob@x.test> wrote:\n> Hello from the quote\n"
    out = visible_body(raw)
    assert "I'll be there" in out
    assert "Hello from the quote" not in out
    assert "Erwann" not in out
    assert "wrote" not in out.lower()


def test_visible_body_forward_keeps_useful_paragraph():
    raw = (
        "---------- Forwarded message ---------\n"
        "From: Bob <bob@x.test>\n"
        "Date: Mon, 15 Sep 2026\n"
        "Subject: Hello\n"
        "To: me@x.test\n\n"
        "Can you come Thursday at noon?\n"
    )
    out = visible_body(raw)
    assert "Thursday" in out
    assert "From:" not in out


FYI_FORWARD = (
    "idée invitée Mag Culturel !\n\n"
    "---------- Forwarded message ---------\n"
    "From: Comm Alliage <communication@alliage.org>\n"
    "Date: Mon, 15 Sep 2026\n"
    "Subject: Dossier de presse\n"
    "To: Viviane Berreur <viviane@orleans.radiocampus.org>\n"
    "Cc: presse@alliage.org\n\n"
    "Une programmation éclectique pour la saison.\n"
)

ASK_FORWARD = (
    "Peux-tu confirmer à Bob pour jeudi midi ?\n\n"
    "De : Bob Martin <bob@x.test>\n"
    "Envoyé : lundi 15 septembre 2026 11:00\n"
    "À : Viviane Berreur\n"
    "Objet : Interview\n\n"
    "Est-ce que je peux passer jeudi à 12h ?\n"
)


def test_forward_context_keeps_note_and_participants():
    ctx = forward_context(FYI_FORWARD, "Fwd: Alliage")
    assert ctx["forwarded"] is True
    assert "idée invitée" in ctx["note"]
    assert any("Comm Alliage" in part for part in ctx["participants"])
    assert any("Viviane" in part for part in ctx["participants"])
    assert "programmation" in ctx["quoted"]
    assert looks_forwarded("Fwd: Alliage", FYI_FORWARD)


def test_forward_context_french_headers():
    ctx = forward_context(ASK_FORWARD, "TR: Interview")
    assert ctx["forwarded"] is True
    assert "confirmer" in ctx["note"]
    assert any(part.startswith("De:") and "Bob" in part for part in ctx["participants"])
    assert any(part.startswith("À:") for part in ctx["participants"])
    assert "jeudi" in ctx["quoted"]


def test_weak_summary_detects_excerpt_copy():
    excerpt = "Bonjour, Je vous contacte pour savoir si vous seriez disponible jeudi."
    assert weak_summary("", excerpt)
    assert weak_summary(short_summary(excerpt), excerpt)
    assert weak_summary(excerpt[:80], excerpt)
    assert not weak_summary(
        "Lou demande si tu es libre jeudi 24 à 12h pour Ecoute ta fac.",
        excerpt,
    )


def test_item_needs_brief_skips_newsletters():
    row = {
        "category": "newsletters",
        "status": "proposed",
        "subject": "Digest",
        "excerpt": "Hello",
        "summary": "",
    }
    assert item_needs_brief(row) is False
    todo = {**row, "category": "todo"}
    assert item_needs_brief(todo) is True
    fwd = {**row, "category": "read", "subject": "Fwd: Alliage", "excerpt": FYI_FORWARD}
    assert item_needs_brief(fwd) is True


def test_reply_parent_from_headers():
    assert reply_parent({"in-reply-to": "<a@x>", "references": "<root@x> <a@x>"}) == "<a@x>"
    assert reply_parent({"references": "<root@x> <mid@x>"}) == "<mid@x>"
    assert reply_parent({}, in_reply_to="<z@x> extra") == "<z@x>"


def test_cluster_groups_by_in_reply_to_then_subject():
    orig = {
        "id": 1,
        "subject": "Interview",
        "message_id": "<orig@x>",
        "in_reply_to": "",
    }
    reply = {
        "id": 2,
        "subject": "Re: Interview",
        "message_id": "<re@x>",
        "in_reply_to": "<orig@x>",
    }
    fwd = {
        "id": 3,
        "subject": "Fwd: Interview",
        "message_id": "<fwd@x>",
        "in_reply_to": "",
    }
    other = {
        "id": 4,
        "subject": "Autre sujet",
        "message_id": "<o@x>",
        "in_reply_to": "",
    }
    groups = cluster_groups([orig, reply, fwd, other])
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 3]
    together = next(g for g in groups if len(g) == 3)
    assert {row["id"] for row in together} == {1, 2, 3}
