"""Gera dez telas SVG ilustrativas da visão final do RiftLab, sem dados pessoais."""
from base64 import b64encode
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "telas"
OUT.mkdir(exist_ok=True)
C = dict(ink="#182B33", deep="#183F48", teal="#126E66", paper="#F2F5F4", white="#FFFFFF", muted="#52666D", line="#CDD9D6", pale="#E7EEEB", blue="#E7F1F3", amber="#9B641F", warm="#FCF0D7", red="#982F35")


def col(value): return C.get(value, value)
def box(x,y,w,h,fill="white",r=0,stroke=None,sw=1):
    s=f' stroke="{col(stroke)}" stroke-width="{sw}"' if stroke else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{col(fill)}"{s}/>'
def text(x,y,value,size=16,color="ink",weight=400,anchor=None,family="Inter,Arial,sans-serif"):
    a=f' text-anchor="{anchor}"' if anchor else ""
    return f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{col(color)}"{a}>{escape(str(value))}</text>'
def rule(x1,y1,x2,y2,color="line",width=1): return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col(color)}" stroke-width="{width}"/>'
def dot(x,y,r=4,color="teal"): return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{col(color)}"/>'
def chip(x,y,value,fill="pale",color="deep",w=150): return box(x,y,w,31,fill,4)+text(x+12,y+21,value,12,color,650)
def button(x,y,w,value,primary=True):
    return box(x,y,w,46,"teal" if primary else "white",6,None if primary else "teal")+text(x+w/2,y+29,value,14,"white" if primary else "teal",700,"middle")
def portrait(x,y,name,size=54):
    encoded=b64encode((ROOT/"assets"/f"{name}.png").read_bytes()).decode()
    return f'<image x="{x}" y="{y}" width="{size}" height="{size}" href="data:image/png;base64,{encoded}"/>'+box(x,y,size,size,"none",2,"#000000",.3)
def start(w=1440,h=900): return [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',box(0,0,w,h,"paper")]
def desktop(page,title,description):
    p=start()
    p += [box(0,0,1440,86,"white"),rule(0,86,1440,86),box(75,23,40,40,"deep",7),text(95,51,"R",23,"white",800,"middle"),text(129,51,"RiftLab",22,"ink",800),text(302,51,"Partidas",15,"deep",650),text(407,51,"Estudo",15,"muted",500),text(500,51,"Avaliação",15,"muted",500),text(1287,51,"Após a partida",13,"muted",600)]
    p += [text(76,133,page,14,"teal",650),text(76,198,title,44,"ink",760),text(77,230,description,16,"muted")]
    p += [rule(76,856,1364,856),text(76,880,"Conceito de interface • dados ilustrativos",12,"muted"),text(1364,880,"Ativos: Riot Data Dragon 16.17.1",12,"muted",400,"end")]
    return p
def panel(x,y,w,h,fill="white",stroke="line",r=10): return box(x,y,w,h,fill,r,stroke)
def save(name,p): (OUT/name).write_text("\n".join(p+["</svg>"]),encoding="utf-8")

def home():
    p=desktop("Início / revisão","Volte ao instante da decisão.","Analise a compra com o inventário e as informações disponíveis naquele momento.")
    p += [panel(76,275,804,284),box(76,275,804,5,"deep"),text(108,324,"Abrir uma partida concluída",24,"ink",700),text(108,357,"Cole o ID de uma partida BR1. Nenhum jogo ao vivo é acompanhado.",15,"muted"),text(108,415,"ID da partida",13,"ink",650),box(108,429,506,49,"paper",5,"line"),text(124,460,"BR1_1234567890",15,"muted"),button(628,429,213,"Analisar partida"),text(108,522,"A preparação informa quando não há catálogo ou dados suficientes.",13,"muted")]
    p += [panel(899,275,465,284,"pale","pale"),text(928,325,"Explorar demonstração",24,"ink",700),text(928,359,"Percorra a revisão sem chave Riot.",15,"muted"),text(928,384,"Os dados são sintéticos e identificados.",15,"muted"),button(928,439,236,"Abrir demonstração",False)]
    p += [text(76,624,"Partidas importadas",25,"ink",700),text(1364,624,"Ver todas",14,"teal",650,"end"),rule(76,645,1364,645)]
    for i,(name,patch,stage) in enumerate([("Demonstração", "DEMO-1", "Pronta para revisão"),("Partida ilustrativa A", "16.17", "Preparada")]):
        y=650+i*78
        p += [text(79,y+31,name,17,"ink",650),text(470,y+31,"Patch "+patch,14,"muted"),text(865,y+31,stage,14,"teal",650),text(1350,y+31,"Abrir",14,"teal",650,"end"),rule(76,y+75,1364,y+75)]
    save("01-inicio.svg",p)

def match():
    p=desktop("Partida / decisões","Escolha um momento para revisar.","A ordem dos eventos e o patch ficam visíveis durante toda a análise.")
    p += [panel(76,270,1288,110,"deep","deep"),text(108,311,"Partida ilustrativa",22,"white",700),text(108,346,"Concluída • 16.17 • Ranqueada solo/duo • Mapa 11",15,"#DAE9E7"),chip(1167,300,"Catálogo fixado","white","deep",165)]
    p += [text(76,424,"Perspectiva",21,"ink",700),text(535,424,"Linha de compras",21,"ink",700)]
    for i,(name,champ,role) in enumerate([("Kai'Sa","Kaisa","Atirador"),("Caitlyn","Caitlyn","Atirador"),("Ezreal","Ezreal","Atirador")]):
        y=455+i*88;p += [panel(76,y,402,73,"pale" if i==0 else "white","teal" if i==0 else "line",7),portrait(88,y+10,champ,52),text(154,y+31,name,17,"ink",700),text(154,y+55,role,12,"muted")]
    p += [rule(567,478,567,784,"line",3)]
    for i,(minute,title,detail) in enumerate([("04:12","Compra 03","Inventário reconstruído"),("09:44","Compra 07","Antes do item alvo"),("15:03","Compra 12","Ouro aproximado por quadro anterior"),("22:17","Compra 18","Qualidade temporal disponível")]):
        y=462+i*85;p += [dot(567,y+17,7),text(595,y+23,minute,20,"deep",700,family="Liberation Sans Narrow,Arial,sans-serif"),text(739,y+22,title,17,"ink",700),text(739,y+48,detail,13,"muted"),text(1330,y+29,"Abrir",13,"teal",650,"end"),rule(595,y+70,1364,y+70)]
    save("02-partida.svg",p)

def context():
    p=desktop("Decisão / contexto","O que você sabia antes da compra?","O item escolhido continua oculto. A qualidade dos dados aparece antes da sua resposta.")
    p += [panel(76,277,808,520),text(108,322,"15:03",34,"deep",750,family="Liberation Sans Narrow,Arial,sans-serif"),text(219,320,"Kai'Sa / compra 12",20,"ink",700),portrait(796,291,"Kaisa",56),rule(108,348,852,348),text(108,390,"Inventário anterior",16,"ink",700)]
    for i,label in enumerate(["Botas","Componente A","Componente B","Espaço vazio","Espaço vazio","Espaço vazio"]):
        x=108+(i%3)*235;y=410+(i//3)*83;p += [box(x,y,219,62,"paper",6,"line"),text(x+14,y+38,label,14,"ink" if i<3 else "muted")]
    p += [text(108,620,"Objetivo para esta revisão",17,"ink",700),box(108,644,744,54,"pale",5,"teal"),text(124,678,"Sobreviver à próxima luta",15,"ink",650),text(108,744,"A interpretação precisa de confirmação antes da comparação.",13,"muted")]
    p += [panel(904,277,460,520,"pale","pale"),text(932,321,"Qualidade pré-decisão",21,"ink",700),chip(932,353,"Ouro aproximado","white","deep",170),text(932,414,"Quadro anterior: há 12 s",15,"ink",650),text(932,446,"Fonte: timeline concluída",14,"muted"),rule(932,476,1332,476),text(932,515,"Limite conhecido",16,"ink",700),text(932,544,"Não há ouro exato no instante da compra.",14,"muted"),text(932,571,"Uma falta de dados pode suspender a análise.",14,"muted"),button(932,704,241,"Confirmar objetivo")]
    save("03-contexto.svg",p)

def study():
    p=desktop("Estudo / escolha","Escolha antes de revelar.","As alternativas são apresentadas em ordem neutra; a resposta do modelo fica escondida.")
    p += [panel(76,270,852,540),text(110,320,"Qual item atende ao objetivo definido?",24,"ink",700),text(110,351,"Sobreviver à próxima luta • orçamento aproximado, não garantido",15,"muted")]
    for i,(name,detail) in enumerate([("Alternativa A","Defesa física e vida"),("Alternativa B","Resistência mágica e utilidade"),("Alternativa C","Mobilidade e proteção")]):
        y=386+i*105;p += [panel(110,y,784,89,"pale" if i==0 else "white","teal" if i==0 else "line",6),box(126,y+22,43,43,"deep" if i==0 else "paper",5),text(147,y+50,chr(65+i),19,"white" if i==0 else "deep",700,"middle"),text(191,y+35,name,17,"ink",700),text(191,y+62,detail,13,"muted")]
    p += [button(110,724,203,"Registrar escolha")]
    p += [panel(952,270,412,540,"pale","pale"),text(982,317,"Seu raciocínio",22,"ink",700),text(982,352,"Por que esta alternativa parece melhor?",14,"muted"),box(982,375,352,109,"white",6,"line"),text(998,403,"Escreva sua justificativa...",13,"muted"),rule(982,531,1334,531),text(982,569,"Precisa de uma pista?",17,"ink",700),text(982,598,"Abra um nível de cada vez.",13,"muted"),button(982,628,167,"Pista 1",False),text(982,774,"Também é possível responder “não sei”.",12,"muted")]
    save("04-estudo.svg",p)

def comparison():
    p=desktop("Comparação / revelação","Agora compare os caminhos.","Sua escolha permanece visível ao lado das evidências e dos limites do modelo.")
    p += [panel(76,270,1288,106,"deep","deep"),text(108,315,"Sua escolha: alternativa A",22,"white",700),text(108,348,"Objetivo: sobreviver à próxima luta",14,"#DAE9E7"),chip(1107,306,"Revelado por escolha","white","deep",222)]
    for i,(label,head,body) in enumerate([("Sua escolha","Alternativa A","Defesa física; atende parte do objetivo."),("Comparação","Alternativa B","Mais resistência mágica neste cenário."),("Sem conclusão","Alternativa C","Evidência insuficiente para ordenar.")]):
        x=76+i*435;p += [panel(x,405,418,277,"white","line",9),box(x,405,418,5,"teal" if i==0 else "deep" if i==1 else "amber"),text(x+24,452,label,13,"muted",650),text(x+24,498,head,23,"ink",700),text(x+24,538,body,14,"muted"),text(x+24,641,"Ver evidências",14,"teal",650)]
    p += [text(76,735,"O modelo pode abster-se. Frequência observada não prova a melhor compra.",15,"muted"),button(76,768,217,"Estudar outro momento")]
    save("05-comparacao.svg",p)

def transfer():
    p=desktop("Transferência / prática","Teste o raciocínio em outro caso.","Um novo contexto mostra se a explicação continua útil, sem transformar o estudo em gabarito.")
    p += [panel(76,275,813,512),text(108,321,"Novo cenário ilustrativo",21,"ink",700),rule(108,341,857,341),text(108,387,"Você tem pouco tempo antes da próxima luta.",18,"ink",650),text(108,420,"O inventário e o adversário mudaram.",15,"muted"),text(108,484,"O que você priorizaria agora?",19,"ink",700)]
    for i,label in enumerate(["Resistência a dano mágico","Mais dano imediato","Guardar ouro para o próximo retorno"]):
        y=513+i*68;p += [box(108,y,749,54,"pale" if i==0 else "white",5,"teal" if i==0 else "line"),text(128,y+34,label,15,"ink")]
    p += [panel(911,275,453,512,"pale","pale"),text(941,321,"O que mudou",21,"ink",700),text(941,380,"Adversários",13,"muted"),text(941,407,"Mais ameaça mágica",16,"ink",650),rule(941,431,1334,431),text(941,470,"Inventário",13,"muted"),text(941,497,"Uma vaga disponível",16,"ink",650),rule(941,521,1334,521),text(941,560,"Qualidade",13,"muted"),text(941,587,"Ouro aproximado",16,"ink",650),button(941,716,214,"Registrar raciocínio")]
    save("06-transferencia.svg",p)

def evaluation():
    p=desktop("Avaliação / resultados","O que o sistema consegue sustentar?","Cobertura, exclusões e abstenção acompanham qualquer resultado agregado.")
    p += [panel(76,274,1288,110,"deep","deep"),text(108,319,"Avaliação por patch, campeão e função",22,"white",700),text(108,350,"Sem inferência causal a partir de uma amostra de conveniência.",14,"#DAE9E7")]
    for i,(title,body) in enumerate([("Cobertura","Pontos elegíveis / partidas examinadas"),("Comparação","Baseline e modelo no mesmo split"),("Abstenção","Casos sem suporte visíveis")]):
        x=76+i*435;p += [panel(x,414,418,207),text(x+24,458,title,22,"ink",700),rule(x+24,475,x+394,475),text(x+24,517,body,14,"muted"),text(x+24,582,"Ver metodologia",14,"teal",650)]
    p += [panel(76,650,1288,146,"pale","pale"),text(108,693,"Exportação rastreável",20,"ink",700),text(108,725,"Inclui versão de catálogo, regras, split e máscaras de ausência; não inclui IDs pessoais.",14,"muted"),button(1132,697,200,"Exportar relatório",False)]
    save("07-avaliacao.svg",p)

def operation():
    p=desktop("Operação / controle","Dado privado tem prazo e destino.","A operação distingue dados brutos, análises derivadas e registros de estudo.")
    for i,(title,body,detail) in enumerate([("01  Coleta permitida","Lista de contas autorizadas","Limites de requisição e parada segura"),("02  Processamento","Jobs retomáveis e catálogo fixado","Sem partida ao vivo ou URL arbitrária"),("03  Retenção","Expurgo com conferência","Evidência do que foi removido")]):
        y=281+i*161;p += [panel(76,y,1288,135),text(108,y+44,title,22,"ink",700),text(108,y+78,body,15,"muted"),text(1302,y+75,detail,14,"deep",650,"end"),rule(108,y+104,1332,y+104)]
    p += [chip(76,799,"Ambiente local","pale","deep",151),chip(242,799,"Acesso restrito","pale","deep",164)]
    save("08-operacao.svg",p)

def mobile_base(page,title,sub):
    p=start(390,844);p += [box(0,0,390,70,"white"),rule(0,70,390,70),box(20,19,32,32,"deep",5),text(36,42,"R",17,"white",800,"middle"),text(62,41,"RiftLab",18,"ink",800),text(370,41,"Partidas",13,"teal",650,"end"),text(20,109,page,12,"teal",650),text(20,155,title,28,"ink",750),text(20,179,sub,12,"muted")];return p

def mobile_home():
    p=mobile_base("Início / revisão","Volte à decisão.","Acompanhe uma partida concluída.")
    p += [panel(20,211,350,247),box(20,211,350,4,"deep"),text(38,250,"Analisar partida",19,"ink",700),text(38,277,"Insira um ID BR1.",13,"muted"),text(38,316,"ID da partida",12,"ink",650),box(38,328,314,44,"paper",5,"line"),text(50,356,"BR1_1234567890",13,"muted"),button(38,390,314,"Analisar partida")]
    p += [panel(20,474,350,144,"pale","pale"),text(38,514,"Demonstração",19,"ink",700),text(38,539,"Sem chave Riot e sem dados reais.",12,"muted"),button(38,560,218,"Abrir demonstração",False)]
    p += [text(20,667,"Partidas importadas",20,"ink",700),rule(20,685,370,685),text(20,718,"Demonstração",14,"ink",650),text(20,741,"Patch DEMO-1",12,"muted"),text(370,727,"Abrir",13,"teal",650,"end"),rule(20,759,370,759),text(20,808,"Conceito / dados ilustrativos",11,"muted")]
    save("09-mobile-inicio.svg",p)

def mobile_study():
    p=mobile_base("Estudo / escolha","Escolha primeiro.","Objetivo: sobreviver à próxima luta.")
    p += [panel(20,210,350,104,"deep","deep"),text(38,245,"15:03  •  Kai'Sa",18,"white",700),text(38,273,"Ouro aproximado; quadro anterior há 12 s",12,"#DAE9E7"),portrait(308,229,"Kaisa",45)]
    p += [text(20,356,"Qual item atende melhor?",19,"ink",700)]
    for i,(label,detail) in enumerate([("Alternativa A","Defesa física e vida"),("Alternativa B","Resistência mágica"),("Alternativa C","Mobilidade e proteção")]):
        y=375+i*91;p += [panel(20,y,350,78,"pale" if i==0 else "white","teal" if i==0 else "line",6),text(39,y+31,label,15,"ink",700),text(39,y+55,detail,12,"muted")]
    p += [button(20,670,350,"Registrar escolha"),text(20,743,"Pistas graduais • resposta ainda oculta",12,"muted"),text(20,808,"Conceito / dados ilustrativos",11,"muted")]
    save("10-mobile-estudo.svg",p)

for fn in (home,match,context,study,comparison,transfer,evaluation,operation,mobile_home,mobile_study): fn()
print(f"Geradas {len(list(OUT.glob('*.svg')))} telas em {OUT}")
