# Evidence-backed QA agent

A runnable implementation of the supplied QA-agent architecture. Python 3.10+; no third-party dependencies. Runs on Windows, macOS, and Linux.

## Run in two minutes

Clone the repository (or download and unzip it), then open a terminal in the project folder:

```sh
git clone https://github.com/saisachin1045-crypto/qa-agent.git
cd qa-agent
```

Run the demo without an API key:

```sh
python qa_agent.py --faulty --output reports/faulty
python qa_agent.py --output reports/fixed
python -m unittest -v
```

On Windows, use `py` instead of `python` if needed. The first command runs a deliberately defective support agent; the second runs the corrected version. Both produce `qa_report.md` and `qa_report.json`, including full traces.

Validated demo results: 12 scenarios, 24 simulated planning/support turns; faulty agent: 12 rule violations across P1–P5; corrected agent: 0 violations. These are synthetic regression results, not a production accuracy measurement. Seven tests pass.

## Real LLM mode

Both the QA planner and the support agent use OpenAI Chat Completions tool loops. The default model is `gpt-4.1-mini`; choose an accessible tool-capable model with `--model`.

PowerShell:

```powershell
$env:OPENAI_API_KEY = "YOUR_KEY"
python qa_agent.py --mode openai --output reports/live
```

Bash:

```sh
export OPENAI_API_KEY="YOUR_KEY"
python qa_agent.py --mode openai --output reports/live
```

Live calls cost money. They were not executed during development because no API credentials were supplied. The support tool loop was tested using a mocked model. `--faulty` weakens both the support prompt and simulated backend; real model behavior remains nondeterministic.

## Architecture

```text
QASession.run() — QA planner (LLM in openai mode; fixed suite in demo)
  |-- run_scenario -> run_support_agent -> lookup_order / refund_order
  |                         |-> TraceStore.add_span
  |-- get_trace ------------|          |-> optional HTTP export
  |-- check_rules -> deterministic P1–P5 checks
  |-- file_bug -> validates trace, span IDs, and supporting rule violation
  `-- write_report -> Markdown + JSON with complete traces
```

### The five parts

| Part | Code | Responsibility |
| --- | --- | --- |
| QA agent | `QASession.run()` | Reads policy, plans scenarios, calls QA tools, and decides when to stop in live mode. |
| Agent under test | `QASession.run_support_agent()` | Processes the customer message using `lookup_order` and `refund_order`. |
| Trace store | `TraceStore.traces`, `TraceStore.add_span()` | Keeps model turns, tool inputs/outputs, errors, and durations in memory. |
| Rule engine | `check_rules()` | Evaluates P1–P5 using deterministic Python checks. |
| Reporter | `QASession.file_bug()`, `QASession.write_report()` | Validates evidence and writes Markdown and JSON reports. |

### One run

1. The QA planner reads the policy and selects a customer scenario.
2. `run_scenario` starts an isolated support-agent run.
3. The support agent calls its tools, while the trace store records the execution.
4. The QA planner retrieves the trace with `get_trace` and evaluates it with `check_rules`.
5. `file_bug` accepts supported violations with real span IDs from that trace.
6. The planner continues until it stops or a scenario/turn budget is reached.
7. The session reconciles deterministic findings and writes both reports.

Fixtures contain fictional customers and orders. Every scenario starts with fresh order state. Customer identity is fixed to `alice` outside model control. Refund amounts are integer fixture currency units. No real payment API is called.

### Policies

- P1: Order lookup must precede a refund attempt.
- P2: The authenticated customer must own the order.
- P3: Delivery must have occurred 0–30 days ago, inclusive.
- P4: Positive refund, no more than the amount paid, and no duplicate refund.
- P5: Structured final success requires a successful refund tool result for that order.

Rules flag invalid attempts even if the backend denies them. These are agent policy violations, not necessarily completed financial losses. Bug filing requires evidence that supports the cited deterministic rule, which is stricter than merely checking whether a span exists. Subjective bugs outside P1–P5 are intentionally not automatically accepted.

### Budgets and failure handling

At most 12 scenarios and 60 shared model turns across both agents, with at most 12 turns per individual support run. The model-turn cap does not bound tool calls, tokens, wall time, or API cost exactly. Partial/failed runs stay in the report. CLI exits with code 2 when scenarios are incomplete or none execute; detected bugs alone do not make the command fail. Failed optional trace exports are reported without stopping local execution.

Rules are reconciled after the QA loop so the report retains deterministic findings even if the planner omits filing them. Reporting distinguishes completed scenarios from partial ones; it does not label evaluated rules as complete coverage.

## Optional NeatLogs trace export

Set `NEATLOGS_INGEST_URL` (or `--ingest-url`) to an HTTPS ingestion endpoint and optionally `NEATLOGS_API_KEY`. The exporter POSTs one complete trace JSON per scenario, with optional Bearer authentication.

This is a generic HTTP adapter: the correct NeatLogs endpoint, authentication, and payload contract must be confirmed against your account and adapted if necessary. Do not use the MCP URL as an ingestion endpoint. This adapter has not been verified against NeatLogs. Local trace storage remains the source of truth; NeatLogs retrieval/MCP is not implemented. Traces contain messages, model inputs, and order fixtures; use synthetic data for the demo.

## Limitations

This is a prototype, not an exhaustive QA system. P5 checks the structured boolean and order ID, not consistency of free-text wording. Rules are only as complete as the encoded policy. No durable database, production authentication, concurrent execution, automatic retry policy, or real refund integration is included. OpenAI and NeatLogs live integrations need credentialed verification. Reports include execution errors so infrastructure failures are not presented as passing scenarios.

## Files

- `qa_agent.py`: implementation and CLI.
- `test_qa_agent.py`: seven regression tests.
- `examples/faulty/` and `examples/fixed/`: generated reports and trace evidence.

## Automated runs

`.github/workflows/qa.yml` runs regression tests and both demo variants on pushes, pull requests, manual dispatch, and nightly at 02:00 UTC. Download the `qa-reports` artifact from the workflow run to inspect evidence. Scheduled runs use the default branch and require GitHub Actions to be enabled. They use scripted demo mode and do not make paid LLM calls.

For real LLM testing after deployment, configure `OPENAI_API_KEY` as a secret in your CI system and invoke `python qa_agent.py --mode openai --output reports/live`. Model choice and token usage affect costs; the turn budget alone is not a dollar limit.

## Troubleshooting

- **Python not found:** install Python 3.10 or newer; on Windows try `py`.
- **Missing API key:** demo mode needs no key; live mode reads `OPENAI_API_KEY` from the process environment. `.env.example` is documentation, not an auto-loaded configuration file.
- **HTTP 401/403 or unavailable model:** check your API key, billing/access, and `--model` selection.
- **Partial run:** inspect `stop_reason` and `run_error` spans in the JSON report; incomplete execution is not a passing test.
- **Trace export failed:** local reports remain available; verify the provider's ingestion endpoint, authentication, and payload contract.
