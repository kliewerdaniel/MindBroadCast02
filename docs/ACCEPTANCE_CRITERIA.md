# Acceptance Criteria — Definition of Done

Source: spec §30 (Definition of Done), §22.10 (Acceptance Gates).

MindBroadcast is not complete because it produces a convincing demonstration of
an AI presenter reading a generated script. It is complete when it functions as
a **persistent broadcasting system whose information, decisions, and outputs
remain understandable and recoverable over time.**

## Acceptance gates (spec §22.10)

| Gate | Requirement | Phase | Status |
|---|---|---|---|
| 1 | Persistence and migrations are reliable. | 1 | NOT MET |
| 2 | Ingestion and deduplication are idempotent. | 2 | NOT MET |
| 3 | Claims and evidence have traceable provenance. | 3 | NOT MET |
| 4 | World snapshots preserve consistent historical state. | 4 | NOT MET |
| 5 | Story selection is explainable and reproducible. | 4 | NOT MET |
| 6 | Generated scripts pass mandatory validation. | 5 | NOT MET |
| 7 | TTS and audio delivery recover from failures. | 5, 8 | NOT MET |
| 8 | Corrections propagate to affected claims and broadcasts. | 3, 5 | NOT MET |
| 9 | Listener interaction cannot bypass editorial controls. | 7 | NOT MET |
| 10 | Long-duration operation remains stable. | 8 | NOT MET |

Each gate has explicit tests and recorded results. **A feature is not complete
because a happy-path demonstration works once.**

## Final acceptance criteria

### Information integrity

- [ ] Every captured article has source provenance.
- [ ] Claims remain distinguishable from evidence.
- [ ] Articles remain distinguishable from events and stories.
- [ ] Corrections do not erase historical records.
- [ ] Entity and event relationships can be inspected.
- [ ] World snapshots have explicit temporal semantics.
- [ ] Processing retries do not create unintended duplicate records.
- [ ] The system can reconstruct the evidence available when a segment was generated.

### Editorial intelligence

- [ ] The system detects material changes to existing stories.
- [ ] It identifies new developments without relying exclusively on article volume.
- [ ] It ranks stories using explicit and inspectable factors.
- [ ] It tracks previous coverage and unresolved questions.
- [ ] It can explain why a story was selected.
- [ ] It avoids repeatedly reporting unchanged information as new.
- [ ] It distinguishes established facts from uncertain interpretations.

### Broadcast generation

- [ ] Segments are generated from bounded, versioned evidence packets.
- [ ] Factual claims can be traced to supporting evidence.
- [ ] Scripts pass mandatory validation before broadcast.
- [ ] Personas express distinct analytical perspectives without inventing factual disagreements.
- [ ] The system generates speech continuously without a human initiating every turn.
- [ ] TTS and audio failures do not corrupt the knowledge base or editorial history.
- [ ] Interruptions do not cause stale audio to appear unexpectedly.
- [ ] Corrections can be published and linked to the affected broadcast.

### Operational reliability

- [ ] The application survives process restarts.
- [ ] Pending work can be recovered.
- [ ] Database transactions and background processing have explicit failure semantics.
- [ ] Queue growth and resource usage are bounded.
- [ ] Source failures do not terminate the entire system.
- [ ] Model-provider failures degrade only the affected capabilities where possible.
- [ ] Backups can be restored.
- [ ] Logs and metrics make failures diagnosable.

### Recursive improvement

- [ ] The system can evaluate its own outputs.
- [ ] It identifies recurring deficiencies.
- [ ] It proposes changes to its ranking, retrieval, generation, or scheduling mechanisms.
- [ ] Proposed changes can be tested against a baseline.
- [ ] Improvements are measured against multiple quality dimensions.
- [ ] Approved changes are versioned and reversible.
- [ ] Historical evaluation results remain available.

## Epistemic invariants as acceptance tests

All twenty invariants in EPISTEMIC_INVARIANTS.md must have passing tests before
the corresponding gate closes. The critical ones by gate:

| Gate | Invariants that must hold |
|---|---|
| 1 | I3 (correction ≠ evidence), I4 (history never rewritten), I5 (three time axes) |
| 2 | I8 (volume ≠ corroboration), I9 (near-duplicates carry corrections) |
| 3 | I1 (status ≠ truth score), I2 (statement-existence ≠ truth), I10 (not every conflict is a contradiction), I14 (a source list is not provenance) |
| 4 | I8, I11 (uncertainty attaches where it occurs) |
| 5 | I6 (personas cannot override evidence), I7 (multiple outputs ≠ corroboration), I12 (generated ≠ validated ≠ delivered), I13 (multi-mechanism validation) |
| 7 | I15 (allegation ≠ assertion), I16 (config flags are not enforcement) |

## Completion discipline

- Every gate closes with a **recorded test run**, not an assertion.
- A partial fix is not a fix: on a reported defect, find the root cause.
  A weaker band-aid that suppresses the visible symptom leaves the gate unmet.
- Known failures are not acceptable as a completed phase.
- The full suite must pass before a phase is declared done.
- Report actual numbers. Never fabricate or assume a result.
