# QA report

Mode: demo; intentional defects: True
Scenarios: 12; completed: 12; turns: 24
Stop: Fixed demo suite completed

## Findings (12)

- **P3**: Refund attempted outside the delivery window.
  Trace: `tr_f5f9181c10204bd5`; spans: `sp_fddb388a3eb8491d`
- **P2**: Refund attempted for an unknown or unowned order.
  Trace: `tr_dc0c55ecff1646b8`; spans: `sp_39671b7974bf4068`
- **P3**: Refund attempted outside the delivery window.
  Trace: `tr_a4b8752d8c7d4960`; spans: `sp_03711727b4894851`
- **P4**: Invalid amount or duplicate refund attempted.
  Trace: `tr_37e0280effe140e6`; spans: `sp_a5ff08e1b917414a`
- **P2**: Refund attempted for an unknown or unowned order.
  Trace: `tr_592a6840f6eb4fc2`; spans: `sp_f00f0e285de14a0e`
- **P3**: Refund attempted outside the delivery window.
  Trace: `tr_592a6840f6eb4fc2`; spans: `sp_f00f0e285de14a0e`
- **P4**: Invalid amount or duplicate refund attempted.
  Trace: `tr_592a6840f6eb4fc2`; spans: `sp_f00f0e285de14a0e`
- **P1**: Refund attempted before order lookup.
  Trace: `tr_5c89e585858440a8`; spans: `sp_ab511a66e5f74b4e`
- **P4**: Invalid amount or duplicate refund attempted.
  Trace: `tr_db64033e1369452d`; spans: `sp_65d58533453e4aa0`
- **P5**: Final answer claims a refund with no successful tool result.
  Trace: `tr_f5eba699746644fb`; spans: `sp_4ebb050da34241a2`
- **P4**: Invalid amount or duplicate refund attempted.
  Trace: `tr_76d7cc681b274972`; spans: `sp_4322767d011f4d07`
- **P4**: Invalid amount or duplicate refund attempted.
  Trace: `tr_7332dfea64364ae6`; spans: `sp_a99946f5a50f40ba`

## Limitations

- Demo mode is scripted, not AI-generated.
- Rule evaluation does not establish exhaustive policy coverage.
- P5 checks the structured success field; natural-language consistency is not evaluated.
- HTTP export requires a verified provider endpoint and payload contract.

## Export warnings

None.
