"""Typeset ``paper/DRAFT.md`` as an IEEEtran conference paper under ``paper/tex/``.

    python -m paper.build_tex            # writes paper/tex/main.tex and copies the figures

The Markdown draft is where the argument has been written and checked against the archives;
this turns it into something a TeX engine can lay out, so that "does it fit six pages" is
answered by a page count rather than a words-per-page estimate. It handles exactly the
constructs the draft uses — headings, paragraphs, emphasis, inline code, pipe tables with
their captions, figure placeholders, one block quote and one code listing — and fails
loudly on anything else, rather than passing it through as text a reader would have to find.

Once the paper moves to Overleaf, the ``.tex`` becomes the source and this script has done
its job. Until then, regenerate rather than hand-edit ``main.tex``: an edit made there is
lost on the next build, and an edit made in the draft is not.

What the draft does not contain is written in :data:`FRONT_MATTER` and ``refs.bib`` and is
marked for the authors: the abstract, the author block, and bibliography entries whose
details have not been checked against their sources.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

PAPER = Path(__file__).resolve().parent
DRAFT = PAPER / "DRAFT.md"
OUT = PAPER / "tex"

#: Figure number in the draft -> rendered file. Each is drawn at one IEEE column (about
#: 3.45 in), so it sits in a column at its natural size and its text stays readable.
FIGURES = {
    "1": "db_architecture_column",
    "2": "f5_a_versus_b",
    "3": "f9_where_the_overhead_is",
}

#: The reference list, keyed as the draft cites it (``[@key]``). Written into ``main.tex``
#: itself rather than a separate ``.bib``, so a pasted ``main.tex`` compiles with its
#: references on its own; entries are numbered in order of first citation, as IEEE requires.
#: Entries marked ``% VERIFY`` name the right work but have a field not yet checked.
REFERENCES = {
    "provdm": r"L.~Moreau and P.~Missier, Eds., ``PROV-DM: The PROV data model,'' W3C "
    r"Recommendation, Apr. 2013.",
    "provo": r"T.~Lebo, S.~Sahoo, and D.~McGuinness, Eds., ``PROV-O: The PROV ontology,'' "
    r"W3C Recommendation, Apr. 2013.",
    "euaiact": r"European Parliament and Council, ``Regulation (EU) 2024/1689 laying down "
    r"harmonised rules on artificial intelligence (Artificial Intelligence Act),'' "
    r"\emph{Official Journal of the European Union}, 2024.",
    "noworkflow": r"J.~F. Pimentel, L.~Murta, V.~Braganholo, and J.~Freire, ``noWorkflow: A "
    r"tool for collecting, analyzing, and managing provenance from Python scripts,'' "
    r"\emph{Proc. VLDB Endowment}, vol.~10, no.~12, pp. 1841--1844, 2017.",
    "yesworkflow": r"T.~McPhillips \emph{et al.}, ``YesWorkflow: A user-oriented, "
    r"language-independent tool for recovering workflow information from scripts,'' "
    r"\emph{Int. J. Digital Curation}, vol.~10, no.~1, pp. 298--313, 2015.",
    "spade": r"A.~Gehani and D.~Tariq, ``SPADE: Support for provenance auditing in "
    r"distributed environments,'' in \emph{Proc. ACM/IFIP/USENIX Middleware}, 2012, "
    r"pp. 101--120.",
    "camflow": r"T.~Pasquier \emph{et al.}, ``Practical whole-system provenance capture,'' "
    r"in \emph{Proc. ACM Symp. Cloud Computing (SoCC)}, 2017, pp. 405--418.",
    "mlflow": r"M.~Zaharia \emph{et al.}, ``Accelerating the machine learning lifecycle "
    r"with MLflow,'' \emph{IEEE Data Eng. Bull.}, vol.~41, no.~4, pp. 39--45, 2018.",
    "yprov4ml": r"G.~Padovani, V.~Anantharaj, and S.~Fiore, ``yProv4ML: Effortless "
    r"provenance tracking for machine learning systems,'' \emph{SoftwareX}, vol.~31, "
    r"art.~102298, 2025.",
    "freire2008": r"J.~Freire, D.~Koop, E.~Santos, and C.~T. Silva, ``Provenance for "
    r"computational tasks: A survey,'' \emph{Computing in Science \& Engineering}, vol.~10, "
    r"no.~3, pp. 11--21, 2008.",
    "herschel2017": r"M.~Herschel, R.~Diestelk\"{a}mper, and H.~Ben~Lahmar, ``A survey on "
    r"provenance: What for? What form? What from?'' \emph{The VLDB Journal}, vol.~26, "
    r"no.~6, pp. 881--906, 2017.",
    "react": r"S.~Yao \emph{et al.}, ``ReAct: Synergizing reasoning and acting in language "
    r"models,'' in \emph{Proc. Int. Conf. Learning Representations (ICLR)}, 2023.",
    "autogen": r"Q.~Wu \emph{et al.}, ``AutoGen: Enabling next-gen LLM applications via "
    r"multi-agent conversation,'' arXiv:2308.08155, 2023.",
    "langgraph": r"LangChain, ``LangGraph,'' 2024. [Online]. Available: "
    r"\url{https://github.com/langchain-ai/langgraph}",
    "otelgenai": r"OpenTelemetry, ``Semantic conventions for generative AI systems,'' 2025. "
    r"[Online]. Available: \url{https://opentelemetry.io/docs/specs/semconv/gen-ai/}",
    "openllmetry": r"Traceloop, ``OpenLLMetry,'' 2025. [Online]. Available: "
    r"\url{https://github.com/traceloop/openllmetry}",
    "langfuse": r"Langfuse, ``Langfuse: Open-source LLM engineering platform,'' 2025. "
    r"[Online]. Available: \url{https://langfuse.com}",
    "langsmith": r"LangChain, ``LangSmith,'' 2025. [Online]. Available: "
    r"\url{https://www.langchain.com/langsmith}",
    "provagent": r"R.~Souza \emph{et al.}, ``PROV-AGENT: Unified provenance for tracking AI "
    r"agent interactions in agentic workflows,'' in \emph{Proc. IEEE 21st Int. Conf. "
    r"e-Science}, 2025, pp. 467--473.",
    "pplan": r"D.~Garijo and Y.~Gil, ``Augmenting PROV with plans in P-PLAN: Scientific "
    r"processes as linked data,'' in \emph{Proc. 2nd Int. Workshop Linked Science (LISC)}, "
    r"CEUR Workshop Proceedings, vol.~951, 2012.",
    "singh2019": r"J.~Singh, J.~Cobbe, and C.~Norval, ``Decision provenance: Harnessing data "
    r"flow for accountable systems,'' \emph{IEEE Access}, vol.~7, pp. 6562--6574, 2019.",
    "chari2020": r"S.~Chari \emph{et al.}, ``Explanation ontology: A model of explanations "
    r"for user-centered AI,'' in \emph{Proc. Int. Semantic Web Conf. (ISWC)}, LNCS, "
    r"vol.~12507, 2020, pp. 228--243.  % VERIFY pages",
    "winikoff2022": r"M.~Winikoff, G.~Sidorenko, V.~Dignum, and F.~Dignum, ``Why bad coffee? "
    r"Explaining BDI agent behaviour with valuings (extended abstract),'' in \emph{Proc. "
    r"Int. Joint Conf. Artificial Intelligence (IJCAI)}, 2022, pp. 5782--5786.",
    "doshivelez2017": r"F.~Doshi-Velez and B.~Kim, ``Towards a rigorous science of "
    r"interpretable machine learning,'' arXiv:1702.08608, 2017.",
    "modelcards": r"M.~Mitchell \emph{et al.}, ``Model cards for model reporting,'' in "
    r"\emph{Proc. Conf. Fairness, Accountability, and Transparency (FAT*)}, 2019, "
    r"pp. 220--229.",
    "datasheets": r"T.~Gebru \emph{et al.}, ``Datasheets for datasets,'' \emph{Commun. ACM}, "
    r"vol.~64, no.~12, pp. 86--92, 2021.",
    "turpin2023": r"M.~Turpin, J.~Michael, E.~Perez, and S.~R. Bowman, ``Language models "
    r"don't always say what they think: Unfaithful explanations in chain-of-thought "
    r"prompting,'' in \emph{Advances in Neural Information Processing Systems (NeurIPS)}, "
    r"2023.",
    "lanham2023": r"T.~Lanham \emph{et al.}, ``Measuring faithfulness in chain-of-thought "
    r"reasoning,'' arXiv:2307.13702, 2023.",
    "chan2024": r"A.~Chan \emph{et al.}, ``Visibility into AI agents,'' in \emph{Proc. ACM "
    r"Conf. Fairness, Accountability, and Transparency (FAccT)}, 2024.",
    "strubell2019": r"E.~Strubell, A.~Ganesh, and A.~McCallum, ``Energy and policy "
    r"considerations for deep learning in NLP,'' in \emph{Proc. ACL}, 2019, pp. 3645--3650.",
    "henderson2020": r"P.~Henderson \emph{et al.}, ``Towards the systematic reporting of the "
    r"energy and carbon footprints of machine learning,'' \emph{J. Mach. Learn. Res.}, "
    r"vol.~21, no.~248, pp. 1--43, 2020.",
    "codecarbon": r"B.~Courty, V.~Schmidt, S.~Luccioni \emph{et al.}, ``mlco2/codecarbon,'' "
    r"version 3.3.1, Zenodo, 2025. [Online]. Available: "
    r"\url{https://github.com/mlco2/codecarbon}  % VERIFY the v3.3.1 Zenodo DOI",
    "shacl": r"H.~Knublauch and D.~Kontokostas, Eds., ``Shapes Constraint Language "
    r"(SHACL),'' W3C Recommendation, Jul. 2017.",
    "pyshacl": r"RDFLib contributors, ``pySHACL,'' 2025. [Online]. Available: "
    r"\url{https://github.com/RDFLib/pySHACL}",
}

UNICODE = {
    "—": "---",
    "–": "--",
    "…": r"\ldots{}",
    "×": r"$\times$",
    "µ": r"\textmu{}",
    "−": r"$-$",
    "±": r"$\pm$",
    "³": r"$^3$",
    "§": r"\S",
}

FRONT_MATTER = r"""\documentclass[conference]{IEEEtran}
\IEEEoverridecommandlockouts
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{textcomp}
\usepackage{cite}
\usepackage{amsmath}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{listings}
\usepackage{xcolor}
\usepackage[hidelinks]{hyperref}
\lstset{basicstyle=\ttfamily\scriptsize, columns=fullflexible, keepspaces=true,
  frame=tb, aboveskip=4pt, belowskip=4pt, showstringspaces=false,
  commentstyle=\color{gray}, breaklines=true, breakatwhitespace=true}
% Tables are exactly one column wide: the first column wraps, the others size to content.
\newcolumntype{L}{>{\raggedright\arraybackslash}X}
\setlength{\tabcolsep}{6pt}
% No word is split across a line end: hyphenation off, with \sloppy spacing absorbing
% the lines that would otherwise overrun.
\hyphenpenalty=10000
\exhyphenpenalty=10000
\sloppy

\begin{document}

\title{Capture, Not Retrieve:\\ Behavioural Provenance for Agentic AI}

\author{\IEEEauthorblockN{Munyaradzi Zhou}
\IEEEauthorblockA{University of Trento\\ Trento, Italy\\ munyaradzizhou11@gmail.com}
\and
\IEEEauthorblockN{Sandro Fiore}
\IEEEauthorblockA{University of Trento\\ Trento, Italy\\ sandro.fiore@unitn.it}}

\maketitle

\begin{abstract}
Provenance tooling can record what an agentic AI system did; it cannot record why. We argue
that the gap is structural. A decision applies a rule to values it computed, and from outside
the step neither is identifiable: no callback, span or post-hoc query can retrieve what was
never captured. We make the argument measurable with a reflexive multi-agent demonstrator
(four operational agents emitting typed W3C PROV about themselves, two meta agents governing
that output), a deliberately generous callback-level baseline, a line tracer inside the step,
and LangChain's own callback handler on a LangGraph port. Over 24 real yProv4ML training runs,
the baseline reconstructs every activity, lineage relation and verdict, yet of the 432 values
the agents' decision rules read it saw 24, each a field of the input record. Our capture
records each decision as a named, versioned rule applied to its inputs, so every decision
replays from its own record and an edited verdict does not. Given what the baseline
observed, gpt-4o reconstructed the reason for 1 of 143 decisions; an operator panel chose
the cause of a decision right in
53\% of trials from the baseline's record and 100\% from ours. We publish the \texttt{yp:}
vocabulary as an OWL ontology with SHACL shapes, and bound the cost: building the record is
equivalent to no provenance within 1\,ms, while writing it adds about a millisecond per
record and 16\% CPU energy per session.
\end{abstract}

\begin{IEEEkeywords}
provenance, W3C PROV, agentic AI, explainability, multi-agent systems, SHACL
\end{IEEEkeywords}
"""

BACK_MATTER = r"""
\end{document}
"""


# ── inline text ───────────────────────────────────────────────────────────────────
def _escape(text: str) -> str:
    """Escape LaTeX's special characters, then map the draft's non-ASCII ones."""
    out = []
    for char in text:
        if char == "\\":
            out.append(r"\textbackslash{}")
        elif char in "&%$#_{}":
            out.append("\\" + char)
        elif char == "~":
            out.append(r"\textasciitilde{}")
        elif char == "^":
            out.append(r"\textasciicircum{}")
        else:
            out.append(UNICODE.get(char, char))
    return "".join(out)


def _code(text: str) -> str:
    """Inline code: escaped, typewriter, and breakable after the punctuation identifiers use."""
    escaped = _escape(text)
    for mark in (r"\_", ".", "/", ":"):
        escaped = escaped.replace(mark, mark + r"\allowbreak{}")
    return r"\texttt{" + escaped + "}"


def inline(text: str) -> str:
    """Markdown inline markup to LaTeX: code first, so nothing inside it is reinterpreted."""
    spans: list[str] = []

    def hold(match: re.Match[str]) -> str:
        spans.append(_code(match.group(1)))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", hold, text)

    # Inline math passes through untouched, held like code so escaping cannot reach it.
    def math(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"\$[^$]+\$", math, text)
    cites: list[str] = []

    def cite(match: re.Match[str]) -> str:
        keys = [key.strip().lstrip("@") for key in match.group(1).split(";")]
        missing = [key for key in keys if key not in REFERENCES]
        if missing:
            raise ValueError(f"no reference entry for {missing}")
        cites.append("~\\cite{" + ",".join(keys) + "}")
        return f"\x02{len(cites) - 1}\x02"

    text = re.sub(r"\s*\[@([^\]]+)\]", cite, text)
    text = _escape(text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\\textbf{\\emph{\1}}", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", text)
    text = re.sub(r"\\S(\d+(?:\.\d+)?)", lambda m: f"Section~\\ref{{sec:{m.group(1)}}}", text)
    text = re.sub(r"\(Table (\d)\)", r"(Table~\\ref{tab:\1})", text)
    text = re.sub(r"\bTable (\d)\b(?!\.)", r"Table~\\ref{tab:\1}", text)
    text = re.sub(r"\bFigure (\d)\b", r"Fig.~\\ref{fig:\1}", text)
    text = re.sub(r"\bSection (\d(?:\.\d)?)\b", r"Section~\\ref{sec:\1}", text)
    text = re.sub(r'"([^"]+)"', r"``\1''", text)
    if "*" in text.replace(r"\*", ""):
        raise ValueError(f"unconverted emphasis marker in: {text[:80]}")
    text = re.sub("\x02(\\d+)\x02", lambda m: cites[int(m.group(1))], text)
    return re.sub("\x00(\\d+)\x00", lambda m: spans[int(m.group(1))], text)


# ── blocks ────────────────────────────────────────────────────────────────────────
def _blocks(markdown: str) -> list[list[str]]:
    """Split into blocks on blank lines, keeping a fenced code block whole."""
    blocks: list[list[str]] = []
    current: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            current.append(line)
            continue
        if not line.strip() and not fenced:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _heading(line: str) -> str:
    match = re.match(r"(#{2,3}) (\d+(?:\.\d+)?)\.? (.+)", line)
    if not match:
        raise ValueError(f"heading without a number: {line}")
    level, number, title = match.groups()
    command = "section" if level == "##" else "subsection"
    return f"\\{command}{{{inline(title)}}}\\label{{sec:{number}}}"


def _table(rows: list[str], caption: str) -> str:
    """A pipe table and its caption, as a float. Captions go above tables in IEEE style."""
    cells = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
    header, rule, body = cells[0], cells[1], cells[2:]

    def align(marker: str) -> str:
        if marker.startswith(":") and marker.endswith(":"):
            return "c"
        return "r" if marker.endswith(":") else "l"

    # One column wraps (L = ragged-right X) and absorbs the spare width, so every table is
    # exactly \columnwidth and never spills into the neighbouring column. It is the column
    # with the most text: wrapping a column of short names instead crams the long one.
    text_columns = [i for i, marker in enumerate(rule) if align(marker) == "l"] or [0]
    widest = max(text_columns, key=lambda i: sum(len(row[i]) for row in [header, *body]))
    spec = "".join("L" if i == widest else align(marker) for i, marker in enumerate(rule))
    number = re.match(r"\*\*Table (\d)\.\*\*", caption)
    if not number:
        raise ValueError(f"a table must be followed by its caption: {caption[:60]}")
    text = caption[number.end() :].strip()
    lines = [
        r"\begin{table}[t]",
        r"\centering\footnotesize",
        f"\\caption{{{inline(text)}}}\\label{{tab:{number.group(1)}}}",
        f"\\begin{{tabularx}}{{\\columnwidth}}{{{spec}}}",
        r"\toprule",
        " & ".join(inline(cell) for cell in header) + r" \\",
        r"\midrule",
        *(" & ".join(inline(cell) for cell in row) + r" \\" for row in body),
        r"\bottomrule",
        r"\end{tabularx}",
        r"\end{table}",
    ]
    return "\n".join(line for line in lines if line)


def _figure(lines: list[str]) -> str:
    text = " ".join(line.removeprefix(">").strip() for line in lines)
    match = re.match(r"\*\*Figure (\d)\.\*\* (.+)", text)
    if not match or match.group(1) not in FIGURES:
        raise ValueError(f"unknown figure: {text[:60]}")
    number, caption = match.groups()
    return "\n".join(
        [
            r"\begin{figure}[t]",
            r"\centering",
            f"\\includegraphics[width=\\columnwidth]{{figures/{FIGURES[number]}.pdf}}",
            f"\\caption{{{inline(caption)}}}\\label{{fig:{number}}}",
            r"\end{figure}",
        ]
    )


def convert(markdown: str) -> str:
    """The draft's body, from its first numbered section, as LaTeX."""
    body = markdown[markdown.index("## 1. ") :]

    out: list[str] = []
    blocks = _blocks(body)
    index = 0
    while index < len(blocks):
        block = blocks[index]
        first = block[0]
        if first.startswith("#"):
            out.append(_heading(first))
            rest = block[1:]
            if rest:
                out.append("\\noindent " + inline(" ".join(rest)))
        elif first == "---":
            pass
        elif first.startswith("```"):
            code = "\n".join(block[1:-1])
            out.append("\\begin{lstlisting}[language=Python]\n" + code + "\n\\end{lstlisting}")
        elif first.startswith("|"):
            caption = " ".join(blocks[index + 1])
            out.append(_table(block, caption))
            index += 1
        elif first.startswith("> **Figure"):
            out.append(_figure(block))
        elif first.startswith(">"):
            quote = " ".join(line.removeprefix(">").strip() for line in block)
            out.append("\\begin{quote}\n" + inline(quote) + "\n\\end{quote}")
        elif re.match(r"^(\d+\.|-) ", first):
            raise ValueError(f"lists are not handled; the draft has one at: {first[:60]}")
        else:
            out.append("\\noindent " + inline(" ".join(line.strip() for line in block)))
        index += 1

    tex = "\n\n".join(out)
    return tex + "\n\n" + bibliography(tex)


def bibliography(tex: str) -> str:
    """The reference list, in order of first citation, as a ``thebibliography`` block."""
    order: list[str] = []
    for group in re.findall(r"\\cite\{([^}]+)\}", tex):
        for key in group.split(","):
            if key not in order:
                order.append(key)
    unused = sorted(set(REFERENCES) - set(order))
    if unused:
        print(f"[tex] note: reference entries not cited: {unused}")
    items = "\n".join(f"\\bibitem{{{key}}} {REFERENCES[key]}" for key in order)
    return (
        "\\begin{thebibliography}{"
        + str(len(order))
        + "}\n"
        + items
        + "\n\\end{thebibliography}"
    )


def main() -> int:
    OUT.mkdir(exist_ok=True)
    tex = FRONT_MATTER + "\n" + convert(DRAFT.read_text(encoding="utf-8")) + "\n" + BACK_MATTER
    (OUT / "main.tex").write_text(tex, encoding="utf-8", newline="\n")
    figures = OUT / "figures"
    figures.mkdir(exist_ok=True)
    for name in FIGURES.values():
        shutil.copyfile(PAPER / "figures" / f"{name}.pdf", figures / f"{name}.pdf")
    print(f"[tex] wrote {OUT / 'main.tex'} and {len(FIGURES)} figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
