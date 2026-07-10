#!/usr/bin/env python3
"""
Etapa B2 — Enriquecimento de LOCAL (venue) a partir da página do evento no Visit Rio.

A API REST do Visit Rio (Tribe/The Events Calendar) NÃO devolve o venue: o campo
`venue` vem sempre vazio. Porém, a página HTML de cada evento
(https://visitrio.com.br/evento/<slug>/) exibe um card "Local" com o nome do
espaço. Esta etapa visita essa página e extrai o venue.

Comportamento INCREMENTAL / custo baixo:
- Só processa eventos cujo `local` está vazio E que ainda não foram tentados
  (flag `local_coletado` ausente/falsa). Assim, no dia a dia só eventos novos
  disparam requisição — os já coletados ficam gravados no events.json.
- Resumível: grava o events.json a cada N eventos, então pode ser reexecutada.
- `--todos` força recoletar todos (útil se a fonte mudar).

Uso:  python etapa_b2_local.py [--todos] [limite]
"""
import sys, os, re, time, json
import html as ihtml
from curl_cffi import requests as creq

BANCO = os.environ.get("BANCO_FILE", "events.json")  # no pipeline: events_ab.json
TIMEOUT = 40
PAUSA = 3.0          # janela do Cloudflare exige espacamento maior
SALVA_A_CADA = 10    # checkpoint p/ ser resumível

# rótulos que o próprio Visit Rio usa quando não há um venue único
NORMALIZAR = {"múltiplo": "Vários locais", "multiplo": "Vários locais",
              "a definir": None, "online": "Online"}

def extrair_local(url):
    """Retorna o nome do venue (str) ou None."""
    raw = None
    for tent in range(3):
        try:
            r = creq.get(url, impersonate="chrome", timeout=TIMEOUT)
            if r.status_code == 200:
                raw = r.text; break
        except Exception:
            pass
        time.sleep(3 * (tent + 1))   # backoff
    if raw is None:
        return "__ERRO__"
    # remove script/style e tags; normaliza espaços
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', raw, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = ihtml.unescape(t)
    t = re.sub(r'\s+', ' ', t)
    # o card de venue segue: "Local <nome> (Site|Endereço|Telefone|Ver no mapa|+ Google|iCal|...)"
    m = re.search(
        r'\bLocal\s+([A-Za-zÀ-ÿ0-9][^|]{2,88}?)\s+'
        r'(?:Site|Endereço|Telefone|Website|Ver no mapa|\+ Google|Adicionar|Google Calendar|iCal|Compartilh)',
        t)
    if not m:
        return None
    v = m.group(1).strip(' -–—•·\u00a0')
    if len(v) < 3:
        return None
    if re.search(r'(inscri|programa|clique|acesse|saiba mais|ingresso)', v, re.I):
        return None
    norm = NORMALIZAR.get(v.lower(), v)
    return norm


def main():
    args = sys.argv[1:]
    todos = "--todos" in args
    args = [a for a in args if a != "--todos"]
    limite = int(args[0]) if args and args[0].isdigit() else None

    d = json.load(open(BANCO, encoding="utf-8"))
    evs = d["eventos"]

    alvo = []
    for e in evs:
        if not e.get("url_visitrio"):
            continue
        if todos or (not e.get("local") and not e.get("local_coletado")):
            alvo.append(e)
    if limite:
        alvo = alvo[:limite]

    print(f"[B2] {len(alvo)} eventos para coletar local (de {len(evs)})")
    ok = 0
    for i, e in enumerate(alvo, 1):
        loc = extrair_local(e["url_visitrio"])
        if loc == "__ERRO__":
            print(f"  [{i}/{len(alvo)}] {e['id']} rede falhou — tentar depois")
            time.sleep(PAUSA)
            continue
        e["local"] = loc                 # pode ser None (sem card de local)
        e["local_coletado"] = True       # não tenta de novo amanhã
        if loc:
            ok += 1
        if i % SALVA_A_CADA == 0:
            json.dump(d, open(BANCO, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"  [{i}/{len(alvo)}] checkpoint salvo — {ok} locais até agora")
        time.sleep(PAUSA)

    json.dump(d, open(BANCO, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    com = sum(1 for e in evs if e.get("local"))
    print(f"[B2] concluído. {ok} locais novos nesta rodada. Total com local: {com}/{len(evs)}")


if __name__ == "__main__":
    main()
