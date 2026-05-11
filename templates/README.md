# Paper templates

One directory per supported `template` value on a strata project
(`lncs` · `ieee` · `acm` · `inted` · `generic`). Each holds:

| file             | purpose                                                                 |
|------------------|-------------------------------------------------------------------------|
| `main.tex`       | a compilable LaTeX skeleton for that venue (title/authors/abstract/sections/bibliography wired up) — `TODO:` markers show where the draft content goes |
| `references.bib` | a starter BibTeX file (header comment + one example entry to copy)      |
| `CITATION.md`    | the citation / reference-format rules for that style — which `\cite*` command, numeric vs. author-year, the `bibliographystyle`, how the reference list should look, common pitfalls |

These are used by the **Export** phase (phase 8 of `/strata`) — the draft's
sections, the library's papers and `references.bib` are assembled into `main.tex`
— and `CITATION.md` is the rubric the **`citation-qa`** skill (phase 7) checks a
draft's references against.

Nothing here pulls in the actual document class (`llncs.cls`, `IEEEtran.cls`,
`acmart.cls`, …); install those from CTAN / the venue's author kit, or build on
Overleaf which ships them. The skeletons are deliberately minimal — adapt freely.
