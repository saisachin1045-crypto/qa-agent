# QA agent

An LLM QA agent that reads a policy, invents customer scenarios, tests an LLM support agent, inspects execution traces, and files bugs with span evidence. The implementation lives in `qa_agent.py`.

## Architecture

```text
                         ┌──────────────────────────────┐
   deploy / nightly ───► │  QA AGENT  (LLM, tool loop)  │
                         │  reads POLICY, plans tests   │
                         └─────────────┬────────────────┘
                                       │ calls 4 tools
          ┌────────────────┬───────────┴───────────┬────────────────┐
          ▼                ▼                       ▼                ▼
     run_scenario      get_trace              check_rules       file_bug
     (send a made-up   (read spans)           (exact code,       (rejected unless
      customer msg)                            no AI)            span IDs exist)
          │                ▲                       ▲                │
          ▼                │                       │                ▼
┌──────────────────────────┐                       │       ┌────────────────┐
│ AGENT UNDER TEST (LLM)    │                       │       │ REPORT         │
│ support agent            │                       │       │ qa_report.md   │
│  ├─ lookup_order  (tool)  │                       │       │ qa_report.json │
│  └─ refund_order  (tool)  │                       │       └────────────────┘
└────────────┬─────────────┘                       │         ▲
             │ every model turn and tool call      │         │ QA final summary
             ▼                                     │
     ┌─────────────────────────────────────────────┐
     │ TRACE STORE                                 │ ──► NeatLogs
     │ trace → spans: input, output, error,         │     (HTTP ingest, optional)
     │ duration_ms                                 │
     └─────────────────────────────────────────────┘
```

## The five parts

| Part | In the code | Job |
| --- | --- | --- |
| QA agent | `QASession.run()` | The brain. It invents scenarios, decides what to run next, and decides when to stop. |
| Agent under test | `run_support_agent()` | The AI being tested, with its own `lookup_order` and `refund_order` tools. |
| Trace store | `TRACES`, `add_span()`, `get_trace()` | Records every model turn and tool call. This is the storage boundary a NeatLogs-backed store can replace. |
| Rule engine | `check_rules()` | Exact policy checks P1–P5, with no AI involved. |
| Reporter | `file_bug()`, `write_report()` | Accepts bugs with real span evidence, then writes Markdown and JSON reports. |

## One run, step by step

1. The QA agent reads `POLICY` and invents a customer message.
2. `run_scenario` sends it to the support agent, which calls its tools and answers.
3. Every step lands in `TRACES` as a span.
4. The QA agent reads the trace using `get_trace` and runs `check_rules`.
5. If a rule broke, or the trace shows something else wrong, it calls `file_bug` with supporting span IDs.
6. It repeats until the configured policy coverage targets are reached, it decides to stop, or 12 scenarios are used. It can finish inspection and reporting after the scenario cap; a shared 60-model-turn cap stops all further LLM calls.
7. `write_report()` writes the final summary, filed bugs, coverage, errors, and traces.

## Three guardrails

- **Rules before opinion:** policy facts come from deterministic code; the LLM plans tests and explains findings.
- **Evidence gate:** a bug without valid span IDs from the referenced scenario is rejected. P1–P5 claims must match a deterministic violation. Other concerns use `OBSERVATION` and are marked `needs_review`; valid span IDs alone do not prove an interpretation correct.
- **Budget cap:** at most 12 scenarios and 60 model turns shared across both LLMs. Limits can be lowered, not raised. Tools and reporting do not consume model turns.

The QA agent has exactly four tools: `run_scenario`, `get_trace`, `check_rules`, and `file_bug`. The reporter does not automatically create bugs for the QA agent.

## Requirements

- Python 3.10 or newer.
- An OpenAI API key with access to a model supporting Chat Completions tool calls.
- No third-party Python packages.

## How to run

```sh
git clone https://github.com/saisachin1045-crypto/qa-agent.git
cd qa-agent
```

### Windows PowerShell

```powershell
$env:OPENAI_API_KEY = "YOUR_API_KEY"
python qa_agent.py
```

If `python` is unavailable, try `py qa_agent.py` after installing Python.

### macOS / Linux

```sh
export OPENAI_API_KEY="YOUR_API_KEY"
python3 qa_agent.py
```

The default command runs **both real LLM agents**. There is no scripted scenario mode. API calls incur charges. `.env.example` lists configuration variables; the program reads the process environment and does not automatically load `.env` files.

### Options

```sh
python qa_agent.py --model gpt-4.1-mini --max-scenarios 12 --max-turns 60 --output reports
```

The QA planner chooses customer messages dynamically. Each scenario starts with fresh fictional order fixtures and authenticated customer `alice`. Refund tools operate on in-memory fixtures; they do not move real money. To test your own agent, replace `run_support_agent()` while preserving its trace instrumentation.

## Reports

Open `reports/qa_report.md` for the summary and findings. `reports/qa_report.json` contains:

- The QA agent's final summary and stop reason.
- Filed bugs with trace IDs, span IDs, and confirmation/review status.
- Per-policy covered and missing targets.
- Scenario traces and the QA agent's own model/tool trace.
- Incomplete or uninspected runs and optional export warnings.

A `rule_confirmed` finding is backed by deterministic checks. A `needs_review` observation is the QA agent's interpretation of cited evidence. Invalid refund attempts are flagged even when the backend rejects them.

Exit codes: **0** = completed and inspected run with no filed bugs; **1** = filed bugs; **2** = execution failure, no scenarios, incomplete/uninspected scenarios, or exhausted model-turn budget. A clean report is not proof of correctness.

## Sample policy and coverage

The architecture specifies policy IDs but not their business definitions. This implementation uses the following editable support/refund policy in `POLICY` and `check_rules()`:

| Rule | Check | Coverage targets from completed, checked traces |
| --- | --- | --- |
| P1 | Look up the order before attempting a refund. | A refund attempt exercises lookup ordering. |
| P2 | Refund only the authenticated customer's orders. | Look up both an owned and a foreign order. |
| P3 | Refund only orders delivered 0–30 days ago, inclusive. | Inspect within-window, day-30, expired, and undelivered fixtures. |
| P4 | Positive amount, at most the amount paid, no duplicate refund. | Exercise a valid amount and inspect an already-refunded order. |
| P5 | Claim refund success only after a successful refund tool result. | Exercise both success and non-success final answers. |

`COVERAGE_TARGETS` defines the finite stopping criteria. Coverage comes from real trace events evaluated by `check_rules`, not the planner's assertion. Once all targets are covered, `run_scenario` refuses further scenarios while the planner finishes inspection, bug filing, and its summary. These targets are not exhaustive branch or natural-language coverage. P5 checks the structured `refund_succeeded` field; inconsistent free-text wording can be raised by the QA agent as an observation.

## Optional NeatLogs HTTP ingestion

Set `NEATLOGS_INGEST_URL` to a provider-confirmed ingestion endpoint and optionally `NEATLOGS_API_KEY` for Bearer authentication:

```powershell
$env:NEATLOGS_INGEST_URL = "https://YOUR_VERIFIED_INGEST_ENDPOINT"
$env:NEATLOGS_API_KEY = "YOUR_INGEST_KEY"
python qa_agent.py
```

The adapter POSTs each completed or interrupted scenario trace as JSON. Its payload has `id`, `customer_id`, `message`, `orders`, `completed`, and `spans`; each span includes `id`, `trace_id`, `name`, `input`, `output`, `error`, and `duration_ms`. Confirm the endpoint, authentication and payload contract with NeatLogs and adapt `export_trace()` if required. The MCP URL is not an HTTP ingestion URL.

This optional adapter has not been verified against a live NeatLogs account. Local `TRACES` remains the backing store; replacing it fully requires provider-backed writes and reads through the same storage functions. Export failures are recorded without discarding local reports.

## Tests and nightly runs

```sh
python -m unittest -v
```

Tests use mocked model responses to exercise the actual QA and support tool loops without API credentials. They cover all five rules, evidence gating, observations, coverage, budgets, partial runs, provider errors, backend behavior, and optional ingestion failure. Mocked tests are not live LLM validation.

The GitHub Actions workflow runs tests on pushes and pull requests. Nightly at 02:00 UTC and on manual dispatch, it also runs the real QA agent when the repository's `OPENAI_API_KEY` Actions secret is configured:

1. Go to **Settings → Secrets and variables → Actions**.
2. Add a repository secret named `OPENAI_API_KEY`.
3. Go to **Actions → QA agent → Run workflow** to run immediately.
4. Download the `qa-reports` artifact for the Markdown and JSON reports.

Without that secret, the live step is explicitly skipped and the workflow summary says so. Report upload runs even when bugs or execution failures cause a nonzero exit. Live OpenAI and NeatLogs integrations still require credentialed verification.
