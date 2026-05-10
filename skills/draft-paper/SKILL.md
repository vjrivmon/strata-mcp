---
name: draft-paper
description: Redacta el paper sección a sección (intro, related work, methodology, results, discussion, conclusion) según el template del proyecto, apoyándose en el gap analysis, la biblioteca y la introspección del repo. Citas SOLO de la biblioteca, con filtro post-generación.
---

# draft-paper

> SCAFFOLD — se destila en la Fase 6 a partir de `apps/backend/domain/agents/research_agent.py` (parte de draft) y de los fixes anti-alucinación recientes del strata actual.

Entrada: gap analysis + biblioteca + `repo_snapshot` + `template`.
Salida: el contenido de cada sección en Markdown, guardado por sección
(`strata_save_draft(section=...)`) — versiones append-only.

Reglas (no negociables):
- Responde SIEMPRE en español.
- Prohibido citar papers que no estén en la biblioteca. Tras generar, pasar un
  filtro que escanea las claves de cita contra la lista de papers y elimina /
  marca las que no existan.
- No inventes cifras, autores ni resultados. Si un dato no está en un paper de
  la biblioteca, no lo afirmes.
- Respeta el formato de citas del template (numérico vs. autor-año).
- Si no hay gap analysis, avisa y ofrece generarlo antes.
