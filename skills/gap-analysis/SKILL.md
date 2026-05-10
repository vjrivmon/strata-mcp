---
name: gap-analysis
description: Cruza los análisis Round2 de toda la biblioteca + los context_analysis + la research question (+ introspección del repo si existe) para producir un gap analysis estructurado en Markdown que sitúa el trabajo propio. Lo razona la sesión de Claude Code (Opus/Sonnet), no un subagente.
---

# gap-analysis

Genera el **análisis de gaps de investigación** de un proyecto de `strata-mcp`.
Esto lo razonas tú (la sesión de Claude Code) — no se delega a Haiku. Tienes las
tools `mcp__strata__strata_*`.

## Antes de empezar

1. `strata_get_project(project_id)` → mira `research_question`, `template`, `repo_url`.
   - Si falta `research_question` → pídesela al usuario antes de seguir.
2. `strata_list_papers(project_id, include_analysis=true)` → la biblioteca con
   sus Round1/Round2 y los `context` por proyecto.
   - Si está vacía → aborta: dile al usuario que primero hay que encolar y
     analizar papers (Fase 1 del flujo `/strata`).
3. Si hay `repo_url`: `strata_get_repo_snapshot(project_id)` (puede no existir
   aún — la introspección de repo es v1; si no hay, sigue sin esa sección).

## Qué producir

Un documento Markdown (≈500-900 palabras) con cabeceras, en español, que cubra:

1. **Panorama del campo** — qué problema abordan los papers de la biblioteca,
   agrupados por enfoque/tema (no por orden de ingesta).
2. **Estado del arte** — qué se ha resuelto bien, con qué métodos, con qué
   resultados (cita los papers concretos por su clave, ver abajo).
3. **Limitaciones y gaps** — qué falta, qué no funciona, qué contradicciones hay
   entre trabajos. Apóyate en los `limitations` de los Round2 y en los
   `gaps_not_covered` de los `context_analysis`.
4. **El hueco que llena este proyecto** — dado `research_question`, dónde encaja
   el trabajo propio: qué gap concreto ataca y por qué nadie lo ha cubierto.
5. *(solo si hay `repo_snapshot`)* **Tu solución propuesta** — resume las capas
   del repo (README/estructura/pendientes, agentes y responsabilidades,
   benchmarks y métricas, datasets/configs) y conéctalas con el gap. No vuelques
   código en crudo.

Al terminar: `strata_save_gap(project_id, content_md=<el Markdown>)` — se guarda
como una versión nueva (las anteriores se conservan). Luego
`strata_set_phase(project_id, 3, "completed")`.

## Citas

Usa una clave `[AutorAño]` para cada paper que menciones (p. ej. `[Vaswani2017]`),
derivada del primer autor + año del paper. **Solo papers que estén en la
biblioteca** — si quieres citar algo que no está, primero encólalo (Fase 1) o no
lo cites. No inventes claves ni uses placeholders como `[Ref]`, `[cita]`.

## Reglas (no negociables)

- Responde SIEMPRE en español.
- No inventes cifras, autores, métodos ni resultados: todo dato sale de un paper
  de la biblioteca (su Round2) o del `repo_snapshot`. Si no lo tienes, no lo afirmes.
- Sin emojis.
- No "completes" la biblioteca tú: si crees que faltan papers clave, dilo y
  sugiere ir a la Fase 4 (Scout), pero genera el gap con lo que hay.
