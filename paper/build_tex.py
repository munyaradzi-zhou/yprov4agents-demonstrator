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

#: Figure number in the draft -> rendered file. All are drawn ~6.8 in wide, so all span both
#: columns: scaled to one column, their text would be unreadable at print size.
FIGURES = {
    "1": "f1_two_layers",
    "2": "f4_two_escalations",
    "3": "f9_where_the_overhead_is",
}

#: Where a name in the draft should carry a citation, cited at its first mention only.
CITATIONS = (
    ("W3C PROV", "prov-dm"),
    ("yProv4ML", "yprov4ml"),
    ("PROV-AGENT", "provagent"),
    ("AER; Vispute et al.", "aer"),
    ("OpenTelemetry's GenAI conventions", "otel-genai"),
    ("OpenLLMetry", "openllmetry"),
    ("Langfuse", "langfuse"),
    ("LangSmith", "langsmith"),
    ("EU AI Act", "euaiact"),
    ("SHACL", "shacl"),
    ("codecarbon", "codecarbon"),
    ("NATS", "nats"),
    ("`pyshacl`", "pyshacl"),
)

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
\usepackage{listings}
\usepackage{xcolor}
\usepackage[hidelinks]{hyperref}
\lstset{basicstyle=\ttfamily\scriptsize, columns=fullflexible, keepspaces=true,
  frame=tb, aboveskip=4pt, belowskip=4pt, showstringspaces=false,
  commentstyle=\color{gray}}

\begin{document}

\title{Capture, Not Retrieve:\\ Behavioural Provenance for Agentic AI}

% TODO(authors): names, affiliations and e-mail addresses.
\author{\IEEEauthorblockN{Author One}
\IEEEauthorblockA{Affiliation\\ email@example.org}
\and
\IEEEauthorblockN{Author Two}
\IEEEauthorblockA{Affiliation\\ email@example.org}}

\maketitle

% TODO(authors): this abstract was drafted for the typeset version and has not been
% reviewed. Every figure in it is quoted from the body.
\begin{abstract}
Provenance tooling can record what an agentic AI system did; it cannot record why. We argue
that this gap is structural: the facts a decision used may cross a step boundary, but the rule
that combined them into a verdict is a local computation, and no callback, span or post-hoc
query can retrieve what was never captured. We make the argument measurable with a reflexive
multi-agent demonstrator --- four operational agents emitting typed W3C PROV about themselves
and two meta agents governing that output --- and a deliberately generous callback-level
baseline over the same run. The baseline reconstructs every activity, all 324 relations and
every verdict, and sees 61\% of the facts the operational justifications cite, but none of the
values the agents computed in order to decide. We publish the \texttt{yp:} vocabulary as an
OWL ontology with SHACL shapes, and measure the cost: building the record is inside every
operational agent's run-to-run variance, justifications are 6.5\% of the records' bytes, and
measured CPU energy per session shows no resolvable difference.
\end{abstract}

\begin{IEEEkeywords}
provenance, W3C PROV, agentic AI, explainability, multi-agent systems, SHACL
\end{IEEEkeywords}
"""

BACK_MATTER = r"""
\bibliographystyle{IEEEtran}
\bibliography{refs}

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
    text = _escape(text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\\textbf{\\emph{\1}}", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", text)
    text = re.sub(r"\\S(\d+(?:\.\d+)?)", lambda m: f"Section~\\ref{{sec:{m.group(1)}}}", text)
    text = re.sub(r"\(Table (\d)\)", r"(Table~\\ref{tab:\1})", text)
    text = re.sub(r"\bTable (\d)\b(?!\.)", r"Table~\\ref{tab:\1}", text)
    text = re.sub(r"\bFigure (\d)\b", r"Fig.~\\ref{fig:\1}", text)
    text = re.sub(r'"([^"]+)"', r"``\1''", text)
    if "*" in text.replace(r"\*", ""):
        raise ValueError(f"unconverted emphasis marker in: {text[:80]}")
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
    spec = "".join("r" if align.endswith(":") else "l" for align in rule)
    number = re.match(r"\*\*Table (\d)\.\*\*", caption)
    if not number:
        raise ValueError(f"a table must be followed by its caption: {caption[:60]}")
    text = caption[number.end() :].strip()
    wide = len(header) >= 5
    lines = [
        r"\begin{table}[t]",
        r"\centering\footnotesize",
        f"\\caption{{{inline(text)}}}\\label{{tab:{number.group(1)}}}",
        r"\resizebox{\columnwidth}{!}{%" if wide else "",
        f"\\begin{{tabular}}{{{spec}}}",
        r"\toprule",
        " & ".join(inline(cell) for cell in header) + r" \\",
        r"\midrule",
        *(" & ".join(inline(cell) for cell in row) + r" \\" for row in body),
        r"\bottomrule",
        r"\end{tabular}" + ("}" if wide else ""),
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
            r"\begin{figure*}[t]",
            r"\centering",
            f"\\includegraphics[width=\\textwidth]{{figures/{FIGURES[number]}.pdf}}",
            f"\\caption{{{inline(caption)}}}\\label{{fig:{number}}}",
            r"\end{figure*}",
        ]
    )


def convert(markdown: str) -> str:
    """The draft's body, from its first numbered section, as LaTeX."""
    body = markdown[markdown.index("## 1. ") :]
    for phrase, key in CITATIONS:
        if phrase not in body:
            raise ValueError(f"citation anchor not found in the draft: {phrase!r}")
        body = body.replace(phrase, f"{phrase}\x01{key}\x01", 1)

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
                out.append(inline(" ".join(rest)))
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
            out.append(inline(" ".join(line.strip() for line in block)))
        index += 1

    tex = "\n\n".join(out)
    return re.sub("\x01([\\w-]+)\x01", lambda m: f"~\\cite{{{m.group(1)}}}", tex)


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
