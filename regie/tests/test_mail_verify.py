from regie.modules.mail.verify import check_draft, source_blob


def test_check_draft_accepts_facts_in_source():
    source = "IP 192.168.10.12 contact emma@test.fr voir dossier.pdf jeudi 29"
    draft = "Salut Emma, l’IP 192.168.10.12 et emma@test.fr, fichier dossier.pdf pour le 29."
    assert check_draft(draft, source) is None


def test_check_draft_drops_invented_ip():
    source = "On se voit jeudi à la radio."
    draft = "L’IP du stream est 10.0.0.8, merci."
    assert check_draft(draft, source) == "10.0.0.8"


def test_check_draft_allows_signature_firstname():
    source = "Dispo mardi ?"
    draft = "Ok pour mardi.\nLaz"
    assert check_draft(draft, source, "—\nLaz\nRadio Campus Orléans 88.3") is None


def test_source_blob_includes_attachment_names():
    blob = source_blob(
        {
            "subject": "Dossier",
            "sender": "a@test",
            "excerpt": "ci-joint",
            "attachments": [{"filename": "press.pdf"}],
        }
    )
    assert "press.pdf" in blob
    assert check_draft("Voici press.pdf", blob) is None
    assert check_draft("Manque secret.pdf", blob) == "secret.pdf"
