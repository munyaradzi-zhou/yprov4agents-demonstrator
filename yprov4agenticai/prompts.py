"""
prompts.py — versioned prompt registry for the NL_Summarizer agent
==================================================================
Single source of truth for every instruction prompt the agent sends to the LLM.

WHY THIS EXISTS
---------------
1. Reproducibility. A prompt is part of the experimental apparatus: change the
   wording and you change the output distribution. Keeping prompts here, each
   with a ``version`` and a content ``sha``, means any run can record *exactly*
   which prompt produced it — and a reviewer can diff two versions.

2. On-theme provenance. This is a provenance library, so we practise what we
   preach: every LLM-bearing activity in the PROV-JSON is stamped with the
   prompt's id / version / sha (see ``Prompt.provenance``). The graph therefore
   records not just *what* the agent did but *under which prompt contract* — a
   detail Strategy-A callbacks and other frameworks do not capture.

   Until Phase 4 that was an aspiration: this module existed and nothing
   imported it, while the two LLM-bearing agents built their prompts as inline
   f-strings (review finding F2). The consumers are now ``runtime/llm.py``,
   which requires a registered :class:`Prompt` rather than a string, and the
   two agents that call it — so a prompt cannot reach a model without its
   identity reaching the record.

HOW TO EVOLVE A PROMPT
----------------------
Edit the ``text``, bump the ``version`` (semver-ish: major = behavioural change,
minor = wording tweak), and add a one-line ``changelog`` note. The ``sha`` is
derived automatically, so a forgotten version bump is still detectable by hash.

The faithfulness hardening (grounding rules in plan/act, a faithfulness-only
evaluator, word-count-as-ceiling) is recorded here as the v1 -> v2 transition.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    """An immutable, versioned, content-addressed prompt."""

    id: str  # stable key, e.g. "act.system"
    version: str  # bump on ANY text change
    text: str
    changelog: str = ""

    @property
    def sha(self) -> str:
        """First 12 hex chars of the SHA-256 of the text — a stable content id."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:12]

    def provenance(self) -> dict:
        """PROV attribute dict to stamp onto the activity that used this prompt."""
        return {
            "yp:prompt_id": self.id,
            "yp:prompt_version": self.version,
            "yp:prompt_sha": self.sha,
        }


# ════════════════════════════════════════════════════════════════════════════
#  The prompts (system instructions for the three LLM-bearing lifecycle nodes)
# ════════════════════════════════════════════════════════════════════════════

PLAN_SYSTEM = Prompt(
    id="plan.system",
    version="2.0",
    changelog="v2: ground the plan strictly in present fields; forbid assuming "
    "datasets/models/dates/hardware not in the records.",
    text=(
        "You are NL_Summarizer, a provenance summariser. You will be given provenance "
        "records as JSON. Plan a summary grounded STRICTLY in those records.\n"
        "Base the plan only on fields actually present in the records — do not assume "
        "datasets, models, frameworks, dates, hardware, or metrics that are not there.\n"
        "Reply in 2 sentences: (1) which concrete facts present in the records you will "
        "highlight, (2) the structure you will use."
    ),
)

ACT_SYSTEM = Prompt(
    id="act.system",
    version="2.0",
    changelog="v2: full faithfulness contract (records-only, omit-absent, copy-exactly) "
    "and word count reframed as a CEILING not a target — the v1 '100-word' "
    "target was causing the model to pad sparse inputs with invented facts.",
    text=(
        "You are NL_Summarizer. Write a natural-language provenance summary using ONLY "
        "the information in the provided records.\n"
        "STRICT FAITHFULNESS RULES:\n"
        "- Use only facts explicitly present in the records. Do NOT add datasets, model "
        "names, optimizers, hyperparameters, dates, hardware, or metrics that are not there.\n"
        "- If a detail is absent, omit it — never guess or fill in from typical ML examples.\n"
        "- Copy names, numbers, and identifiers exactly as written; do not contradict any value.\n"
        "- Length is a CEILING of ~100 words, not a target: if the records are sparse, write "
        "less. Never pad to reach a word count.\n"
        "Write plain prose for a non-expert, with no preamble."
    ),
)

OBSERVE_SYSTEM = Prompt(
    id="observe.system",
    version="2.1",
    changelog="v2.1: fix false-positive bias. The v2.0 'strict evaluator + always quote a "
    "flagged claim' framing manufactured objections — it failed faithful "
    "paraphrases (e.g. 'the dataset labeled train_dataset/Training' when that "
    "string is literally in the records) and even miscounted records ('only three "
    "models' when ten were present). v2.1 flips the default to PASS, treats "
    "rewording/grouping/hedging of present facts as faithful, and requires the "
    "judge to quote the record value it searched for and could NOT find BEFORE it "
    "may FAIL — which kills invented objections while keeping real fabrications "
    "(specifics absent from the records) caught.",
    text=(
        "You are a provenance-summary faithfulness checker. Judge ONLY whether the summary "
        "invents information that is NOT in the input records.\n"
        "DEFAULT TO PASS. A summary is faithful as long as every concrete specific it names "
        "(dataset, model, file, number, date, metric, identifier) appears somewhere in the "
        "records — even if reworded, reordered, grouped, or summarised. Treat ALL of these "
        "as faithful, NOT as errors:\n"
        "- Paraphrase and natural phrasing of a value that is present (e.g. 'the dataset "
        "labeled X' when the string X appears in the records).\n"
        "- Grouping or hedging like 'and others' / 'several models' when the records do in "
        "fact contain more such items than the summary lists.\n"
        "- Omitting details, reordering, or describing structure. Do NOT penalise omissions "
        "or reward extra detail. Ignore style and completeness.\n"
        "FAIL ONLY when the summary states a specific (a dataset/model/number/date/metric) "
        "that does NOT appear anywhere in the records, or contradicts a value that does.\n"
        "Before you may answer FAIL you MUST first search the records for the value in "
        "question and quote the record text you checked; if you cannot quote where it "
        "should be and show it is absent or contradicted, answer PASS.\n"
        "Do not apply outside world knowledge. Reply on one line: PASS or FAIL, then one "
        "sentence. If FAIL, name the invented specific and the record search that came up empty."
    ),
)

# ════════════════════════════════════════════════════════════════════════════
#  DEEP variant (opt-in via YPROV_DEPTH) — adds a COVERAGE objective on top of
#  faithfulness so the summary surfaces the salient provenance STRUCTURE that is
#  present in the records (activity phases, wasDerivedFrom lineage / versioning
#  chains, notable or anomalous choices, resource/energy) instead of a generic,
#  faithful-but-shallow paraphrase. Faithfulness rules are carried over verbatim:
#  coverage rewards describing structure that IS present, never inventing facts.
#  A deterministic provenance digest (see mvp._prov_digest) is prepended to the
#  plan/act user message so depth is achievable WITHOUT guessing.
# ════════════════════════════════════════════════════════════════════════════

PLAN_DEEP = Prompt(
    id="plan.system.deep",
    version="1.0",
    changelog="v1: coverage-aware planning. Plan around the salient provenance STRUCTURE "
    "in the records (phases + ordering, derivation/versioning lineage, notable/"
    "anomalous choices, resources), still grounded strictly in present fields.",
    text=(
        "You are NL_Summarizer, a provenance summariser. You are given provenance records "
        "as JSON, plus a DIGEST of their structure. Plan a summary grounded STRICTLY in the "
        "records that surfaces what is INTERESTING about this run's provenance, not just its "
        "surface facts.\n"
        "Plan to cover, ONLY where the records actually support it: (1) the distinct activity "
        "phases and their order; (2) any derivation or versioning lineage — artifacts derived "
        "from one another, model checkpoints/versions (wasDerivedFrom); (3) any notable or "
        "anomalous choice evidenced in the records — an unusual loss, a conspicuously absent "
        "metric, duplicated/renamed activities; (4) resource/energy facts if present.\n"
        "Do not assume datasets, models, dates, hardware, or metrics that are not there.\n"
        "Reply in 2-3 sentences: which of these structural facts (present in the records) you "
        "will highlight, and the structure you will use."
    ),
)

ACT_DEEP = Prompt(
    id="act.system.deep",
    version="1.0",
    changelog="v1: same strict faithfulness contract as act.system v2.0, but the length "
    "framing is reframed from 'write less' to 'cover the salient structure the plan "
    "identified' (lineage, phases, anomalies, resources) — grounded, never invented.",
    text=(
        "You are NL_Summarizer. Write a natural-language provenance summary using ONLY the "
        "information in the provided records (a structure DIGEST is included to help you find "
        "the salient parts).\n"
        "STRICT FAITHFULNESS RULES (unchanged):\n"
        "- Use only facts explicitly present in the records. Do NOT add datasets, model names, "
        "optimizers, hyperparameters, dates, hardware, or metrics that are not there.\n"
        "- If a detail is absent, omit it — never guess or fill in from typical ML examples.\n"
        "- Copy names, numbers, and identifiers exactly as written; do not contradict any value.\n"
        "COVERAGE (what makes a summary good, not just safe):\n"
        "- Surface the run's STRUCTURE, not only its settings: the activity phases and their "
        "order, any derivation/versioning lineage (checkpoints or versioned artifacts derived "
        "from one another), and any notable or anomalous choice the records actually show "
        "(e.g. an unusual loss function, a metric that is conspicuously absent, "
        "duplicated/renamed activities). Mention resource/energy facts if present.\n"
        "- Every such statement must still be supported by the records. Covering more STRUCTURE "
        "is not license to invent SPECIFICS.\n"
        "Length is a ceiling of ~160 words. Write plain prose for a non-expert, no preamble."
    ),
)

OBSERVE_DEEP = Prompt(
    id="observe.system.deep",
    version="1.0",
    changelog="v1: two-axis judge = faithfulness (as observe.system v2.1) AND coverage. FAILs a "
    "faithful-but-shallow summary that omits salient structure DEMONSTRABLY present in "
    "the records, using the same quote-the-record discipline so coverage objections "
    "cannot be manufactured. This is what makes the reflexion loop actually iterate.",
    text=(
        "You are a provenance-summary checker with TWO criteria: FAITHFULNESS and COVERAGE. "
        "PASS only if BOTH hold.\n"
        "FAITHFULNESS — FAIL if the summary states a specific (dataset/model/file/number/date/"
        "metric/identifier) that does NOT appear anywhere in the records, or contradicts a value "
        "that does. Paraphrase, grouping ('several models'), reordering, and omission of detail "
        "are all faithful — do not flag them here.\n"
        "COVERAGE — the summary must surface the SALIENT PROVENANCE STRUCTURE that IS present: "
        "(a) the distinct activity phases; (b) any derivation/versioning lineage actually in the "
        "records — checkpoints or versioned artifacts derived from one another (wasDerivedFrom); "
        "(c) any clearly notable/anomalous choice the records evidence — an unusual loss, a "
        "conspicuously absent expected metric, duplicated/renamed activities. FAIL for coverage "
        "ONLY if the summary omits such a structure that is demonstrably present, and you MUST "
        "quote the record evidence of that present-but-omitted structure. Never demand detail "
        "the records do not contain.\n"
        "Before you may answer FAIL you MUST quote the record text that justifies it: for "
        "faithfulness, the specific you searched for and could not find; for coverage, the "
        "present structure the summary skipped. If you cannot quote it, answer PASS.\n"
        "Reply on one line: PASS or FAIL, then one sentence naming either the invented specific "
        "or the present-but-omitted structure with its record evidence."
    ),
)


# ── Registry: id -> Prompt ───────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
#  Analytics narration (CA3). The findings are computed before the model is
#  called and are passed in; the model's job is commentary, never arithmetic.
# ════════════════════════════════════════════════════════════════════════════

NARRATE_FINDINGS = Prompt(
    id="analytics.narrate",
    version="1.0",
    changelog="v1: lifted verbatim from the inline f-string in ca3_analytics._narrate so "
    "the prompt that drove a narration is recorded with it (todo.md F2). The "
    "anomaly-versus-completeness distinction is in the prompt because it is the "
    "distinction the agent's verdict rests on: an anomaly says the run behaved "
    "unexpectedly, a completeness gap says the producer recorded less than it "
    "could have, and conflating them made ANOMALY the verdict for every graph.",
    text=(
        "You are CA3, a provenance analytics agent. Write 3-4 sentences of "
        "root-cause commentary on these COMPUTED findings. Do not invent numbers; "
        "use only what is given. Distinguish anomalies, which indicate the run "
        "behaved unexpectedly, from completeness gaps, which indicate the "
        "producer recorded less than it could have."
    ),
)

PROMPTS: dict[str, Prompt] = {
    p.id: p
    for p in (
        PLAN_SYSTEM,
        ACT_SYSTEM,
        OBSERVE_SYSTEM,
        PLAN_DEEP,
        ACT_DEEP,
        OBSERVE_DEEP,
        NARRATE_FINDINGS,
    )
}


def get(prompt_id: str) -> Prompt:
    """Look up a prompt by id (raises KeyError if unknown — fail loud)."""
    return PROMPTS[prompt_id]


if __name__ == "__main__":
    # `python -m yprov4agenticai.prompts` prints the registry (versions/shas).
    print(f"{'id':<24} {'version':<8} {'sha':<14} chars")
    print("-" * 60)
    for pid, p in PROMPTS.items():
        print(f"{pid:<24} {p.version:<8} {p.sha:<14} {len(p.text)}")
