#!/usr/bin/env python3
"""Validação pós-pipeline: garante que events.json é consistente antes do commit."""
import json, sys

try:
    d = json.load(open("events.json", encoding="utf-8"))
except Exception as e:
    print(f"ERRO: events.json inválido ou ilegível: {e}")
    sys.exit(1)

total = d.get("total", 0)
evs   = d.get("eventos", [])
novos = sum(1 for e in evs if e.get("novo"))
corp  = sum(1 for e in evs if e.get("categoria") == "corporativos")
trav  = sum(1 for e in evs if e.get("contato", {}).get("travado"))
com_local = sum(1 for e in evs if e.get("local"))

print(f"total: {total} | corp: {corp} | novos: {novos} | travados: {trav} | com local: {com_local}")

if total == 0:
    print("ERRO: 0 eventos — abortando para não sobrescrever o banco anterior.")
    sys.exit(1)

if len(evs) != total:
    print(f"AVISO: total={total} mas len(eventos)={len(evs)}")

print("OK — events.json validado com sucesso.")
