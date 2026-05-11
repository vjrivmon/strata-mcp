---
description: /strata -- flujo de investigación académica asistida por fases (Setup -> Biblioteca -> Relevancia -> Gap -> Scout -> Lit Review -> Draft -> QA -> Export -> Iteración), espejo de /apex. Usa el servidor MCP strata-mcp; Claude Code es el modelo que razona.
---

# /strata — Flujo de investigación académica por fases

Conduces un proyecto de investigación (un paper o TFG) por fases, igual que
`/apex` conduce un proyecto de software. **strata-mcp guarda y trae datos; tú
razonas.** El análisis masivo de papers se delega a subagentes Haiku; el
razonamiento pesado (gap, draft, lit review, ranking del scout, QA) lo haces tú
en la sesión (Opus/Sonnet).

## Paso cero

1. `ToolSearch("strata")` para cargar las tools `mcp__strata__strata_*`. Si no
   aparecen → avisa de que hay que registrar el servidor (`scripts/install.sh`
   del repo strata-mcp) y reiniciar Claude Code; para aquí.
2. `strata_init_project()` — asegura `./.strata/strata.db`. Si devuelve `ok:false`
   → muestra el error (BD corrupta, falta FTS5, disco lleno...) y para.
3. `strata_get_status()` — ¿qué proyectos hay y en qué fase está cada uno?
   - Sin proyectos → Fase 0 (Setup).
   - Hay proyectos → pregunta cuál (o usa el único) y continúa **desde la
     primera fase no completada**. Las fases son secuenciales; no preguntes
     "¿cuál primero?".

## Las fases

| # | Fase | Qué hace | Quién razona | Tools / skill |
|---|---|---|---|---|
| 0 | Setup | Definir el proyecto. | Claude (mini-socrático) | `strata_create_project` |
| 1 | Biblioteca | Encolar papers y drenar la cola con subagentes Haiku. | Subagentes Haiku | skill `analyze-paper` |
| 2 | Relevancia | `context_analysis` por paper × proyecto. | Claude | skill `relevance-analysis`, `strata_save_context` |
| 3 | Gap | Cruzar Round2 + context + research_question (+ repo) → gap. | Claude | skill `gap-analysis`, `strata_save_gap` |
| 4 | Scout | Diseñar queries → buscar arXiv → rankear → candidatos. | Claude | skill `scout`, `strata_search_arxiv`, `strata_save_candidates` |
| 5 | Lit Review | Related work / estado del arte desde la biblioteca. | Claude | skill `literature-review`, `strata_save_literature_review` |
| 6 | Draft | Redactar el paper sección a sección. | Claude | skill `draft-paper`, `strata_save_draft` |
| 7 | QA | Verificar citas, anti-alucinación, formato de refs. | Claude | skill `citation-qa` |
| 8 | Export | Generar `.tex`/`.md` final con bibliografía según template. | Claude | `strata_get_latest_draft` + escribir archivo |
| 9 | Iteración | Añadir papers, regenerar secciones, re-scout. | — | — |

Marca cada fase con `strata_set_phase(project_id, N, "in_progress")` al empezarla
y `"completed"` al cerrarla; eso es lo que `strata_get_status` lee para saber
dónde retomar.

### Fase 0 — Setup

Mini-socrático breve (3-5 preguntas, no más): ¿de qué va el proyecto? ¿cuál es
la **research question** exacta? ¿dominio/subcampo? ¿qué **template** (LNCS, IEEE,
ACM, INTED, generic)? ¿hay un **repo** asociado (`repo_url`)? Con eso:
`strata_create_project(name, description, research_question, template, repo_url)`.
Guarda el `id` devuelto — es el `project_id` de aquí en adelante.
`strata_set_phase(project_id, 0, "completed")`, `strata_set_phase(project_id, 1, "in_progress")`.

### Fase 1 — Biblioteca

1. Pregunta al usuario qué papers añadir: ids/URLs de arXiv, URLs de PDF, rutas a
   PDFs locales. `strata_queue_papers(project_id, urls=[...], hint="arxiv"|"pdf"|None)`
   — los duplicados (por URL normalizada) contra ítems aún activos se ignoran.
2. **Drenar la cola con subagentes Haiku** (la herencia de MCP en subagentes
   está confirmada — Plan A). Mira `strata_queue_status(project_id)`; si hay
   `pending`, lanza un lote de subagentes con la **Task tool, `model="haiku"`,
   en paralelo** (tantos como ítems pendientes, hasta un máximo razonable —
   `STRATA_INGEST_BATCH`, por defecto ~5). A cada subagente dale **la skill
   `analyze-paper`** como instrucción y un `worker_id` único. Cada subagente:
   `strata_dequeue_paper` → `strata_fetch_and_stage(queue_id)` (deja el `raw_text`
   grande en el servidor, no lo arrastra) → Round1 + Round2 sobre `raw_text_truncated`
   → `strata_save_paper(..., from_queue_id=queue_id)` → `strata_save_paper_analysis(round1=...)`
   → `strata_save_paper_analysis(round2=...)` → `strata_mark_ingested`. Si un paper
   falla → `strata_mark_failed(queue_id, error, permanent=true)` para fallos duros
   (404, no es un paper, PDF escaneado), o sin `permanent` si parece transitorio.
3. "Poco a poco": si quedan ítems `pending` tras el lote, dile al usuario cuántos
   y que con volver a invocar `/strata` se drena el siguiente lote. La cola
   persiste entre sesiones.
4. Cuando `pending == 0 && processing == 0` y hay al menos unos cuantos papers
   en la biblioteca (`strata_list_papers`): `strata_set_phase(project_id, 1, "completed")`.

### Fases 2-5 (resumen)

- **2 Relevancia**: aplica la skill **`relevance-analysis`** — para cada paper,
  `{contribution_to_project, gaps_covered[], gaps_not_covered[], relevance_score}`
  a partir de su Round2 + la `research_question`, y `strata_save_context(...)`.
- **3 Gap**: aplica la skill **`gap-analysis`**. Termina en `strata_save_gap`.
- **4 Scout**: aplica la skill **`scout`** *(de momento solo arXiv; Semantic
  Scholar y la introspección de repo llegan en v1)*. `strata_search_arxiv` →
  rankea → `strata_save_candidates`. El usuario aprueba/rechaza:
  `strata_approve_candidate` (re-encola a Fase 1) / `strata_reject_candidate`.
- **5 Lit Review**: aplica la skill **`literature-review`** — related work /
  estado del arte desde la biblioteca → `strata_save_literature_review`.

### Fase 6 — Draft

Aplica la skill **`draft-paper`**: sección a sección (`introduction`,
`related_work`, `methodology`, `results`, `discussion`, `conclusion`, y por
último `abstract`), cada una con `strata_save_draft(section=...)`. Filtro
anti-alucinación obligatorio: solo citas a papers de la biblioteca. Al terminar,
`strata_set_phase(project_id, 6, "completed")`.

### Fases 7-9 (resumen)

- **7 QA**: aplica la skill **`citation-qa`** — revisa el último draft contra la
  biblioteca y el template; reporta citas colgantes, cifras no trazables, secciones
  flojas, formato incorrecto. No "arregles" el draft, repórtalo (el usuario decide;
  para corregir, se vuelve a la Fase 6 a regenerar la sección).
- **8 Export**: `strata_get_latest_draft(project_id)` → conviértelo al formato
  del template (refs numéricas IEEE/ACM, autor-año LNCS/INTED) y escribe el
  `.tex`/`.md` final en el directorio del proyecto, con su bibliografía.
- **9 Iteración**: encolar papers nuevos (vuelta a Fase 1), regenerar secciones,
  re-scout. Las versiones de gap/lit-review/draft son append-only.

## Reglas

- **Sin emojis** en ningún output.
- strata-mcp no lleva LLM ni API key: el cerebro eres tú (Opus/Sonnet) y los
  subagentes Haiku para el análisis masivo. No introduzcas Ollama/VRAIN ni
  llames a la API de Anthropic.
- Si el directorio del proyecto es un repo git: conventional commits en los
  cambios significativos (el `.tex` exportado, etc.).
- Las fases son secuenciales. Marca `strata_set_phase` al empezar y al cerrar
  cada una.
- Anti-alucinación en todo lo que se redacta (gap, lit review, draft): solo se
  cita lo que está en la biblioteca; no se inventan cifras, autores ni resultados.
