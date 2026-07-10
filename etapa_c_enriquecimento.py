#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etapa C — Enriquecimento de contatos (só eventos corporativos).

Para cada evento corporativo com site oficial, baixa a home + páginas de
contato prováveis e extrai, quando publicados abertamente pela organização:
  - e-mails
  - telefones (formato Brasil)
  - números de WhatsApp
  - produtora/organizadora inferida (domínio + menções de "realização")

curl_cffi é obrigatório (mesmo motivo da Etapa A: Cloudflare/TLS).

Conformidade (LGPD): coletamos apenas dados profissionais que a própria
organização do evento publica em seu site. Nada de dados pessoais privados.

Entrada:  events_ab.json  (gerado pela Etapa A+B)
Saída:    events_enriquecido.json  (consumido pela Etapa D)

Uso:
  python etapa_c_enriquecimento.py        # processa todos os corporativos
  python etapa_c_enriquecimento.py 5      # só os 5 primeiros corporativos
"""

import re
import sys
import json
import time
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as creq

ENTRADA = "events_ab.json"
SAIDA = "events_enriquecido.json"
IMPERSONATE = "chrome"
TIMEOUT = 12                 # por página; sites de evento respondem rápido
PAUSA = 0.5                  # cortesia entre requisições (segundos)
MAX_PAGINAS_POR_SITE = 3     # home + até 2 páginas de contato

# "Sites oficiais" que na verdade são posts de rede social / encurtadores:
# bloqueiam scraping, penduram a conexão e não trazem contato da organização.
# Pulamos direto (status "sem_contato") em vez de gastar tempo.
DOMINIOS_PULAR = ("instagram.com", "facebook.com", "linktr.ee", "linkedin.com",
                  "twitter.com", "x.com", "tiktok.com", "youtube.com", "bit.ly")

# --------------------------------------------------------------------------- #
# Regex de extração
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
# Telefone BR — exige âncora inequívoca para evitar capturar anos, IDs e
# intervalos numéricos (ex.: "0100-0130") de scripts/CSS:
#   - DDD entre parênteses:            (21) 2529-5000  /  (21) 99999-9999
#   - código do país:                  +55 21 3333-4444
#   - linha gratuita:                  0800 123 4567
TEL_RE = re.compile(
    r"(?:\+55[\s.\-]*)?\(\d{2}\)[\s.\-]*9?\d{4}[\s.\-]?\d{4}"      # (DD) ...
    r"|\+55[\s.\-]*\d{2}[\s.\-]*9?\d{4}[\s.\-]?\d{4}"              # +55 DD ...
    r"|0800[\s.\-]?\d{3}[\s.\-]?\d{4}"                            # 0800 ...
)
# Links de WhatsApp
WA_LINK_RE = re.compile(
    r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp\.com/send\?phone=)(\+?\d{8,15})"
)

# E-mails de telemetria/plataforma que não são contato real da organização.
# (checado contra o e-mail inteiro, cobrindo subdomínios como
#  ...@o37417.ingest.sentry.io e ...@sentry-next.wixpress.com)
LIXO_EMAIL = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?)$"
    r"|sentry|wixpress|ingest|cloudfront|sentry\.io"
    r"|@(?:example|domain|email|test|your|site)\."
    r"|john@doe|jane@doe|@doe\.|johndoe|noreply@|no-reply@|you@|name@|mail@mail",
    re.IGNORECASE,
)

# Páginas prováveis de contato (tentadas além da home).
CAMINHOS_CONTATO = ["contato", "contact", "fale-conosco", "sobre", "about"]


def normalizar_tel(bruto):
    d = re.sub(r"\D", "", bruto)
    # com o regex ancorado (DDD/+55/0800), um telefone BR válido tem 10-13 díg.
    if 10 <= len(d) <= 13:
        return re.sub(r"\s+", " ", bruto.strip())
    return None


def extrair_de_html(texto_html):
    emails, tels, whats, prods = set(), set(), set(), set()

    # Normaliza escapes JSON (\u003e) e entidades HTML (&gt;) que grudam lixo
    # em e-mails, ex.: "\u003eprivacy@..." -> " privacy@...".
    texto_html = texto_html.replace("\\u003e", " ").replace("\\u003c", " ")
    texto_html = texto_html.replace("\\u0026", " ").replace("\\/", "/")
    texto_html = texto_html.replace("&gt;", " ").replace("&lt;", " ")

    for m in EMAIL_RE.findall(texto_html):
        if not LIXO_EMAIL.search(m):
            emails.add(m.lower())

    for m in WA_LINK_RE.findall(texto_html):
        whats.add(re.sub(r"\D", "", m))

    # telefones: buscamos no texto visível aproximado (sem tags) p/ reduzir ruído
    texto_visivel = re.sub(r"<[^>]+>", " ", texto_html)
    for m in TEL_RE.findall(texto_visivel):
        t = normalizar_tel(m)
        if t:
            tels.add(t)

    return emails, tels, whats, prods


def produtora_do_dominio(url):
    """Deriva um palpite de produtora a partir do domínio do site oficial."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    host = host.replace("www.", "")
    # ignora domínios genéricos de bilheteria/hospedagem (não são a produtora)
    genericos = ("ticketsports", "sympla", "eventbrite", "even3", "doity",
                 "blogspot", "wordpress.com", "wixsite", "instagram",
                 "facebook", "linktr.ee")
    if any(g in host for g in genericos):
        return None
    base = host.split(".")[0]
    if len(base) > 2:
        return base
    return None


def paginas_a_tentar(site):
    urls = [site]
    for c in CAMINHOS_CONTATO:
        urls.append(urljoin(site.rstrip("/") + "/", c))
    return urls[:1 + (MAX_PAGINAS_POR_SITE - 1) * 2]


# Marcadores típicos de página de desafio anti-bot (mesmo com HTTP 200).
_DESAFIO_MARCAS = ("just a moment", "checking your browser", "cf-browser-verification",
                   "attention required", "cf-challenge", "/cdn-cgi/challenge",
                   "enable javascript and cookies", "ddos protection by")


def _eh_desafio(r):
    """True se o corpo parece um desafio anti-bot em vez do site real."""
    txt = (r.text or "")
    if len(txt) < 8000:  # páginas de desafio são curtas
        low = txt.lower()
        return any(m in low for m in _DESAFIO_MARCAS)
    return False


def _get_com_retry(url, tentativas=2):
    """GET com uma retentativa em falha de rede/5xx transitório."""
    for i in range(tentativas):
        try:
            r = creq.get(url, impersonate=IMPERSONATE, timeout=TIMEOUT,
                         allow_redirects=True)
            # 5xx pode ser transitório; tenta de novo uma vez
            if r.status_code >= 500 and i < tentativas - 1:
                time.sleep(1.5)
                continue
            return r
        except Exception:
            if i < tentativas - 1:
                time.sleep(1.5)
                continue
            return None
    return None


def enriquecer_site(site):
    """Retorna dict de contato + status para um único site oficial."""
    vazio = {"emails": [], "telefones": [], "whatsapp": [],
             "produtoras_inferidas": [], "status": "sem_contato"}

    host = ""
    try:
        host = urlparse(site).netloc.lower().replace("www.", "")
    except Exception:
        pass
    if any(host.endswith(d) or host == d for d in DOMINIOS_PULAR):
        # post de rede social: não é site da organização
        return vazio

    emails, tels, whats, prods = set(), set(), set(), set()
    paginas_ok = 0
    bloqueado = False

    for url in paginas_a_tentar(site):
        if paginas_ok >= MAX_PAGINAS_POR_SITE:
            break
        r = _get_com_retry(url)
        if r is None:
            continue
        # 403/503 + página curta = desafio de bot (Cloudflare/WAF/Sucuri)
        if r.status_code in (403, 503, 429) or _eh_desafio(r):
            bloqueado = True
            continue
        if r.status_code != 200 or not r.text:
            continue
        paginas_ok += 1
        e, t, w, p = extrair_de_html(r.text)
        emails |= e
        tels |= t
        whats |= w
        prods |= p
        time.sleep(PAUSA)

    dom = produtora_do_dominio(site)
    if dom:
        prods.add(dom)

    achou = bool(emails or tels or whats)
    if achou:
        status = "coletado"
    elif paginas_ok > 0:
        status = "sem_contato"   # site respondeu, mas sem contato público
    elif bloqueado:
        status = "bloqueado"     # WAF/Cloudflare — exige navegador real
    else:
        status = "erro"          # DNS/conexão/timeout — site indisponível

    return {
        "emails": sorted(emails),
        "telefones": sorted(tels),
        "whatsapp": sorted(whats),
        "produtoras_inferidas": sorted(prods),
        "status": status,
    }


def main():
    # Uso:
    #   etapa_c_enriquecimento.py            -> só pendentes (nao_coletado, erro)
    #   etapa_c_enriquecimento.py 5          -> só os 5 primeiros pendentes
    #   etapa_c_enriquecimento.py --todos    -> força reprocessar TODOS
    #   etapa_c_enriquecimento.py --todos 5  -> força os 5 primeiros
    #
    # Preservar coletado/bloqueado/sem_contato é o que torna o pipeline
    # incremental: contatos já obtidos (inclusive resgates manuais) não são
    # re-raspados nem sobrescritos a cada execução diária.
    forcar_todos = "--todos" in sys.argv
    args_num = [a for a in sys.argv[1:] if a.lstrip("-").isdigit()]
    limite = int(args_num[0]) if args_num else None

    # status que ainda merecem uma (nova) tentativa
    PENDENTES = {"nao_coletado", "erro"}

    with open(ENTRADA, encoding="utf-8") as f:
        data = json.load(f)
    eventos = data["eventos"]

    corp = [e for e in eventos if e["categoria"] == "corporativos"]

    # Contatos travados (ex.: resgatados manualmente por navegador) NUNCA são
    # re-raspados nem sobrescritos — nem com --todos. Isso protege o trabalho
    # manual de ser rebaixado por uma raspagem headless que falharia (Cloudflare).
    def travado(e):
        return bool(e.get("contato", {}).get("travado"))

    if forcar_todos:
        candidatos = [e for e in corp if not travado(e)]
    else:
        candidatos = [e for e in corp
                      if not travado(e)
                      and e.get("contato", {}).get("status", "nao_coletado")
                      in PENDENTES]
    alvo = candidatos[:limite] if limite else candidatos
    travados = sum(1 for e in corp if travado(e))
    preservados = len(corp) - len(candidatos)
    print(f"[C] {len(alvo)} a enriquecer | {preservados} preservados "
          f"({travados} travados/manuais) | {len(corp)} corporativos no total")

    stats = {"coletado": 0, "sem_contato": 0, "bloqueado": 0, "erro": 0,
             "sem_site": 0}
    for i, e in enumerate(alvo, 1):
        site = e.get("site_oficial")
        if not site:
            e["contato"]["status"] = "sem_site"
            stats["sem_site"] += 1
            print(f"  {i:>3}/{len(alvo)}  [sem_site] {e['titulo'][:50]}")
            continue

        contato = enriquecer_site(site)
        e["contato"] = contato
        stats[contato["status"]] = stats.get(contato["status"], 0) + 1
        marca = {"coletado": "✓", "sem_contato": "·", "bloqueado": "⚠",
                 "erro": "✗"}.get(contato["status"], "?")
        n_mails = len(contato["emails"])
        n_tels = len(contato["telefones"])
        print(f"  {i:>3}/{len(alvo)}  [{marca}] {n_mails}m {n_tels}t  "
              f"{e['titulo'][:50]}")

    data["gerado_em"] = data.get("gerado_em")
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] salvo em {SAIDA}")
    print(f"     resultado: {stats}")


if __name__ == "__main__":
    main()
