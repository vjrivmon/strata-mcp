---
name: scout
description: Diseña 6 queries de búsqueda (2 técnicas, 2 de dominio, 2 de evaluación) a partir de la research question + gap + draft + títulos ya en la biblioteca; busca en arXiv (Semantic Scholar es v1), deduplica, descarta lo que ya está, y rankea cada candidato 0-10 con una justificación de una frase. Lo razona la sesión de Claude Code, no un subagente.
---

# scout

Fase 4 del flujo `/strata` (**Scout**). Buscas papers nuevos relevantes para el
proyecto, los rankeas y los dejas como candidatos para que el usuario apruebe o
rechace. Esto lo razonas tú (la sesión de Claude Code) — no se delega a Haiku.
Tienes las tools `mcp__strata__strata_*`.

## Antes de empezar

1. `strata_get_project(project_id)` → `research_question`, `description`,
   `template`, `repo_url`.
   - Si falta `research_question` → pídesela al usuario antes de seguir.
2. Contexto para diseñar buenas queries (usa lo que haya, no es obligatorio que
   exista todo):
   - `strata_get_gap(project_id)` → el gap analysis (Fase 3), si existe.
   - `strata_get_latest_draft(project_id, section="introduction")` → la dirección
     del paper, si ya hay borrador (Fase 6).
   - `strata_list_papers(project_id)` → los **títulos ya en la biblioteca**
     (y sus `arxiv_id`) para no volver a sugerirlos.
3. `strata_set_phase(project_id, 4, "in_progress")`.

## 1. Diseñar 6 queries

A partir de `research_question` + gap + intro del draft + títulos existentes,
produce **exactamente 6 queries de búsqueda**, en inglés, terminología
académica, **máximo ~6 palabras cada una**:

- **2 técnicas** — arquitectura/sistemas/algoritmos (agentes, LLMs, métodos...).
- **2 de dominio** — el problema concreto del campo de aplicación.
- **2 de evaluación** — métricas, benchmarks, estudios con usuarios.

Evita términos que ya están bien cubiertos por los papers de la biblioteca
(busca lo que falta, no más de lo mismo). Si no tienes gap ni draft, deriva las
queries de la `research_question` y la `description`.

## 2. Buscar

Para cada query: `strata_search_arxiv(query, max_results=15)`. Junta los `hits`,
**deduplica** entre queries (por `arxiv_id`, o por DOI/URL, o por título
normalizado). *(La búsqueda en Semantic Scholar es v1 — cuando exista
`strata_search_semantic_scholar`, búscala también y deduplica entre fuentes; si
una fuente da 429 persistente, degrada a la otra. Por ahora solo arXiv.)*

## 3. Filtrar y rankear

- Descarta los que **ya estén en la biblioteca** (match por `arxiv_id`/DOI o por
  título muy parecido a uno existente). Si quieres dejar constancia, puedes
  guardarlos igualmente como candidatos pero el flujo normal es simplemente no
  incluirlos.
- Descarta el **propio paper del proyecto** si aparece.
- Para cada candidato nuevo, razona un **`relevance_score` 0-10** y un
  **`relevance_reason` de UNA frase** (¿ataca el gap identificado? ¿está
  técnicamente relacionado con la pregunta? ¿reforzaría el related work?). Sé
  crítico: 0-2 tangencial, 3-4 roza el tema, 5-6 relevante de fondo, 7-8 muy
  pertinente, 9-10 imprescindible.
- Ordena por score descendente.

Guardar: `strata_save_candidates(project_id, candidates=[{title, abstract,
authors, arxiv_id, doi, url, relevance_score, relevance_reason, source}, ...])`
— `title` es obligatorio; `source` es `arxiv` (o `semantic_scholar` en v1).

## 4. Presentar al usuario

Muéstrale la lista ordenada (título, año, score, la frase de justificación, link)
y deja que decida:
- aprobar uno → `strata_approve_candidate(candidate_id, project_id)` (lo
  re-encola en la cola de ingesta — vuelve a la Fase 1, lo analizará un subagente);
- rechazar uno → `strata_reject_candidate(candidate_id)`;
- "añadir todos los de score ≥ N" → aprueba en bloque los que pasen el umbral.

Cuando el usuario termine de triar: `strata_set_phase(project_id, 4, "completed")`.
Si aprobó candidatos, recuérdale que hay ítems nuevos en la cola — al volver a
`/strata` se drenan (Fase 1) y luego conviene re-ejecutar Relevancia/Gap.

## Reglas (no negociables)

- Responde SIEMPRE en español (las `relevance_reason` también; las queries van en
  inglés a propósito, para buscar mejor).
- No inventes papers: solo entran candidatos que devuelva una búsqueda real. No
  rellenes con títulos plausibles.
- Sin emojis.
- No re-sugieras papers que ya están en la biblioteca ni el propio paper del
  proyecto.
- No llames a ningún LLM externo: el diseño de queries y el ranking son tuyos.
