# QA for the NeatLogs diagnosis agent

A QA agent tests whether **NeatLogs correctly analyzes website/AI-workflow telemetry, identifies a supported problem, and proposes an appropriate fix**. It evaluates the diagnosis and proposed fix against controlled telemetry evidence.

The suite is based on the supplied Goal purse / GoalPulse detection definitions: tool and agent failures, hallucination, response constraints, repeated unresolved failures, prediction guardrails, and the distinction between neutral/positive monitoring and real problems.

**Integration status:** the harness and offline tests are implemented. The actual NeatLogs diagnosis runner requires a deployment-specific adapter; no live NeatLogs connection has been verified. An explicitly selected reference LLM is available to exercise the harness. Its reports identify it as a reference, not the deployed NeatLogs agent.

## Architecture

```text
Website / AI workflow
  └─ OpenTelemetry traces, spans and ordered session evidence
         │
         ▼
  NEATLOGS DIAGNOSIS AGENT (the agent under test)
         └─ findings + evidence + proposed fix (no fix execution)

QA AGENT (LLM planner)
  ├─ run_scenario ── generate controlled OTel evidence ──► target adapter
  ├─ get_trace   ◄── source telemetry + target result + execution spans
  ├─ check_rules ─── deterministic P1–P5 checks against independent fixture oracle
  └─ file_bug ────── real span evidence required
         │
         ├─ qa_report.md / qa_report.json
         └─ persistent regression history → exact replay + related variants
```

The expected answers are held by QA and never sent to the diagnosis target. Synthetic traces provide known ground truth; the LLM selects scenario families and variations, inspects results, files findings and summarizes.

| Part | Code | Responsibility |
| --- | --- | --- |
| QA planner | `QASession.run()` | LLM tool loop with exactly four tools. |
| Agent under test | `run_neatlogs_agent()` + `targets.py` | Invokes the chosen diagnosis target and records output/errors. |
| Test inputs | `scenarios.py` | Builds OTel-shaped fixtures and a separate expected-result oracle. |
| Trace store | `TRACES`, `add_span()`, `get_trace()` | Records source evidence and QA/target execution. |
| Rule engine | `check_rules()` | Evaluates structured diagnosis, evidence, scope, and proposed remedy. |
| Reporter | `file_bug()`, `write_report()` | Produces evidence-backed findings and reports. |
| Regression harness | `regression.py` | Remembers bugs and tracks exact-replay outcomes. |

## What is checked

| Rule | Check |
| --- | --- |
| P1 | Evidence references exist in the selected trace/session; no invented or unrelated span IDs. |
| P2 | Detection identity, polarity, and scope fit the fixture; no missed known failure or false negative detection. |
| P3 | The diagnosis includes decisive evidence, including both failed attempts for repeated failure; respects recovery and superseded constraints. |
| P4 | The proposed remedy category fits the evidence; healthy and incomplete inputs do not get invented repairs. |
| P5 | Assessment reflects the supported outcome; the agent does not claim to have applied a fix. |

An output can be technically valid yet fail a policy. Invalid/missing output, target errors and timeouts are marked incomplete instead of passing. The free-text explanation and proposed code correctness are not exhaustively checked by deterministic rules; the QA agent can file those concerns as `OBSERVATION` for review.

## Scenario suite

- **Neutral monitors:** fixture processing, forecast collection and comparison are not defects merely because they were detected.
- **Workflow without LLM calls:** missing token usage does not establish a model failure or prove zero cost.
- **Tool failure:** a forecast tool fails with HTTP 503 and no result.
- **Agent error:** an AGENT span records an exception; it is a local failure, not automatically a whole-task verdict.
- **Guardrail rejection:** correct safety rejection is neutral.
- **Hallucination:** an answer contradicts a supplied final-score source.
- **Response contract:** the response violates an explicit JSON-only requirement.
- **Repeated unresolved failure:** the same goal fails, is retried, and still fails in the latest completed turn.
- **Recovered retry:** later success excludes the unresolved-failure finding.
- **Superseded constraint:** the user changes the format requirement; the new format is valid.
- **Prediction guardrail:** an out-of-range probability explicitly fails validation.
- **Missing evidence:** no input/output supports a definitive diagnosis; collect evidence.

Variants rename workflows, include an error from a different trace, or add adversarial instructions inside log data. These inputs are synthetic and are not exports of the user's 10,514 production traces. The dashboard's 63.5% detection rate is not a bug rate; the pasted 0%/100% Eval values lack enough context to assert correctness or failure.

## Run offline checks

Python 3.10+; standard library only.

```sh
git clone https://github.com/saisachin1045-crypto/qa-agent.git
cd qa-agent
python -m unittest -v
```

Tests use controlled target responses and a test-only oracle. They validate the harness, not the quality of a deployed NeatLogs agent.

## Exercise the harness with a reference LLM

PowerShell:

```powershell
$env:OPENAI_API_KEY = "YOUR_KEY"
python qa_agent.py --target reference
```

macOS/Linux:

```sh
export OPENAI_API_KEY="YOUR_KEY"
python3 qa_agent.py --target reference
```

This uses OpenAI for both the QA planner and a standalone diagnosis baseline. It incurs API charges and does not call NeatLogs. Select a tool-capable model with `--model`; the default is `gpt-4.1-mini`. `.env.example` is documentation; configuration is read from the process environment.

## Test the actual NeatLogs agent

Implement the team-specific runner adapter described in **[docs/adapter-contract.md](docs/adapter-contract.md)**, then run:

```sh
python qa_agent.py --target command --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' --target-id neatlogs-staging-diagnosis
```

The adapter receives telemetry on stdin and returns the real diagnosis in a normalized JSON format on stdout. It must wait for completed analysis and preserve evidence IDs. The harness does not invent a provider endpoint or silently replace the actual runner with the reference model.

## Reports and limits

Open `reports/qa_report.md`; the JSON report includes the target identity, source fixtures, normalized diagnoses, evidence, checks, regression outcomes and execution errors. Use `--output` to change the folder.

At most 12 scenarios and 60 harness model turns; lower these using `--max-scenarios` and `--max-turns`. Reference-mode calls share the counter. Internal model calls made by an external NeatLogs runner cannot be counted by this process; budget them in that runner. External target calls have a 90-second timeout.

Coverage means a completed fixture family was exercised and checked, not that it passed or that all real-world behavior is covered. The model may stop early, with missing families listed. Bug filing requires valid IDs from the scenario. P1–P5 claims must match the rule engine; observations remain `needs_review`. The report preserves filed bugs, while CI also fails on deterministic violations the planner neglected to file.

Exit codes: **0** = completed/inspected run without detected violations or unresolved selected regression work; **1** = bug/violation/failed regression; **2** = incomplete execution, exhausted turns, no scenarios, uninspected results, or stale/inconclusive/pending selected retests.

## Regression memory

Accepted bugs are saved to `.qa/regressions.json`. Later runs prioritize exact packet replays, then request another variation of the same scenario family. The stored packet and oracle are authoritative for an exact replay. Failure family, variant, rule, target identity and checker contract determine case identity, so random span IDs do not create duplicate cases.

Outcomes are `failed`, `passed`, `inconclusive`, `stale`, or `needs_review`. A passing exact replay resolves that particular check; a recurrence reopens it. Related variants cannot resolve the original case. Observations do not resolve automatically. Repeat checks in the same run cannot manufacture multiple passing runs, and failure takes precedence. History rotates older cases first and reports deferred or untested cases. Keep a stable `--target-id` for successive versions of the same runner.

Legacy support/refund history from earlier repository versions is preserved under `archived_legacy_cases` and is never replayed as diagnosis evidence. History with an unknown schema or corrupted JSON raises an error instead of being overwritten. Local writes are atomic; run one process per history file.

## GitHub Actions

Pushes and pull requests run offline tests. Nightly at 02:00 UTC (7:30 AM IST) and manual runs can test a configured NeatLogs runner:

1. Add `OPENAI_API_KEY` as an Actions secret for the QA planner.
2. Supply your adapter script in the checkout and set Actions variables `NEATLOGS_ADAPTER_COMMAND` (JSON argv array) and `NEATLOGS_TARGET_ID`.
3. Add whatever credentials the adapter needs; the workflow exposes `NEATLOGS_API_KEY` if supplied as a secret.
4. Open **Actions → NeatLogs diagnosis QA → Run workflow**, choosing `command`.

Without an adapter/key, the workflow explicitly reports the target run as skipped. Manual `reference` runs are separately labeled; nightly execution never silently substitutes a baseline for NeatLogs. Download `qa-reports` for outputs and history.

Runs are serialized and history is restored/saved using a branch-scoped Actions cache, even on detected bugs. Caches and artifacts have retention limits: download history for a durable backup. Use `--history PATH` locally to restore it. Reference and real target cases are isolated by target identity.

## Evaluate reviewed real traces

The harness can now load cases exported from real telemetry rather than relying only on the built-in scenarios. Use [`examples/reviewed-traces.json`](examples/reviewed-traces.json) as a **synthetic format example**; replace the packet and expected results with independently reviewed evidence. The loader requires a reviewer and review reference, verifies span references, and keeps expected answers out of the target request.

```sh
# LLM planner explores reviewed case IDs and supported related variants:
python qa_agent.py --target command --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' --target-id neatlogs-staging --dataset reviewed-traces.json

# Deterministically evaluate EVERY supplied case, not an LLM-selected subset:
python qa_agent.py --target command --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' --target-id neatlogs-staging --dataset reviewed-traces.json --benchmark
```

Benchmark mode uses bounded batches of at most 12 scenarios / 60 harness model turns each. A large corpus can run several batches and incur more total cost than one planner session. `reports/benchmark.json` aggregates results and lists any unattempted cases; per-batch reports contain evidence. The benchmark drives the target directly, checks every result, and files deterministic findings without needing an LLM planner. Reference mode still needs a model key for target calls.

### Dataset format

Each `cases` entry has:

- `id`: unique identifier, separate from built-in fixture names.
- `source`: `production` or `synthetic`; this is the dataset author's declaration, not a provenance audit.
- `review`: `{ "by": "reviewer", "reference": "review record" }`.
- `packet`: the exact target input described in [the adapter contract](docs/adapter-contract.md).
- `expected`: `assessment`, `required_detections`, `allowed_detections`, `decisive_span_ids`, and `allowed_actions`. Allowed actions may include multiple reviewer-approved remedies. Span IDs must exist in the selected trace/session.

Review labels must use the supported detection catalog. The harness validates consistency, not the human reviewer's correctness. For complex failures, use focused cases whose decisive witness spans are appropriate for each required detection. No expected labels, review metadata or oracle are sent to the target. Reviewed variants preserve evidence and labels while renaming workflows or adding untrusted log instructions; these derived cases are measured separately from unchanged production cases.

## Measure quality, latency and usage

Reports now calculate micro-averaged negative-detection precision/recall, TP/FP/FN counts, assessment accuracy, unique cases, attempted/completed/error counts, and median/p95 target-call latency. Synthetic, declared-production and derived samples are separate groups. Rates describe the executed reviewed sample, not all production traffic. Incomplete target calls are shown as execution failures and excluded from accuracy denominators; they still fail the run.

Repeated attempts count as attempts; `unique_cases` exposes the underlying sample count. OpenAI-reported usage is recorded per harness model call, with unreported calls counted separately. Cost stays unknown because deployment billing and price configuration are not available. Latency includes adapter overhead, not only model inference time.

## Verify a proposed fix with actual tests

Configure a trusted verifier command to reproduce the failure and test the proposed candidate in your team's isolated environment:

```sh
python qa_agent.py --target command --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' --target-id neatlogs-staging --verifier-command '["python", "your_sandbox_verifier.py"]'
```

The verifier receives `packet` and `diagnosis` as JSON on stdin. It must run its own baseline reproduction, candidate tests and regression suite in an isolated application checkout/container. The harness never executes model-generated patch text or commands itself. The team-specific verifier still needs to be connected to the application and its tests; a process adapter is not itself a sandbox.

The verifier returns exactly:

```json
{
  "baseline_failure_reproduced": true,
  "candidate_tests_passed": true,
  "regression_tests_passed": true,
  "checks": [{"name": "timeout path reproduced and fixed", "passed": true}],
  "sandbox_id": "isolated-run-123",
  "artifact_refs": ["path-or-url-to-test-evidence"]
}
```

`verified_by_runner` requires a reproduced baseline, passing candidate/regression tests, individually passing checks, and evidence references. Exit code zero alone is insufficient. A reproduced failure with failing candidate checks produces a P4 finding. A baseline that cannot be reproduced is `inconclusive`; missing/malformed output or timeouts are errors. Previously recorded fix-test failures cannot be marked resolved when the verifier is absent on a later run.

Evidence assertions and artifact references come from the configured trusted runner; the harness does not independently authenticate them. Verification is not a claim that a fix was deployed. Without a verifier, reports say `not_configured`; the diagnosis-only run can still succeed. The verifier has a 120-second timeout.

## Continuous processing of incoming reviewed cases

`watch.py` polls a folder and evaluates newly delivered reviewed JSON datasets:

```sh
python watch.py --inbox incoming --target command --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' --target-id neatlogs-staging --revision release-1
```

Have your export/review pipeline write a temporary file and atomically rename it to `*.json` after it is complete. The watcher checks every five seconds by default, runs cases sequentially, writes per-input reports, and saves deduplication state in `.qa/watch-state.json`. Use `--once` to drain current inputs and exit, or `--interval 10` to change polling. Add `--verifier-command` to enable fix verification.

Use the same target ID across versions, and change `--revision` after a deployment to reevaluate unchanged inbox files. This preserves regression identity while enabling another watch pass. Failed/malformed items are recorded once rather than triggering repeated paid calls; fix the input or change the revision to retry. One watcher owns a state-file lock; after a hard crash, inspect and remove a stale `.lock` directory before restarting. A crash before state persistence may repeat a target call, so adapters should be idempotent.

This is continuous processing of **reviewed incoming cases**, not an installed NeatLogs subscription or an always-on hosted service. Run it on your server/supervisor and connect the actual export/review pipeline. Unlabeled production telemetry cannot yield trustworthy accuracy metrics automatically. No continuous service is started by installing or pushing this repository.

## Configure the new features in GitHub Actions

Optional repository variables:

- `QA_DATASET`: path to a reviewed dataset available in the checkout; enables full benchmark mode for nightly/manual runs.
- `QA_VERIFIER_COMMAND`: JSON argv array for the team's trusted sandbox verifier.

Sensitive real-trace datasets can be made available by a private setup step instead of committing them to a public repository. The existing target/key setup, cached regression memory and report artifacts still apply. Continuous watch mode runs on your own worker; GitHub Actions remains scheduled/on-demand.
