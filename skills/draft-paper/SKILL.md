---
name: draft-paper
description: Redacta el paper sección a sección (abstract, introduction, related_work, methodology, results, discussion, conclusion) según el template del proyecto, apoyándose en el gap analysis, la biblioteca y la introspección del repo. Citas SOLO de la biblioteca, con filtro post-generación. Lo razona la sesión de Claude Code, no un subagente.
---

# draft-paper

Redacta el borrador de un paper de `strata-mcp`, sección a sección. Esto lo
razonas tú (la sesión de Claude Code, Opus/Sonnet). Tienes las tools
`mcp__strata__strata_*`.

## Antes de empezar

1. `strata_get_project(project_id)` → `research_question`, `template`
   (lncs|ieee|acm|inted|generic), `repo_url`.
2. `strata_get_gap(project_id)` → el gap analysis más reciente.
   - Si no hay → avisa al usuario y ofrece generarlo (skill `gap-analysis`,
     Fase 3) antes de continuar.
3. `strata_list_papers(project_id, include_analysis=true)` → la biblioteca con
   Round2 + `context`. `strata_get_literature_review(project_id)` si existe.
4. Si hay `repo_url`: `strata_get_repo_snapshot(project_id)` (v1; si no hay,
   sigue sin la sección "solución propuesta").

## Cómo redactar

Trabaja **una sección a la vez**, en este orden, y guarda cada una en cuanto esté:

1. `introduction` — problema, motivación, contribuciones, estructura del paper.
2. `related_work` — estado del arte agrupado por tema (puedes basarte en la
   literature review si existe). Posiciona el trabajo propio.
3. `methodology` — el método/arquitectura/diseño experimental propio (apóyate en
   el `repo_snapshot` si lo hay).
4. `results` — resultados y su análisis. **Las cifras propias salen del
   `repo_snapshot` (capa de benchmarks/métricas); las ajenas, de los papers.**
   No inventes números.
5. `discussion` — interpretación, comparación con el estado del arte, amenazas a
   la validez.
6. `conclusion` — qué se aporta, limitaciones, trabajo futuro.
7. `abstract` — al final, ~150-250 palabras, UN párrafo, sin citas ni
   subcabeceras, destilando intro + método + resultados + conclusión.

Cada sección: profundidad académica real, ~400-700 palabras (el abstract menos),
Markdown, español. Para cada una:
`strata_save_draft(project_id, content_md=<sección>, section="<nombre>")`
(versiones append-only por sección).

Cuando estén todas: opcionalmente componlas en un draft completo y guárdalo con
`strata_save_draft(project_id, content_md=<todo>)` (sin `section`). Luego
`strata_set_phase(project_id, 6, "completed")` y sugiere la Fase 7 (QA).

## Citas (anti-alucinación)

- Usa una clave `[AutorAño]` por paper (p. ej. `[Vaswani2017]`), del primer
  autor + año del paper en la biblioteca.
- **Prohibido citar papers que no estén en la biblioteca.** Si una afirmación
  necesita una referencia que no tienes, escríbela **sin cita** antes que
  inventar una. Nunca uses placeholders (`[AutorAño]`, `[Ref]`, `[cita]`) en el
  texto final.
- **Filtro post-generación obligatorio**: antes de guardar cada sección, extrae
  todas las claves `[...]` que has usado y compáralas con `strata_list_papers`.
  Cualquier clave que no corresponda a un paper de la biblioteca: elimínala (deja
  la frase sin cita) o corrígela. No guardes una sección con citas colgantes.
- Respeta el estilo de citas del `template`: numérico para IEEE/ACM, autor-año
  para LNCS/INTED; en el draft Markdown basta con la clave `[AutorAño]`
  consistente — la conversión al formato final es la Fase 8 (Export).

## Reglas (no negociables)

- Responde SIEMPRE en español.
- No inventes cifras, autores, métodos ni resultados.
- Sin emojis.
- No llames a ningún LLM externo: la redacción es tuya.
