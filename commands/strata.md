---
description: /strata -- flujo de investigación académica asistida por fases (Setup -> Biblioteca -> Relevancia -> Gap -> Scout -> Lit Review -> Draft -> QA -> Export -> Iteración), espejo de /apex. Usa el servidor MCP strata-mcp.
---

# /strata -- Flujo de investigación académica por fases

> SCAFFOLD — el flujo completo se escribe en las Fases 6-8 (espejo estructural
> de `/apex`). De momento esto documenta las fases; las instrucciones detalladas
> por sub-paso llegan cuando core + adapters + tools MCP existan.

## Paso cero

`ToolSearch("strata")` para cargar las tools `mcp__strata__strata_*`. Si no
aparecen, avisar de que hay que registrar el servidor (`scripts/install.sh`).

## Detección

- `strata_get_status` → ¿existe `.strata/strata.db` en el cwd? ¿qué proyectos
  hay? ¿en qué fase está cada uno?
- Si no existe → Fase 0 (Setup). Si existe → continuar desde la fase pendiente.

## Las fases

| # | Fase | Qué hace | Quién razona |
|---|---|---|---|
| 0 | Setup | Definir proyecto: research question, dominio, template, repo_url opcional. Crear `.strata/`. | Claude (mini-socrático) |
| 1 | Biblioteca | Encolar papers (URL/DOI/PDF/local). Subagentes Haiku drenan la cola: fetch texto → Round1 → Round2 → guardar. Batch (`STRATA_INGEST_BATCH`). | Subagentes Haiku (skill `analyze-paper`) |
| 2 | Relevancia | Por paper × proyecto: `context_analysis`. | Claude (skill `relevance-analysis`) |
| 3 | Gap analysis | Cruzar todos los Round2 + context + research question (+ repo) → gap estructurado. | Claude (skill `gap-analysis`) |
| 4 | Scout | 6 queries → buscar arXiv + Semantic Scholar → rankear 0-10 → guardar candidatos. Aprobar → re-encola a Fase 1. | Claude (skill `scout`) |
| 5 | Lit Review | Redactar related work / estado del arte desde la biblioteca. | Claude (skill `literature-review`) |
| 6 | Draft | Redactar el paper sección a sección. Citas SOLO de la biblioteca + filtro post-generación. | Claude (skill `draft-paper`) |
| 7 | QA | Verificar cada cita, anti-alucinación, formato de refs según template. | Claude (skill `citation-qa`) |
| 8 | Export | Generar `.tex`/`.md` final con bibliografía según template. | Claude |
| 9 | Iteración | Añadir papers, regenerar secciones, re-scout. | — |

## Reglas

- Sin emojis en ningún output.
- El razonamiento pesado (gap, draft, lit review, ranking del scout, QA) lo hace
  la sesión de Claude Code (Opus/Sonnet). El análisis masivo de papers lo hacen
  subagentes Haiku vía la cola. strata-mcp no lleva LLM.
- Conventional commits si el directorio de investigación es un repo.
- Las fases son secuenciales; no preguntar "cuál primero".
