"""Gera o diagrama de casos de uso em SVG, com layout de leitura para APS."""

from html import escape
from pathlib import Path

OUT = Path(__file__).with_name("diagrama-casos-de-uso.svg")
W, H = 1600, 1100
parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
         f'<rect width="{W}" height="{H}" fill="#F2F6F7"/>',
         f'<rect x="42" y="38" width="1516" height="{H - 76}" rx="25" fill="white" stroke="#D8E4E8"/>',
         '<rect x="42" y="38" width="1516" height="112" rx="25" fill="#102B3A"/>',
         '<rect x="42" y="120" width="1516" height="30" fill="#102B3A"/>']


def t(x,y,s,size=16,color="#193342",weight=400,anchor="middle"):
    parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(s)}</text>')


def path(d,color="#8CAAB2",width=2):
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')


def actor(x,y,name,sub):
    parts.append(f'<circle cx="{x}" cy="{y-31}" r="15" fill="none" stroke="#087F76" stroke-width="3"/>')
    path(f'M{x} {y-16} V{y+30} M{x-28} {y+1} H{x+28} M{x} {y+30} L{x-27} {y+60} M{x} {y+30} L{x+27} {y+60}',"#087F76",3)
    t(x,y+88,name,17,"#193342",700)
    t(x,y+108,sub,12,"#617885")


def usecase(x,y,code,label,detail,w=350,ry=47):
    parts.append(f'<ellipse cx="{x}" cy="{y}" rx="{w/2}" ry="{ry}" fill="#EAF7F4" stroke="#087F76" stroke-width="2"/>')
    t(x,y-19,code,11,"#087F76",800)
    t(x,y+4,label,18,"#193342",700)
    t(x,y+26,detail,12,"#617885")


t(85,90,"RiftLab · casos de uso",29,"white",800,"start")
t(85,123,"Visão final do produto · Painel, Campeões, Minha posição e Histórico · estudo de compras em partidas concluídas",14,"#BDD5D9",400,"start")
parts.append('<rect x="285" y="177" width="1030" height="808" rx="20" fill="#FCFDFD" stroke="#AEC9CE" stroke-width="2"/>')
t(800,208,"LIMITE DO SISTEMA · RIFTLAB",12,"#617885",800)

# Coluna esquerda: operação; coluna direita: casos do jogador e do revisor.
L, R = 520, 1090
LW, RW = 300, 350
right = [(250, "UC07 · RF18–20", "Consultar painel e histórico", "métricas, partidas, busca e filtros"),
         (360, "UC02 · RF04–08", "Revisar compra", "aberta pelo Histórico; contexto e alternativas"),
         (470, "UC03 · RF09", "Informar objetivo", "formulário ou texto confirmado"),
         (580, "UC06 · RF13–16", "Estudar decisão", "escolher, revelar e transferir"),
         (690, "UC09 · RF22", "Consultar minha posição", "elo oficial só da conta própria"),
         (800, "UC08 · RF21", "Estudar campeão", "retrato, habilidades, itens observados"),
         (915, "UC04 · RF10", "Avaliar e exportar", "julgamento cego ou abstenção")]
left = [(520, "UC01 · RF01–03, RF17", "Importar partida ou demo", "concluída; acompanhar processamento"),
        (270, "UC05 · RF11–12", "Operar e remover dados", "experimentos, manifesto, expurgo")]

# Associações UML (linhas sem seta), desenhadas antes dos nós.
op, riot, player, reviewer = (144, 398), (144, 748), (1456, 520), (1456, 912)
lx0, rx0 = L - LW / 2, R + RW / 2
path(f"M{op[0]} {op[1]} L{lx0} 270")
path(f"M{op[0]} {op[1]} L{lx0} 520")
path(f"M{riot[0]} {riot[1]} L{lx0 + 14} 548")
path(f"M{riot[0]} {riot[1]} L{R - RW / 2} 690")
path(f"M{riot[0]} {riot[1]} L{R - RW / 2} 800")
for y, *_ in right[:-1]:
    path(f"M{player[0]} {player[1]} L{rx0} {y}")
path(f"M{reviewer[0]} {reviewer[1]} L{rx0} 915")

actor(116, 395, "Operador local", "configuração e dados")
actor(116, 745, "Serviço de dados da Riot", "Match-V5 · League-V4 · Data Dragon")
actor(1484, 517, "Jogador", "painel, histórico e estudo")
actor(1484, 909, "Revisor", "avaliação cega")

for y, code, label, detail in left:
    usecase(L, y, code, label, detail, LW)
for y, code, label, detail in right:
    usecase(R, y, code, label, detail, RW)

t(800, 1020, "Escopo: análise pós-partida · demonstração sintética offline · sem recomendação ao vivo · sem ranking entre jogadores", 13, "#617885")
parts.append('</svg>')
OUT.write_text("\n".join(parts),encoding="utf-8")
print(OUT)
