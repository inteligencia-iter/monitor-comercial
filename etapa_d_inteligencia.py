#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etapa D — Inteligência comercial (custo zero, 100% determinística).

Para cada evento corporativo, gera um briefing B2B a partir do título +
descrição + duração + sinais de contato já coletados na Etapa C. Nenhuma
chamada a API paga ou LLM: tudo é regra explícita e auditável.

O produto por trás do monitor é hospitalidade/eventos no Rio (hospedagem de
delegações, experiências para acompanhantes, parceria com organizadoras).
O "ângulo comercial" e o score B2B refletem esse uso.

Campos gerados em `inteligencia`:
  segmento            área / subárea (ex.: "Saúde / Neurologia")
  porte_estimado      pequeno | médio | grande
  porte_justificativa texto curto explicando o porte
  publico_alvo        quem frequenta (pauta a oferta)
  empresas_citadas    siglas/organizações extraídas do título
  angulo_comercial    recomendação de abordagem
  relevancia_b2b      0–100 (múltiplos de 10) — ver MODELO DE SCORE abaixo
  _fonte              "heuristica"

Entrada:  events_enriquecido.json  (Etapa C)
Saída:    events_inteligencia.json  (arquivo final, copiado para events.json)

Uso:
  python etapa_d_inteligencia.py        # todos os corporativos
  python etapa_d_inteligencia.py 5      # só os 5 primeiros corporativos
"""

import re
import sys
import json
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

ENTRADA = "events_enriquecido.json"
SAIDA = "events_inteligencia.json"

# --------------------------------------------------------------------------- #
# 1) SEGMENTO — área / subárea por palavras-chave
# --------------------------------------------------------------------------- #
# Subáreas de saúde (verificadas antes da área genérica).
SUB_SAUDE = {
    "Cardiologia": ["cardio", "coração", "cardiac"],
    "Neurologia": ["neuro", "neurolog", "avc", "cérebro"],
    "Oncologia": ["oncolog", "câncer", "cancer", "tumor"],
    "Dermatologia": ["dermato", "pele", "estética médica"],
    "Ginecologia": ["gineco", "obstetr", "gestante"],
    "Pediatria": ["pediatr", "criança", "infantil"],
    "Ortopedia": ["ortoped", "trauma", "coluna", "joelho"],
    "Pneumologia": ["pneumo", "pulmão", "respirat"],
    "Endocrinologia": ["endocrino", "diabetes", "tireoide", "hormôn"],
    "Hematologia": ["hematolog", "sangue", "transfus"],
    "Geriatria": ["geriatr", "idoso", "envelhec"],
    "Imunização": ["imuniz", "vacina", "aids", "hiv", "infec"],
    "Especialidades": ["cirurg", "surgery", "anestesi"],
}
AREAS = {
    "Saúde": ["saúde", "saude", "health", "médic", "medic", "clínic", "clinic",
              "enfermagem", "odonto", "farmac", "hospital", "terapia", "doença"],
    "Tecnologia": ["software", "startup", "fintech", "blockchain", "cyber",
                   "aeroespacial", "aerospace", "robótic", "roboti",
                   "inteligência artificial", "artificial intelligence",
                   "machine learning", " ti ", " app ", "devops", "cloud",
                   "programaç", "desenvolvedor", "developer"],
    "Finanças": ["finanç", "financ", "banco", "investiment", "seguro",
                 "contábil", "contabil", "accounting", "econom", "fintech",
                 "auditoria", "tribut"],
    "Jurídico": ["jurídic", "juridic", "direito", "advocacia", "law", "legal",
                 "compliance", "arbitragem"],
    "Energia": ["energia", "energy", "óleo", "oleo", "gás", "gas", "petróleo",
                "petroleo", "oil", "renovável", "solar", "eólic"],
    "Beleza": ["beleza", "estética", "estetica", "cosmét", "cosmet", "cabelo"],
    "Gastronomia": ["gastron", "culinár", "culinar", "chef", "food", "vinho",
                    "cerveja", "café", "restaurante"],
    "Desenvolvimento": ["negócios", "negocios", "empreend", "vendas", "sales",
                        "marketing", "liderança", "lideranca", "gestão",
                        "gestao", "rh ", "recursos humanos"],
    "Saúde Animal": ["veterin", "animal", "pet ", "zootec"],
    "Ciência": ["ciência", "ciencia", "science", "scientific", "científic",
                "cientific", "pesquisa", "research", "acadêmic", "academic",
                "matemát", "matematic", "física", "fisica", "physics",
                "química", "quimica", "chemistry", "biolog", "geolog",
                "astronom", "probabilidade", "estatística"],
}


def _match(texto, kws):
    return any(k in texto for k in kws)


def _classificar(t):
    # saúde com subárea tem prioridade
    if _match(t, AREAS["Saúde"]):
        for sub, kws in SUB_SAUDE.items():
            if _match(t, kws):
                return f"Saúde / {sub}"
        return "Saúde / Geral"
    for area in ["Finanças", "Jurídico", "Energia", "Beleza", "Gastronomia",
                 "Saúde Animal"]:
        if _match(t, AREAS[area]):
            sub = {"Finanças": "Geral", "Energia": "Óleo e Gás",
                   "Saúde Animal": "Veterinária"}.get(area)
            return f"{area} / {sub}" if sub else area
    # Ciência (congresso/conferência acadêmica) ANTES de Tecnologia: um
    # "Congress on Science and Technology" é ciência, não um evento de TI.
    if _match(t, AREAS["Ciência"]):
        return "Ciência / Acadêmico"
    if _match(t, AREAS["Tecnologia"]):
        return "Tecnologia / Inovação"
    if _match(t, AREAS["Desenvolvimento"]):
        return "Desenvolvimento / Negócios"
    return None


def classificar_segmento(titulo, descricao):
    # o título é o sinal mais confiável; a descrição só desempata
    seg = _classificar(titulo.lower())
    if seg:
        return seg
    seg = _classificar(descricao.lower())
    return seg or "Corporativo / Geral"


# --------------------------------------------------------------------------- #
# 2) PORTE — duração + escala/alcance
# --------------------------------------------------------------------------- #
KW_INTERNACIONAL = ["internacional", "international", "congreso", "congress",
                    "world", "mundial", "latino", "iberoamerican", "global",
                    "pan-americano", "panamerican"]
KW_GRANDE = ["expo", "feira", "summit", "convenção", "convencao", "convention",
             "megaevento", "arena", "estádio", "estadio"]


def duracao_dias(e):
    try:
        a = datetime.fromisoformat(e["data_inicio"]).date()
        b = datetime.fromisoformat(e["data_fim"]).date()
        return max(1, (b - a).days + 1)
    except Exception:
        return 1


def estimar_porte(t, dias):
    internacional = _match(t, KW_INTERNACIONAL)
    grande_kw = _match(t, KW_GRANDE)

    # grande: exige escala real — feira/convenção OU internacional multi-dia
    # OU evento nacional longo (>=5 dias).
    if grande_kw:
        return "grande", "Feira/convenção de grande formato.", internacional
    if internacional and dias >= 3:
        return "grande", f"Evento internacional, {dias} dias.", internacional
    if dias >= 5:
        return "grande", f"Evento longo, {dias} dias.", internacional
    # médio: multi-dia OU internacional curto
    if dias >= 2 or internacional:
        just = (f"Evento internacional, {dias} dias." if internacional
                else f"Duração de {dias} dias.")
        return "médio", just, internacional
    return "pequeno", "Evento de um dia.", internacional


# --------------------------------------------------------------------------- #
# 3) PÚBLICO-ALVO — templado por área
# --------------------------------------------------------------------------- #
PUBLICO = {
    "Saúde": "profissionais de saúde e indústria farmacêutica",
    "Tecnologia": "profissionais de tecnologia, startups e investidores",
    "Finanças": "executivos financeiros, bancos e seguradoras",
    "Jurídico": "advogados, magistrados e departamentos jurídicos",
    "Energia": "engenheiros, operadoras e cadeia de óleo e gás",
    "Beleza": "profissionais de estética e distribuidores",
    "Gastronomia": "chefs, restaurantes e food service",
    "Desenvolvimento": "líderes, gestores e times comerciais",
    "Saúde Animal": "veterinários e indústria pet",
    "Ciência": "pesquisadores e academia",
    "Corporativo": "público corporativo diversos setores",
}


def definir_publico(segmento):
    area = segmento.split(" / ")[0]
    return PUBLICO.get(area, PUBLICO["Corporativo"])


# --------------------------------------------------------------------------- #
# 4) EMPRESAS / SIGLAS citadas no título
# --------------------------------------------------------------------------- #
SIGLA_RE = re.compile(r"\b[A-ZÀ-Ú]{2,}(?:[0-9]{0,4})?\b")
STOP_SIGLAS = {"DE", "DA", "DO", "DAS", "DOS", "E", "OF", "THE", "AND", "ON",
               "IN", "FOR", "RJ", "SP", "BR", "II", "III", "IV", "VI", "VII"}


def extrair_empresas(titulo):
    achados = []
    for m in SIGLA_RE.findall(titulo):
        if m in STOP_SIGLAS:
            continue
        # ignora números romanos puros (XXXII etc.) — não são empresas
        if re.fullmatch(r"[IVXLCDM]+", m):
            continue
        if len(m) >= 2 and m not in achados:
            achados.append(m)
    return achados[:5]


# --------------------------------------------------------------------------- #
# 5) ÂNGULO COMERCIAL — templado por área/porte/alcance
# --------------------------------------------------------------------------- #
def montar_angulo(area, porte, internacional):
    area_txt = {"Saúde": "saúde", "Tecnologia": "tecnologia",
                "Finanças": "finanças", "Jurídico": "jurídico",
                "Energia": "energia", "Ciência": "ciência",
                "Gastronomia": "gastronomia", "Beleza": "beleza",
                "Desenvolvimento": "negócios",
                "Saúde Animal": "saúde animal"}.get(area, "corporativo")

    if porte == "grande" and internacional:
        return (f"Evento de {area_txt} de grande porte com público "
                "internacional. Priorizar: pacotes de hospedagem para "
                "delegações, tours e experiências para acompanhantes, e "
                "proposta para a organização do evento.")
    if porte == "grande":
        return (f"Evento de {area_txt} de grande porte. Abordar a organizadora "
                "com pacotes corporativos de hospedagem e locação de espaços; "
                "alto volume de participantes.")
    if porte == "médio":
        return (f"Evento de {area_txt} de médio porte. Oferecer hospedagem e "
                "experiências para participantes de fora do Rio; contato com a "
                "produtora pode render parceria recorrente.")
    return (f"Evento de {area_txt} de pequeno porte. Oportunidade pontual de "
            "hospedagem e serviços; priorizar se houver contato direto.")


# --------------------------------------------------------------------------- #
# 6) SCORE B2B — modelo aditivo determinístico e auditável
# --------------------------------------------------------------------------- #
# MODELO DE SCORE (0–100, arredondado a múltiplos de 10)
#
#   base .......................... 15
#   porte:  grande +35 | médio +20 | pequeno +8
#   alcance internacional ......... +12
#   segmento (propensão a gasto B2B com hospedagem/eventos):
#       alto   (Saúde, Jurídico, Energia, Finanças, Tecnologia,
#               Desenvolvimento, Saúde Animal) .... +12
#       médio  (Ciência, Corporativo, Beleza) ...... +6
#       baixo  (Gastronomia) ....................... +2
#   duração: >=4 dias +8 | 2–3 dias +4 | 1 dia 0
#   contato acionável já coletado (email/telefone) . +5
#
#   score = clamp(soma, 0, 100), arredondado para o múltiplo de 10 mais próximo
#
# Tudo é reproduzível: mesmo evento => mesmo score. Sem aleatoriedade, sem LLM.
SEG_PESO = {
    "alto": (12, {"Saúde", "Jurídico", "Energia", "Finanças", "Tecnologia",
                  "Desenvolvimento", "Saúde Animal"}),
    "médio": (6, {"Ciência", "Corporativo", "Beleza"}),
    "baixo": (2, {"Gastronomia"}),
}


def peso_segmento(area):
    for peso, areas in SEG_PESO.values():
        if area in areas:
            return peso
    return 6


def calcular_score(porte, internacional, area, dias, tem_contato):
    s = 15
    s += {"grande": 35, "médio": 20, "pequeno": 8}[porte]
    if internacional:
        s += 12
    s += peso_segmento(area)
    if dias >= 4:
        s += 8
    elif dias >= 2:
        s += 4
    if tem_contato:
        s += 5
    s = max(0, min(100, s))
    return int(round(s / 10.0) * 10)


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def gerar_inteligencia(e):
    t = f"{e['titulo']} {e['descricao']}".lower()
    dias = duracao_dias(e)
    segmento = classificar_segmento(e["titulo"], e["descricao"])
    area = segmento.split(" / ")[0]
    porte, porte_just, internacional = estimar_porte(t, dias)
    c = e.get("contato") or {}
    tem_contato = bool(c.get("emails") or c.get("telefones"))

    return {
        "segmento": segmento,
        "porte_estimado": porte,
        "porte_justificativa": porte_just,
        "publico_alvo": definir_publico(segmento),
        "empresas_citadas": extrair_empresas(e["titulo"]),
        "angulo_comercial": montar_angulo(area, porte, internacional),
        "relevancia_b2b": calcular_score(porte, internacional, area, dias,
                                         tem_contato),
        "_fonte": "heuristica",
    }


def main():
    limite = None
    if len(sys.argv) > 1:
        try:
            limite = int(sys.argv[1])
        except ValueError:
            print(f"Argumento inválido: {sys.argv[1]!r}")
            sys.exit(1)

    with open(ENTRADA, encoding="utf-8") as f:
        data = json.load(f)
    eventos = data["eventos"]

    corp = [e for e in eventos if e["categoria"] == "corporativos"]
    alvo = corp[:limite] if limite else corp
    print(f"[D] Gerando inteligência para {len(alvo)} de {len(corp)} "
          "eventos corporativos...")

    for e in alvo:
        e["inteligencia"] = gerar_inteligencia(e)

    saida = {
        "gerado_em": datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(timespec="seconds"),
        "modo": "deterministico (custo zero)",
        "total": len(eventos),
        "eventos": eventos,
    }
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    # resumo
    scores = [e["inteligencia"]["relevancia_b2b"] for e in alvo]
    if scores:
        media = sum(scores) / len(scores)
        alta = sum(1 for s in scores if s >= 70)
        print(f"\n[OK] salvo em {SAIDA}")
        print(f"     score B2B — média {media:.0f}, "
              f"alta relevância (>=70): {alta}/{len(scores)}")


if __name__ == "__main__":
    main()
