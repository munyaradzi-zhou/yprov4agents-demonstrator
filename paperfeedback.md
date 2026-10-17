# Review: "Capture, Not Retrieve: Behavioural Provenance for Agentic AI"

Reviewed as for a top-tier venue, against `paper/tex/main.tex` as of commit `704a308`.

**Recommendation as it stands: weak reject.** The idea is good and a revision could get in.

## Summary of the submission

The paper argues that automatic provenance capture (callbacks, spans, queries after the run) can record *what* an agentic system did but not *why*, because the rule behind a decision is computed locally and never passed anywhere. To support this, it offers:

- a six-agent demonstrator that records its own W3C PROV;
- a callback baseline built from the same run;
- a measure of which facts in each justification the baseline could see;
- the `yp:` OWL ontology with SHACL shapes;
- measurements of the cost in time, bytes and energy.

## Strengths

1. **A clear thesis that's easy to remember.** "Capture, not retrieve" is a good title and a good idea. Separating the facts a decision used from the rule that combined them is the right distinction.
2. **An honest baseline.** Giving the baseline every declared structural fact, so it is an upper bound, is good method, and saying so builds trust.
3. **Unusually careful measurement.** The paper uses interleaved conditions, the 3× spread rule, says which verdicts flipped between occasions, labels energy as `partly-measured`, and reports when energy can't be attributed to a step. Most systems papers do none of this.
4. **Reproducibility.** Archives are frozen from clean commits with per-file hashes, and there are 246 tests on two operating systems.
5. **A real, published resource.** The ontology has SHACL shapes, and a test checks in both directions that every term the code emits is defined and every defined term is used.

## Major concerns (these decide acceptance)

### M1. The central claim is close to true by definition, and the experiment confirms what was built in

A sceptical reviewer will say: "A callback can't see a function's local variables. Of course it can't recover the rule. The 0 of 19 result was guaranteed by how the baseline was built."

The claim is also stated too broadly. The quote block says "*no* callback, span or post-hoc query" can retrieve the rule. But fine-grained tracing (noWorkflow's variable-level mode, `sys.settrace`, dynamic program slicing) *does* see the computed values. What it doesn't recover is the *meaning* of the rule.

*Fix:*

- Scope the claim to observers that sit at a framework or function boundary.
- State it formally: a decision function `d(x) = v`, the set of things an observer at a boundary can see, and recoverability defined as a property. Present it as a proposition, with the demonstrator as evidence for it.
- Add at least one stronger baseline so the boundary is measured rather than asserted:
  - **(a)** line-level tracing, which recovers the values but not the rule, at a cost you can measure;
  - **(b)** an LLM given A's full trace and asked to reconstruct each justification, scored against the real one. Reviewers will ask for this specifically.

### M2. Nothing checks that the justifications are faithful

The paper cites research showing a model's chain of thought may not reflect how it reached its answer (turpin2023, lanham2023). But its own justifications are free text written by a developer, and nothing checks them against the code either. An unfaithful hand-written string is as weak as model text that may not match the process.

*Fix:* Make the justification structured, with fields such as `yp:usedValue`, `yp:rule` and `yp:threshold`, and generate it from the same variables the decision reads. Then add a SHACL or test check that the cited values match the recorded inputs. This turns the API's "one hand-written call" from a weakness into a contribution. It also gives the fact measure a principled ground truth instead of a regex.

### M3. The decisions being explained are not LLM decisions

CA1, CA2, CA3, MC1 and MC3 decide in deterministic code. Only CAx uses an LLM, and even there a code check decides the verdict.

In agentic AI as most readers mean it, the *LLM* chooses the action. In that case the "rule" isn't a local computation in code at all. The paper has to say what "capture" means then (the prompt, tool schema and parsed decision, plus a code-side policy check?). Otherwise the title promises more than the evaluation covers.

### M4. The study is small and its validity outside this setup is weak

It uses two input records, 24 documents and one machine. The authors wrote both the agents and the baseline, and no agent framework is used. The paper presents "no agent framework" as evidence of portability, but a reviewer will read it as never having been tested on a real framework.

*Fix:*

- Port at least the operational agents to LangGraph, and capture Strategy A with that framework's real callback or OpenTelemetry instrumentation rather than a hand-built one.
- Use at least an order of magnitude more input records.
- Ideally, add one agent written by someone else.

### M5. The fact metric hasn't been validated

The facts are extracted with a regex the authors designed. The caption itself admits "seeing a fact over-counts knowing it". With 49 facts in total, a top venue will want:

- precision and recall of the extractor against a hand-labelled set;
- agreement between two people labelling the facts;
- confidence intervals.

The meta row (16 of 16) adds nothing. Drop it or explain why it's there.

### M6. No evidence that the justifications are useful

The paper lists this as a limitation, but that won't satisfy a top venue. The minimum is a diagnosis task. For example, give the operator (a human, or a panel of LLM judges as a proxy) CA1 escalations with only A's record versus B's record, and measure correct diagnosis and time taken. The example of corrupted storage versus a consumer being down is already a ready-made task.

### M7. "No difference" claims need equivalence tests

- *"Not resolvable at 3× its spread"* is not the same as *"no cost"*. Use an equivalence test (TOST) with a stated margin, or report effect sizes with 95% confidence intervals. The energy result (12 rounds, +20% at 2.3×) is underpowered, and a reviewer will say so.
- Put the overhead in context. A 1.5 ms write against an LLM call of 1 to 5 s is the comparison readers care about. Show the end-to-end session cost including model latency.

### M8. The three contributions don't fit together tightly

The reflexive meta agents (C2) don't support the thesis (C1). What claim does reflexivity prove? Either connect it ("governance agents can only judge *why* because B recorded it; under A, MC1 is blind") or cut it to a sentence.

Also pick a framing. As written it sits between three kinds of paper:

- a position paper with an evaluation;
- a systems paper;
- an ontology resource paper.

The resource-track framing would need competency questions, SPARQL queries, FAIR metadata and a DOI.

## Missing related work (a reviewer will flag these)

- **Decision provenance:** Singh, Cobbe and Norval, IEEE Access 2019. This is the closest conceptual prior work and must be cited and distinguished.
- **Plans in PROV:** P-Plan (Garijo and Gil) and ProvONE. The Receive/Plan/Act/…/Decide lifecycle overlaps with these.
- **Explanation ontologies:** the Explanation Ontology (Chari et al., ISWC 2020).
- **Explainable BDI and multi-agent agents:** for example Winikoff and colleagues. That community has recorded the *reasons* behind agents' actions for years.
- **Design rationale and argumentation models:** IBIS and AIF, which are possible representations for `yp:justification`.
- **Flowcept:** the PROV-AGENT authors' capture system. Also AgentOps among the observability tools.
- **EU AI Act:** cite specific articles (Art. 12 record-keeping, Art. 13 transparency, Art. 86 right to explanation), not the whole regulation.

Check each citation before adding it. The three `% VERIFY` entries (yProv4ML, PROV-AGENT, CodeCarbon) still need venues and DOIs.

## Minor issues and presentation

1. `main.tex:38`: the `TODO(authors)` comment is still above the abstract. Remove it.
2. **The abstract and §5.1 contradict each other.** The abstract quotes "all 324 relations", and §5.1 says "the relation total is the wrong number to quote … the structural claim is 228". Use 228 in the abstract, or don't quote a relation count there.
3. **Lab-notebook details belong in an appendix or the artifact:**
   - "We first concluded the counter was broken" (§5.3);
   - "74 confident, wrong edges of 250";
   - "96 of 96 activities recorded zero duration".

   They're interesting, but they read as a development diary and use up space.
4. **Repository paths in the body** (`experiments/audit-reference`): replace them with a Zenodo DOI and add an "Artifact availability" paragraph.
5. **Table 1 is a feature table you scored yourselves, where you win.** Reviewers distrust these. Define each criterion in the caption, cite the systems in each row, and replace "partly" with what exactly is partial.
6. **Agent names are unclear.** CAx, CA1–CA3, MC1 and MC3: why "x", and where is MC2? Use role names (Summariser, Charter, Analyst, Archiver, Compliance, Auditor), or explain the scheme once.
7. Write "3x" as `$3\times$` throughout, in the text, in Table 4 and in the margins.
8. **Table 4 leaves out condition A** (1.47 J), although the comparison of A versus B is the thesis. Add it.
9. **Cut or shrink §5.6, Verdict reachability.** It reports test coverage, not a research result. It's the first thing to cut if the paper is over six pages.
10. **Name the strategies consistently.** "Strategy A/B", "callback baseline" and "explicit capture" are all used. Choose one pair of names and use it everywhere.
11. **Make Fig. 2 and Fig. 3 one column wide if possible.** Three `figure*` floats are expensive in a six-page IEEE paper.
12. **Turning hyphenation off everywhere** (`\hyphenpenalty=10000` together with `\sloppy`) will leave visible gaps in justified two-column text. Check the PDF. If lines look loose, let LaTeX hyphenate at line ends and keep the "no dashes" rule only for the prose itself.
13. **The quote block** that states the thesis is unusual. It's fine for a position paper. Once M1's formal statement exists, the proposition should replace it.
14. **The conclusion slogan** ("storage for explainability, not speed for explainability") only holds if M7's equivalence result supports it.

## Questions for the authors

1. What fraction of the justification could an LLM reconstruct from A's trace alone?
2. How would `decide()` work when an LLM chooses the verdict?
3. What stops a developer from writing a justification that doesn't match the code?
4. Does the 61% / 0-of-19 result hold on a real framework's instrumentation and on more than two records?

## Ranked plan to get to a top paper

| Priority | Action | Fixes |
|---|---|---|
| 1 | Formal statement of the thesis, scoped to boundary observers | M1 |
| 2 | Structured, machine-checked justification fields | M2, M5 |
| 3 | An LLM-reconstruction baseline, plus a line-level tracing baseline | M1 |
| 4 | Port to LangGraph with its real callbacks or OpenTelemetry; more records | M3, M4 |
| 5 | A diagnosis task comparing A and B (human or LLM-judge proxy) | M6 |
| 6 | Equivalence tests, and overhead shown relative to LLM latency | M7 |
| 7 | Cite decision provenance, P-Plan, ProvONE and the Explanation Ontology | Related work |
| 8 | Cleanup: TODO, 324 vs 228, ×, Table 4 missing A, names, artifact DOI | Minor |

Items 7 and 8 are text changes only. Items 1 and 2 are mostly writing plus a small library change. Items 3 to 6 need new experiments. Item 3, and item 5 if it uses an LLM judge, need paid LLM calls, so they need approval first.

`main.tex` is generated from `paper/DRAFT.md` by `paper/build_tex.py`. Edits go into `DRAFT.md`, otherwise the next rebuild overwrites them.

## Progress

| Item | Status | Where |
|---|---|---|
| 1 | open | |
| 2 | open | |
| 3 | open | |
| 4 | open | |
| 5 | open | |
| 6 | open | |
| 7 | open | |
| 8 | open | |
