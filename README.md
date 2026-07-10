# Monitor de Eventos Corporativos — Rio de Janeiro

Monitor de eventos corporativos do Rio de Janeiro, alimentado pelo calendário
público do Visit Rio. Hospedado no **GitHub Pages** e atualizado automaticamente
por **GitHub Actions**. Custo zero — sem APIs pagas.

## Estrutura do repositório

```
├── index.html                    # Frontend (GitHub Pages serve este arquivo)
├── events.json                   # BANCO acumulado + dado consumido pelo frontend
├── requirements.txt              # Dependência única: curl_cffi
├── scraper_visitrio.py           # Etapa A+B: coleta + categoria + MERGE incremental
├── etapa_b2_local.py             # Etapa B2: LOCAL (venue) da página do evento
├── etapa_c_enriquecimento.py     # Etapa C: contatos (só pendentes)
├── etapa_d_inteligencia.py       # Etapa D: inteligência comercial (custo zero)
├── run_c_batch.py                # (opcional) roda a Etapa C em lotes retomáveis
└── .github/workflows/deploy.yml  # Automação semanal (segundas)
```

## Como funciona o pipeline (modo incremental)

O `events.json` é ao mesmo tempo o **dado do frontend** e o **banco acumulado**.
A cada execução, o pipeline não recomeça do zero: ele mescla o que há de novo
no calendário e **preserva o trabalho já feito**.

```
Visit Rio (The Events Calendar / wp-json)
        │  Etapa A: busca o feed a partir de hoje
        ▼
scraper_visitrio.py ── MERGE por `id` no events.json existente ──► events_ab.json
        │      • id novo        → insere (primeiro_visto=hoje, novo=true)
        │      • id existente   → atualiza título/data/site, PRESERVA contato + inteligência + LOCAL
        │      • saiu do feed   → retém por RETENCAO_DIAS, depois arquiva (no_feed)
        ▼
etapa_b2_local.py ── coleta o LOCAL (venue) da página HTML do evento ─► (atualiza events_ab.json)
        │      • a API não devolve venue; o card "Local" está na página do evento
        │      • incremental: só visita eventos sem local_coletado (novos)
        ▼
etapa_c_enriquecimento.py ── enriquece SÓ pendentes ──────────────► events_enriquecido.json
        │      • processa status "nao_coletado" e "erro"
        │      • preserva "coletado", "bloqueado", "sem_contato" (não re-raspa)
        ▼
etapa_d_inteligencia.py ── recalcula score B2B (determinístico) ──► events_inteligencia.json
        ▼
        cp events_inteligencia.json events.json   (o Actions commita de volta)
```

### Por que isso importa

- **Contatos já coletados sobrevivem** às execuções — inclusive resgates
  manuais (ver "Leads bloqueados").
- **Sem re-scraping desnecessário**: só eventos novos ou com erro vão à rede.
- **Eventos passados** saem do arquivo após `RETENCAO_DIAS` (padrão: 30) — é um
  monitor prospectivo. Ajuste a constante em `scraper_visitrio.py`.
- **Noção de "novo"**: campos `primeiro_visto`, `novo` e `no_feed` no modelo de
  dados; o frontend exibe um selo "Novo" para o que entrou nos últimos 7 dias.

## Como publicar (passo a passo)

1. **Crie o repositório** no GitHub e envie todos os arquivos.
   - O `deploy.yml` deve ficar em `.github/workflows/deploy.yml`.
   - Envie o `events.json` inicial junto — ele é o banco de partida.

2. **Ative o GitHub Pages**: Settings → Pages → Source: `Deploy from a branch` →
   branch `main`, pasta `/ (root)`. O site ficará em
   `https://SEU-USUARIO.github.io/NOME-DO-REPO/`.

3. **Ative o GitHub Actions**: Settings → Actions → General → permita
   "Read and write permissions" (necessário para o workflow commitar o events.json).

4. **Rode o workflow**: Actions → "Atualizar Monitor de Eventos" → "Run workflow".

Depois disso, o workflow roda sozinho toda segunda-feira às 06:00 (horário de Brasília).
Na 1ª segunda de cada mês ele faz um **refresh completo** (`--todos`), reprocessando também os
`sem_contato`/`bloqueado` — porque alguns sites publicam a página de contato só
perto da data do evento.

## Rodar localmente (opcional, para testar)

```bash
pip install -r requirements.txt
python scraper_visitrio.py            # gera events_ab.json (mescla no events.json)
python etapa_c_enriquecimento.py      # gera events_enriquecido.json (só pendentes)
python etapa_d_inteligencia.py        # gera events_inteligencia.json
cp events_inteligencia.json events.json

# servir o site localmente
python -m http.server 8000            # abrir http://localhost:8000
```

Argumentos úteis:

```bash
python scraper_visitrio.py 5             # processa só 5 eventos (teste rápido)
python etapa_c_enriquecimento.py         # só pendentes (nao_coletado, erro)
python etapa_c_enriquecimento.py --todos # força reprocessar TODOS os corporativos
python etapa_c_enriquecimento.py 10      # só os 10 primeiros pendentes
python run_c_batch.py 30                 # Etapa C em lotes de 30, retomável
```

## Status de contato (Etapa C)

| status        | significado                                             |
|---------------|---------------------------------------------------------|
| `coletado`    | e-mail/telefone/WhatsApp extraído com sucesso           |
| `sem_contato` | site respondeu, mas sem contato público                 |
| `bloqueado`   | WAF/Cloudflare bloqueou — **exige navegador real**      |
| `erro`        | DNS/conexão/timeout — site indisponível                 |
| `sem_site`    | evento sem site oficial no Visit Rio                    |
| `nao_coletado`| ainda não processado (evento novo)                      |

### Leads bloqueados

Alguns sites (congressos médicos, principalmente) ficam atrás de Cloudflare
endurecido e **não cedem a nenhum cliente automatizado** — testado com 10
versões de fingerprint TLS, `/wp-json` e `/sitemap`. Para esses, use um navegador
real (ex.: Claude no Chrome) para coletar o contato uma vez. Como a Etapa C
preserva o status `coletado`, o resgate manual sobrevive às execuções automáticas.

## Pontos técnicos importantes

- **curl_cffi é obrigatório.** O Visit Rio usa Cloudflare, que bloqueia a
  biblioteca `requests` do Python pelo fingerprint TLS. O `curl_cffi` imita um
  Chrome real e passa. Não troque por `requests`.
- **Fonte estruturada.** O Visit Rio roda o plugin *The Events Calendar*, cuja
  REST API (`/wp-json/tribe/events/v1/events`) devolve JSON — mais confiável que
  raspar o HTML da listagem.
- **O frontend filtra por categoria.** Por padrão mostra "corporativos", mas o
  `events.json` contém todos os eventos. Só os corporativos recebem o briefing
  comercial (`inteligencia`).
- **Score B2B** é determinístico e auditável. O modelo aditivo (base + porte +
  alcance internacional + peso do segmento + duração + contato) está documentado
  no topo de `etapa_d_inteligencia.py`. Mesmo evento ⇒ mesmo score.

## Conformidade (LGPD)

A coleta se limita a dados profissionais publicados publicamente pela própria
organização dos eventos. Recomenda-se validação com o jurídico antes do uso
comercial em escala.
