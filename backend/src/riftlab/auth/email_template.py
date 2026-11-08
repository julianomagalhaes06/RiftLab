# ruff: noqa: E501 - HTML de e-mail: estilo inline em cada elemento, em linhas longas
"""Corpo dos e-mails da conta, em texto e em HTML, no padrão visual Arena do site.

Clientes de e-mail não aceitam folha de estilo externa, fonte própria, `clip-path` nem SVG de
forma confiável: o HTML usa tabelas e estilo inline, sem imagem e sem link, e as cores são os
tokens de `frontend/src/design/tokens.css` (n0, n1, nline, ntext, accent). O texto puro vai
junto, para quem não abre HTML. O código nunca entra no assunto nem na prévia da caixa de entrada.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal

EmailKind = Literal["VERIFY", "RESET", "ALREADY_REGISTERED"]

_BACKGROUND = "#0f1013"
_SURFACE = "#16181d"
_LINE = "#262a32"
_TEXT = "#ecedef"
_MUTED = "#b4b8c1"
_SUBTLE = "#8c919b"
_ACCENT = "#b8a6ff"
_ACCENT_STRONG = "#8b6cff"
_ACCENT_SURFACE = "#221c3d"
_ON_ACCENT = "#0e0f13"
_FONT = "Inter,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_FOOTER_LEGAL = (
    "RiftLab isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot "
    "Games or anyone officially involved in producing or managing Riot Games properties."
)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


@dataclass(frozen=True, slots=True)
class _Copy:
    subject: str
    eyebrow: str
    title: str
    lead: str
    preview: str
    closing: str


_COPY: dict[EmailKind, _Copy] = {
    "VERIFY": _Copy(
        subject="Confirme seu e-mail no RiftLab",
        eyebrow="Confirmação de e-mail",
        title="Confirme seu e-mail",
        lead="Use este código para concluir seu cadastro no RiftLab.",
        preview="Falta só confirmar seu e-mail para começar a revisar suas partidas.",
        closing="Se não foi você, ignore este e-mail. Nenhuma conta será ativada.",
    ),
    "RESET": _Copy(
        subject="Redefina sua senha no RiftLab",
        eyebrow="Recuperação de senha",
        title="Redefina sua senha",
        lead="Use este código para criar uma senha nova no RiftLab.",
        preview="Recebemos um pedido para redefinir a senha da sua conta.",
        closing="Se não foi você, ignore este e-mail. Sua senha continua a mesma.",
    ),
    "ALREADY_REGISTERED": _Copy(
        subject="Sua conta no RiftLab",
        eyebrow="Aviso da conta",
        title="Este e-mail já tem conta",
        lead=(
            "Alguém tentou criar uma conta no RiftLab com este endereço, "
            "mas ele já está cadastrado."
        ),
        preview="Houve uma tentativa de cadastro com o seu e-mail.",
        closing="Se não foi você, ignore este e-mail. Nada mudou na sua conta.",
    ),
}

_EXPIRES = "O código vale por 15 minutos."
_ALREADY_HINT = 'Se foi você, entre normalmente ou use "Esqueci minha senha" na tela de entrada.'


def _text(copy: _Copy, code: str | None) -> str:
    if code is not None:
        return f"{copy.lead}\n\n{code}\n\n{_EXPIRES}\n{copy.closing}\n\nRiftLab\n"
    return f"{copy.lead}\n\n{_ALREADY_HINT}\n{copy.closing}\n\nRiftLab\n"


def _paragraph(text: str, color: str, *, margin: str = "0 0 12px") -> str:
    return (
        f'<p style="margin:{margin};font-family:{_FONT};font-size:15px;line-height:22px;'
        f'color:{color};">{escape(text, quote=False)}</p>'
    )


def _code_block(code: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:separate;margin:0 0 20px;">'
        f'<tr><td align="center" bgcolor="{_ACCENT_SURFACE}" style="padding:20px 8px 20px 18px;'
        f"background-color:{_ACCENT_SURFACE};border:1px solid {_ACCENT_STRONG};"
        f"border-radius:4px;font-family:{_FONT};font-size:36px;line-height:42px;font-weight:700;"
        f'letter-spacing:10px;color:{_TEXT};">{escape(code)}</td></tr></table>'
    )


def _html(copy: _Copy, code: str | None) -> str:
    if code is not None:
        middle = _code_block(code) + _paragraph(_EXPIRES, _TEXT)
    else:
        middle = _paragraph(_ALREADY_HINT, _TEXT)
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{escape(copy.subject, quote=False)}</title>
</head>
<body style="margin:0;padding:0;background-color:{_BACKGROUND};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{_BACKGROUND};">{escape(copy.preview, quote=False)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_BACKGROUND}" style="background-color:{_BACKGROUND};">
<tr><td align="center" style="padding:32px 16px;">
<!--[if mso]><table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:480px;">
<tr><td style="padding:0 0 20px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td width="32" height="32" align="center" valign="middle" bgcolor="{_ACCENT_STRONG}" style="width:32px;height:32px;background-color:{_ACCENT_STRONG};border-radius:6px;font-family:{_FONT};font-size:16px;font-weight:700;line-height:32px;color:{_ON_ACCENT};">R</td>
<td style="padding-left:10px;font-family:{_FONT};font-size:18px;font-weight:700;color:{_TEXT};">RiftLab</td>
</tr></table>
</td></tr>
<tr><td bgcolor="{_SURFACE}" style="padding:28px;background-color:{_SURFACE};border:1px solid {_LINE};border-top:3px solid {_ACCENT_STRONG};border-radius:4px;">
<p style="margin:0 0 6px;font-family:{_FONT};font-size:12px;line-height:16px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:{_ACCENT};">{escape(copy.eyebrow, quote=False)}</p>
<h1 style="margin:0 0 12px;font-family:{_FONT};font-size:24px;line-height:30px;font-weight:700;color:{_TEXT};">{escape(copy.title, quote=False).replace("e-mail", "e‑mail")}</h1>
{_paragraph(copy.lead, _MUTED, margin="0 0 20px")}
{middle}
{_paragraph(copy.closing, _MUTED, margin="0")}
</td></tr>
<tr><td style="padding:20px 4px 0;font-family:{_FONT};font-size:12px;line-height:18px;color:{_SUBTLE};">
<p style="margin:0 0 8px;">E-mail automático do RiftLab, revisão pós-partida de League of Legends. Não é preciso responder.</p>
<p lang="en" style="margin:0;">{escape(_FOOTER_LEGAL, quote=False)}</p>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>
"""


def render(kind: EmailKind, code: str | None) -> RenderedEmail:
    copy = _COPY[kind]
    return RenderedEmail(copy.subject, _text(copy, code), _html(copy, code))
