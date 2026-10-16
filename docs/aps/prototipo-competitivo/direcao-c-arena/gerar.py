"""Direção C do RiftLab — "Arena".

Linguagem de HUD de e-sports: fundo escuro azulado, cantos chanfrados, violeta
elétrico como acento principal, dourado para liga/ranking e verde/vermelho para
resultado. Gera oito pranchas SVG (quatro desktop 1440, quatro mobile 390).
Todos os números, perfis e partidas são fictícios, como no protótipo A.
Fontes: Chakra Petch, Barlow e Barlow Condensed (Google Fonts).
"""
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageEnhance, ImageFilter

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"

C = {
    "bg": "#090B12", "bg2": "#0F1220", "bg3": "#161A2C", "bg4": "#1D2238",
    "line": "#252B45", "line2": "#363D63",
    "text": "#F1F3FA", "text2": "#B0B7D0", "text3": "#8D95B4",
    "violet": "#8F6BFF", "violet2": "#B9A6FF", "violetd": "#2A2152",
    "gold": "#F2B84B", "goldd": "#3A2C12",
    "win": "#34D399", "wind": "#0F2E26", "loss": "#FF5C74", "lossd": "#3A1520",
    # Base neutra (painel redesenhado, cabeçalho e rodapé): poucas superfícies, bordas discretas.
    "n0": "#0F1013", "n1": "#16181D", "n2": "#1C1F25", "nline": "#262A32", "nline2": "#343944",
    "ntext": "#ECEDEF", "ntext2": "#B4B8C1", "ntext3": "#8C919B",
    "accent": "#8B6CFF", "accent2": "#B8A6FF", "accentd": "#221C3D",  # roxo só para ação e seleção
    "winN": "#3DD68C", "lossN": "#F2667A",
}
DISPLAY, BODY, COND = "Chakra Petch", "Barlow", "Barlow Condensed"
UI = "Inter"  # dados, textos e controles
CHAMPS = {
    "Kaisa": {"name": "Kai'Sa", "role": "Atirador", "subtitle": "Adaptação ao contexto",
              "abilities": ["Disparo", "Alcance", "Reposição", "Engajamento"],
              "build": ["Componente inicial", "Item de dano", "Defesa situacional"],
              "note": "Compare o momento da compra com o ouro, o inventário e as ameaças presentes."},
    "Caitlyn": {"name": "Caitlyn", "role": "Atirador", "subtitle": "Controle de distância"},
    "Ezreal": {"name": "Ezreal", "role": "Atirador", "subtitle": "Mobilidade e alcance"},
}
MATCHES = [
    ("DEMO-VISUAL-01", "Kaisa", "win", "Hoje, 20:18", "8 / 3 / 7", "214", "31:24"),
    ("DEMO-VISUAL-02", "Caitlyn", "loss", "Ontem, 22:04", "4 / 6 / 9", "181", "28:16"),
    ("DEMO-VISUAL-03", "Ezreal", "win", "Ontem, 19:40", "11 / 2 / 6", "243", "35:02"),
    ("DEMO-VISUAL-04", "Kaisa", "win", "Há 2 dias", "6 / 4 / 10", "207", "30:48"),
    ("DEMO-VISUAL-05", "Caitlyn", "loss", "Há 3 dias", "3 / 5 / 8", "192", "29:35"),
]
# Ordenação como no ranqueado: elo, divisão (IV → I) e PDL (0–100 por divisão).
LADDER = [
    (1, "Jogador 01", "Caitlyn", "Platina", "IV", 18, 92, False),
    (2, "Jogador 02", "Ezreal", "Ouro", "I", 64, 87, False),
    (3, "Perfil ilustrativo", "Kaisa", "Ouro", "III", 76, 84, True),
    (4, "Jogador 04", "Kaisa", "Ouro", "III", 41, 90, False),
    (5, "Jogador 05", "Ezreal", "Ouro", "IV", 88, 81, False),
    (6, "Jogador 06", "Caitlyn", "Ouro", "IV", 35, 79, False),
]
TIERS = [("Ferro", "iron"), ("Bronze", "bronze"), ("Prata", "silver"), ("Ouro", "gold"), ("Platina", "platinum"),
         ("Esmeralda", "emerald"), ("Diamante", "diamond"), ("Mestre", "master"), ("Grão-Mestre", "grandmaster"),
         ("Desafiante", "challenger")]
TIER_FILE = dict(TIERS)
DIVS = ["IV", "III", "II", "I"]
APEX = {"Mestre", "Grão-Mestre", "Desafiante"}  # sem divisões, PDL sem teto

# Identidade por elo: base (metal), light (reflexo), dark/deep (sombra) e text (leitura sobre o card).
# Todo o card de posição (placas, barra de PDL, halo, colchetes e título) deriva destes cinco tons.
TIER_STYLE = {
    "Ferro":       dict(base="#8A7F7B", light="#CFC6C2", dark="#3B3432", deep="#221E1D", text="#C9BFBB"),
    "Bronze":      dict(base="#B0704F", light="#EDB08F", dark="#4F2C1D", deep="#2A1811", text="#E4A07E"),
    "Prata":       dict(base="#98A6B4", light="#E6EDF4", dark="#3D4652", deep="#20252D", text="#C9D3DD"),
    "Ouro":        dict(base="#D09A3E", light="#F8DC95", dark="#5C4015", deep="#2E210C", text="#F2B84B"),
    "Platina":     dict(base="#3FA9A2", light="#A5E8DF", dark="#17433F", deep="#0D2624", text="#6FD3C8"),
    "Esmeralda":   dict(base="#2FB36E", light="#94EDB8", dark="#114229", deep="#0A2718", text="#5CD795"),
    "Diamante":    dict(base="#6283E6", light="#C4D3FF", dark="#1F2C61", deep="#121A3B", text="#A3B8FF"),
    "Mestre":      dict(base="#A85FDA", light="#E6C2F9", dark="#3F1C58", deep="#241034", text="#D19BF2"),
    "Grão-Mestre": dict(base="#D45454", light="#F7B0B0", dark="#521818", deep="#300E0E", text="#F28A8A"),
    "Desafiante":  dict(base="#48ABF2", light="#F3DC96", dark="#173B59", deep="#0D2235", text="#93CEFA"),
}
TIER_ORDER = [t for t, _ in TIERS]


def tier_key(tier): return TIER_FILE[tier]


def next_division(tier, div):
    """Próximo degrau da escada: IV → III → II → I → IV do elo seguinte (Diamante I → Mestre)."""
    i = DIVS.index(div)
    if i < 3:
        return tier, DIVS[i + 1]
    nt = TIER_ORDER[TIER_ORDER.index(tier) + 1]
    return nt, (None if nt in APEX else "IV")


def tier_defs():
    out = []
    for tier, st in TIER_STYLE.items():
        k = tier_key(tier)
        out.append(
            f'<linearGradient id="gPlate-{k}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{st["light"]}"/>'
            f'<stop offset=".55" stop-color="{st["base"]}"/><stop offset="1" stop-color="{st["dark"]}"/></linearGradient>'
            f'<linearGradient id="gDone-{k}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{st["dark"]}"/>'
            f'<stop offset="1" stop-color="{st["deep"]}"/></linearGradient>'
            f'<linearGradient id="gFill-{k}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{st["dark"]}"/>'
            f'<stop offset=".65" stop-color="{st["base"]}"/><stop offset="1" stop-color="{st["light"]}"/></linearGradient>'
            f'<radialGradient id="gHalo-{k}" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{st["base"]}" stop-opacity=".32"/>'
            f'<stop offset="1" stop-color="{st["base"]}" stop-opacity="0"/></radialGradient>')
    out.append('<linearGradient id="gNext" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1C2136"/><stop offset="1" stop-color="#121526"/></linearGradient>'
               '<linearGradient id="gSheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".38"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>'
               '<pattern id="pBrushed" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)"><rect width="1" height="6" fill="#FFFFFF" opacity=".13"/></pattern>'
               '<filter id="fSoft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>')
    return "".join(out)
ROUTES = ["Painel", "Campeões", "Minha posição", "Histórico"]

_img = {}


def col(c): return C.get(c, c)


def img_uri(champ, kind="plain"):
    """plain: retrato; glow: versão ampliada e desfocada para fundo de destaque."""
    key = (champ, kind)
    if key not in _img:
        im = Image.open(ASSETS / f"{champ}.png").convert("RGB")
        buf = BytesIO()
        if kind == "glow":
            im = im.resize((320, 320), Image.LANCZOS).filter(ImageFilter.GaussianBlur(22))
            im = ImageEnhance.Brightness(ImageEnhance.Color(im).enhance(1.4)).enhance(0.75).convert("RGBA")
            mask = Image.radial_gradient("L").resize((320, 320)).point(lambda v: max(0, 255 - int(v * 1.9)))
            im.putalpha(mask)
            im.save(buf, "PNG", optimize=True)
            _img[key] = "data:image/png;base64," + b64encode(buf.getvalue()).decode()
        else:
            im.save(buf, "PNG")
            _img[key] = "data:image/png;base64," + b64encode(buf.getvalue()).decode()
    return _img[key]


def _emblem_path(tier, mini):
    return ASSETS / f"ranked-emblem-{TIER_FILE[tier]}{'-mini' if mini else ''}.png"


def emblem_uri(tier="Ouro", mini=False):
    key = ("emblem", tier, mini)
    if key not in _img:
        _img[key] = "data:image/png;base64," + b64encode(_emblem_path(tier, mini).read_bytes()).decode()
    return _img[key]


def emblem_size(tier, w):
    im = Image.open(_emblem_path(tier, True))
    return w, w * im.height / im.width


def chamfer_pts(x, y, w, h, c=12, tl=True, br=True, tr=False, bl=False):
    pts = []
    pts += [(x, y + c), (x + c, y)] if tl else [(x, y)]
    pts += [(x + w - c, y), (x + w, y + c)] if tr else [(x + w, y)]
    pts += [(x + w, y + h - c), (x + w - c, y + h)] if br else [(x + w, y + h)]
    pts += [(x + c, y + h), (x, y + h - c)] if bl else [(x, y + h)]
    return " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)


def wrap(s, n):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > n:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur: lines.append(cur)
    return lines


def tspans(*parts):
    return "".join(f'<tspan fill="{col(f)}">{escape(t)}</tspan>' for t, f in parts)


DEFS = f"""<defs>
<linearGradient id="gViolet" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#8E6DFF"/><stop offset="1" stop-color="#B38CFF"/></linearGradient>
<linearGradient id="gGold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFD98A"/><stop offset=".55" stop-color="#F2B84B"/><stop offset="1" stop-color="#A8701E"/></linearGradient>
<linearGradient id="gArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8F6BFF" stop-opacity=".38"/><stop offset="1" stop-color="#8F6BFF" stop-opacity="0"/></linearGradient>
<linearGradient id="gFadeL" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{C['bg2']}"/><stop offset=".55" stop-color="{C['bg2']}" stop-opacity=".85"/><stop offset="1" stop-color="{C['bg2']}" stop-opacity="0"/></linearGradient>
<linearGradient id="gFadeB" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C['bg2']}" stop-opacity="0"/><stop offset="1" stop-color="{C['bg2']}"/></linearGradient>
<linearGradient id="gCard" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1B2036"/><stop offset="1" stop-color="#11142A"/></linearGradient>
<linearGradient id="gWin" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#34D399" stop-opacity=".16"/><stop offset=".45" stop-color="#34D399" stop-opacity="0"/></linearGradient>
<linearGradient id="gLoss" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FF5C74" stop-opacity=".16"/><stop offset=".45" stop-color="#FF5C74" stop-opacity="0"/></linearGradient>
<radialGradient id="gHalo" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#8F6BFF" stop-opacity=".45"/><stop offset="1" stop-color="#8F6BFF" stop-opacity="0"/></radialGradient>
<radialGradient id="gHaloGold" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#F2B84B" stop-opacity=".35"/><stop offset="1" stop-color="#F2B84B" stop-opacity="0"/></radialGradient>
<pattern id="pStripes" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="8" fill="#F2B84B" opacity=".55"/></pattern>
<pattern id="pGrid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0 H0 V32" fill="none" stroke="#1A1F33" stroke-width="1"/></pattern>
<filter id="fGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5"/></filter>
"""  # fechado em P.svg com as definições por elo


class P:
    def __init__(self): self.out = []

    def add(self, s): self.out.append(s); return self

    def g(self, name, extra=""): return self.add(f'<g id="{escape(name)}"{extra}>')

    def end(self): return self.add("</g>")

    def rect(self, x, y, w, h, fill="bg2", stroke=None, sw=1, name=None, rx=0, opacity=None):
        n = f' id="{escape(name)}"' if name else ""
        s = f' stroke="{col(stroke)}" stroke-width="{sw}"' if stroke else ""
        o = f' opacity="{opacity}"' if opacity is not None else ""
        f = "none" if fill is None else (fill if fill.startswith("url") else col(fill))
        return self.add(f'<rect{n} x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{f}"{s}{o}/>')

    def cham(self, x, y, w, h, fill="gCard", stroke="line", c=12, name=None, sw=1, **k):
        n = f' id="{escape(name)}"' if name else ""
        f = "none" if fill is None else (f"url(#{fill})" if fill.startswith("g") and fill not in C else col(fill))
        s = f' stroke="{col(stroke)}" stroke-width="{sw}"' if stroke else ""
        return self.add(f'<polygon{n} points="{chamfer_pts(x, y, w, h, c, **k)}" fill="{f}"{s}/>')

    def line(self, x1, y1, x2, y2, stroke="line", sw=1, dash=None, opacity=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{opacity}"' if opacity is not None else ""
        return self.add(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{col(stroke)}" stroke-width="{sw}"{d}{o}/>')

    def text(self, x, y, s, size=16, fill="text", family=BODY, weight=400, anchor=None, ls=None, name=None, tab=False):
        a = f' text-anchor="{anchor}"' if anchor else ""
        a += ' style="font-variant-numeric: tabular-nums"' if tab else ""
        l = f' letter-spacing="{ls}"' if ls is not None else ""
        n = f' id="{escape(name)}"' if name else ""
        f = fill if fill.startswith("url") else col(fill)
        body = s if s.startswith("<tspan") else escape(s)
        return self.add(f'<text{n} x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
                        f'font-weight="{weight}" fill="{f}"{a}{l}>{body}</text>')

    def ui(self, x, y, s, size=14, fill="ntext", weight=400, anchor=None, tab=False, name=None):
        """Texto de interface (Inter), sem caixa alta nem espaçamento extra."""
        return self.text(x, y, s, size, fill, UI, weight, anchor, None, name, tab)

    def surface(self, x, y, w, h, name=None, fill="n1"):
        return self.cham(x, y, w, h, fill, "nline", 8, name=name)

    def link(self, x, y, s, anchor="end", size=14):
        return self.ui(x, y, s + "  →", size, "accent2", 600, anchor)

    def label(self, x, y, s, size=13, fill="text3", anchor=None, weight=600, ls=1.4):
        return self.text(x, y, s.upper(), size, fill, COND, weight, anchor, ls)

    def display(self, x, y, s, size=40, fill="text", anchor=None, weight=700, ls=0):
        return self.text(x, y, s, size, fill, DISPLAY, weight, anchor, ls)

    def para(self, x, y, s, n, size=16, lh=None, **kw):
        lh = lh or round(size * 1.5)
        lines = wrap(s, n)
        for i, ln in enumerate(lines):
            self.text(x, y + i * lh, ln, size, **kw)
        return y + len(lines) * lh

    def portrait(self, x, y, champ, size, ring=None, c=None, glow=False):
        cid = f"clip{len(self.out)}"
        c = c if c is not None else max(6, size // 7)
        self.add(f'<clipPath id="{cid}"><polygon points="{chamfer_pts(x, y, size, size, c)}"/></clipPath>')
        if glow:
            self.add(f'<polygon points="{chamfer_pts(x - 2, y - 2, size + 4, size + 4, c)}" fill="{col(ring or "violet")}" filter="url(#fGlow)" opacity=".8"/>')
        self.add(f'<image id="Retrato {escape(CHAMPS[champ]["name"])}" x="{x}" y="{y}" width="{size}" height="{size}" '
                 f'preserveAspectRatio="xMidYMid slice" clip-path="url(#{cid})" href="{img_uri(champ)}"/>')
        if ring:
            self.add(f'<polygon points="{chamfer_pts(x, y, size, size, c)}" fill="none" stroke="{col(ring)}" stroke-width="2"/>')
        return self

    def backdrop(self, x, y, w, h, champ, opacity=.9, cx=None):
        cid = f"clip{len(self.out)}"
        self.add(f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></clipPath>')
        cx = x + w - h * .8 if cx is None else cx
        self.add(f'<image id="Fundo desfocado" x="{cx - h * 1.1}" y="{y + h / 2 - h * 1.1}" width="{h * 2.2}" height="{h * 2.2}" '
                 f'preserveAspectRatio="xMidYMid slice" clip-path="url(#{cid})" opacity="{opacity}" href="{img_uri(champ, "glow")}"/>')
        return self

    def button(self, x, y, w, label, primary=True, h=48, size=16):
        self.g(f"Botão — {label}")
        if primary:
            self.add(f'<polygon points="{chamfer_pts(x, y, w, h, 10)}" fill="url(#gViolet)" filter="url(#fGlow)" opacity=".45"/>')
            self.cham(x, y, w, h, "gViolet", None, 10)
            self.text(x + w / 2, y + h / 2 + size * .36, label.upper(), size, "#0B0718", COND, 700, "middle", 1.2)
        else:
            self.cham(x, y, w, h, None, "line2", 10, sw=1.5)
            self.text(x + w / 2, y + h / 2 + size * .36, label.upper(), size, "text", COND, 600, "middle", 1.2)
        return self.end()

    def chip(self, x, y, label, fill="bg3", ink="text2", stroke=None, h=26, size=12):
        w = len(label) * size * .58 + 22
        self.cham(x, y, w, h, fill, stroke, 6)
        self.label(x + w / 2, y + h / 2 + size * .36, label, size, ink, "middle", 700, 1.2)
        return x + w

    def brackets(self, x, y, w, h, s=14, color="line2"):
        for (ax, ay, dx, dy) in [(x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)]:
            self.add(f'<path d="M{ax} {ay + dy * s} L{ax} {ay} L{ax + dx * s} {ay}" fill="none" stroke="{col(color)}" stroke-width="1.5"/>')

    def section(self, x, y, title, sub=None, right=None, rx=None):
        self.rect(x, y - 14, 4, 18, "violet")
        self.label(x + 14, y, title, 17, "text", weight=700, ls=1.8)
        if sub:
            self.text(x + 14 + len(title) * 10.6 + 16, y, sub, 14, "text3")
        if right:
            self.label(rx, y, right + "  ›", 14, "violet2", "end", 700)

    def ring(self, cx, cy, r, pct, sw=8, color="gViolet"):
        from math import pi, cos, sin
        self.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{C["bg4"]}" stroke-width="{sw}"/>')
        a0, a1 = -pi / 2, -pi / 2 + 2 * pi * pct
        x0, y0, x1, y1 = cx + r * cos(a0), cy + r * sin(a0), cx + r * cos(a1), cy + r * sin(a1)
        large = 1 if pct > .5 else 0
        self.add(f'<path d="M{x0:.1f} {y0:.1f} A{r} {r} 0 {large} 1 {x1:.1f} {y1:.1f}" fill="none" '
                 f'stroke="url(#{color})" stroke-width="{sw}" stroke-linecap="round"/>')

    def xpbar(self, x, y, w, pct, n=20, h=10, color="gGold", gap=3):
        sw = (w - gap * (n - 1)) / n
        k = round(n * pct)
        for i in range(n):
            self.add(f'<polygon points="{x + i * (sw + gap) + 3:.1f},{y} {x + i * (sw + gap) + sw:.1f},{y} '
                     f'{x + i * (sw + gap) + sw - 3:.1f},{y + h} {x + i * (sw + gap):.1f},{y + h}" '
                     f'fill="{"url(#" + color + ")" if i < k else C["bg4"]}"/>')

    def emblem(self, cx, cy, s=1.0, tier="Ouro"):
        """Emblema oficial do elo (Riot, pacote Ranked Emblems do portal Developer)."""
        self.g(f"Emblema {tier} (oficial Riot)")
        self.add(f'<circle cx="{cx}" cy="{cy}" r="{95 * s}" fill="url(#gHalo-{tier_key(tier)})"/>')
        w, h = emblem_size(tier, 190 * s)
        if h > 170 * s:
            w, h = w * 170 * s / h, 170 * s
        self.add(f'<image id="Emblema {escape(tier)}" x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" href="{emblem_uri(tier)}"/>')
        return self.end()

    def mini_emblem(self, x, y, w, tier="Ouro", opacity=None):
        """Emblema oficial em miniatura, centralizado verticalmente em y."""
        w, h = emblem_size(tier, w)
        o = f' opacity="{opacity}"' if opacity is not None else ""
        return self.add(f'<image id="Emblema {escape(tier)} (mini)" x="{x}" y="{y - h / 2:.1f}" width="{w}" height="{h:.1f}"{o} href="{emblem_uri(tier, True)}"/>')

    def svg(self, w, h, title):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" '
                f'height="{h}" viewBox="0 0 {w} {h}"><title>{escape(title)}</title>{DEFS}{tier_defs()}</defs>' + "".join(self.out) + "</svg>")


# ------------------------------------------------------------ ícones simples
def icon(p, name, x, y, s=20, color="text2"):
    c = col(color)
    k = s / 20
    st = f'fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"'
    t = f'transform="translate({x} {y}) scale({k})"'
    d = {
        "Painel": f'<g {t}><rect x="2" y="2" width="7" height="9" {st}/><rect x="11" y="2" width="7" height="5" {st}/><rect x="2" y="13" width="7" height="5" {st}/><rect x="11" y="9" width="7" height="9" {st}/></g>',
        "Campeões": f'<g {t}><path d="M10 2 L17 5 V10 C17 14 14 17 10 18 C6 17 3 14 3 10 V5 Z" {st}/><path d="M7 9 L10 12 L13 8" {st}/></g>',
        "Minha posição": f'<g {t}><path d="M6 3 H14 V8 C14 11 12 12 10 12 C8 12 6 11 6 8 Z" {st}/><path d="M6 5 H3 C3 8 4 9 6 9 M14 5 H17 C17 8 16 9 14 9 M10 12 V15 M6 18 H14 M7 18 L8 15 H12 L13 18" {st}/></g>',
        "Histórico": f'<g {t}><circle cx="10" cy="10" r="7.5" {st}/><path d="M10 5.5 V10 L13 12" {st}/></g>',
        "search": f'<g {t}><circle cx="8.5" cy="8.5" r="5.5" {st}/><path d="M13 13 L17.5 17.5" {st}/></g>',
        "info": f'<g {t}><circle cx="10" cy="10" r="7.5" {st}/><path d="M10 9 V14 M10 6.2 V6.4" {st}/></g>',
        "sword": f'<g {t}><path d="M16 3 L17 4 L8 13 L7 12 Z M5 11 L9 15 M4 16 L6 14" {st}/></g>',
        "target": f'<g {t}><circle cx="10" cy="10" r="7" {st}/><circle cx="10" cy="10" r="3" {st}/></g>',
        "eye": f'<g {t}><path d="M2 10 C5 5 15 5 18 10 C15 15 5 15 2 10 Z" {st}/><circle cx="10" cy="10" r="2.5" {st}/></g>',
        "play": f'<g {t}><path d="M7 5 L15 10 L7 15 Z" {st}/></g>',
        "close": f'<g {t}><path d="M5 5 L15 15 M15 5 L5 15" {st}/></g>',
        "warn": f'<g {t}><path d="M10 3 L18 17 H2 Z" {st}/><path d="M10 8 V12 M10 14.5 V14.7" {st}/></g>',
    }[name]
    p.add(d)


# ------------------------------------------------------------ desktop
W, X0, X1 = 1440, 40, 1400


def logo(p, cx, cy, r, size):
    hexp = " ".join(f"{cx + r * dx:.1f},{cy + r * dy:.1f}" for dx, dy in [(0, -1), (.87, -.5), (.87, .5), (0, 1), (-.87, .5), (-.87, -.5)])
    p.add(f'<polygon points="{hexp}" fill="{C["accent"]}"/>')
    p.display(cx, cy + r * .36, "R", r * 1.05, "#0E0F13", "middle", 700)
    p.display(cx + r + 12, cy + size * .34, "RiftLab", size, "ntext", weight=600)


def chrome(p, active, h):
    p.rect(0, 0, W, h, "n0", name="Fundo")
    p.g("Barra superior")
    p.line(0, 64, W, 64, "nline")
    logo(p, 58, 32, 14, 20)
    x = 380
    for r in ROUTES:
        on = r == active
        tw_ = len(r) * 8.4
        p.g(f"Aba — {r}")
        icon(p, r, x, 23, 18, "accent2" if on else "ntext3")
        p.ui(x + 26, 38, r, 15, "ntext" if on else "ntext2", 600 if on else 500)
        if on:
            p.rect(x, 62, 26 + tw_, 2, "accent", name="Aba ativa")
        p.end()
        x += 26 + tw_ + 40
    p.g("Perfil")
    p.rect(1094, 20, 40, 24, None, "nline2", 1, rx=4)
    p.ui(1114, 36, "BR1", 12, "ntext2", 600, "middle")
    p.portrait(1150, 16, "Kaisa", 32, "nline2", 6)
    p.ui(1192, 29, "Perfil ilustrativo", 14, "ntext", 500)
    p.mini_emblem(1192, 44, 16, "Ouro")
    p.ui(1212, 49, "Ouro III · 76 PDL", 12, TIER_STYLE["Ouro"]["text"], 600, tab=True)
    p.end()
    p.end()
    p.g("Aviso de demonstração")
    p.rect(0, 65, W, 32, "n1")
    icon(p, "info", X0, 72, 16, "ntext3")
    p.ui(X0 + 24, 85, tspans(("Modo demonstração", C["ntext2"]), (" · estatísticas, perfis, ranking e partidas desta tela são fictícios.", C["ntext3"])), 13, "ntext2", 500)
    p.ui(X1, 85, "Patch 16.17 · Data Dragon 16.17.1", 12, "ntext3", anchor="end")
    p.line(0, 97, W, 97, "nline")
    p.end()


def footer(p, y):
    p.line(0, y, W, y, "nline")
    p.ui(X0, y + 32, "RiftLab", 12, "ntext3")
    p.ui(W / 2, y + 32, "Dados ilustrativos; não afirma funcionalidades entregues.", 12, "ntext3", anchor="middle")
    p.ui(X1, y + 32, "Retratos e emblemas © Riot Games", 12, "ntext3", anchor="end")


def match_card(p, x, y, w, m, h=84, compact=False, selected=False):
    mid, champ, res, when, kda, cs, length = m
    win = res == "win"
    p.g(f"Partida {mid}")
    p.cham(x, y, w, h, "bg2", "violet" if selected else "line", 10, sw=1.5 if selected else 1)
    p.add(f'<polygon points="{chamfer_pts(x, y, w, h, 10)}" fill="url(#{"gWin" if win else "gLoss"})"/>')
    p.rect(x, y + 10, 4, h - 20, "win" if win else "loss")
    p.portrait(x + 22, y + 14, champ, h - 28, "win" if win else "loss", 8)
    tx = x + 22 + h - 28 + 18
    p.display(tx, y + 36, "VITÓRIA" if win else "DERROTA", 18, "win" if win else "loss", weight=700, ls=1)
    p.text(tx, y + 60, f"{CHAMPS[champ]['name']} · Ranqueada ilustrativa", 14, "text2")
    if not compact:
        p.label(tx + 250, y + 36, when, 14, "text", weight=600, ls=.6)
        p.label(tx + 250, y + 60, f"Duração {length}", 13, "text3", ls=.8)
    kx = x + w - (330 if not compact else 250)
    p.display(kx, y + 42, kda, 24, "text", weight=700)
    p.label(kx, y + 62, f"K / D / A · {cs} tropas", 12, "text3")
    bx = x + w - 130
    p.cham(bx, y + h / 2 - 18, 108, 36, "bg3", "line2", 8)
    icon(p, "eye", bx + 12, y + h / 2 - 9, 18, "violet2")
    p.label(bx + 38, y + h / 2 + 5, "Contexto", 14, "text", weight=700, ls=1)
    p.end()


def division_plate(p, x, y, w, h, label, state, tier):
    """Placa de divisão. state: 'done' (concluída), 'now' (atual) ou 'next' (a seguir)."""
    st, k, c = TIER_STYLE[tier], tier_key(tier), 7
    pts = chamfer_pts(x, y, w, h, c)
    p.g(f"Placa {label} — {state}")
    if state == "now":
        p.add(f'<polygon points="{chamfer_pts(x - 1, y - 1, w + 2, h + 2, c)}" fill="{st["base"]}" filter="url(#fSoft)" opacity=".45"/>')
        p.add(f'<polygon points="{pts}" fill="url(#gPlate-{k})" stroke="{st["light"]}" stroke-width="1.2"/>')
        p.add(f'<polygon points="{pts}" fill="url(#pBrushed)" opacity=".6"/>')
        p.add(f'<polygon points="{chamfer_pts(x + 2, y + 2, w - 4, h / 2 - 2, c - 2, br=False)}" fill="url(#gSheen)"/>')
        p.line(x + c + 2, y + 1.6, x + w - 2, y + 1.6, "#FFFFFF", 1, opacity=.6)
        p.line(x + 2, y + h - 1.6, x + w - c - 2, y + h - 1.6, "#000000", 1, opacity=.35)
        p.add(f'<polygon points="{x + w / 2 - 5:.1f},{y + h + 3} {x + w / 2 + 5:.1f},{y + h + 3} {x + w / 2:.1f},{y + h + 8}" fill="{st["light"]}"/>')
        ink = "#0E0F16"
    elif state == "done":
        p.add(f'<polygon points="{pts}" fill="url(#gDone-{k})" stroke="{st["base"]}" stroke-width="1" stroke-opacity=".7"/>')
        p.line(x + c + 2, y + 1.6, x + w - 2, y + 1.6, "#FFFFFF", 1, opacity=.14)
        p.rect(x + 8, y + h - 5, w - 8 - c - 4, 2, st["base"], opacity=.8)
        ink = st["text"]
    else:
        p.add(f'<polygon points="{pts}" fill="url(#gNext)" stroke="#2F3654" stroke-width="1"/>')
        p.line(x + c + 2, y + 1.6, x + w - 2, y + 1.6, "#FFFFFF", 1, opacity=.07)
        ink = "#A7AEC8"
    p.display(x + w / 2, y + h / 2 + 5.5, label, 15, ink, "middle", 700, 1)
    return p.end()


def division_track(p, x, y, w, tier, div):
    """Trilha IV → I do elo atual + próximo elo à direita."""
    p.g("Trilha de divisões")
    zone = 64
    gap = 6
    pw_ = (w - zone - 3 * gap) / 4
    cur = DIVS.index(div)
    for i, d in enumerate(DIVS):
        division_plate(p, x + i * (pw_ + gap), y, pw_, 32, d, "done" if i < cur else "now" if i == cur else "next", tier)
    nt = TIER_ORDER[TIER_ORDER.index(tier) + 1]
    zx = x + w - zone
    p.add(f'<path d="M{zx + 6} {y + 9} L{zx + 13} {y + 16} L{zx + 6} {y + 23}" fill="none" stroke="{C["text3"]}" stroke-width="2"/>')
    p.mini_emblem(zx + 22, y + 12, 40, nt, .9)
    p.label(zx + 42, y + 46, f"{nt} IV" if nt not in APEX else nt, 11, "text3", "middle", 700, .6)
    return p.end()


def lp_bar(p, x, y, w, tier, lp, target):
    """Barra de PDL 0–100: trilho escuro, preenchimento do elo com textura escovada e ponta destacada."""
    st, k = TIER_STYLE[tier], tier_key(tier)
    p.g("Barra de PDL (0–100)")
    p.label(x, y, "PDL na divisão", 13, "text3")
    p.display(x + w, y + 2, tspans((str(lp), st["text"]), (" / 100", "text3")), 18, "text", "end", 700)
    ty, th = y + 12, 14
    p.cham(x, ty, w, th, "#0A0C14", "#2A3150", 4)
    p.line(x + 5, ty + 1.5, x + w - 2, ty + 1.5, "#000000", 1, opacity=.55)
    fw = max(w * lp / 100, 10)
    fp = chamfer_pts(x + 1, ty + 1, fw - 1, th - 2, 3, br=lp >= 100)
    p.add(f'<polygon points="{fp}" fill="url(#gFill-{k})"/>')
    p.add(f'<polygon points="{fp}" fill="url(#pBrushed)"/>')
    p.line(x + 5, ty + 2.5, x + fw - 3, ty + 2.5, "#FFFFFF", 1, opacity=.3)
    if lp < 100:
        p.add(f'<ellipse cx="{x + fw:.1f}" cy="{ty + th / 2}" rx="5" ry="9" fill="{st["light"]}" filter="url(#fSoft)" opacity=".5"/>')
        p.rect(round(x + fw - 1.5, 1), ty, 2, th, st["light"], name="Ponta do progresso")
    for t in (25, 50, 75):
        tx_ = x + w * t / 100
        p.line(tx_, ty + th + 3, tx_, ty + th + 7, "#3A4160", 1)
    p.label(x, y + 48, "0", 12, "text3")
    p.label(x + w, y + 48, "100", 12, "text3", "end")
    p.text(x + w / 2, y + 48, tspans((f"Faltam {100 - lp} PDL para ", C["text2"]), (target, st["text"])), 14, "text2", BODY, 600, "middle")
    return p.end()


def rank_panel(p, x, y, w, h=482, tier="Ouro", div="III", lp=76, gain=22, title="Posição ilustrativa", surface=None):
    st = TIER_STYLE[tier]
    p.g(f"Card de posição — {tier} {div or ''}".strip())
    p.cham(x, y, w, h, surface or "gCard", "nline" if surface else "line", 14)
    p.brackets(x + 10, y + 10, w - 20, h - 20, 12, st["dark"])
    p.label(x + 24, y + 36, title, 16, "text", weight=700, ls=1.6)
    p.chip(x + w - 70, y + 20, "BR1", st["deep"], st["text"])
    p.emblem(x + w / 2, y + 138, .9, tier)
    p.display(x + w / 2, y + 244, f"{tier.upper()} {div}" if div else tier.upper(), 30, st["text"], "middle", 700, 1)
    p.text(x + w / 2, y + 266, "Simulação visual, sem classificação Riot", 14, "text3", anchor="middle")
    if tier in APEX:
        division_plate(p, x + 28, y + 286, w - 56, 32, "SEM DIVISÕES · PDL SEM TETO", "now", tier)
        p.g("PDL (elo sem divisões)")
        p.label(x + 28, y + 362, "PDL acumulados", 13, "text3")
        p.display(x + w - 28, y + 364, f"{lp} PDL", 18, st["text"], "end", 700)
        p.text(x + w / 2, y + 410, "Elos do topo não têm divisões.", 14, "text2", BODY, 600, "middle")
        p.end()
    else:
        division_track(p, x + 28, y + 286, w - 56, tier, div)
        nt, nd = next_division(tier, div)
        lp_bar(p, x + 28, y + 362, w - 56, tier, lp, f"{nt} {nd}" if nd else nt)
    p.cham(x + 28, y + 424, w - 56, 34, "wind", None, 6)
    p.text(x + w / 2, y + 446, f"▲ +{gain} PDL na última vitória ilustrativa", 14, "win", BODY, 600, "middle")
    p.end()


def metric_block(p, x, y, label, value, foot, bar=None, info_x=None):
    """Métrica principal: rótulo + info, valor grande em fonte de interface, rodapé e barra opcional."""
    p.g(f"Métrica — {label}")
    p.ui(x, y, label, 14, "ntext2", 500)
    icon(p, "info", info_x, y - 13, 16, "ntext3")
    p.ui(x - 2, y + 50, value, 44, "ntext", 600, tab=True)
    p.ui(x, y + 76, foot, 13, "ntext3")
    if bar is not None:
        p.rect(x, y + 90, 300, 4, "nline", rx=2)
        p.rect(x, y + 90, 300 * bar, 4, "winN", rx=2, name="Proporção de vitórias")
    p.end()


def secondary_metric(p, x, y, label, value, foot):
    p.g(f"Métrica secundária — {label}")
    p.ui(x, y, tspans((label + "  ", C["ntext3"]), (value, C["ntext"]), (("  ·  " + foot) if foot else "", C["ntext3"])), 14, "ntext3", 500, tab=True)
    return p.end()


def match_rows(p, x, y, w, rows, compact=False, rh=None, more=None):
    """Lista de partidas numa única superfície, com separadores sutis e colunas alinhadas."""
    rh = rh or (64 if compact else 68)
    fh = 52 if more else 0
    p.g("Partidas recentes — lista")
    p.surface(x, y, w, rh * len(rows) + fh)
    for i, (mid, champ, res, when, kda, cs, length) in enumerate(rows):
        top, win = y + i * rh, res == "win"
        cy = top + rh / 2
        rc = "winN" if win else "lossN"
        p.g(f"Partida {mid}")
        if i:
            p.line(x + 16, top, x + w - 16, top, "nline")
        p.rect(x, top + 14, 3, rh - 28, rc, name="Resultado")
        if compact:
            p.portrait(x + 16, cy - 18, champ, 36, "nline2", 6)
            p.ui(x + 64, cy - 3, CHAMPS[champ]["name"], 15, "ntext", 600)
            p.ui(x + 64, cy + 15, tspans(("Vitória" if win else "Derrota", C[rc]), (f" · {when}", C["ntext3"])), 13, "ntext3", 600)
            p.ui(x + w - 16, cy - 3, kda, 15, "ntext", 600, "end", tab=True)
            p.ui(x + w - 16, cy + 15, "Contexto  →", 13, "accent2", 600, "end")
        else:
            p.ui(x + 24, cy + 5, "Vitória" if win else "Derrota", 14, rc, 600)
            p.portrait(x + 112, cy - 18, champ, 36, "nline2", 6)
            p.ui(x + 160, cy - 3, CHAMPS[champ]["name"], 15, "ntext", 600)
            p.ui(x + 160, cy + 16, "Ranqueada ilustrativa", 13, "ntext3")
            p.ui(x + 380, cy - 3, when, 14, "ntext2", 500)
            p.ui(x + 380, cy + 16, f"Duração {length}", 13, "ntext3", tab=True)
            p.ui(x + 560, cy - 3, kda, 16, "ntext", 600, tab=True)
            p.ui(x + 560, cy + 16, f"K / D / A · {cs} tropas", 12, "ntext3", tab=True)
            icon(p, "eye", x + w - 112, cy - 8, 16, "accent2")
            p.ui(x + w - 24, cy + 5, "Contexto", 14, "accent2", 600, "end")
        p.end()
    p.end()
    if more:
        fy = y + rh * len(rows)
        p.g("Rodapé — histórico completo")
        p.line(x, fy, x + w, fy, "nline")
        p.ui(x + (16 if compact else 24), fy + 31, more[0], 13, "ntext3", tab=True)
        p.link(x + w - (16 if compact else 24), fy + 31, more[1], size=14 if not compact else 13)
        p.end()
    return y + rh * len(rows) + fh


def champion_rows(p, x, y, w, compact=False):
    """Campeões em foco: seleção em roxo discreto; demais linhas neutras."""
    rh = 60 if compact else 68
    counts = {ch: sum(1 for m in MATCHES if m[1] == ch) for ch in CHAMPS}
    p.g("Campeões em foco — lista")
    p.surface(x, y, w, rh * len(CHAMPS))
    for i, ch in enumerate(CHAMPS):
        top, on = y + i * rh, i == 0
        cy = top + rh / 2
        p.g(f"Campeão — {CHAMPS[ch]['name']}")
        if i:
            p.line(x + 16, top, x + w - 16, top, "nline")
        if on:
            p.rect(x + 1, top + 1, w - 2, rh - 2, "accent", opacity=.09, name="Selecionado")
            p.rect(x, top + 14, 3, rh - 28, "accent")
        p.portrait(x + 20, cy - 18, ch, 36, "accent" if on else "nline2", 6)
        n = counts[ch]
        extra = CHAMPS[ch]["role"] if compact else f"{n} {'partida' if n == 1 else 'partidas'} no recorte"
        p.ui(x + 68, cy - 3, f'<tspan>{escape(CHAMPS[ch]["name"])}</tspan><tspan fill="{C["ntext3"]}" font-size="13" font-weight="400" dx="8">{escape(extra)}</tspan>', 15, "ntext", 600)
        p.ui(x + 68, cy + 15, CHAMPS[ch]["subtitle"] if compact else f"{CHAMPS[ch]['role']} · {CHAMPS[ch]['subtitle']}", 13, "ntext3")
        p.ui(x + w - 20, cy + 5, "Guia  →" if compact else "Ver guia  →", 14 if not compact else 13, "accent2", 600, "end")
        p.end()
    p.end()
    return y + rh * len(CHAMPS)


def section_title(p, x, y, title, link=None, lx=None, size=16):
    p.ui(x, y, title, size, "ntext", 600)
    if link:
        p.link(lx, y, link, size=14 if size >= 16 else 13)


def panel_desktop():
    b = P()  # conteúdo; o fundo e o cabeçalho entram depois, quando a altura é conhecida
    b.g("Cabeçalho da página")
    b.ui(X0, 136, "Painel · últimas 20 partidas ilustrativas", 13, "ntext3", 500)
    b.display(X0, 172, "Entenda a partida. Evolua a decisão.", 30, "ntext", weight=600)
    b.ui(X0, 200, "Olhe contexto, reconheça padrões e estude compras sem prometer uma resposta universal.", 15, "ntext2")
    b.end()

    lx, lw = X0, 880
    rx, rw = X0 + lw + 24, X1 - X0 - lw - 24
    y = 232
    # coluna esquerda: desempenho + histórico resumido
    b.g("Desempenho")
    b.surface(lx, y, lw, 196)
    metric_block(b, lx + 28, y + 40, "Taxa de vitória", "58%", "Últimas 20 partidas ilustrativas", .58, lx + lw / 2 - 44)
    b.line(lx + lw / 2, y + 22, lx + lw / 2, y + 126, "nline")
    metric_block(b, lx + lw / 2 + 28, y + 40, "KDA médio", "3,4", "(Abates + assistências) / mortes · relação simplificada", None, lx + lw - 44)
    b.line(lx + 16, y + 142, lx + lw - 16, y + 142, "nline")
    secondary_metric(b, lx + 28, y + 175, "Decisões revistas", "18", "7 com reflexão registrada")
    secondary_metric(b, lx + lw / 2 + 28, y + 175, "Campeões no recorte", "3", "")
    for j, ch in enumerate(CHAMPS):
        b.portrait(lx + lw / 2 + 196 + j * 26, y + 160, ch, 22, "nline2", 4)
    b.end()
    my = y + 196 + 40
    section_title(b, lx, my, "Partidas recentes")
    left_end = match_rows(b, lx, my + 20, lw, MATCHES[:5], rh=72,
                          more=("Mostrando as 5 mais recentes de 20 partidas ilustrativas", "Ver as 20 partidas no Histórico"))

    # coluna direita: card de elo (foco) + campeões em foco
    rank_panel(b, rx, y, rw, surface="n1")
    cy_ = y + 482 + 40
    section_title(b, rx, cy_, "Campeões em foco", "Todos os guias", X1)
    right_end = champion_rows(b, rx, cy_ + 20, rw)

    H = max(left_end, right_end) + 48 + 60
    p = P()
    chrome(p, "Painel", H)
    p.out += b.out
    footer(p, H - 60)
    return p.svg(W, H, "RiftLab — Painel (desktop)")


def champions_desktop():
    p, H = P(), 1380
    c = CHAMPS["Kaisa"]
    chrome(p, "Campeões", H)
    y = 132
    p.g("Seletor de campeões")
    p.label(X0, y + 22, "Biblioteca de campeões", 15, "violet2", weight=700, ls=2.4)
    p.display(X0, y + 70, "ESTUDE UM CAMPEÃO.", 44, "text", weight=700)
    p.text(X0, y + 98, "Retratos oficiais; organização didática e trajetos abaixo são ilustrativos.", 16, "text2")
    x = X1 - 3 * 212 + 12
    for i, ch in enumerate(CHAMPS):
        on = i == 0
        p.g(f"Seleção — {CHAMPS[ch]['name']}")
        p.cham(x, y + 20, 200, 80, "violetd" if on else "bg2", "violet" if on else "line", 10, sw=1.5 if on else 1)
        p.portrait(x + 12, y + 32, ch, 56, "violet" if on else "line2", 8, glow=on)
        p.display(x + 80, y + 58, CHAMPS[ch]["name"].upper(), 17, "text", weight=700)
        p.label(x + 80, y + 80, "Selecionado" if on else "Ver guia", 12, "violet2" if on else "text3", weight=700)
        p.end()
        x += 212
    p.end()

    y = 262
    p.g("Destaque do campeão")
    p.cham(X0, y, X1 - X0, 360, "bg2", "line", 18)
    p.backdrop(X0, y, X1 - X0, 360, "Kaisa", 1, X0 + 190)
    p.add(f'<circle cx="{X0 + 190}" cy="{y + 180}" r="200" fill="url(#gHalo)"/>')
    p.portrait(X0 + 60, y + 50, "Kaisa", 260, "violet", 30, glow=True)
    gx = X0 + 380
    p.label(gx, y + 70, "Guia de estudo · 16.17.1", 15, "violet2", weight=700, ls=2.4)
    p.display(gx - 4, y + 160, "KAI'SA", 104, "text", weight=700, ls=2)
    p.display(gx, y + 204, c["subtitle"].upper(), 24, "violet2", weight=600, ls=1.4)
    p.para(gx, y + 244, c["note"], 90, 18, 26, fill="text2")
    cx = gx
    cx = p.chip(cx, y + 282, "Atirador", "bg3", "text", "line2", 32, 13) + 10
    cx = p.chip(cx, y + 282, "Patch do conceito 16.17", "bg3", "text2", "line2", 32, 13) + 10
    p.g("Aviso — sem ranking validado")
    p.cham(cx, y + 282, 290, 32, "goldd", "gold", 6)
    icon(p, "warn", cx + 10, y + 288, 20, "gold")
    p.label(cx + 38, y + 303, "Sem ranking de itens validado", 13, "gold", weight=700, ls=1)
    p.end()
    p.end()

    y = 660
    p.section(X0, y, "Habilidades em contexto", "Mapa resumido para prototipação; não substitui a ficha oficial.")
    aw = (X1 - X0 - 60) / 4
    for i, ab in enumerate(c["abilities"]):
        x = X0 + i * (aw + 20)
        p.g(f"Habilidade {'QWER'[i]}")
        p.cham(x, y + 22, aw, 140, "gCard", "line", 12)
        p.cham(x + 20, y + 42, 52, 52, "violetd" if i == 3 else "bg4", "violet" if i == 3 else "line2", 8, sw=1.5)
        p.display(x + 46, y + 78, "QWER"[i], 26, "violet2" if i == 3 else "text", "middle", 700)
        p.display(x + 88, y + 70, ab.upper(), 20, "text", weight=700, ls=.6)
        p.label(x + 88, y + 90, "Ultimate" if i == 3 else "Básica", 12, "violet2" if i == 3 else "text3", weight=700)
        p.text(x + 20, y + 134, "Como muda a próxima decisão", 15, "text2")
        p.end()

    y = 870
    p.section(X0, y, "Trajeto de itens", "Exemplo pedagógico, não uma build recomendada.")
    p.g("Trajeto")
    sw_ = (X1 - X0 - 2 * 40) / 3
    for i, step in enumerate(c["build"]):
        x = X0 + i * (sw_ + 40)
        p.cham(x, y + 22, sw_, 120, "gCard", "violet" if i == 0 else "line", 12, sw=1.5 if i == 0 else 1)
        p.cham(x + 20, y + 42, 80, 80, "bg4", "line2", 10)
        p.add(f'<polygon points="{chamfer_pts(x + 30, y + 52, 60, 60, 8)}" fill="none" stroke="{C["line2"]}" stroke-dasharray="4 4"/>')
        p.display(x + 60, y + 92, str(i + 1), 26, "violet2" if i == 0 else "text3", "middle", 700)
        p.label(x + 120, y + 60, f"Etapa {i + 1}", 13, "violet2" if i == 0 else "text3", weight=700)
        p.display(x + 120, y + 90, step.upper(), 19, "text", weight=700, ls=.4)
        p.text(x + 120, y + 116, "Confira custo e cenário", 14, "text3")
        if i < 2:
            ax = x + sw_ + 8
            p.add(f'<path d="M{ax} {y + 70} L{ax + 12} {y + 82} L{ax} {y + 94} M{ax + 10} {y + 70} L{ax + 22} {y + 82} L{ax + 10} {y + 94}" '
                  f'fill="none" stroke="{C["violet"]}" stroke-width="2.5"/>')
    p.end()

    y = 1070
    p.section(X0, y, "Matchups e decisão", "Perguntas para orientar a revisão.")
    p.g("Matchups")
    qs = [("target", "Alcance adversário", "Revisar exposição"), ("sword", "Tipo de dano", "Verificar defesa"),
          ("Histórico", "Tempo até a próxima luta", "Verificar componentes")]
    for i, (ic, q, a) in enumerate(qs):
        yy = y + 22 + i * 72
        p.cham(X0, yy, 900, 60, "bg2", "line", 10)
        p.cham(X0 + 14, yy + 12, 36, 36, "bg4", None, 6)
        icon(p, ic, X0 + 22, yy + 20, 20, "violet2")
        p.display(X0 + 66, yy + 37, q.upper(), 18, "text", weight=700, ls=.4)
        p.label(X0 + 880, yy + 36, a + "  ›", 15, "violet2", "end", 700, .8)
    p.cham(X0 + 924, y + 22, X1 - X0 - 924, 204, "gCard", "line", 12)
    p.brackets(X0 + 934, y + 32, X1 - X0 - 944, 184, 12)
    p.label(X0 + 948, y + 62, "Antes de sugerir uma build", 14, "gold", weight=700)
    p.para(X0 + 948, y + 92, "O produto futuro precisa de amostra e avaliação por campeão, papel e patch.", 44, 16, 24, fill="text2")
    p.button(X0 + 948, y + 158, X1 - X0 - 972, "Ver partidas ilustrativas  ›", False, 46, 15)
    p.end()
    footer(p, H - 60)
    return p.svg(W, H, "RiftLab — Campeões (desktop)")


def ranking_desktop():
    p, H = P(), 1390
    chrome(p, "Minha posição", H)
    y = 132
    p.label(X0, y + 22, "Escada competitiva · simulação", 15, "violet2", weight=700, ls=2.4)
    p.display(X0, y + 70, "VEJA SUA POSIÇÃO.", 44, "text", weight=700)
    p.text(X0, y + 98, "Um exercício visual. Nenhuma posição representa a classificação oficial de um jogador.", 16, "text2")
    p.g("Filtro de escopo")
    p.cham(X0 + 640, y + 42, 110, 42, "gViolet", None, 8)
    p.label(X0 + 695, y + 69, "Geral", 15, "#0B0718", "middle", 700)
    p.cham(X0 + 758, y + 42, 170, 42, None, "line2", 8, sw=1.5)
    p.label(X0 + 843, y + 69, "Amigos fictícios", 15, "text", "middle", 600)
    p.end()

    y = 270
    p.g("Pódio")
    order = [(1, 1), (0, 0), (2, 2)]  # (slot, índice em LADDER)
    pw = 290
    base = y + 330
    heights = {0: 310, 1: 282, 2: 266}
    colors = {0: "gold", 1: "text2", 2: "#D08A57"}
    for slot, idx in order:
        pos, name, ch, tier, dv, lp, played, you = LADDER[idx]
        x = X0 + slot * (pw + 16)
        h = heights[idx]
        top = base - h
        p.g(f"Pódio {pos}º — {name}")
        p.cham(x, top, pw, h, "violetd" if you else "gCard", "violet" if you else "line", 14, sw=1.5 if you else 1)
        if idx == 0:
            p.add(f'<circle cx="{x + pw / 2}" cy="{top + 70}" r="110" fill="url(#gHalo-{tier_key(tier)})"/>')
        p.portrait(x + pw / 2 - 45, top + 26, ch, 90, colors[idx] if not you else "violet", 12, glow=you)
        p.display(x + pw / 2, base - 110, f"{pos}º", 40, colors[idx], "middle", 700)
        p.text(x + pw / 2, base - 80, name, 17, "text", BODY, 600, "middle")
        p.label(x + pw / 2, base - 58, f"{CHAMPS[ch]['name']} · {played} partidas", 13, "text3", "middle", ls=.8)
        ew, _ = emblem_size(tier, 30)
        lab = f"{tier.upper()} {dv} · {lp} PDL"
        lw_ = len(lab) * 11.2
        p.mini_emblem(x + pw / 2 - lw_ / 2 - 20, base - 27, 30, tier)
        p.display(x + pw / 2 + 18, base - 20, lab, 20, "text", "middle", 700, .6)
        if you:
            p.chip(x + pw - 70, top + 14, "Você", "violet", "#0B0718")
        p.end()
    p.end()

    tx, tw = X0, 3 * pw + 32
    y = 640
    p.section(tx, y, "Classificação ilustrativa", "Perfis inteiramente fictícios.")
    p.g("Tabela")
    hy = y + 40
    for lab, x, a in [("Pos.", tx + 20, None), ("Jogador", tx + 100, None), ("Campeão frequente", tx + 380, None),
                      ("Elo", tx + 560, None), ("PDL", tx + 716, None), ("Partidas", tx + tw - 20, "end")]:
        p.label(x, hy, lab, 13, "text3", a)
    for i, (pos, name, ch, tier, dv, lp, played, you) in enumerate(LADDER):
        yy = hy + 14 + i * 64
        p.g(f"Linha {pos} — {name}")
        p.cham(tx, yy, tw, 56, "violetd" if you else "bg2", "violet" if you else "line", 8, sw=1.5 if you else 1)
        if you:
            p.rect(tx, yy + 8, 4, 40, "violet")
        p.display(tx + 20, yy + 37, f"{pos:02d}", 22, "gold" if pos == 1 else "violet2" if you else "text2", weight=700)
        p.text(tx + 100, yy + 26, name, 16, "text", BODY, 600)
        p.text(tx + 100, yy + 45, "Perfil de demonstração", 13, "text3")
        if you:
            p.chip(tx + 270, yy + 15, "Você", "violet", "#0B0718")
        p.portrait(tx + 380, yy + 12, ch, 32, "line2", 5)
        p.text(tx + 422, yy + 33, CHAMPS[ch]["name"], 16, "text", BODY, 500)
        p.mini_emblem(tx + 556, yy + 28, 34, tier)
        p.display(tx + 598, yy + 34, f"{tier.upper()} {dv}", 16, TIER_STYLE[tier]["text"], weight=700, ls=.4)
        p.display(tx + 716, yy + 35, str(lp), 18, "text", weight=700)
        p.xpbar(tx + 752, yy + 24, 70, lp / 100, 10, 8, f"gFill-{tier_key(tier)}", 2)
        p.display(tx + tw - 20, yy + 35, str(played), 18, "text2", "end", 600)
        p.end()
    p.end()

    sx = X0 + tw + 24
    sw_ = X1 - sx
    rank_panel(p, sx, 270, sw_)
    p.g("Resumo da disputa")
    yy = 772
    p.cham(sx, yy, sw_, 170, "gCard", "line", 12)
    p.label(sx + 24, yy + 36, "Disputa na tabela", 16, "text", weight=700, ls=1.6)
    p.label(sx + 24, yy + 70, "Para Ouro II", 13, "text3")
    p.display(sx + 24, yy + 108, "24 PDL", 34, "gold", weight=700)
    p.label(sx + sw_ / 2 + 10, yy + 70, "À frente do 4º (Ouro III)", 13, "text3")
    p.display(sx + sw_ / 2 + 10, yy + 108, "+35 PDL", 34, "win", weight=700)
    p.text(sx + 24, yy + 146, "Diferenças calculadas sobre a tabela fictícia.", 14, "text3")
    p.end()
    p.g("Como funciona o PDL")
    yy = 962
    p.cham(sx, yy, sw_, 330, "gCard", "line", 12)
    p.label(sx + 24, yy + 36, "Como funciona o PDL", 16, "text", weight=700, ls=1.6)
    rules = ["Ferro a Diamante: divisões IV → I, com 0 a 100 PDL cada.",
             "Chegou a 100 PDL, sobe de divisão (sem série de promoção).",
             "Mestre, Grão-Mestre e Desafiante: sem divisões, PDL sem teto."]
    ry = yy + 66
    for rl in rules:
        p.rect(sx + 24, ry - 9, 6, 6, "violet")
        ry = p.para(sx + 40, ry, rl, 46, 14, 20, fill="text2") + 6
    n = len(TIERS)
    ew = (sw_ - 48) / n
    ey = yy + 222
    for i, (t, _) in enumerate(TIERS):
        on = t == "Ouro"
        if on:
            p.rect(sx + 24 + i * ew, ey + 22, ew - 4, 3, "gold")
        p.mini_emblem(sx + 24 + i * ew + 2, ey, ew - 8, t, None if on else .45)
    p.label(sx + 24, ey + 46, "Ferro", 11, "text3")
    p.label(sx + 24 + 3.5 * ew, ey + 46, "Ouro", 11, "gold", "middle", 700)
    p.label(sx + sw_ - 24, ey + 46, "Desafiante", 11, "text3", "end")
    p.text(sx + 24, yy + 310, "Ranking real exige fonte autorizada e identidade de conta.", 13, "text3")
    p.end()
    footer(p, H - 60)
    return p.svg(W, H, "RiftLab — Ranking (desktop)")


def history_desktop():
    p, H = P(), 1040
    chrome(p, "Histórico", H)
    y = 132
    p.label(X0, y + 22, "Arquivo de partidas · simulação", 15, "violet2", weight=700, ls=2.4)
    p.display(X0, y + 70, "PARTIDAS QUE VIRAM APRENDIZADO.", 44, "text", weight=700)
    p.text(X0, y + 98, "Filtre resultados e abra o contexto. Os registros são fictícios.", 16, "text2")

    y = 262
    lw = 900
    p.g("Resumo do recorte")
    p.cham(X0, y, lw, 96, "gCard", "line", 12)
    p.ring(X0 + 60, y + 48, 28, .6, 7)
    p.display(X0 + 60, y + 55, "60%", 16, "text", "middle", 700)
    p.label(X0 + 106, y + 40, "Recorte fictício", 13, "text3")
    p.display(X0 + 106, y + 70, tspans(("3V ", "win"), ("2D", "loss")), 26, "text", weight=700)
    p.label(X0 + 250, y + 40, "Sequência", 13, "text3")
    for i, m in enumerate(MATCHES):
        win = m[2] == "win"
        p.cham(X0 + 250 + i * 40, y + 50, 32, 30, "wind" if win else "lossd", "win" if win else "loss", 6)
        p.label(X0 + 266 + i * 40, y + 71, "V" if win else "D", 15, "win" if win else "loss", "middle", 700)
    p.label(X0 + 500, y + 40, "KDA do recorte", 13, "text3")
    p.display(X0 + 500, y + 72, "32 / 20 / 40", 24, "text", weight=700)
    p.label(X0 + 720, y + 40, "Tropas médias", 13, "text3")
    p.display(X0 + 720, y + 72, "207", 24, "text", weight=700)
    p.end()

    y = 382
    p.g("Busca e filtros")
    p.cham(X0, y, 340, 48, "bg2", "line2", 8, sw=1.5)
    icon(p, "search", X0 + 16, y + 14, 20, "text2")
    p.text(X0 + 48, y + 30, "Buscar partida ou campeão", 16, "text3")
    fx = X0 + lw
    for lab, on in reversed([("Todas", True), ("Vitórias", False), ("Derrotas", False)]):
        w = len(lab) * 10 + 40
        fx -= w
        if on:
            p.cham(fx, y + 2, w - 6, 44, "gViolet", None, 8)
            p.label(fx + (w - 6) / 2, y + 30, lab, 15, "#0B0718", "middle", 700)
        else:
            p.cham(fx, y + 2, w - 6, 44, None, "line2", 8, sw=1.5)
            p.label(fx + (w - 6) / 2, y + 30, lab, 15, "text", "middle", 600)
    p.label(X0 + 358, y + 30, "5 de 5 partidas", 13, "text3")
    p.end()
    for i, m in enumerate(MATCHES):
        match_card(p, X0, y + 70 + i * 96, lw, m, selected=i == 0)

    dx, dw = X0 + lw + 24, X1 - X0 - lw - 24
    y = 262
    p.g("Painel lateral — contexto aberto")
    p.cham(dx, y, dw, 666, "bg2", "violet", 16, sw=1.5)
    p.backdrop(dx, y, dw, 220, "Kaisa", .7)
    p.rect(dx, y, dw, 220, "url(#gFadeB)")
    p.cham(dx + dw - 50, y + 16, 34, 34, "bg3", "line2", 6)
    icon(p, "close", dx + dw - 43, y + 23, 20, "text")
    p.label(dx + 24, y + 40, "Detalhes da partida", 13, "violet2", weight=700, ls=2)
    p.portrait(dx + 24, y + 70, "Kaisa", 88, "win", 12)
    p.display(dx + 130, y + 100, "VITÓRIA", 26, "win", weight=700, ls=1)
    p.label(dx + 130, y + 126, "Kai'Sa · 31:24", 15, "text", weight=600)
    p.label(dx + 130, y + 148, "DEMO-VISUAL-01", 12, "text3")
    stats = [("K / D / A", "8 / 3 / 7"), ("Tropas", "214"), ("Duração", "31:24")]
    for i, (k, v) in enumerate(stats):
        sx = dx + 24 + i * (dw - 48) / 3
        p.label(sx, y + 200, k, 12, "text3")
        p.display(sx, y + 228, v, 20, "text", weight=700)
    p.line(dx + 24, y + 256, dx + dw - 24, y + 256, "line")
    p.display(dx + 24, y + 296, "REPLAY E CONTEXTO", 22, "text", weight=700, ls=.6)
    ey = p.para(dx + 24, y + 328, "Vitória no cenário fictício. Os dados de partida concluída permitem reconstruir compras e limites temporais.", 44, 15, 23, fill="text2")
    p.cham(dx + 24, ey + 4, dw - 48, 130, "goldd", "gold", 10)
    icon(p, "warn", dx + 40, ey + 20, 20, "gold")
    p.label(dx + 70, ey + 36, "Sem arquivo de replay", 14, "gold", weight=700)
    p.para(dx + 40, ey + 66, "No produto, o acesso dependeria de uma fonte permitida e da disponibilidade do arquivo; a revisão de compras usa apenas dados pós-partida.", 46, 13, 19, fill="text")
    p.button(dx + 24, y + 666 - 72, dw - 48, "Entendi", True, 48)
    p.end()
    footer(p, H - 60)
    return p.svg(W, H, "RiftLab — Histórico (desktop)")


# ------------------------------------------------------------ mobile
MW, ML, MR = 390, 16, 374


def chrome_m(p, active, h):
    p.rect(0, 0, MW, h, "n0", name="Fundo")
    p.g("Barra superior")
    logo(p, ML + 14, 28, 12, 18)
    p.rect(MR - 88, 16, 40, 24, None, "nline2", 1, rx=4)
    p.ui(MR - 68, 32, "BR1", 12, "ntext2", 600, "middle")
    p.portrait(MR - 36, 12, "Kaisa", 32, "nline2", 6)
    p.line(0, 56, MW, 56, "nline")
    p.end()
    p.g("Aviso de demonstração")
    p.rect(0, 57, MW, 28, "n1")
    icon(p, "info", ML, 63, 16, "ntext3")
    p.ui(ML + 22, 75, tspans(("Modo demonstração", C["ntext2"]), (" · dados fictícios", C["ntext3"])), 12, "ntext2", 500)
    p.line(0, 85, MW, 85, "nline")
    p.end()
    p.g("Navegação inferior (fixa)")
    p.rect(0, h - 68, MW, 68, "n0")
    p.line(0, h - 68, MW, h - 68, "nline")
    tw = MW / 4
    for i, r in enumerate(ROUTES):
        on = r == active
        cx = i * tw + tw / 2
        if on:
            p.rect(i * tw + 24, h - 68, tw - 48, 2, "accent")
        icon(p, r, cx - 10, h - 54, 20, "accent2" if on else "ntext3")
        p.ui(cx, h - 16, r, 12, "ntext" if on else "ntext3", 600 if on else 500, "middle")
    p.end()


def msec(p, y, title, right=None):
    p.rect(ML, y - 13, 3, 16, "violet")
    p.label(ML + 12, y, title, 15, "text", weight=700, ls=1.4)
    if right:
        p.label(MR, y, right + "  ›", 13, "violet2", "end", 700)
    return y + 16


def mmatch(p, y, m):
    mid, champ, res, when, kda, cs, length = m
    win = res == "win"
    w = MR - ML
    p.g(f"Partida {mid}")
    p.cham(ML, y, w, 76, "bg2", "line", 8)
    p.add(f'<polygon points="{chamfer_pts(ML, y, w, 76, 8)}" fill="url(#{"gWin" if win else "gLoss"})"/>')
    p.rect(ML, y + 8, 3, 60, "win" if win else "loss")
    p.portrait(ML + 14, y + 12, champ, 52, "win" if win else "loss", 7)
    p.display(ML + 80, y + 32, "VITÓRIA" if win else "DERROTA", 15, "win" if win else "loss", weight=700, ls=.8)
    p.text(ML + 80, y + 52, f"{CHAMPS[champ]['name']} · {when}", 13, "text2")
    p.label(ML + 80, y + 68, f"{length} · {cs} tropas", 11, "text3", ls=.6)
    p.display(MR - 14, y + 34, kda, 17, "text", "end", 700)
    p.label(MR - 14, y + 58, "Contexto  ›", 12, "violet2", "end", 700)
    p.end()


def panel_mobile():
    b = P()
    w = MR - ML
    b.g("Cabeçalho da página")
    b.ui(ML, 112, "Painel · últimas 20 partidas ilustrativas", 12, "ntext3", 500)
    b.display(ML, 142, "Entenda a partida.", 22, "ntext", weight=600)
    b.display(ML, 168, "Evolua a decisão.", 22, "ntext", weight=600)
    b.ui(ML, 194, "Olhe contexto, reconheça padrões e estude", 14, "ntext2")
    b.ui(ML, 214, "compras sem prometer uma resposta universal.", 14, "ntext2")
    b.end()

    y = 234
    b.g("Desempenho")
    b.surface(ML, y, w, 180)
    half = w / 2
    for j, (lab, val, foot) in enumerate([("Taxa de vitória", "58%", "Últimas 20 ilustrativas"), ("KDA médio", "3,4", "Relação simplificada")]):
        x = ML + 18 + j * half
        b.g(f"Métrica — {lab}")
        b.ui(x, y + 30, lab, 13, "ntext2", 500)
        icon(b, "info", x + half - 44, y + 18, 16, "ntext3")
        b.ui(x - 1, y + 70, val, 32, "ntext", 600, tab=True)
        b.ui(x, y + 90, foot, 12, "ntext3")
        if j == 0:
            b.rect(x, y + 102, half - 36, 4, "nline", rx=2)
            b.rect(x, y + 102, (half - 36) * .58, 4, "winN", rx=2)
        b.end()
    b.line(ML + half, y + 16, ML + half, y + 108, "nline")
    b.line(ML + 14, y + 122, MR - 14, y + 122, "nline")
    secondary_metric(b, ML + 18, y + 146, "Decisões revistas", "18", "7 com reflexão")
    secondary_metric(b, ML + 18, y + 168, "Campeões no recorte", "3", "")
    b.end()

    y += 180 + 36
    section_title(b, ML, y, "Partidas recentes", size=15)
    y = match_rows(b, ML, y + 16, w, MATCHES[:5], compact=True,
                   more=("5 de 20 ilustrativas", "Ver as 20 partidas"))
    y += 20
    rank_panel(b, ML, y, w, surface="n1")
    y += 482 + 36
    section_title(b, ML, y, "Campeões em foco", "Guias", MR, 15)
    y = champion_rows(b, ML, y + 16, w, compact=True)

    H = y + 24 + 68
    p = P()
    chrome_m(p, "Painel", H)
    p.out += b.out
    return p.svg(MW, H, "RiftLab — Painel (mobile)")


def champions_mobile():
    p, H = P(), 1260
    c = CHAMPS["Kaisa"]
    chrome_m(p, "Campeões", H)
    y = 104
    p.label(ML, y + 16, "Biblioteca de campeões", 12, "violet2", weight=700, ls=2)
    p.display(ML, y + 52, "ESTUDE UM CAMPEÃO.", 28, "text", weight=700)
    tw = (MR - ML - 16) / 3
    for i, ch in enumerate(CHAMPS):
        on = i == 0
        x = ML + i * (tw + 8)
        p.cham(x, y + 70, tw, 104, "violetd" if on else "bg2", "violet" if on else "line", 8, sw=1.5 if on else 1)
        p.portrait(x + tw / 2 - 28, y + 82, ch, 56, "violet" if on else "line2", 8, glow=on)
        p.display(x + tw / 2, y + 162, CHAMPS[ch]["name"].upper(), 13, "text", "middle", 700)
    y = 296
    p.g("Destaque do campeão")
    p.cham(ML, y, MR - ML, 330, "bg2", "line", 14)
    p.backdrop(ML, y, MR - ML, 200, "Kaisa", .9)
    p.rect(ML, y, MR - ML, 200, "url(#gFadeB)")
    p.portrait(ML + 20, y + 24, "Kaisa", 110, "violet", 14, glow=True)
    p.label(ML + 146, y + 46, "Guia · 16.17.1", 12, "violet2", weight=700, ls=1.6)
    p.display(ML + 144, y + 96, "KAI'SA", 46, "text", weight=700, ls=1)
    p.label(ML + 146, y + 124, c["subtitle"], 14, "violet2", weight=700)
    p.para(ML + 20, y + 170, c["note"], 44, 15, 22, fill="text2")
    cx = p.chip(ML + 20, y + 226, "Atirador", "bg3", "text", "line2", 28, 12) + 8
    p.chip(cx, y + 226, "Patch 16.17", "bg3", "text2", "line2", 28, 12)
    p.cham(ML + 20, y + 266, MR - ML - 40, 40, "goldd", "gold", 6)
    icon(p, "warn", ML + 32, y + 276, 20, "gold")
    p.label(ML + 60, y + 291, "Sem ranking de itens validado", 13, "gold", weight=700, ls=1)
    p.end()
    y = msec(p, 660, "Habilidades em contexto")
    tw = (MR - ML - 12) / 2
    for i, ab in enumerate(c["abilities"]):
        x = ML + (i % 2) * (tw + 12)
        yy = y + 8 + (i // 2) * 96
        p.cham(x, yy, tw, 84, "gCard", "line", 10)
        p.cham(x + 12, yy + 14, 38, 38, "violetd" if i == 3 else "bg4", "violet" if i == 3 else "line2", 6)
        p.display(x + 31, yy + 40, "QWER"[i], 19, "violet2" if i == 3 else "text", "middle", 700)
        p.display(x + 60, yy + 38, ab.upper(), 14, "text", weight=700)
        p.text(x + 12, yy + 72, "Muda a próxima decisão", 12, "text3")
    y = msec(p, y + 220, "Trajeto de itens")
    for i, step in enumerate(c["build"]):
        yy = y + 8 + i * 76
        p.cham(ML, yy, MR - ML, 66, "gCard", "violet" if i == 0 else "line", 10, sw=1.5 if i == 0 else 1)
        p.cham(ML + 12, yy + 11, 44, 44, "bg4", "line2", 6)
        p.display(ML + 34, yy + 41, str(i + 1), 20, "violet2" if i == 0 else "text3", "middle", 700)
        p.label(ML + 70, yy + 26, f"Etapa {i + 1}", 11, "violet2" if i == 0 else "text3", weight=700)
        p.display(ML + 70, yy + 48, step.upper(), 15, "text", weight=700)
    p.text(ML, y + 250, "Exemplo pedagógico, não uma build recomendada.", 13, "text3")
    return p.svg(MW, H, "RiftLab — Campeões (mobile)")


def ranking_mobile():
    p, H = P(), 1352
    chrome_m(p, "Minha posição", H)
    y = 104
    p.label(ML, y + 16, "Escada competitiva · simulação", 12, "violet2", weight=700, ls=2)
    p.display(ML, y + 52, "VEJA SUA POSIÇÃO.", 28, "text", weight=700)
    p.cham(ML, y + 72, 96, 40, "gViolet", None, 8)
    p.label(ML + 48, y + 97, "Geral", 14, "#0B0718", "middle", 700)
    p.cham(ML + 104, y + 72, 160, 40, None, "line2", 8, sw=1.5)
    p.label(ML + 184, y + 97, "Amigos fictícios", 14, "text", "middle", 600)
    rank_panel(p, ML, 236, MR - ML)
    y = msec(p, 756, "Classificação ilustrativa")
    for i, (pos, name, ch, tier, dv, lp, played, you) in enumerate(LADDER):
        yy = y + 8 + i * 64
        p.g(f"Linha {pos} — {name}")
        p.cham(ML, yy, MR - ML, 56, "violetd" if you else "bg2", "violet" if you else "line", 8, sw=1.5 if you else 1)
        p.display(ML + 14, yy + 36, f"{pos:02d}", 20, "gold" if pos == 1 else "violet2" if you else "text2", weight=700)
        p.portrait(ML + 52, yy + 10, ch, 36, "line2", 6)
        p.text(ML + 100, yy + 26, name, 15, "text", BODY, 600)
        p.label(ML + 100, yy + 44, ("Você · " if you else "") + f"{CHAMPS[ch]['name']} · {played} partidas", 11,
                "violet2" if you else "text3", weight=700 if you else 600, ls=.6)
        p.mini_emblem(MR - 110, yy + 28, 30, tier)
        p.display(MR - 12, yy + 27, f"{lp} PDL", 16, "text", "end", 700)
        p.label(MR - 12, yy + 45, f"{tier} {dv}", 11, TIER_STYLE[tier]["text"], "end", 700, .6)
        p.end()
    p.para(ML, y + 420, "Ranking real exige fonte autorizada, identidade de conta e privacidade; não faz parte do backend atual.", 50, 12, 18, fill="text3")
    return p.svg(MW, H, "RiftLab — Ranking (mobile)")


def history_mobile():
    p, H = P(), 1360
    chrome_m(p, "Histórico", H)
    y = 104
    p.label(ML, y + 16, "Arquivo de partidas · simulação", 12, "violet2", weight=700, ls=2)
    p.display(ML, y + 52, "PARTIDAS QUE VIRAM", 28, "text", weight=700)
    p.display(ML, y + 86, "APRENDIZADO.", 28, "violet2", weight=700)
    p.cham(ML, y + 106, MR - ML, 46, "bg2", "line2", 8, sw=1.5)
    icon(p, "search", ML + 14, y + 119, 20, "text2")
    p.text(ML + 44, y + 135, "Buscar partida ou campeão", 15, "text3")
    x = ML
    for lab, on in [("Todas", True), ("Vitórias", False), ("Derrotas", False)]:
        w = len(lab) * 9 + 34
        p.cham(x, y + 162, w, 38, "gViolet" if on else None, None if on else "line2", 7, sw=1.5)
        p.label(x + w / 2, y + 186, lab, 14, "#0B0718" if on else "text", "middle", 700 if on else 600)
        x += w + 8
    p.g("Resumo")
    p.label(ML, y + 232, "Recorte fictício", 12, "text3")
    p.display(ML + 110, y + 234, tspans(("3V ", "win"), ("2D", "loss")), 18, "text", weight=700)
    for i, m in enumerate(MATCHES):
        win = m[2] == "win"
        p.cham(MR - 5 * 30 + i * 30, y + 214, 24, 26, "wind" if win else "lossd", "win" if win else "loss", 5)
        p.label(MR - 5 * 30 + i * 30 + 12, y + 232, "V" if win else "D", 12, "win" if win else "loss", "middle", 700)
    p.end()
    for i, m in enumerate(MATCHES):
        mmatch(p, y + 254 + i * 86, m)
    y = y + 254 + 5 * 86 + 16
    p.g("Folha de contexto (estado aberto)")
    p.label(ML, y + 4, "Estado: folha aberta", 11, "violet2", weight=700)
    p.cham(ML, y + 14, MR - ML, 200, "bg2", "violet", 12, sw=1.5)
    p.label(ML + 18, y + 44, "Detalhes da partida", 12, "violet2", weight=700, ls=1.6)
    p.display(ML + 18, y + 74, "REPLAY E CONTEXTO", 20, "text", weight=700)
    p.para(ML + 18, y + 100, "DEMO-VISUAL-01: Kai'Sa, 31:24, vitória no cenário fictício. Sem arquivo de replay neste protótipo.", 46, 13, 19, fill="text2")
    p.button(ML + 18, y + 150, 120, "Entendi", True, 42, 14)
    p.end()
    return p.svg(MW, H, "RiftLab — Histórico (mobile)")


def tiers_board():
    """Mesmo componente de card em vários elos: cores, bordas e efeitos vêm de TIER_STYLE."""
    p, H = P(), 1260
    p.rect(0, 0, W, H, "bg", name="Fundo")
    p.rect(0, 0, W, 420, "url(#pGrid)", opacity=.7)
    p.label(X0, 64, "Sistema de elos · card de posição", 15, "violet2", weight=700, ls=2.4)
    p.display(X0, 110, "UM COMPONENTE, TODOS OS ELOS.", 40, "text", weight=700)
    p.text(X0, 140, "Placas de divisão, barra de PDL, halo, colchetes e título derivam da paleta do elo. Valores fictícios.", 16, "text2")
    cards = [("Ferro", "II", 12, 18), ("Bronze", "I", 90, 21), ("Prata", "IV", 5, 20), ("Ouro", "III", 76, 22),
             ("Platina", "II", 40, 19), ("Esmeralda", "I", 63, 18), ("Diamante", "IV", 30, 17), ("Mestre", None, 214, 16)]
    cw = (X1 - X0 - 3 * 20) / 4
    for i, (t, d, lp, g) in enumerate(cards):
        rank_panel(p, X0 + (i % 4) * (cw + 20), 180 + (i // 4) * 506, cw, 482, t, d, lp, g)
    return p.svg(W, H, "RiftLab — Sistema de elos")


SCREENS = [
    ("C-01-painel-desktop.svg", panel_desktop), ("C-02-campeoes-desktop.svg", champions_desktop),
    ("C-03-ranking-desktop.svg", ranking_desktop), ("C-04-historico-desktop.svg", history_desktop),
    ("C-01-painel-mobile.svg", panel_mobile), ("C-02-campeoes-mobile.svg", champions_mobile),
    ("C-03-ranking-mobile.svg", ranking_mobile), ("C-04-historico-mobile.svg", history_mobile),
    ("C-05-elos-desktop.svg", tiers_board),
]

if __name__ == "__main__":
    for name, fn in SCREENS:
        (HERE / name).write_text(fn(), encoding="utf-8")
        print(name)
