---
name: analyze-paper
description: Analiza un paper académico en dos rondas (triage Round1 + análisis profundo Round2) con salida JSON estricta y reglas anti-alucinación. Pensada para subagentes Haiku que drenan la cola de ingesta de strata-mcp.
---

# analyze-paper

> SCAFFOLD — el contenido completo se destila en la Fase 6 a partir de los
> prompts de `apps/backend/domain/agents/round1_agent.py` y `round2_agent.py`
> del strata actual.

## Qué hace

Dado el texto de un paper (`raw_text_truncated` para Round1; secciones concretas
para Round2), produce dos JSON:

- **Round1** (triage rápido): `{ bullets[], relevance_score (0-10), worth_reading (bool), summary_es }`
- **Round2** (análisis profundo): `{ intro_summary, related_work, methodology, results, strengths, limitations, key_contributions }`

## Reglas (no negociables)

- Responde SIEMPRE en español.
- Solo afirma lo que está en el texto. Cita sección/página cuando des un dato o cifra. No inventes números, autores ni resultados.
- Salida = JSON válido y nada más (sin texto antes/después, sin bloques `<think>`).
- Round1 y Round2 se guardan por separado: si Round2 falla, Round1 ya está persistido.

## Modos de ejecución

- **Plan A** (el subagente tiene acceso a las tools `mcp__strata__strata_*`): el
  subagente hace `strata_dequeue_paper` → `strata_fetch_paper_text` → análisis →
  `strata_save_paper` + `strata_save_paper_analysis` → `strata_mark_ingested`.
- **Plan B** (no las tiene): el subagente recibe el texto en el prompt y
  DEVUELVE los dos JSON; el orquestador (sesión principal) hace los `strata_*`.

(Cuál aplica se decide en el spike de la Fase 5/6 — ver `next_steps` de APEX.)
