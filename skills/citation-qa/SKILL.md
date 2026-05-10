---
name: citation-qa
description: Verifica un draft antes de exportar — cada cita existe en la biblioteca, no hay alucinación, números y afirmaciones son trazables, y el formato de referencias respeta el template. Reporta las violaciones.
---

# citation-qa

> SCAFFOLD — se destila en la Fase 6/v1 a partir de `apps/backend/domain/agents/citation_agent.py` y `qa_agent.py`.

Entrada: el último draft + la biblioteca + el `template`.
Salida: un informe de violaciones — citas a papers que no están en la biblioteca,
claves `\cite{}` sin mapeo, cifras/afirmaciones no trazables, formato de
referencias incorrecto para el template.

Reglas: responde SIEMPRE en español; no "arregla" el draft, solo reporta;
sé exhaustivo con las citas (es el último filtro anti-alucinación).
