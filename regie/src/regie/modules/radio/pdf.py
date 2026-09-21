"""Générateur PDF minimal (une page, texte Helvetica), sans dépendance : attestations et autorisations."""

from __future__ import annotations

from datetime import date


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _latin(text: str) -> str:
    return text.encode("cp1252", errors="replace").decode("cp1252")


def simple_pdf(title: str, lines: list[str], footer: str = "") -> bytes:
    """Rend un PDF A4 valide : titre, paragraphes (retour à la ligne géré grossièrement), pied de page."""
    content: list[str] = ["BT", "/F1 18 Tf", "56 780 Td", f"({_escape(_latin(title))}) Tj", "ET"]
    y = 740
    for raw in lines:
        for chunk in _wrap(raw, 88):
            if y < 80:
                break
            content += ["BT", "/F1 11 Tf", f"56 {y} Td", f"({_escape(_latin(chunk))}) Tj", "ET"]
            y -= 16
        y -= 6
    if footer:
        content += ["BT", "/F1 9 Tf", "56 48 Td", f"({_escape(_latin(footer))}) Tj", "ET"]
    stream = "\n".join(content).encode("cp1252", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def attestation(kind: str, name: str, details: list[str], signer: str = "Radio Campus Orléans") -> bytes:
    today = date.today().strftime("%d/%m/%Y")
    title = {"guest": "Autorisation d'enregistrement et de diffusion", "volunteer": "Attestation de bénévolat", "service_civique": "Attestation de service civique"}.get(kind, "Attestation")
    lines = [f"Radio Campus Orléans — 88.3 FM — orleans.radiocampus.org", "", *details, "", f"Fait à Orléans, le {today}.", "", f"Pour {signer} :", "", "Signature :"]
    return simple_pdf(title, [f"Concernant : {name}", *lines], footer="Document généré par Régie. Une copie est conservée sur le NAS de la radio.")
