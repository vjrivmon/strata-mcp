---
name: citation-qa
description: Verifica un draft antes de exportar — cada cita existe en la biblioteca, no hay alucinación, números y afirmaciones son trazables, las secciones están completas y el formato de referencias respeta el template. Produce un informe de violaciones; no arregla el draft. Lo razona la sesión de Claude Code, no un subagente.
---

# citation-qa

Fase 7 del flujo `/strata` (**QA**). Repasas el último borrador del paper y
reportas todo lo que está mal antes de exportar: citas colgantes, cifras no
trazables, secciones flojas, formato de referencias incorrecto. Es el último
filtro anti-alucinación. **No arreglas el draft** — lo razonas tú (la sesión de
Claude Code) y entregas un informe; el usuario decide. Tienes las tools
`mcp__strata__strata_*`.

## Antes de empezar

1. `strata_get_project(project_id)` → `template` (lncs|ieee|acm|inted|generic),
   `research_question`, `repo_url`.
2. `strata_get_latest_draft(project_id)` → el borrador completo. Si no hay draft
   completo, reconstrúyelo de las secciones:
   `strata_get_latest_draft(project_id, section="abstract"|"introduction"|"related_work"|"methodology"|"results"|"discussion"|"conclusion")`.
   - Si no hay ningún draft → aborta: dile al usuario que primero hay que
     redactarlo (skill `draft-paper`, Fase 6).
3. `strata_list_papers(project_id, include_analysis=true)` → la biblioteca con
   Round2 (resultados, cifras, contribuciones) — la fuente de verdad de las citas.
4. `strata_get_literature_review(project_id)` y, si hay `repo_url`,
   `strata_get_repo_snapshot(project_id)` (v1; puede no existir) — contexto extra
   para juzgar trazabilidad de las cifras propias.
5. `strata_set_phase(project_id, 7, "in_progress")`.

## Qué comprobar

### A. Citas (anti-alucinación) — el bloque crítico

- Extrae del texto todas las claves de cita. El draft de `strata-mcp` usa
  `[AutorAño]` (p. ej. `[Vaswani2017]`); un draft ya exportado puede usar `[N]`
  numérico + sección de Referencias — cubre ambos.
- **Cita colgante / alucinada** (`error`): clave `[AutorAño]` que no corresponde
  a ningún paper de la biblioteca (`strata_list_papers`). También: placeholders
  literales en el texto final (`[Ref]`, `[cita]`, `[AutorAño]` sin sustituir).
- **Cita numérica sin entrada en Referencias** (`error`, solo drafts numerados):
  `[N]` en el texto sin su `[N] ...` en la sección de Referencias.
- **Referencia que no se cita** (`warning`): entrada en Referencias que nunca
  aparece en el texto.
- **Referencia no verificable** (`warning`): entrada de Referencias cuyo título/
  autor/año no coincide con ningún paper de la biblioteca.
- **Uso incoherente de la cita** (`warning`): la clave se usa en un contexto que
  no tiene relación con lo que ese paper realmente trata o demuestra (compáralo
  con su Round2). Revisa con especial cuidado las primeras citas de cada sección.

### B. Trazabilidad de cifras y afirmaciones

- **Cifra ajena no trazable** (`error`): un número atribuido a un paper (p. ej.
  "X alcanza 86,5% en FEVER `[Yao2023]`") que no aparece en los `results` del
  Round2 de ese paper. Si la cifra no está en ningún Round2, es alucinación.
- **Cifra propia sin respaldo** (`error`): un resultado presentado como del
  proyecto que no sale del `repo_snapshot` (capa de benchmarks/métricas) ni de
  ningún sitio. Si no hay `repo_snapshot`, marca todas las cifras propias como
  "no verificables aquí" (`warning`) y dilo.
- **Afirmación que pide cita y no la tiene** (`warning`): frases tipo "se ha
  demostrado que...", "según estudios recientes", "many studies have shown",
  porcentajes sueltos sin fuente.

### C. Estructura y formato

- **Sección vacía o ausente** (`error`): falta `abstract`, `introduction` o
  `conclusion`, o están casi vacías (<~50 chars de contenido real).
- **Sección demasiado corta** (`warning`): una sección requerida con
  <~300 caracteres de contenido.
- **Abstract mal formado** (`warning`): <~100 palabras o >~300 palabras; o lleva
  citas / subcabeceras (debe ser un párrafo, sin `[...]`).
- **Pocas citas** (`warning`): <5 citas en todo el paper (un trabajo académico
  suele tener bastantes más).
- **Placeholders / lenguaje de relleno** (`warning`): `XXX`, `TODO`, `FIXME`,
  `PENDING`, "cabe destacar que", etc.
- **Formato de referencias incorrecto para el template** (`warning`): IEEE/ACM →
  citas numéricas `[N]` y lista de Referencias numerada; LNCS/INTED → autor-año.
  En el draft Markdown basta con que las claves `[AutorAño]` sean consistentes;
  señala desajustes evidentes (mezcla de estilos, numeración rota).

### D. Coherencia global

- Contradicciones entre secciones (p. ej. el abstract promete algo que los
  resultados no muestran).
- Conclusiones que no derivan de los resultados presentados.
- Saltos bruscos de tema, secciones que no conectan, contenido claramente
  incoherente o inventado.

## El informe

Entrega un Markdown en español con:

1. **Veredicto** — `LISTO PARA EXPORTAR` (0 errores) o `BLOQUEADO` (≥1 error) +
   conteo de errores y avisos.
2. **Errores** (bloquean la exportación) — tabla: tipo · ubicación (sección /
   cita) · qué pasa · qué habría que hacer.
3. **Avisos** (no bloquean, pero conviene mirarlos) — misma tabla.
4. **Resumen de citas** — nº de claves usadas, nº mapeadas a la biblioteca, nº
   colgantes; lista de claves colgantes.

**No edites el draft.** Si el usuario quiere arreglar algo, que vuelva a la
Fase 6 (skill `draft-paper`) para regenerar la sección afectada — las versiones
son append-only. Solo cuando el informe salga limpio (0 errores) sugiere
`strata_set_phase(project_id, 7, "completed")` y pasar a la Fase 8 (Export).

## Reglas (no negociables)

- Responde SIEMPRE en español.
- Sé exhaustivo con las citas y las cifras: es el último filtro anti-alucinación;
  mejor un falso positivo que dejar pasar una cita inventada.
- No inventes problemas para rellenar el informe: solo violaciones reales y
  concretas, cada una con su ubicación.
- No arregles el draft — solo repórtalo.
- Sin emojis.
- No llames a ningún LLM externo: el análisis es tuyo.
