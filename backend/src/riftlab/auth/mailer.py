"""Envio dos e-mails da conta (confirmação, recuperação e avisos da conta) por SMTP.

Serve qualquer servidor: o Mailpit local do compose (sem TLS nem login) ou o Gmail com senha
de app (STARTTLS na 587). O envio roda em segundo plano, depois da resposta: a resposta é a
mesma exista ou não a conta. Falha só vai ao log, pelo tipo: a mensagem de erro do SMTP pode
conter o endereço, que não entra em log.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

from riftlab.auth.email_template import EmailKind, render
from riftlab.config import Settings

logger = logging.getLogger("riftlab.api")


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    kind: EmailKind
    code: str | None = None
    # Nome do serviço de login nos avisos de login social ligado ("Google", "Discord").
    service: str | None = None


class Mailer(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        assert settings.smtp_host
        self._settings = settings

    def _deliver(self, message: EmailMessage) -> None:
        settings = self._settings
        assert settings.smtp_host
        timeout = settings.smtp_timeout_seconds
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as smtp:
            if settings.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                # A senha de app do Google é exibida em grupos separados por espaço.
                password = (settings.smtp_password or "").replace(" ", "")
                smtp.login(settings.smtp_username, password)
            smtp.send_message(message)

    async def send(self, email: OutgoingEmail) -> None:
        rendered = render(email.kind, email.code, email.service)
        message = EmailMessage()
        message["Subject"] = rendered.subject
        message["From"] = self._settings.smtp_from
        message["To"] = email.to
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain="riftlab.local")
        message.set_content(rendered.text)
        message.add_alternative(rendered.html, subtype="html")
        await asyncio.to_thread(self._deliver, message)


def build_mailer(settings: Settings) -> Mailer | None:
    """Sem `RIFTLAB_SMTP_HOST`, não há e-mail: cadastro e recuperação respondem indisponível."""
    return SmtpMailer(settings) if settings.smtp_host else None


async def deliver(mailer: Mailer, email: OutgoingEmail) -> None:
    """Tarefa de segundo plano: nunca levanta, e o log não leva endereço nem código."""
    try:
        await mailer.send(email)
    except Exception as exc:  # noqa: BLE001 - qualquer falha de envio só pode ir ao log
        logger.error("envio de e-mail falhou tipo=%s erro=%s", email.kind, type(exc).__name__)
