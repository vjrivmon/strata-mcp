---
name: relevance-analysis
description: Evalúa la relevancia de un paper para un proyecto de investigación concreto, produciendo un context_analysis (contribución al proyecto, gaps que cubre y que no, score 0-10).
---

# relevance-analysis

> SCAFFOLD — se destila en la Fase 6/v1 a partir de `apps/backend/domain/agents/context_agent.py`.

Dado un paper (su Round2) + la `research_question` del proyecto, produce:
`{ contribution_to_project, gaps_covered[], gaps_not_covered[], relevance_score (0-10) }`.

Reglas: responde SIEMPRE en español; básate en el análisis del paper, no inventes;
salida JSON estricta.
