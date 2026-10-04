"""E-mail users whose saved filter preset (with "notify" on) matches a new build.

Recipients are worked out inside the request; the mails are sent only after
the database commit succeeds. A mail failure is logged and never breaks the
upload. Without SMTP_HOST configured, nothing is sent.
"""
import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import urljoin

import transaction
from sqlalchemy import func, select
from tg import config

from bingarde.lib import filters
from bingarde.model import DBSession, FilterPreset, Build

log = logging.getLogger(__name__)

MESSAGE = """\
A new build was added to Bingarde: {url}

Name: {name}
Project: {project}
Branch: {branch}
Platform: {platform}
Build: {build}

You receive this message because you subscribed to the preset "{preset}"."""


def matching_recipients(build):
    """[(email, preset name)] for every notifying preset that matches ``build``."""
    DBSession.flush()
    presets = DBSession.scalars(
        select(FilterPreset).where(FilterPreset.notify.is_(True),
                                   FilterPreset.user_id.is_not(None)))
    found = []
    for preset in presets:
        if not preset.user.email:
            continue
        try:
            where = filters.clause((preset.data or {}).get("filter"))
            matches = DBSession.scalar(
                select(func.count()).select_from(Build).where(Build.id == build.id, where))
        except Exception:
            log.exception("preset %s has an unusable filter", preset.id)
            continue
        if matches:
            found.append((preset.user.email, preset.name))
    return found


def build_messages(build, recipients):
    url = urljoin(config.get("base_url", "http://localhost:8080/"), "builds/%d" % build.id)
    messages = []
    for email, preset in recipients:
        msg = EmailMessage()
        msg["Subject"] = "Build notification"
        msg["From"] = config.get("mail_from", "bingarde@localhost")
        msg["To"] = email
        msg.set_content(MESSAGE.format(
            url=url, name=build.display_name, project=build.project, branch=build.branch,
            platform=build.platform, build=build.build, preset=preset))
        messages.append(msg)
    return messages


def notify_new_build(build):
    messages = build_messages(build, matching_recipients(build))
    if messages:
        transaction.get().addAfterCommitHook(lambda ok: ok and send_messages(messages))
    return messages


def send_messages(messages):
    host = config.get("smtp_host")
    if not host:
        log.info("SMTP_HOST not set, skipping %d notification mail(s)", len(messages))
        return
    try:
        with smtplib.SMTP(host, int(config.get("smtp_port", 587)), timeout=30) as smtp:
            if str(config.get("smtp_starttls", "true")).lower() == "true":
                smtp.starttls()
            if config.get("smtp_user"):
                smtp.login(config["smtp_user"], config.get("smtp_password", ""))
            for msg in messages:
                smtp.send_message(msg)
    except Exception:
        log.exception("sending %d notification mail(s) failed", len(messages))
