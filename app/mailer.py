"""Send an email through any SMTP server (Gmail app password). Settings come from .env / environment."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Optional


def split_addresses(v: Optional[str]) -> list[str]:
    return [a.strip() for a in (v or "").replace(";", ",").split(",") if a.strip()]


def send(subject: str, text: str, html: str, to: list[str], attachments: Iterable[Path] = (),
         env: Optional[dict] = None) -> None:
    g = env if env is not None else os.environ
    if not to:
        raise ValueError("no recipients")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (g.get("MAIL_FROM") or g["SMTP_USER"]).strip()
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for p in attachments:
        p = Path(p)
        if p.exists():
            kind = ("application", "pdf") if p.suffix.lower() == ".pdf" else ("text", "html")
            msg.add_attachment(p.read_bytes(), maintype=kind[0], subtype=kind[1], filename=p.name)
    with smtplib.SMTP(g["SMTP_HOST"].strip(), int(g.get("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls()
        s.login(g["SMTP_USER"].strip(), g["SMTP_PASS"].strip())
        s.send_message(msg)
