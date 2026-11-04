# `fi/` — Fault Injection Framework Guide (CNN_PEP)

> **Status: November 4, 2026 — V4f4G Windows qualified (offline only).** The original V2 four-class replay (8/8), directed20 (20/20 and 40/40 repeated executions), and MEM/BUF directed timing proofs remain **historical, scoped evidence**, not Full400 results.
>
> **Pilot400:** 400 **frozen planned** tuples (20 targets × 10 images × 2 configurations). V4f4C/D/E consumed all **8/8 bounded real-XSim attempt slots**; two historical post-FI probes remain `NOT_REPRODUCED` (slots 3 and 7). The V4f4E slot-8 Tcl LF receipt reproduced deterministic `INTERRUPTED_AFTER_FI_NO_RESULT` with **0 FI outcomes committed**. The eight-attempt archival independent review reported **293/293 offline checks and 590/590 raw artifact SHA256 matches**.
>
> **V4f4F (Windows):** 100 tests discovered, **95 passed + 5 skipped**; 400/400 production plan, synthetic pause at 137 → resume to 400, and independent audit 400/400 **synthetic receipts**. **V4f4G (Windows):** after the canonical UTF-8/LF JSON hotfix, 38 discovered, **37 passed + 1 skipped**, 400/400 offline invocation contracts and 400/400 independent blueprint audit. **No new XSim runs** in either phase.
>
> **RELEASE LOCK:** `G6_PRODUCTION_RUNNER_NOT_QUALIFIED`; `G7_PROTOCOL_NOT_APPROVED`; **`FULL400_BLOCKED`**. Blueprint and synthetic receipts are **not 400 real fault injections**. Do not run `--execute` for Full400 or allocate an unapproved ninth canary attempt.
>
> **Start here:** [repository README](../README.md), [live project progress](../PROJECT_PROGRESS.md), [FI specification](docs/FI_SPEC.md), [Phase 4F Windows runbook](v4/docs/V4F4F_WINDOWS_RUNBOOK.md), and [Phase 4G Windows runbook](v4/docs/V4F4G_WINDOWS_RUNBOOK.md).

`fi/` contains **fault-injection test/campaign scripts and experimental data**, not the synthesizable P0 CNN RTL. Each FI scenario injects one bit flip into stored RTL state during an inference and observes the result. Experiments use **Vivado/XSim simulation**, not physical FPGA-board fault injection.

## 1. Directory map — start here

```text
fi/
├── README.md
├── v3b/                           # Scoped consumer-observation qualification
├── v4bc/                          # Isolated V4b-C monitor
├── v4/                            # V4 qualification, safety, offline contracts
├── config/
│   ├── fi_targets_candidates_v2.csv    # CTL/BUF/MEM/DAT candidate catalog
│   ├── pilot400_targets.csv             # Source timing plan for 20 targets
│   └── history/                        # Refreeze backups
├── docs/
│   └── FI_SPEC.md                      # Fault model and outcome definitions
├── scenarios/
│   ├── deterministic20.csv             # Locked 20-scenario regression
│   ├── pilot400.csv                    # 400 PLANNED scenarios
│   └── history/                        # Archived manifests
├── scripts/
│   ├── run_deterministic20.py          # Locked FI V2 runner
│   ├── generate_pilot400.py            # Manifest generator and verifier
│   ├── preflight_pilot400.py            # Validate assets and plan
│   ├── export_pilot400_samples.py       # Export samples from frozen P0 weights
│   ├── install_pilot400_tb.py           # Register testbench in sim_1
│   ├── profile_pilot400.py              # Fault-free timing-event profiler
│   ├── refreeze_pilot400_timing.py      # Candidate timing refreeze and backup
│   ├── run_pilot400.py                  # XSim runner, replay, resume
│   ├── analyze_pilot400.py              # Paired RQ-A/RQ-B analysis
│   ├── pilot400_common.py               # Shared helpers
│   ├── pilot400_direct_xsim.py         # Bounded direct-XSim backend
│   ├── test_pilot400.py                # Includes snapshot/marker regression tests
│   ├── run_pilot400_target_validation_v3.py
│   └── test_pilot400_target_v3.py
├── tb/
│   ├── tb_cnn_mnist_fi_timeline.v
│   └── tb_cnn_mnist_fi_pilot400.v
├── tcl/                                 # Legacy dedicated timeline Tcl scripts
├── pilot400/                            # Golden sample bundles for 10 images
└── results/                             # Local raw runs and compact evidence
```

Folders such as `results/automation_replay/` and `results/deterministic20*/` may not appear in a clean clone because some outputs are ignored by `.gitignore` and generated locally. Compact results under `results/parsed/` and `results/summary/` may be tracked in Git. Full timeline logs may not be tracked because of their size. Use `git ls-files fi/results` to identify the evidence actually stored in the repository.

## 2. Five file categories — do not confuse them

| Category | Example | Purpose | Change/deletion policy |
|---|---|---|---|
| **Target catalog** | `config/fi_targets_candidates_v2.csv` | RTL hierarchy, class, width, valid bit indices, semantic role | Review all changes; target IDs are stable contracts |
| **Scenario manifest** | `scenarios/deterministic20.csv` | One row per scenario: ID, sample, target, bit, injection offset | Preserve the locked deterministic20 set |
| **Injection testbench** | `Simulation.srcs/sim_1/new/tb_cnn_mnist_fi_campaign_v2.v` | Performs bit flip, records events and outcomes | Verilog simulation source, not a Python script; must be registered in Vivado |
| **Automation runner** | `scripts/run_deterministic20.py` | Controls Vivado/XSim, generates Tcl/logs, invokes the parser | Modify only with preflight and replay regression |
| **Evidence outputs** | `results/.../logs/`, `parsed/`, `summary/` | Raw FI events, normalized outcomes, summaries | Preserve PASS evidence; selectively clean unsuccessful/dry-run artifacts |

**Important distinction:** `generate_fi_v2.py` generates **Verilog testbench dispatch**, not a random 400-row CSV manifest. `run_campaign.py` is an older wrapper; it must not be used as evidence that Pilot400 automatically achieved 400/400 PASS.

## 3. Configuration CSVs

### Target catalog: `config/fi_targets_candidates_v2.csv`

The catalog contains **80 candidate entries** (`CTL=31`, `BUF=33`, `MEM=6`, `DAT=10`). This is **not** a complete enumeration of flip-flops or injection sites, and not every candidate has been validated at runtime.

Important fields include `target_id`, `class`, `module`, `hier_signal`, `width`, `bit_min`, `bit_max`, `semantic_role`, and `notes`.

Two selected targets have dedicated **value-aware, cycle-accurate evidence of arrival at arithmetic inputs**:

| Target | RTL hierarchy | Observed timeline |
|---|---|---|
| `MEM_0002` | `dut.u_feature_mem1.mem[84][0]` | Write 880 → inject 881 → read 1050 → Conv2 input acceptance at 1257/1258 |
| `BUF_0031` | `dut.u_conv2_layer.u_conv2_buf.feature_mem[337][0]` | Capture 1051 → inject 1052 → window emission 1257 → input acceptance 1258 |

These assertions establish that a corrupted byte reached a convolution arithmetic input. **They do not, by themselves, establish that the arithmetic output changed.**

### Scenario manifests: `scenarios/*.csv`

Required header:

```csv
scenario_id,sample_id,target_id,target_class,bit_index,injection_offset
```

Examples from the locked FI regression:

```csv
FI_CTL_0005,IMG0000,CTL_0001,CTL,2,125
FI_MEM1_0001,IMG0000,MEM_0001,MEM,0,794
FI_BUF_0001,IMG0000,BUF_0030,BUF,0,965
FI_DAT_0001,IMG0000,DAT_0010,DAT,0,1340
```

Offsets are measured relative to **accepted START**, not absolute simulation time. In the baseline smoke run, START occurred at cycle 3, so time-based generic scenarios use `injection_cycle = 3 + injection_offset`. Some dedicated smoke testbenches are event-triggered and locate write/consume windows themselves; do not assume the CSV offset drives every injection mechanism in the same way.

`IMG0000` denotes a **specific fixed input**, MNIST test-set index 3. Reusing `SAMPLE_ID=IMG0000` does not mean distinct MNIST images have been tested. Sample IDs do not change HEX contents unless a sample-aware loading mechanism is present.

## 4. Which testbench should be used?

| Test group | XSim top | Notes |
|---|---|---|
| CTL smoke | `tb_cnn_mnist_fi_campaign_v2` | Generic injection at `FI_OFFSET` |
| MEM1 smoke | `tb_cnn_mnist_fi_mem1_smoke` | Event-triggered write/inject/read |
| BUF smoke | `tb_cnn_mnist_fi_buf_smoke` | Event-triggered capture/window/consume |
| DAT smoke | `tb_cnn_mnist_fi_dat_smoke` | FC/ArgMax handoff verification |
| Additional directed scenarios | `tb_cnn_mnist_fi_campaign_v2` | Generic target/bit/offset dispatch |
| Two dedicated timeline targets | `tb_cnn_mnist_fi_timeline` | Self-checking assertions; `$fatal` on timing/data mismatch |
| Pilot400 | `tb_cnn_mnist_fi_pilot400` | Separate Pilot400 testbench; selected target-specific timeline assertions |

The four generic/smoke testbenches may be registered in an **external Vivado project**, not necessarily under the repository's `fi/tb/` folder. The dedicated timeline testbench is present under `fi/tb/`. Reproducing the experiments on another machine requires the correct simulation sources, ROM/HEX files, and `Simulation.xpr` linkage, or a full project-generation script. The repository does not yet establish complete one-command Vivado project reconstruction.

## 5. Historical FI regression commands (Windows / VS Code PowerShell)

> **Execution boundary:** the `--execute` commands in §§5.3–5.5 document the **legacy V2 directed experiments**, not a recommendation to consume new XSim capacity. Do not replay them during V4f4F/G offline qualification. The current approved next steps are **read-only/offline** commands in §9.

Run commands from the **repository root**, not from `fi/scripts`:

```powershell
$VivadoProject = "D:/Share_Data_ubuntu_window/Github/Simulation/Simulation.xpr"
$VivadoExe     = "D:/vivado_2022.2/Vivado/2022.2/bin/vivado.bat"
Test-Path $VivadoProject
Test-Path $VivadoExe
```

### 5.1 Python-only preflight

```powershell
python fi/scripts/test_deterministic20_preflight.py
```

### 5.2 Tcl dry-run generation (safe default)

```powershell
python fi/scripts/run_deterministic20.py --project "$VivadoProject"
```

A dry run **only prepares Tcl/manifest artifacts**. It does not simulate faults and must not be reported as an XSim PASS milestone.

### 5.3 Four-class smoke replay ×2

```powershell
python fi/scripts/run_four_class_smoke.py `
  --project "$VivadoProject" `
  --vivado "$VivadoExe" `
  --out-dir fi/results/automation_replay `
  --repeats 2 `
  --execute
```

### 5.4 Directed20 replay ×2

```powershell
python fi/scripts/run_deterministic20.py `
  --project "$VivadoProject" `
  --vivado "$VivadoExe" `
  --out-dir fi/results/deterministic20_replay_new `
  --repeats 2 `
  --execute
```

For **historical directed20 replay only** (not Full400 authorization), the runner executes scenarios sequentially and stores per-run logs, Tcl scripts, normalized CSV files, summaries, and `runner_manifest.json`. With two repeats, it checks whether the **normalized CSV files are byte-identical**. Historical evidence is 40/40 PASS; every new run must still be validated independently.

### 5.5 Dedicated MEM/BUF timeline proofs

```powershell
& "$VivadoExe" -mode batch -source "fi/tcl/run_mem_0002_timeline.tcl" `
  -log "fi/results/timeline_mem_0002.log" -nojournal
Write-Host "MEM exit code: $LASTEXITCODE"

& "$VivadoExe" -mode batch -source "fi/tcl/run_buf_0031_timeline.tcl" `
  -log "fi/results/timeline_buf_0031.log" -nojournal
Write-Host "BUF exit code: $LASTEXITCODE"
```

On another machine, update `set project_file` in both Tcl files to point to the correct `.xpr`. These scripts can change the **simulation top/fileset settings**, but do not modify synthesizable P0 RTL.

### 5.6 Manual parsing and summarization

```powershell
python fi/scripts/parse_fi_v2.py `
  --logs-dir fi/results/raw/logs `
  --scenarios fi/scenarios/four_class_smoke.csv `
  --out fi/results/parsed/four_class_smoke_results.csv

python fi/scripts/summarize_campaign.py `
  --input fi/results/parsed/four_class_smoke_results.csv `
  --output fi/results/summary/four_class_smoke_summary.txt
```

For each scenario, require a corresponding FI log containing `FI_DISABLED_CONTROL_PASS`, `FI_APPLIED`, **exactly one** `FI_RESULT_V2` record, and a testbench PASS marker. `parse_fi_v2.py` reads CSV with `utf-8-sig`, including a UTF-8 BOM that Windows PowerShell may generate.

## 6. Understanding outcomes — FI PASS is not an error-free prediction

| Outcome | Meaning |
|---|---|
| `NONCOMPLETION` | Timeout or failure to complete |
| `PROTOCOL_VIOLATION` | Invalid handshake, valid/done behavior, or transaction counts |
| `WRONG_LABEL` | Predicted class differs from the golden reference |
| `WRONG_LOGITS_SAME_LABEL` | One or more logits differ, but predicted class is unchanged |
| `FINAL_OUTPUT_MATCH` | Compared final output checkpoints match the golden reference |

For the current experimental taxonomy, **harmful = `NONCOMPLETION` + `PROTOCOL_VIOLATION` + `WRONG_LABEL`**. A `FINAL_OUTPUT_MATCH` outcome does **not** establish that the corrupted bit never reached the datapath: selected `MEM_0002`/`BUF_0031` monitors observed corrupted bytes at Conv2 arithmetic inputs while the final output matched.

**Interpret carefully:** 5/5 CTL or 20/20 FI **verification PASS** does not mean that all inferences were fault-free. The 40 replay executions are not 40 independent FI scenarios. Do not report the harmful rate from directed20 as a whole-chip vulnerability probability: its 20 rows cover only six unique target IDs and one image.

## 7. Results: what to preserve, archive, and commit

| Folder or file | Generated by | Retention policy |
|---|---|---|
| `results/parsed/*.csv` | Verified parser output | **Preserve; commit only if deliberately approved under the repo evidence policy** |
| `results/summary/*.txt` | Summaries and timeline evidence | **Preserve; commit only if deliberately approved under the repo evidence policy** |
| `results/raw/logs/` | Raw FI evidence | Retain for traceability; store outside Git when large |
| `results/automation_replay/` | Four-class runner | Retain 8/8 PASS baseline; commonly Git-ignored |
| `results/deterministic20_stdout_fix/` | One-repeat directed20 run | Retain verified 20/20 evidence |
| `results/deterministic20_replay_after_reset/` | Two-repeat directed20 run | **Preserve historical 40/40 PASS evidence** in archival storage |
| `results/timeline_*.log` | Dedicated timeline Tcl | Retain full logs locally or in an archive |
| `results/deterministic20_trial/`, `deterministic20_incremental_fix/`, `deterministic20_replay/`, `deterministic20/` | Historical failed/dry-run outputs | Previously cleaned with `cleanup_fi.ps1`; regenerable |
| `scripts/__pycache__/` | Python cache | Safe to delete; protected by `.gitignore` |

Do **not** run `git clean -fdX` or delete `results/` indiscriminately: verified PASS logs may be ignored by Git. If `cleanup_fi.ps1` exists at the repository root:

```powershell
.\cleanup_fi.ps1          # Preview only
.\cleanup_fi.ps1 -Apply   # Run only after reviewing the preview

git status --short
git ls-files fi/results
```

For each milestone, record the scenario manifest, Git/P0 revisions, Vivado version, source logs, hashes, and normalized result summaries. Avoid committing `Simulation.sim`, `.Xil`, `*.wdb`, generated `*.log`, or Python `__pycache__` directories.

## 8. Common XSim issues observed during development

| Symptom | Observed context | Recommended action |
|---|---|---|
| `[WinError 2]` | Invalid `--vivado` path | Check `Test-Path $VivadoExe` and use the exact `.bat` path |
| `boost::filesystem::remove ... simulate.log ... used by another process` | Competing Vivado GUI/XSim process holds a file | Close competing simulations; run one FI runner at a time |
| `XSim::Compile design` then `[Vivado 12-4473]` | Intermittent compilation/build-state problem; `xvlog.log` sometimes empty | Inspect logs; do not change RTL without evidence; consider a controlled `reset_simulation` |
| `can not find channel named stdout` after `$finish` | Tcl stdout channel was closed | Current replay runner uses a `.done` marker rather than relying solely on echoed output |
| `Validated 0` or `empty scenarios file` | CSV schema/path/BOM mismatch | Inspect `Get-Content`, `scenario_id` header, UTF-8 BOM handling |
| `FI_APPLIED` with `FINAL_OUTPUT_MATCH` | Injection took place but output did not differ | Require timeline or checkpoint evidence before making propagation claims |

Reset XSim only when debugging has a concrete reason, not before every scenario by default:

```tcl
open_project {D:/Share_Data_ubuntu_window/Github/Simulation/Simulation.xpr}
catch {close_sim -force}
reset_simulation -mode behavioral sim_1
close_project
exit 0
```

Run the Tcl through Vivado batch after preserving necessary artifacts. It operates on generated simulation cache under `Simulation.sim`, **not** on the repository's separate `fi/results/` folder.

## 9. Pilot400 / V3b / V4 — current evidence and operator runbook (November 4, 2026)

### 9.1 Frozen scope and scientific interpretation

Pilot400 is a **fixed, targeted** 400-scenario design: **20 structural targets × 10 images × 2 configurations**. The 400 entries are **planned trials**, not observed faults or a random physical upset distribution. Four frozen manifests/assets must remain hash-pinned; in particular, the two `DAT_0010` configurations at offset 1340 remain frozen. Do **not** edit synthesizable P0 RTL or the frozen Pilot400 testbench to make a gate pass.

The research outcome precedence is:

`NONCOMPLETION > PROTOCOL_VIOLATION > WRONG_LABEL > WRONG_LOGITS_SAME_LABEL > FINAL_OUTPUT_MATCH`.

For the current harmful category, count **only** the first three. A process timeout or a terminated/aborted attempt is **infrastructure-invalid**, not automatically CNN `NONCOMPLETION`. `FINAL_OUTPUT_MATCH` does not by itself prove a flipped bit was masked at the injection, storage, or arithmetic stages. Keep evidence levels E0 provenance, E1 injection/readback, E2 fault-free temporal eligibility, E3 consumer/decision observation, and E4 outcome distinct.

### 9.2 Qualification chronology — evidence scope, not a Full400 campaign

| Milestone | Established scope | What it **does not** prove |
|---|---|---|
| V2, V3a, V3b | Directed FI regressions and consumer-observation qualification | Physical fault distribution or universal masking |
| V4b/V4c | Target-specific observability and directed consumer/decision monitors | 400 independent actual FI outcomes |
| V4d-A/B/C/D | Bounded canaries, fault-free temporal windows, controlled resume | A production Full400 runner |
| V4e / V4f Phase 1–3 | Ancestry, scheduling and release-gap reviews | Release authorization |
| V4f Phase 4A | G7 scientific protocol `prepare`, pinned candidate content | Final human attestations or protocol seal |
| V4f Phase 4B | 16 × 25 synthetic scheduler, pause/resume | 400 XSim injections |
| V4f Phase 4C/D/E | **8/8 bounded attempt slots consumed**; slot 8 proves post-FI Tcl LF ACK termination | G6 production executable qualification |
| V4f Phase 4F | Windows 400/400 plan, 137→400 **synthetic** checkpoint resume and audit | Real XSim transaction execution |
| V4f Phase 4G | Windows 400/400 JSON invocation blueprint and independent audit | Executable backend or Full400 approval |
| V4H | Offline science/G6 intake **source exists**; local folders appear in the operator's status | V4H Windows gate PASS unless independent logs supplied |

The historic eight-attempt audit covered 293/293 offline checks and 590/590 raw SHA256 items. The original eight bounded attempts include **two** `NOT_REPRODUCED` probes, which remain so even though subsequent tests qualified the intended failure mechanism. No ninth attempt is authorized.

### 9.3 Windows qualification results already reported

| Check | Windows result | Interpretation |
|---|---|---|
| `test_production_runner_v4f4f.py` | `Ran 100 tests ... OK (skipped=5)` | 95 executed PASS; no FI |
| V4f4F `--mode plan` | `400/400 frozen tuples; 16 x 25` | Source execution NOT authorized |
| V4f4F `--mode rehearsal --pause-after 137` + `--resume` | 137 + 263 = 400/400 synthetic receipts | Checkpoint and recovery mechanics only |
| V4f4F rehearsal audit | 400 synthetic receipts PASS | 0 XSim |
| V4f4G original Windows test | `Ran 35 ... FAILED (errors=22)` | Real cross-platform newline defect; retain history |
| V4f4G post-hotfix tests | `Ran 38 ... OK (skipped=1)` | 37 executed PASS; symlink test skipped by host |
| V4f4G post-hotfix blueprint | 400/400 PASS | Canonical offline invocation JSON; no commands executed |
| V4f4G post-hotfix audit | 400/400 PASS | Independent replay of the JSON contract; 0 XSim |

**Windows newline defect:** V4f4G initially generated JSON through a text writer that could translate `LF` to `CRLF`, whereas verification required canonical LF bytes. The hotfix writes canonical UTF-8/LF bytes and retains strict byte/SHA256 comparison. Never relabel the earlier 22 Windows test errors as passing or silently overwrite the old generated artifacts.

### 9.4 Offline, read-only next steps (PowerShell)

From the repository root, with the two **original** G6 archives present, first ensure new output directories do not already exist:

```powershell
$Project = 'D:/Share_Data_ubuntu_window/Github/Simulation/Simulation.xpr'
$Vivado  = 'D:/vivado_2022.2/Vivado/2022.2/bin/vivado.bat'

$Outputs = @(
  'fi/results/v4f4f_history_audit_01',
  'fi/results/v4f4f_source_preflight_01',
  'fi/results/v4f4f_release_review_01'
)
$Outputs | ForEach-Object { "$_ exists: $(Test-Path $_)" }
```

If any path exists, **inspect it and select a new versioned output directory**, rather than overwrite evidence. These commands are intended for offline/read-only qualification, with **0 new XSim**:

```powershell
python fi/v4/scripts/audit_production_v4f4f.py `
  --mode history `
  --zip-c v4f4c_g6_evidence_01.zip `
  --zip-de v4f4de_postfi_evidence_01.zip `
  --out-dir fi/results/v4f4f_history_audit_01

python fi/v4/scripts/production_runner_v4f4f.py `
  --mode source-preflight `
  --project "$Project" `
  --vivado "$Vivado" `
  --out-dir fi/results/v4f4f_source_preflight_01

python fi/v4/scripts/audit_production_v4f4f.py `
  --mode release-review `
  --source-dir fi/results/v4f4f_source_preflight_01 `
  --history-dir fi/results/v4f4f_history_audit_01 `
  --rehearsal-audit-dir fi/results/v4f4f_rehearsal_audit_01 `
  --out-dir fi/results/v4f4f_release_review_01
```

**Evidence reporting:** the 2026-11-04 project progress document treats these three Windows checks as **pending confirmation in the shared terminal logs**. Their output directories being present in `git status` is *not* sufficient proof of PASS. Treat all subsequent approval as blocked until their raw reports are reviewed.

For code regression only, use the appropriate focused suite; note that Windows filesystem-heavy synthetic tests can take 150–190 seconds:

```powershell
python -m unittest discover -s fi/v4/scripts -p 'test_production_runner_v4f4f.py' -q
python -m unittest discover -s fi/v4/scripts -p 'test_production_blueprint_v4f4g.py' -q
```

The frozen V4f4F/G offline `execute` modes are intentionally **fail-closed**. Do **not** try to bypass the guard with `--execute`, use `--allow-candidates`, or replay 400 real-XSim injections.

### 9.5 Source map, version control, and archival policy

- `fi/scripts/pilot400_direct_xsim.py` and `fi/scripts/test_pilot400.py`: hardened `expected_snapshot` elaboration classification and script-generation success-marker tests. These were committed together as **`be98577`**, `fix(fi/pilot400): harden XSim snapshot and script-generation validation` (local `main`, pending remote push confirmation).
- `fi/scripts/run_pilot400_target_validation_v3.py`, `fi/scripts/test_pilot400_target_v3.py`: Pilot400 V3 target-validation source and tests.
- `fi/v3b/` and `fi/v4bc/`: isolated consumer monitoring and qualification testbench/script sources.
- `fi/v4/`: versioned V4 scripts, frozen protocol candidate, runbooks, independent auditors and offline production contracts. See `v4/docs/V4F4F_EXECUTION_SAFETY_SPEC.md` and `v4/docs/V4F4G_PRODUCTION_BLUEPRINT_SPEC.md`.
- `fi/results/`: local generated outputs, immutable attempts and scientific evidence. **Never run `git clean -fd`, `git clean -fdX`, `git reset --hard` or delete evidence directories indiscriminately.** Keep archival ZIPs separately and SHA256-pin them before relocation.
- `PROJECT_PROGRESS.md`: authoritative engineering chronology and G0–G7 gate ledger; a README is a navigation guide, not evidence of a test PASS.

During repository cleanup, changes were grouped into purpose-specific commits for Git ignore policy, V3 scripts, V3b, V4b-C, V4 and Pilot400 fixes. The operator reported that local `main` was **6 commits ahead of `origin/main`** after `be98577`. This is a Git state observation, **not proof that `git push` has completed**.

Before publishing:

```powershell
git status --short
git log --oneline origin/main..HEAD
git diff --check origin/main..HEAD
git diff --name-only origin/main..HEAD | Select-String 'fi/results/|\.zip$|__pycache__|\.pyc$|\.lock$'
```

The final pipeline must **not** mistake `G6 bounded safety PASS`, synthetic `400/400`, or blueprint `400/400` for scientific or engineering permission to execute the full campaign.

### 9.6 Release gate and forward plan

| Gate | Current standing |
|---|---|
| G0 source/provenance | Partial; authoritative Vivado source preflight and missing original pins require review |
| G6 bounded Windows process-safety mechanics | Supported by the scoped eight-attempt history; two probes remain `NOT_REPRODUCED` |
| G6 production executor | **NOT IMPLEMENTED / NOT QUALIFIED** by V4f4G results |
| G7 protocol | `PREPARED_NOT_APPROVED`; human sign-off and seal outstanding |
| Overall G0–G7 | **BLOCKED** |
| **Full400 real FI** | **BLOCKED** |

**Next engineering milestone:** versioned build-once executor design and independently audited contracts (Phase 4H), **offline/hard-locked first**. Source/toolchain provenance, retry/process isolation, checkpoint crash-safety, validity/commit invariants, and the separate human G7 approvals must all be qualified before any production run can be considered. No Full400 release follows from a Git commit.
