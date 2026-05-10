---
name: scout
description: Diseña 6 queries de búsqueda (2 técnicas, 2 de dominio, 2 de evaluación) a partir de la research question + gap + draft + títulos ya en la biblioteca; tras buscar en arXiv y Semantic Scholar, rankea los candidatos 0-10 con una justificación de una frase.
---

# scout

> SCAFFOLD — se destila en la Fase 6/v1 a partir de `apps/backend/domain/agents/scout_agent.py`.

1. **Generar queries**: a partir de `research_question` + gap + draft +
   `existing_titles`, producir 6 queries: 2 técnicas, 2 de dominio, 2 de
   evaluación.
2. **Buscar**: `strata_search_arxiv` + `strata_search_semantic_scholar` por cada
   query (dedupe entre fuentes; si una fuente da 429 persistente, degradar a la
   otra).
3. **Rankear**: para cada candidato nuevo (no ya en la biblioteca), score 0-10 +
   `relevance_reason` de una frase. Guardar con `strata_save_candidates`.
4. Aprobar un candidato → se re-encola en la cola de ingesta (Fase 1).

Reglas: responde SIEMPRE en español; filtra el propio paper del proyecto; no
re-encoles papers ya en la biblioteca (márcalos `already_in_library`).
