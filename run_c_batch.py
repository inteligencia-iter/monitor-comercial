#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Roda a Etapa C em lotes retomáveis. Uso: python run_c_batch.py <n_por_lote>"""
import sys, json, time
import etapa_c_enriquecimento as C

ENTRADA = "events_ab.json"
SAIDA = "events_enriquecido.json"
LOTE = int(sys.argv[1]) if len(sys.argv) > 1 else 20

# carrega o parcial se existir, senão o base
import os
fonte = SAIDA if os.path.exists(SAIDA) else ENTRADA
data = json.load(open(fonte, encoding="utf-8"))
eventos = data["eventos"]
corp = [e for e in eventos if e["categoria"] == "corporativos"]

pendentes = [e for e in corp if e["contato"]["status"] == "nao_coletado"]
print(f"pendentes: {len(pendentes)} de {len(corp)} corporativos")

feitos = 0
for e in pendentes[:LOTE]:
    site = e.get("site_oficial")
    if not site:
        e["contato"]["status"] = "sem_site"
    else:
        e["contato"] = C.enriquecer_site(site)
    feitos += 1
    st = e["contato"]["status"]
    m = {"coletado": "✓", "sem_contato": "·", "erro": "✗", "sem_site": "○"}.get(st, "?")
    print(f"  [{m}] {e['titulo'][:48]}")

json.dump(data, open(SAIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
restantes = sum(1 for e in corp if e["contato"]["status"] == "nao_coletado")
print(f"lote de {feitos} salvo. restam {restantes}.")
