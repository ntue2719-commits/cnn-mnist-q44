#!/usr/bin/env python3
"""Fresh single-build, ten-process XSim control10 backend (Windows, opt-in).

No P0 RTL, FI TB, plan or scenario file is altered. Strict gates require:
(1) Vivado source paths and FI TB match the selected authority policy,
(2) new compile and elaborate logs prove a fresh snapshot,
(3) each of ten independent XSim runs has the correct *content* markers.
This code is NOT a claim that XSim has run in this environment.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess

from pilot400_common import ASSET_SIZES, SAMPLES, asset_lines, sha256
from run_pilot400 import TOP, safe_tcl

SOURCE_PRJ_RE = re.compile(r'"([^"]+\.(?:v|sv))"', re.I)
SNAPSHOT = TOP + '_behav'
ELAB_BUILT_MARKER = f'Built simulation snapshot {SNAPSHOT}'
ELAB_RELINK_MARKER = ('INFO: [XSIM 43-4323] No Change in HDL. '
                      'Linking previously generated obj files to create kernel')
SCRIPTS_ONLY_MARKER = 'CONTROL10_SCRIPT_GENERATION_PASS'


def snapshot_signature(snapshot: Path):
    """Snapshot metadata for a strict before/after relink comparison.

    Directory timestamps matter because XSim can relink a kernel in place.
    File names, sizes and mtimes detect changed snapshot artifacts as well.
    This is *freshness evidence*, not an alternative to actual HDL hashes.
    """
    if not snapshot.is_dir():
        return None
    files = sorted((item.relative_to(snapshot).as_posix(),
                    item.stat().st_size, item.stat().st_mtime_ns)
                   for item in snapshot.rglob('*') if item.is_file())
    return (snapshot.stat().st_mtime_ns, tuple(files))


def classify_elaboration(log_text: str, snapshot: Path, before_signature,
                         *, expected_snapshot: str | None = None):
    """Verify a Vivado full build or incremental relink for the intended snapshot.

    The default retains the frozen Control10/V3a snapshot name. Isolated V3b
    callers must pass their own expected_snapshot explicitly. A successful
    xelab invocation for another snapshot is never sufficient evidence.
    """
    expected = SNAPSHOT if expected_snapshot is None else expected_snapshot
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', expected):
        raise ValueError('Invalid XSim expected snapshot name')
    if expected_snapshot is not None and snapshot.name != expected:
        raise ValueError('XSim snapshot directory does not match requested name')
    built_marker = f'Built simulation snapshot {expected}'
    if any(bad in log_text for bad in ('ERROR:', 'FATAL', 'Expected a switch')):
        raise ValueError('xelab log contains error/fatal or CLI parser failure')
    if 'Running:' not in log_text or 'xelab' not in log_text or \
            'Starting static elaboration' not in log_text or \
            'Completed static elaboration' not in log_text:
        raise ValueError('xelab log lacks an actual completed elaboration pass')
    if any(line.strip() == built_marker for line in log_text.splitlines()):
        mode = 'FULL_BUILD'
    elif ELAB_RELINK_MARKER in log_text:
        mode = 'INCREMENTAL_RELINK'
    else:
        raise ValueError('xelab log has neither snapshot-built nor incremental-relink marker')
    after_signature = snapshot_signature(snapshot)
    if after_signature is None or not after_signature[1]:
        raise ValueError('XSim snapshot directory missing/empty after elaboration')
    if before_signature is not None and after_signature[1] == before_signature[1]:
        # A directory timestamp alone is not a kernel rebuild/relink proof.
        raise ValueError('XSim snapshot files not refreshed by elaboration')
    return mode



def batch_safe(value: str) -> str:
    """Fail closed rather than silently invoking CMD metacharacter expansion."""
    if any(ch in value for ch in ('\n', '\r', '"', '%', '!', '^', '&', '|', '<', '>')):
        raise ValueError('Unsafe character in Windows batch argument: ' + repr(value))
    return value.replace('\\', '/')


def prepare_tcl(project: Path, fileset: str, marker_file: Path) -> str:
    """Generate XSim scripts without writing to Vivado's fragile stdout channel.

    Vivado 2022.2 can close stdout during launch_simulation -scripts_only;
    a trailing `puts "...PASS"` then raises `can not find channel named stdout`.
    Write the success token to a separate file *only after* script generation.
    """
    if not fileset.isidentifier():
        raise ValueError('Invalid simulation fileset')
    return (
        '# Generate XSim scripts, no simulation or fault injection.\n'
        f'set success_marker {safe_tcl(marker_file.resolve().as_posix())}\n'
        # Fail closed on stale markers, including from an earlier crashed run.
        'file delete -force -- $success_marker\n'
        f'open_project {safe_tcl(project.resolve().as_posix())}\n'
        f'set fs [get_filesets {safe_tcl(fileset)}]\n'
        'if {[llength $fs] != 1} {error {Missing or ambiguous simulation fileset}}\n'
        f'set_property top {TOP} $fs\n'
        'set_property xsim.compile.incremental false $fs\n'
        'update_compile_order -fileset $fs\n'
        'if {[catch {launch_simulation -scripts_only -absolute_path -noclean_dir} err opts]} {\n'
        '    catch {puts stderr "CONTROL10_SCRIPT_GENERATION_FAIL: $err"}\n'
        '    exit 1\n'
        '}\n'
        'set success_channel [open $success_marker w]\n'
        f'puts $success_channel {{{SCRIPTS_ONLY_MARKER}}}\n'
        'close $success_channel\n'
        # No subsequent stdout writes; exit closes the Vivado project cleanly.
        'exit 0\n'
    )


def verify_scripts_only_evidence(marker_file: Path, prepare_log: Path) -> None:
    """Require a *real*, fresh Tcl-created marker, never a source echo in the log."""
    if not marker_file.is_file():
        raise ValueError('Missing scripts_only success marker: '+str(marker_file))
    if marker_file.read_text(encoding='utf-8').strip() != SCRIPTS_ONLY_MARKER:
        raise ValueError('Invalid scripts_only success marker: '+str(marker_file))
    log = prepare_log.read_text(encoding='utf-8', errors='replace')
    for name in ('compile.bat', 'elaborate.bat', 'simulate.bat'):
        if not any(line.startswith('INFO: [USF-XSim-60] Script generated:')
                   and line.rstrip().endswith('/'+name)
                   for line in log.splitlines()):
            raise ValueError('Vivado did not report generating '+name+': '+str(prepare_log))
    if any(line.startswith(('ERROR:', 'FATAL:')) for line in log.splitlines()):
        raise ValueError('Vivado scripts_only log contains error/fatal: '+str(prepare_log))


def project_source_hashes(project_prj: Path, repo: Path,
                          rtl_source: str = 'repo',
                          project: Path | None = None) -> tuple[dict[str, str], int]:
    """Verify ALL compiled HDL and its origin, never merely hash an unrelated tree.

    repo (default): byte-compare matching Verilog basenames with repo/rtl.
    vivado (opt-in): use sources inside the selected Vivado project's .srcs
    tree as the P0 authority. Still verify FI TB against the repository.
    Both modes freeze SHA256 of every .prj HDL file, including generated glbl.
    """
    if rtl_source not in ('repo', 'vivado'):
        raise ValueError('Unknown RTL source authority: ' + rtl_source)
    contents = project_prj.read_text(encoding='utf-8', errors='replace')
    listed = SOURCE_PRJ_RE.findall(contents)
    if len(listed) < 20:
        raise ValueError('Generated XSim .prj lacks expected >=20 HDL source paths')
    paths = [Path(x.replace('\\', '/')).resolve() for x in listed]
    if any(not p.is_file() for p in paths):
        missing = [str(p) for p in paths if not p.is_file()]
        raise ValueError('Generated .prj contains missing HDL source: '+', '.join(missing))
    if len({str(p).lower() for p in paths}) != len(paths):
        raise ValueError('Duplicate HDL sources in generated .prj')

    rtl_by_name: dict[str, Path] = {}
    vivado_src_root = None
    if rtl_source == 'repo':
        rtl_files = list((repo/'rtl').rglob('*.v'))
        if len(rtl_files) < 20:
            raise ValueError('Expected tracked/current P0 RTL tree under repo/rtl')
        for file in rtl_files:
            key = file.name.lower()
            if key in rtl_by_name:
                raise ValueError('Ambiguous RTL basename prevents source comparison: '+key)
            rtl_by_name[key] = file
    else:
        if project is None or not project.is_file():
            raise ValueError('Vivado authority requires existing --project .xpr')
        vivado_src_root = (project.parent/(project.stem+'.srcs')).resolve()
        if not vivado_src_root.is_dir():
            raise ValueError('Vivado .srcs directory not found: '+str(vivado_src_root))

    actual_tb = (repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v').resolve()
    actual_tb_hash = sha256(actual_tb)
    matched = 0
    found_tb = 0
    seen_rtl_names: set[str] = set()
    for source in paths:
        key = source.name.lower()
        if key == actual_tb.name.lower():
            if sha256(source) != actual_tb_hash:
                raise ValueError('Vivado compiled TB differs from current repo FI TB')
            found_tb += 1
        elif key == 'glbl.v':
            continue  # XSim-generated module; hash is still recorded below
        elif rtl_source == 'repo':
            if key in rtl_by_name:
                if sha256(source) != sha256(rtl_by_name[key]):
                    raise ValueError(f'Vivado HDL differs from current repo RTL: {key}: {source}')
                matched += 1
        else:
            # Prevent mixing arbitrary external/old RTL with the approved .srcs
            # tree. This does NOT require any repository RTL copy to match.
            try:
                source.relative_to(vivado_src_root)
            except ValueError as exc:
                raise ValueError('Vivado HDL outside selected project .srcs: '+str(source)) from exc
            if key in seen_rtl_names:
                raise ValueError('Duplicate Vivado RTL basename in .prj: '+key)
            seen_rtl_names.add(key)
            matched += 1

    if matched < 20 or found_tb != 1:
        raise ValueError(f'Insufficient Vivado HDL or verified FI TB in .prj: rtl={matched}, tb={found_tb}')
    if rtl_source == 'vivado':
        required = {'cnn_mnist_top.v', 'top_fsm.v', 'argmax.v',
                    'fully_connected.v', 'feature_mem1.v', 'feature_mem2.v',
                    'conv1_layer.v', 'conv2_layer.v'}
        missing = sorted(required - seen_rtl_names)
        if missing:
            raise ValueError('Vivado .prj missing required CNN P0 RTL: '+', '.join(missing))
    return {str(path):sha256(path) for path in paths}, matched


def make_cmd_stage(simdir: Path, vivado_root: Path, stage: str,
                   console_path: Path) -> str:
    if stage not in ('compile', 'elaborate'):
        raise ValueError('Unknown XSim stage')
    return ('@echo off\nsetlocal EnableExtensions\n'
            f'set "XILINX_VIVADO={batch_safe(str(vivado_root))}"\n'
            'set "PATH=%XILINX_VIVADO%/bin;%PATH%"\n'
            f'cd /d "{batch_safe(str(simdir))}"\n'
            'if errorlevel 1 exit /b 10\n'
            f'call "{stage}.bat" > "{batch_safe(str(console_path))}" 2>&1\n'
            'set "RC=%ERRORLEVEL%"\nexit /b %RC%\n')


def make_cmd_run(simdir: Path, vivado_root: Path, row: dict[str,str],
                 sample_dir: Path, tcl: Path, log: Path, console: Path,
                 *, profile_only: bool = True) -> str:
    """Quote the WHOLE NAME=VALUE token: verified to fix Windows XSim 2022.2."""
    required=('scenario_id','target_id','bit_index','sample_id','injection_offset')
    if any(not row.get(key) for key in required):
        raise ValueError('Incomplete FI manifest row')
    plusargs={
        'FI_OFFSET':row['injection_offset'],
        'SCENARIO_ID':row['scenario_id'],
        'TARGET_ID':row['target_id'],
        'FI_BIT':row['bit_index'],
        'SAMPLE_ID':row['sample_id'],
    }
    if profile_only:
        plusargs['PILOT_PROFILE_ONLY'] = '1'
    plusargs.update({
        'INPUT_FILE':(sample_dir/'input.hex').resolve().as_posix(),
        'POOL1_FILE':(sample_dir/'golden_pool1.hex').resolve().as_posix(),
        'POOL2_FILE':(sample_dir/'golden_pool2.hex').resolve().as_posix(),
        'LOGITS_FILE':(sample_dir/'golden.hex').resolve().as_posix(),
        'PREDICTION_FILE':(sample_dir/'prediction.hex').resolve().as_posix(),
    })
    extras = ' '.join(f'-testplusarg "{batch_safe(k+"="+v)}"' for k,v in plusargs.items())
    return ('@echo off\nsetlocal EnableExtensions\n'
            f'set "XILINX_VIVADO={batch_safe(str(vivado_root))}"\n'
            'set "PATH=%XILINX_VIVADO%/bin;%PATH%"\n'
            f'cd /d "{batch_safe(str(simdir))}"\n'
            'if errorlevel 1 exit /b 10\n'
            f'call "{batch_safe(str(vivado_root/"bin/xsim.bat"))}" {SNAPSHOT} '
            f'-tclbatch "{batch_safe(tcl.resolve().as_posix())}" '
            f'-log "{batch_safe(log.resolve().as_posix())}" {extras} '
            f'> "{batch_safe(console.resolve().as_posix())}" 2>&1\n'
            'set "RC=%ERRORLEVEL%"\nexit /b %RC%\n')


def call_checked(command: list[str], cwd: Path, logfile: Path,
                 timeout: int, stage: str) -> None:
    with logfile.open('w',encoding='utf-8',errors='replace') as fout:
        try:
            cp = subprocess.run(command,cwd=cwd,stdout=fout,
                                stderr=subprocess.STDOUT,
                                timeout=timeout,check=False)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError(f'{stage} execution failed/timeout; inspect {logfile}: {exc}') from exc
    if cp.returncode != 0:
        raise ValueError(f'{stage} returned {cp.returncode}; inspect {logfile}')


def call_cmd(batch: Path, cwd: Path, output: Path, timeout: int, stage: str) -> None:
    if os.name != 'nt':
        raise ValueError('--direct-xsim --execute requires Windows CMD / Vivado')
    call_checked(['cmd.exe','/d','/c',str(batch.resolve())],cwd,output,timeout,stage)


def execute_direct_control10(repo: Path, project: Path, vivado: str,
                             fileset: str, out: Path,
                             by_sample: dict[str,dict[str,str]], timeout: int,
                             check_control_log, records: list[dict],
                             rtl_source: str = 'repo'):
    if os.name != 'nt':
        raise ValueError('XSim direct backend requires Windows')
    binary = shutil.which(vivado) or vivado
    binary = Path(binary).resolve()
    if not binary.is_file():
        raise ValueError('Expected an absolute Vivado executable path: '+str(binary))
    vivado_root=binary.parent.parent
    if not (vivado_root/'bin/xsim.bat').is_file():
        raise ValueError('xsim.bat not found next to Vivado: '+str(vivado_root))
    simdir=(project.parent/(project.stem+'.sim')/fileset/'behav'/'xsim').resolve()
    prepare=out/'prepare_snapshot.tcl'
    marker=out/'scripts_only_success.marker'
    # Never accept a leftover marker from an interrupted earlier attempt.
    marker.unlink(missing_ok=True)
    prepare.write_text(prepare_tcl(project,fileset,marker),encoding='ascii')
    prep_log=out/'prepare_snapshot.log'
    call_checked([str(binary),'-mode','batch','-source',str(prepare.resolve()),
                  '-nolog','-nojournal'],project.parent,prep_log,timeout,'Vivado scripts_only')
    verify_scripts_only_evidence(marker,prep_log)
    src_prj=simdir/(TOP+'_vlog.prj')
    for generated in (simdir/'compile.bat',simdir/'elaborate.bat',src_prj):
        if not generated.is_file() or generated.stat().st_size < 100:
            raise ValueError('Vivado did not generate full XSim script/source project: '+str(generated))
    source_hashes,verified_count=project_source_hashes(
        src_prj,repo,rtl_source=rtl_source,project=project)
    archived_prj=out/'snapshot_vlog.prj'
    shutil.copy2(src_prj,archived_prj)
    snapshot = simdir/'xsim.dir'/SNAPSHOT
    elaboration_mode = None
    for stage in ('compile','elaborate'):
        before_snapshot = snapshot_signature(snapshot) if stage == 'elaborate' else None
        script=out/(stage+'_snapshot.cmd')
        stage_log=out/(stage+'_snapshot.log')
        process_log=out/(stage+'_wrapper.log')
        script.write_text(make_cmd_stage(simdir,vivado_root,stage,stage_log),encoding='ascii')
        call_cmd(script,project.parent,process_log,timeout,'XSim '+stage)
        stage_text=stage_log.read_text(encoding='utf-8',errors='replace') if stage_log.is_file() else ''
        if stage=='compile' and ('analyzing module ' not in stage_text and 'analyzing module' not in stage_text):
            # In some incremental builds verbose VRFC lines may be absent; -incr false
            # and the freshly-generated .prj still bind the requested source set.
            if 'ERROR:' in stage_text or not stage_text.strip():
                raise ValueError('XSim compile log missing or contains errors: '+str(stage_log))
        if stage == 'elaborate':
            try:
                elaboration_mode = classify_elaboration(stage_text, snapshot, before_snapshot)
            except ValueError as exc:
                raise ValueError(str(exc)+'; inspect '+str(stage_log)) from exc
        if 'ERROR:' in stage_text or 'FATAL' in stage_text:
            raise ValueError('XSim '+stage+' errors in '+str(stage_log))
    if not snapshot.is_dir():
        raise ValueError('Verified elaboration did not create expected XSim snapshot: '+str(snapshot))
    # Reject even source changes occurring while building, not just before build.
    if any(not Path(p).is_file() or sha256(Path(p)) != h for p,h in source_hashes.items()):
        raise ValueError('Vivado HDL sources changed during compile/elaborate')
    tcl=out/'direct_runall.tcl'
    tcl.write_text('run all\nquit\n',encoding='ascii')
    for sample_id,_ in SAMPLES:
        row=by_sample[sample_id]
        sample_dir=repo/'fi/pilot400/samples'/sample_id
        for name in ASSET_SIZES:
            if not (sample_dir/name).is_file():
                raise ValueError(f'Missing fault-free HEX for {sample_id}/{name}')
        log=out/(sample_id+'.log')
        console=out/(sample_id+'.console.log')
        wrapper=out/(sample_id+'.cmd')
        wrapper.write_text(make_cmd_run(simdir,vivado_root,row,sample_dir,tcl,log,console),encoding='ascii')
        run_stdout=out/(sample_id+'.wrapper.log')
        call_cmd(wrapper,simdir,run_stdout,timeout,'XSim direct '+sample_id)
        console_text=console.read_text(encoding='utf-8',errors='replace') if console.is_file() else ''
        if not log.is_file() or 'Expected a switch' in console_text or 'ERROR:' in console_text or 'FATAL' in console_text:
            raise ValueError(f'{sample_id}: direct XSim parser/runtime error; inspect {console}')
        expected=asset_lines(sample_dir/'prediction.hex',1,2)[0]
        check_control_log(log,sample_id,expected)
        records.append({'sample_id':sample_id,'prediction':expected,
                        'log':str(log),'sha256':sha256(log)})
        print('CONTROL10_XSIM_SAMPLE_PASS:',sample_id,flush=True)
    if any(not Path(p).is_file() or sha256(Path(p)) != h for p,h in source_hashes.items()):
        raise ValueError('Vivado HDL sources changed during control10')
    build={
        'status':'FRESH_COMPILE_ELAB_PASS',
        'elaboration_mode':elaboration_mode,
        'source_authority':rtl_source,
        ('verified_vivado_rtl_count' if rtl_source == 'vivado' else 'verified_repo_rtl_count'):verified_count,
        'snapshot':str(snapshot),
        'compile_prj_copy':str(archived_prj),
        'compile_prj_sha256':sha256(archived_prj),
        'source_hashes':source_hashes,
        'logs':{stage:{'path':str(out/(stage+'_snapshot.log')),
                        'sha256':sha256(out/(stage+'_snapshot.log'))}
                for stage in ('compile','elaborate')},
    }
    return records,build
