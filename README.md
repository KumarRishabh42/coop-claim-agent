# Co-op claim agent

An independent HVAC/appliance/power-equipment dealer that carries several
brands usually has co-op advertising money sitting on the table: a
manufacturer sets aside funds to reimburse part of the dealer's local ad
spend, but claiming it means reading a brand's PDF rulebook, running the ad,
collecting an invoice, a proof of payment and proof it ran, and keying all of
that into a manufacturer portal correctly enough to not get rejected over a
logo that's a fraction of an inch too small. Dealers routinely leave this
money unclaimed — industry estimates put it in the tens of billions of
dollars a year, nationally — not because the money isn't real, but because
claiming it is a slow, fiddly, error-prone paperwork exercise nobody's job
is to do well.

This is a prototype agent for that problem: point it at a manufacturer's
co-op program guide and a dealer's claim packet (the ad, the invoice, proof
of payment, the claim form), and it tells you exactly what qualifies, cites
the rule it's applying, computes what's owed, and either files the clean
stuff automatically, asks a person one targeted question, or explains
clearly why something isn't eligible. Every step — every model call, every
decision, every dollar — is logged and priced.

**Design rule:** *the model perceives, code decides.* The model's only job
is reading documents and images and returning structured facts (a bounding
box, a date, a dollar amount, a phrase it found). Every comparison, every
date calculation, every dollar amount and every final decision happens in
plain, tested Python — so the same packet always produces the same decision,
and that decision is fully explainable.

## What this proves

Given a program guide and a claim packet, the agent:

- extracts the guide's rules with a citation (section + verbatim quote) for each
- checks a draft ad *before* it runs ("pre-check"), catching fixable issues early
- checks a full claim packet — ad, invoice, proof of payment, claim form
- computes the reimbursement: exclusions, rate, and the dealer's remaining balance
- decides: file it automatically, send it to a person for approval, ask a
  targeted question, flag a missing document, or explain why it's not eligible
- keeps a funds ledger per dealer/program and a full audit log with cost per step

Six synthetic claim packets (see `SPEC.md` section 7.3) exercise every one of
those paths, and `tests/test_packets.py` checks the agent's output against
the exact expected decision and dollar amount for each — the agent's own
code never reads the "expected" files, only the tests do.

## What's mocked / simplified for this prototype

This was built as a fast, working proof of concept rather than a full
build-out of `SPEC.md` (which remains the fuller spec this was scoped down
from). Notably:

- **Synthetic ads/documents are rendered with Pillow, not Playwright.** Same
  idea (logos are colored boxes at exact, code-controlled widths, so
  violations are planted precisely) — just a lighter dependency.
- **Replay-mode fixtures are hand-authored ground truth**, not necessarily
  a live model's actual output, so `make test` and the default demo run
  fully offline, deterministically, and for free. The `agent/llm.py`
  wrapper's `live`/`record` modes are real (they call Claude directly) and
  have been smoke-tested against the real API — see `LLM_MODE` below.
- **No manufacturer portal integration.** "Filing" a claim means recording
  a pending ledger entry and an audit event, not driving a browser against a
  real (or mock) portal.
- **No file uploads.** Resolving a question (attach a missing document,
  confirm an amount, confirm an offer approval) is a one-click simulated
  action in the UI, not a real upload flow.
- **One program, one dealer.** Northwind Comfort 2026 / Summit Heating and
  Air, per `SPEC.md` section 7. The rules engine is general-purpose, but the
  UI's per-rule answer forms are written against this program's specific
  rule IDs (R1–R9).

## Quickstart

```bash
make setup   # venv + dependencies
make data    # render the six packets, seed the database
make test    # 44 tests, replay mode, no network, no API key needed
make demo    # http://localhost:8000
```

Fresh clone to running demo takes well under five minutes.

## How it's organized

```
agent/
  llm.py            single model wrapper: live / record / replay, cost accounting
  extract_rules.py  guide text -> rules with verified citations
  precheck.py       ad image/script -> observed facts (no verdicts)
  documents.py       invoice/payment/claim form -> observed facts (no verdicts)
  rules_engine.py    facts -> pass/fail/unsure verdict per rule (pure code)
  reimbursement.py   eligible cost, exclusions, rate, balance cap (pure code)
  policy.py          verdicts + reimbursement -> one decision (pure code)
  pipeline.py         orchestrates the above; also handles answer/approve/reject
  ledger.py, audit.py, db.py, models.py, config.py, seed.py
app.py               FastAPI + Jinja2 UI: home, claim detail, queue, program, funds, audit
scripts/
  make_packets.py    renders the six synthetic packets + hand-authored fixtures
  seed_db.py         seeds the database and runs the pipeline over every packet
data/
  guides/            the Northwind Comfort 2026 program guide
  packets/            six packets: manifest, rendered files, expected.json (test-only)
  recordings/         replay-mode fixtures for every model call
tests/               44 tests: pure-logic unit tests + full-pipeline packet tests
```

## `LLM_MODE`

| Value | Behavior |
|---|---|
| `replay` (default) | Serves the fixtures in `data/recordings/`. No network, no API key. |
| `live` | Calls Claude directly (`ANTHROPIC_API_KEY` / `LLM_API_KEY`). |
| `record` | Calls Claude and saves the real response as a new fixture. |

Model name and per-token prices live in `config.yaml`, not hardcoded in
code — check `claude.com/pricing` before changing them.

## License

MIT. See `LICENSE`.
