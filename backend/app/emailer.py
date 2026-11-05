"""Optional: email the daily digest over SMTP."""
from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .config import get_settings
from .db import Digest, Story, utcnow

log = logging.getLogger(__name__)


def render_digest_html(session: Session, digest: Digest) -> str:
    base = get_settings().public_base_url.rstrip("/")
    rows = []
    for n, t in enumerate(digest.top, 1):
        s = session.get(Story, t["story_id"])
        if not s:
            continue
        link = s.items[0].url if s.items else f"{base}/story/{s.id}"
        rows.append(
            f"<li style='margin-bottom:10px'><a href='{html.escape(link)}'><b>{html.escape(s.title)}</b></a>"
            f" <span style='color:#666'>[{html.escape(s.category)} · {s.importance}/5]</span><br>"
            f"{html.escape(t['line'])} <a href='{base}/story/{s.id}'>details</a></li>"
        )
    topics = "".join(
        f"<li><b>{html.escape(t['title'])}</b><br>{html.escape(t.get('angle', ''))}</li>" for t in digest.topics
    )
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:640px'>"
        f"<h2>AI Pulse · {digest.date}</h2><p>{html.escape(digest.headline)}</p>"
        f"<h3>What matters today</h3><ol>{''.join(rows)}</ol>"
        f"<h3>Ideas to write about</h3><ul>{topics}</ul>"
        f"<p><a href='{base}'>Open the dashboard</a></p></div>"
    )


def send_digest(session: Session, digest: Digest) -> bool:
    s = get_settings()
    if not s.email_enabled or digest.emailed_at is not None:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"AI Pulse · {digest.date}"
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = s.smtp_to
    msg.set_content("Your AI Pulse digest is best viewed as HTML: " + s.public_base_url)
    msg.add_alternative(render_digest_html(session, digest), subtype="html")
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        digest.emailed_at = utcnow()
        log.info("digest emailed to %s", s.smtp_to)
        return True
    except Exception as exc:
        log.warning("email digest failed: %s", exc)
        return False
