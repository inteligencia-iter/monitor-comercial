#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etapa A+B — Coleta base do Monitor de Eventos Corporativos (Rio de Janeiro).

A: lista todos os eventos futuros do calendário público do Visit Rio.
B: define local, site oficial e categoria de cada evento.

Fonte: The Events Calendar (plugin WordPress "Tribe") do visitrio.com.br,
exposto em /wp-json/tribe/events/v1/events. É JSON estruturado — não há
necessidade de parsear HTML da listagem.

IMPORTANTE — curl_cffi é obrigatório:
  O Visit Rio fica atrás do Cloudflare, que bloqueia a lib `requests` do Python
  pelo fingerprint TLS. O curl_cffi imita um Chrome real (impersonate="chrome")
  e passa. NÃO troque por `requests`.

Saída: events_ab.json  (consumido pela Etapa C).

Uso:
  python scraper_visitrio.py         # coleta tudo
  python scraper_visitrio.py 5       # coleta só os 5 primeiros (teste rápido)
"""

import sys
import re
import json
import html
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

from curl_cffi import requests as creq

# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
BASE = "https://visitrio.com.br/wp-json/tribe/events/v1/events"
PER_PAGE = 50               # máximo aceito com folga pela API
IMPERSONATE = "chrome"      # fingerprint TLS de navegador real (passa no Cloudflare)
TIMEOUT = 30
SAIDA = "events_ab.json"

# --- Modo incremental (stateful) --------------------------------------------
# O events.json commitado pelo GitHub Actions é o BANCO acumulado. A cada
# execução mesclamos o feed novo nele por `id`, preservando contatos já
# coletados (Etapa C) e resgates manuais. Eventos que já terminaram há mais de
# RETENCAO_DIAS saem do arquivo (é um monitor prospectivo).
BANCO = "events.json"
RETENCAO_DIAS = 30

# --------------------------------------------------------------------------- #
# Etapa B — classificação de categoria
# --------------------------------------------------------------------------- #
# O Visit Rio só rotula parte dos eventos na própria taxonomia (hoje, só
# "esportivos"). Quando a taxonomia traz categoria, ela é a fonte da verdade
# (categoria_fonte="visitrio"). Quando não traz, inferimos por palavras-chave
# no título + descrição (categoria_fonte="heuristica").
#
# As 4 categorias do frontend: corporativos, esportivos, culturais, outros.

# Mapeia rótulos da taxonomia do Visit Rio -> categoria do monitor.
MAPA_TAXONOMIA = {
    "esportivos": "esportivos",
    "esporte": "esportivos",
    "esportes": "esportivos",
    "corporativos": "corporativos",
    "corporativo": "corporativos",
    "negocios": "corporativos",
    "negócios": "corporativos",
    "culturais": "culturais",
    "cultura": "culturais",
    "cultural": "culturais",
    "shows": "culturais",
    "musica": "culturais",
    "música": "culturais",
}

# Palavras-chave da heurística (avaliadas em ordem: esportivo, cultural,
# corporativo; o que não casar em nada vira "outros").
KW_ESPORTIVO = [
    "corrida", "maratona", "run", "marathon", "triatlo", "tênis", "tennis",
    "campeonato", "torneio", "copa", "cup", "regata", "ciclismo", "bike",
    "natação", "surf", "vôlei", "futebol", "jiu-jitsu", "crossfit", "pedal",
]
KW_CULTURAL = [
    "festival", "show", "concerto", "musical", "orquestra", "banda", "ópera",
    "opera", "teatro", "exposição", "exposicao", "arte", "cinema", "dança",
    "danca", "carnaval", "réveillon", "reveillon", "réveilon", "gastronô",
    "sinfôn", "sinfon", "symphonic", "circo", "stand-up", "comédia",
]
KW_CORPORATIVO = [
    "congresso", "congress", "conferência", "conference", "conferencia",
    "convenção", "convencao", "convention", "seminário", "seminario",
    "simpósio", "simposio", "symposium", "summit", "fórum", "forum",
    "workshop", "expo", "feira", "encontro", "jornada", "meeting",
    "reunião", "reuniao", "assembleia", "palestra", "curso", "capacitação",
    "treinamento", "empresarial", "b2b", "startup", "inovação", "inovacao",
    "tecnologia", "científic", "cientific", "scientific", "acadêmic",
    "academic", "médic", "medic", "saúde", "saude", "health", "jurídic",
    "juridic", "direito", "advocacia", "contábil", "contabil", "finanç",
    "financ", "econom", "annual meeting", "international",
]


def _tem_kw(texto, kws):
    return any(kw in texto for kw in kws)


def classificar(titulo, descricao, categorias_api):
    """Retorna (categoria, categoria_fonte)."""
    # 1) taxonomia do Visit Rio tem prioridade
    for c in categorias_api:
        nome = (c.get("slug") or c.get("name") or "").strip().lower()
        if nome in MAPA_TAXONOMIA:
            return MAPA_TAXONOMIA[nome], "visitrio"

    # 2) heurística por palavras-chave.
    #    Ordem = prioridade. Num MONITOR CORPORATIVO, o formato profissional
    #    (congresso, conferência, expo, feira, summit...) define o evento
    #    mesmo quando o tema é cultural — ex.: "International Congress of
    #    Symphonic Bands" é um congresso B2B, não um show. Por isso
    #    corporativo é avaliado ANTES de cultural.
    t = f"{titulo} {descricao}".lower()
    if _tem_kw(t, KW_ESPORTIVO):
        return "esportivos", "heuristica"
    if _tem_kw(t, KW_CORPORATIVO):
        return "corporativos", "heuristica"
    if _tem_kw(t, KW_CULTURAL):
        return "culturais", "heuristica"
    return "outros", "heuristica"


# --------------------------------------------------------------------------- #
# Limpeza de texto
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def limpar_texto(s):
    """Remove tags HTML e entidades, normaliza espaços."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def extrair_local(ev):
    """O 'local' vem do venue quando preenchido; hoje quase sempre vazio."""
    venue = ev.get("venue")
    if isinstance(venue, list) and venue:
        venue = venue[0]
    if isinstance(venue, dict):
        nome = limpar_texto(venue.get("venue") or "")
        cidade = limpar_texto(venue.get("city") or "")
        partes = [p for p in (nome, cidade) if p]
        if partes:
            return ", ".join(partes)
    return None


# --------------------------------------------------------------------------- #
# Coleta (Etapa A)
# --------------------------------------------------------------------------- #
def coletar(limite=None):
    hoje = date.today().isoformat()
    eventos = []
    page = 1
    total_pages = None

    print(f"[A] Coletando eventos a partir de {hoje} ...")
    while True:
        url = f"{BASE}?per_page={PER_PAGE}&page={page}&start_date={hoje}"
        r = creq.get(url, impersonate=IMPERSONATE, timeout=TIMEOUT)
        if r.status_code != 200:
            # A API responde 400 quando a página passa do total — fim natural.
            if r.status_code == 400 and page > 1:
                break
            raise RuntimeError(f"HTTP {r.status_code} em {url}")

        data = r.json()
        lote = data.get("events", [])
        if total_pages is None:
            total_pages = data.get("total_pages", 1)
            print(f"    total informado pela API: {data.get('total')} eventos "
                  f"em {total_pages} páginas")
        if not lote:
            break

        for ev in lote:
            titulo = limpar_texto(ev.get("title") or "")
            descricao = limpar_texto(ev.get("description") or "")
            categorias_api = ev.get("categories") or []
            categoria, fonte = classificar(titulo, descricao, categorias_api)

            eventos.append({
                "id": ev.get("id"),
                "titulo": titulo,
                "slug": ev.get("slug"),
                "data_inicio": ev.get("start_date"),
                "data_fim": ev.get("end_date") or ev.get("start_date"),
                "descricao": descricao,
                "categoria": categoria,
                "categoria_fonte": fonte,          # Etapa B
                "url_visitrio": ev.get("url"),
                "site_oficial": html.unescape((ev.get("website") or "").strip()) or None,  # B
                "local": extrair_local(ev),        # Etapa B
                # esqueleto preenchido pela Etapa C:
                "contato": {
                    "emails": [],
                    "telefones": [],
                    "whatsapp": [],
                    "produtoras_inferidas": [],
                    "status": "nao_coletado",
                },
                # preenchido pela Etapa D:
                "inteligencia": None,
            })
            if limite and len(eventos) >= limite:
                print(f"    limite de {limite} atingido — parando.")
                return eventos

        print(f"    página {page}/{total_pages}: +{len(lote)} "
              f"(acumulado {len(eventos)})")
        if page >= total_pages:
            break
        page += 1

    return eventos


def _skeleton_contato():
    return {"emails": [], "telefones": [], "whatsapp": [],
            "produtoras_inferidas": [], "status": "nao_coletado"}


def _ja_passou(ev, hoje):
    """True se o evento terminou há mais de RETENCAO_DIAS."""
    try:
        fim = datetime.fromisoformat(ev["data_fim"]).date()
    except Exception:
        return False
    return (hoje - fim).days > RETENCAO_DIAS


def carregar_banco():
    """Lê o events.json acumulado (se existir) e indexa por id."""
    import os
    if not os.path.exists(BANCO):
        return {}
    try:
        with open(BANCO, encoding="utf-8") as f:
            dados = json.load(f)
        return {e["id"]: e for e in dados.get("eventos", []) if "id" in e}
    except Exception:
        return {}


def mesclar(feed, banco, hoje):
    """
    Mescla o feed novo (campos-base recém-coletados) no banco por `id`.

    Regras:
      - id novo            -> insere, primeiro_visto=hoje, novo=True
      - id existente       -> atualiza campos-base (título/datas/site podem mudar)
                              e PRESERVA contato + inteligencia + primeiro_visto.
                              Reenriquecimento manual (status coletado/bloqueado)
                              sobrevive porque a Etapa C só toca pendentes.
      - só corporativo carrega contato/inteligencia; se a categoria mudou,
        o enriquecimento antigo é descartado (esqueleto limpo).
      - id sumiu do feed   -> mantém enquanto dentro da janela de retenção,
                              marcando no_feed=True (saiu do calendário público).
    """
    saida = []
    vistos = set()

    for ev in feed:
        vistos.add(ev["id"])
        antigo = banco.get(ev["id"])
        if antigo is None:
            ev["primeiro_visto"] = hoje.isoformat()
            ev["novo"] = True
            ev["no_feed"] = False
            saida.append(ev)
            continue

        ev["primeiro_visto"] = antigo.get("primeiro_visto", hoje.isoformat())
        ev["novo"] = False
        ev["no_feed"] = False

        # LOCAL (Etapa B2) — a API nunca devolve venue; preserva o que está no banco.
        if antigo.get("local_coletado"):
            ev["local"] = antigo.get("local")
            ev["local_coletado"] = True

        # CONTATO e INTELIGÊNCIA
        corp_antes = antigo.get("categoria") == "corporativos"
        corp_agora = ev["categoria"] == "corporativos"
        contato_antigo = antigo.get("contato") or {}
        travado = bool(contato_antigo.get("travado"))

        if travado:
            # Resgatado manualmente: preserva SEMPRE, independente de categoria.
            ev["contato"] = contato_antigo
            ev["inteligencia"] = antigo.get("inteligencia")
        elif corp_agora and corp_antes:
            # Mesma categoria corporativa: preserva enriquecimento existente.
            ev["contato"] = contato_antigo or _skeleton_contato()
            ev["inteligencia"] = antigo.get("inteligencia")
        # Categoria mudou e não é travado: mantém esqueleto limpo do feed.

        saida.append(ev)

    # eventos do banco que não vieram mais no feed
    arquivados = 0
    for id_, antigo in banco.items():
        if id_ in vistos:
            continue
        if _ja_passou(antigo, hoje):
            arquivados += 1
            continue                      # expira da retenção
        antigo["novo"] = False
        antigo["no_feed"] = True          # saiu do calendário, mas ainda vigente
        saida.append(antigo)

    return saida, arquivados


def main():
    limite = None
    if len(sys.argv) > 1:
        try:
            limite = int(sys.argv[1])
        except ValueError:
            print(f"Argumento inválido: {sys.argv[1]!r} (esperado um número).")
            sys.exit(1)

    hoje = date.today()
    feed = coletar(limite=limite)
    banco = carregar_banco()
    print(f"[merge] banco atual: {len(banco)} eventos | feed novo: {len(feed)}")

    eventos, arquivados = mesclar(feed, banco, hoje)
    novos = sum(1 for e in eventos if e.get("novo"))
    fora_feed = sum(1 for e in eventos if e.get("no_feed"))

    saida = {
        "gerado_em": datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(timespec="seconds"),
        "total": len(eventos),
        "eventos": eventos,
    }
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    from collections import Counter
    cats = Counter(e["categoria"] for e in eventos)
    print(f"\n[OK] {len(eventos)} eventos salvos em {SAIDA}")
    print(f"     novos: {novos} | fora do feed (retidos): {fora_feed} | "
          f"arquivados (passaram): {arquivados}")
    print(f"     por categoria: {dict(cats)}")


if __name__ == "__main__":
    main()
