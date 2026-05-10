---
name: gap-analysis
description: Cruza los análisis Round2 de toda la biblioteca + los context_analysis + la research question (+ introspección del repo si existe) para producir un gap analysis estructurado que sitúa el trabajo propio.
---

# gap-analysis

> SCAFFOLD — se destila en la Fase 6 a partir de `apps/backend/domain/agents/research_agent.py` (parte de gap).

Entrada: todos los Round2 + context_analysis + `research_question` (+ `repo_snapshot`).
Salida: un gap analysis en Markdown — qué se ha hecho, qué falta, dónde está el
hueco que el proyecto llena. Si hay repo: añade la sección "TU SOLUCIÓN PROPUESTA"
con los datos en capas del repo.

Reglas: responde SIEMPRE en español; si la biblioteca está vacía, aborta y pide
encolar/analizar papers primero; si falta `research_question`, pídela antes de
generar; cita solo papers de la biblioteca.
