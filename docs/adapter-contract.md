# Connect the actual NeatLogs diagnosis runner

The target is the agent that receives website/AI-workflow traces and returns findings or a proposed fix.

`CommandTarget` is a process adapter protocol owned by this repository. **It is not a claim about the NeatLogs Public API schema.** No NeatLogs diagnosis endpoint or private runner implementation was supplied, so a deployment-specific adapter must be implemented by the team with access to that runner. An API key by itself does not implement this adapter.

## Input: one JSON object on stdin

The object contains `schema_version`, `target_trace_id`, `session`, `task`, `detection_definitions`, `scope_notes`, and `otel`. `otel` contains an OTLP/HTTP JSON-shaped `resourceSpans` payload with 32-character hexadecimal trace IDs, 16-character span IDs, nanosecond timestamps, status codes, attributes, resource and instrumentation scope. `session` specifies ordered completed turns using trace/span references. Custom `test.input` and `test.output` attributes contain synthetic application I/O; map those into the actual NeatLogs ingestion contract if needed.

The input never contains the fixture oracle, expected assessment, required detections, allowed remedy or regression result. The adapter must not read QA files or history to decide what answer to return.

## Adapter responsibilities

1. Read the JSON request.
2. Submit the OTel evidence to a dedicated test project using your deployment's supported ingestion route, or invoke its internal diagnosis function with equivalent normalized evidence.
3. Correlate by `target_trace_id`; wait for the investigation to complete. An ingestion HTTP success or an unfinished investigation is not a diagnosis result.
4. Obtain the agent's actual findings and proposed fix. Preserve raw evidence IDs, or maintain an exact verified ID mapping if ingestion rewrites them. Scope evidence to the selected trace/session.
5. Normalize that result to the output below. Normalize the reported recommendation to an action category based on the result itself, never the expected test answer. Unknown recommendations can use `other` and will fail the fixture's allowed-action check.
6. Print exactly one JSON result to stdout. Put diagnostic logs on stderr. Return a nonzero exit on failed ingestion, unavailable evidence or incomplete investigation. The harness records this as incomplete, not passed.
7. Do not apply fixes or send alerts/messages as part of the adapter. This harness evaluates diagnosis/proposals.

An outline to implement in your own repository:

```python
packet = json.load(sys.stdin)
# Import your real team's runner/client here; the repository does not invent it.
raw_result = your_runner.analyze_completed_trace(packet)
normalized = normalize_actual_result(raw_result)
print(json.dumps(normalized))
```

## Output: one JSON object on stdout

```json
{
  "assessment": "issue",
  "detections": [
    {
      "name": "Tool Failure",
      "polarity": "Negative",
      "scope": "span",
      "evidence_span_ids": ["0123456789abcdef"]
    }
  ],
  "root_cause_span_ids": ["0123456789abcdef"],
  "recommendation": {
    "action": "bounded_retry_or_escalate",
    "text": "The forecast tool returned HTTP 503; consider a bounded retry or escalation."
  },
  "change_applied": false,
  "explanation": "The cited tool span ended with an error and returned no result."
}
```

Use the actual span IDs from the request, not the illustrative ID above. `assessment` is `issue`, `no_issue` or `insufficient_evidence`. No-issue and incomplete-evidence cases have empty root-cause evidence. Individual neutral detections can still cite real spans.

Known recommendation action categories:

- `none`
- `collect_missing_evidence`
- `bounded_retry_or_escalate`
- `inspect_exception_handling`
- `ground_response_in_source`
- `validate_response_contract`
- `investigate_repeated_failure`
- `validate_prediction_range`

This is a finite test rubric; expand the fixture oracle when multiple remedies are acceptable. The deterministic checks assess structured categories and IDs, not the complete semantic correctness of prose or a code patch. The QA LLM can file a separate `OBSERVATION` about unsupported text, which remains subject to human review.

## Run

```sh
python qa_agent.py --target command \
  --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' \
  --target-id neatlogs-staging-diagnosis
```

On PowerShell:

```powershell
python qa_agent.py --target command --adapter-command '["python", "your_neatlogs_runner_adapter.py"]' --target-id neatlogs-staging-diagnosis
```

Use a stable target ID for repeat runs of the same deployment so resolved bugs can reopen after a regression. Choose a different target ID for a different deployment/reference target. The external command runs without a shell and has a 90-second timeout per scenario. Its internal LLM calls are opaque to the harness, so enforce their budgets in the adapter/runner. The 60-turn harness counter covers the QA planner and, in reference mode, the reference target.

Public documentation consulted for domain semantics:

- https://docs.neatlogs.com/docs/core-concepts/detections-incidents-alerts
- https://docs.neatlogs.com/docs/core-concepts/investigations-fixes-verification
- https://docs.neatlogs.com/integrations/mcp

The supplied dashboard text provides the scenario definitions; it does not supply raw production traces, evaluator verdicts, or an authenticated runner connection.
