---
name: literature-review
description: Redacta el related work / estado del arte de un proyecto a partir de la biblioteca de papers — agrupa por tema, narra la evolución del campo, hace una ficha por paper significativo (resumen / qué proponen / cómo evalúan / diferencias con el proyecto) y posiciona el trabajo propio. Citas solo de la biblioteca. Lo razona la sesión de Claude Code, no un subagente.
---

# literature-review

Fase 5 del flujo `/strata` (**Lit Review**). Escribes la revisión de literatura
/ estado del arte del proyecto a partir de la biblioteca. Esto lo razonas tú
(la sesión de Claude Code, Opus/Sonnet) — no se delega a Haiku. Tienes las tools
`mcp__strata__strata_*`. Este documento alimenta luego la sección `related_work`
del draft (Fase 6).

## Antes de empezar

1. `strata_get_project(project_id)` → `name`, `description`, `research_question`,
   `template`, `repo_url`.
   - Si falta `research_question` → pídesela al usuario antes de seguir.
2. `strata_list_papers(project_id, include_analysis=true)` → la biblioteca con
   Round1/Round2 y el `context` por proyecto (la relevancia de la Fase 2).
   - Si está vacía → aborta: primero hay que poblar y analizar la biblioteca
     (Fase 1) y, idealmente, valorar relevancia (Fase 2).
3. `strata_get_gap(project_id)` si existe (Fase 3) — te ayuda a saber dónde
   encaja cada paper.
4. Si hay `repo_url`: `strata_get_repo_snapshot(project_id)` (introspección de
   repo, v1 — puede no existir aún; si no hay, sigue sin esa parte).
5. `strata_set_phase(project_id, 5, "in_progress")`.

## Qué producir

Un documento Markdown en español (≈900-1600 palabras) con esta forma:

1. **Narrativa por temas** — agrupa los papers por enfoque/sub-problema (NO por
   orden de ingesta). Para cada tema: cuenta cómo ha evolucionado el campo, qué
   se ha resuelto bien y con qué métodos/resultados, dónde están las grietas.
   Cita cada paper que menciones por su clave (ver abajo).
2. **Una ficha por paper significativo** — para los papers con `relevance_score`
   alto (o todos, si la biblioteca es pequeña), cuatro apartados cortos:
   - **Resumen propio** — 2-4 frases directas con lo esencial; incluye números si
     los hay; di qué es lo más interesante o lo más flojo del paper.
   - **Qué proponen** — con precisión técnica: arquitectura, componentes,
     configuraciones. Si no proponen sistema nuevo (usan modelos existentes con
     prompts), dilo directamente.
   - **Cómo lo evalúan** — métricas, dataset, tamaño de muestra, resultados
     numéricos concretos (QWK, accuracy, F1, lo que reporten).
   - **Diferencias con este proyecto** — mínimo 3-4 diferencias, numeradas, con
     etiqueta en mayúsculas: `1. TAREA DISTINTA: ...`, `2. SIN APRENDIZAJE: ...`.
     Cuando la limitación del paper justifique la existencia del proyecto,
     señálalo explícitamente: `N. SU LIMITACIÓN NOS JUSTIFICA: ...`.
3. **Posicionamiento** — cierra situando el trabajo propio: dado lo anterior,
   qué hueco ocupa, en qué se distingue de todo lo revisado, y *(si hay
   `repo_snapshot`)* qué de tu solución concreta lo respalda.

Al terminar: `strata_save_literature_review(project_id, content_md=<el Markdown>)`
(se guarda como versión nueva; las anteriores se conservan). Luego
`strata_set_phase(project_id, 5, "completed")` y sugiere la Fase 6 (Draft).

## Estilo de escritura

Escribe como el autor (Vicente): directo y técnico, primera persona del plural
("nuestro sistema", "lo que hacemos"), frases cortas sin relleno académico
("cabe destacar", "es importante mencionar" → fuera). Los números importan:
siempre que haya una métrica concreta, ponla. Mayúsculas para enfatizar las
diferencias. Opinión propia cuando proceda ("el punto más interesante es...",
"me parece una contribución honesta"). En español siempre.

## Citas (anti-alucinación)

- Usa una clave `[AutorAño]` por paper (p. ej. `[Vaswani2017]`), del primer autor
  + año del paper **tal como está en la biblioteca** — la misma convención que el
  draft (Fase 6).
- **Prohibido citar papers que no estén en la biblioteca.** Si quieres apoyarte
  en algo que no está, primero encólalo (Fase 1) o no lo cites. Nunca uses
  placeholders (`[Ref]`, `[cita]`, `[AutorAño]` literal).
- **Filtro post-generación obligatorio**: antes de guardar, extrae todas las
  claves `[...]` que has usado y compáralas con `strata_list_papers`. Cualquiera
  que no corresponda a un paper de la biblioteca: elimínala (deja la frase sin
  cita) o corrígela. No guardes el documento con citas colgantes.

## Reglas (no negociables)

- Responde SIEMPRE en español.
- No inventes cifras, autores, métodos ni resultados: todo dato sale de la Round2
  del paper o del `repo_snapshot`. Si no lo tienes, no lo afirmes.
- Sin emojis.
- Agrupa por tema, no por orden de ingesta.
- No llames a ningún LLM externo (ni a la API de Anthropic): la redacción es tuya.
