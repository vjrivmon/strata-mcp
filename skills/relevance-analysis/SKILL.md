---
name: relevance-analysis
description: Evalúa la relevancia de cada paper de la biblioteca para un proyecto de investigación concreto, produciendo un context_analysis (contribución al proyecto, gaps que cubre y que no, score 0-10). Lo razona la sesión de Claude Code, no un subagente.
---

# relevance-analysis

Fase 2 del flujo `/strata` (**Relevancia**). Para cada paper de la biblioteca,
produces un `context_analysis` que lo conecta con la `research_question` del
proyecto y lo persistes. Esto lo razonas tú (la sesión de Claude Code) — no se
delega a Haiku. Tienes las tools `mcp__strata__strata_*`.

## Antes de empezar

1. `strata_get_project(project_id)` → mira `research_question`, `description`.
   - Si falta `research_question` → pídesela al usuario antes de seguir; sin ella
     no se puede valorar la relevancia.
2. `strata_list_papers(project_id, include_analysis=true)` → la biblioteca con
   sus Round1/Round2 y el `context` por proyecto (si ya existe para alguno).
   - Si está vacía → aborta: dile al usuario que primero hay que encolar y
     analizar papers (Fase 1).
3. `strata_set_phase(project_id, 2, "in_progress")`.

## Qué producir, paper a paper

Recorre los papers que **aún no tengan `context`** para este proyecto (o todos,
si el usuario pide re-evaluar). Para cada uno, a partir de su Round2 (`intro_summary`,
`related_work`, `methodology`, `results`, `strengths`, `limitations`,
`key_contributions`) y la `research_question`, razona:

- **`contribution_to_project`** — cómo de concreto aporta este paper a responder
  la pregunta de investigación: qué pieza da (un método, un baseline, un dato, un
  encuadre, una limitación que tú resuelves...). **Si no aporta gran cosa, dilo
  claramente** — no fuerces relevancia donde no la hay.
- **`gaps_covered`** — qué aspectos de la `research_question` quedan cubiertos o
  bien tratados por este paper (lista, frases cortas).
- **`gaps_not_covered`** — qué aspectos de la `research_question` siguen sin
  respuesta después de leer este paper (lista, frases cortas).
- **`relevance_score`** — 0-10, sé crítico y usa todo el rango:
  - 0-2: tema tangencial, casi nada que ver con la pregunta.
  - 3-4: roza el tema (un dato suelto, contexto general), poco aprovechable.
  - 5-6: relevante de fondo — baseline, trabajo relacionado, encuadre útil.
  - 7-8: directamente sobre el problema; lo citarás sí o sí en el related work.
  - 9-10: pieza central — define el SOTA que mejoras, o la limitación que tu
    trabajo ataca de frente.

Si la Round2 del paper está vacía (el ingest falló en profundidad): pon
`contribution_to_project = "No se pudo analizar el paper en profundidad (Round2 vacía)."`,
listas vacías, `relevance_score` bajo, y avisa al usuario de que ese paper
convendría reprocesarlo (re-encolarlo en Fase 1).

Persistir cada uno en cuanto esté:
`strata_save_context(paper_id=<id>, project_id=<id>, context_analysis={"contribution_to_project": "...", "gaps_covered": [...], "gaps_not_covered": [...]}, relevance_score=<0-10>)`.

## Al terminar

- Dale al usuario un resumen breve: cuántos papers valorados, los 3 más
  relevantes y los menos (con su score), y si hay algún paper con Round2 vacía
  pendiente de reprocesar.
- `strata_set_phase(project_id, 2, "completed")` y sugiere la Fase 3 (Gap),
  que cruzará todos estos `context_analysis`.

## Reglas (no negociables)

- Responde SIEMPRE en español (todos los textos del `context_analysis` también).
- No inventes: la contribución, los gaps y el score salen del Round2 del paper y
  de la `research_question` — si el Round2 no lo dice, no lo afirmes.
- Sin emojis.
- No "completes" la biblioteca aquí: si crees que faltan papers clave, dilo y
  sugiere ir luego a la Fase 4 (Scout); valora con lo que hay.
- No llames a ningún LLM externo: el razonamiento es tuyo.
