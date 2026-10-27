# CTL Smoke Campaign V1

This bundle is the first campaign layer built on top of the validated
`FI_SMOKE_0001` test.

## Scope

The first five scenarios intentionally keep the same proven target:

- target: `dut.u_top_fsm.state[2]`
- class: `CTL`
- expected pre-state: `001` (`ST_CONV1`)
- expected faulted state: `101` (`ST_DONE`)
- reference event: accepted `i_start`

Only the relative injection offset changes:

`25, 50, 75, 100, 125` cycles after accepted start.

This is deliberate. The goal of this stage is to validate repeatability,
scenario bookkeeping, exact-cycle injection, and result logging before
expanding to additional bits/signals.

## Files

- `config/campaign_config.json`: fixed campaign contract.
- `scenarios/pilot_scenarios.csv`: five deterministic CTL scenarios.
- `results/raw/results.csv`: canonical raw result schema.
- `scripts/generate_scenarios.py`: deterministic/random scenario generator.
- `scripts/parse_results.py`: result validator.
- `scripts/summarize_campaign.py`: outcome-rate summary.

## Next implementation step

Parameterize the validated XSim FI testbench so `injection_offset` comes from
the scenario row (or a simulator plusarg), append one result row per run, then
execute all five scenarios.

Do not expand to random CTL/BUF/MEM/DAT campaigns until these five runs are
reproducible and the result CSV is correct.
