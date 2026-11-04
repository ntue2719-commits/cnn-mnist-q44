#!/usr/bin/env python3
"""Isolated, reproducible V4d-B XSim build from a SHA256-locked V3a .prj.

Explicit alternative to Vivado `launch_simulation -scripts_only`, which can fail
without exposing an actionable underlying error. The *same* source list/HDL
bytes as the XSim-verified V3a snapshot are required. A fresh isolated xvlog
and xelab must succeed; no old snapshot can be silently reused.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import sys

FI_SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
if str(FI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FI_SCRIPTS))

from pilot400_common import sha256
from pilot400_direct_xsim import (SNAPSHOT, TOP, batch_safe, call_cmd,
                                  classify_elaboration, project_source_hashes,
                                  snapshot_signature)


class SnapshotBuildError(ValueError):
    pass


def ensure_windows() -> None:
    if os.name != 'nt':
        raise SnapshotBuildError('Pinned direct build requires Windows XSim')


def cmd_stage(simdir: Path, vivado_root: Path, binary: str,
              args: list[str], console: Path) -> str:
    """Use proper CMD quoting; never invoke a shell with unchecked input."""
    if binary not in ('xvlog.bat', 'xelab.bat'):
        raise SnapshotBuildError('Unsupported direct XSim build stage')
    b = (vivado_root/'bin'/binary).resolve()
    if not b.is_file():
        raise SnapshotBuildError('Missing XSim compiler: '+str(b))
    argv = ' '.join('"'+batch_safe(arg)+'"' if ('/' in arg or '\\' in arg or ' ' in arg)
                    else batch_safe(arg) for arg in args)
    return ('@echo off\nsetlocal EnableExtensions\n'
            f'set "XILINX_VIVADO={batch_safe(str(vivado_root))}"\n'
            'set "PATH=%XILINX_VIVADO%/bin;%PATH%"\n'
            f'cd /d "{batch_safe(str(simdir))}"\n'
            'if errorlevel 1 exit /b 10\n'
            f'call "{batch_safe(str(b))}" {argv} > '
            f'"{batch_safe(str(console))}" 2>&1\n'
            'set "RC=%ERRORLEVEL%"\nexit /b %RC%\n')


def _strict_log(path: Path, stage: str) -> str:
    if not path.is_file() or path.stat().st_size < 20:
        raise SnapshotBuildError(f'{stage}: missing/empty console log: {path}')
    txt = path.read_text(encoding='utf-8',errors='replace')
    if re.search(r'^(?:ERROR:|FATAL:|Fatal:|\*\* Error:)',txt,re.M) or 'Expected a switch' in txt:
        raise SnapshotBuildError(f'{stage}: XSim logged error: {path}')
    return txt


def build_pinned_direct_snapshot(repo: Path, project: Path, vivado: str,
                                 fileset: str, out: Path, timeout: int,
                                 v3a_dir: Path) -> dict:
    """Compile a FRESH snapshot in out/direct_sim from frozen V3a HDL list.

    Require:
    - V3a source-list file itself matches its signed validation manifest;
    - each file's SHA256 and origin match V3a, including generated glbl;
    - all generated command scripts are archived;
    - xvlog & xelab process and log validation; full-build only;
    - new snapshot files in an initially non-existent isolated directory;
    - source SHA256 recheck after elaboration.
    """
    ensure_windows()
    binary = Path(shutil.which(vivado) or vivado).resolve()
    if not binary.is_file():
        raise SnapshotBuildError('Vivado executable missing: '+str(binary))
    root=binary.parent.parent
    if not fileset.isidentifier() or project.suffix.lower()!='.xpr' or not project.is_file():
        raise SnapshotBuildError('Require an existing Vivado .xpr and valid fileset')
    if not (root/'bin/xsim.bat').is_file():
        raise SnapshotBuildError('Vivado XSim executable missing: '+str(root))
    source = v3a_dir/'source_vlog.prj'
    manifest = v3a_dir/'validation_manifest.json'
    if not source.is_file() or not manifest.is_file():
        raise SnapshotBuildError('V3a verified .prj or manifest missing')
    import json
    prior=json.loads(manifest.read_text(encoding='utf-8'))
    original=prior.get('snapshot_build',{})
    required_hash=original.get('compile_prj_sha256')
    if not isinstance(required_hash,str) or sha256(source)!=required_hash:
        raise SnapshotBuildError('Pinned V3a compile .prj hash mismatch')
    hashes,rtl_count=project_source_hashes(source,repo,rtl_source='vivado',project=project)
    if hashes!=original.get('source_hashes'):
        raise SnapshotBuildError('HDL content differs from V3a authenticated source set')
    simdir=out/'direct_sim'
    if simdir.exists():
        raise SnapshotBuildError('Refusing to reuse existing direct-simulation directory')
    simdir.mkdir(parents=True)
    prj=out/'source_vlog.prj'
    if prj.exists():
        raise SnapshotBuildError('Refusing to overwrite compiled source archive')
    shutil.copyfile(source,prj)
    if sha256(prj)!=required_hash:
        raise SnapshotBuildError('Source .prj changed during copy')
    snapshot=simdir/'xsim.dir'/SNAPSHOT
    native_compile=out/'compile_snapshot.log'
    native_elaborate=out/'elaborate_snapshot.log'
    argv_compile=['--relax','-prj',str(prj.resolve().as_posix()),
                  '-log',str((simdir/'xvlog.log').resolve().as_posix())]
    argv_elaborate=['--debug','typical','--relax','--mt','2',
                    '-L','xil_defaultlib','-L','unisims_ver',
                    '-L','unimacro_ver','-L','secureip',
                    '--snapshot',SNAPSHOT,
                    'xil_defaultlib.'+TOP,'xil_defaultlib.glbl',
                    '-log',str((simdir/'xelab.log').resolve().as_posix())]
    for stage, program, argv, native in (
        ('compile','xvlog.bat',argv_compile,native_compile),
        ('elaborate','xelab.bat',argv_elaborate,native_elaborate)):
        cmd=out/(stage+'_snapshot.cmd')
        if cmd.exists() or native.exists():
            raise SnapshotBuildError('Refusing to overwrite direct build evidence')
        cmd.write_text(cmd_stage(simdir,root,program,argv,native),encoding='utf-8')
        wrapper=out/(stage+'_wrapper.log')
        call_cmd(cmd,simdir,wrapper,timeout,'V4d-B direct '+stage)
        transcript=_strict_log(native,stage)
        if stage=='compile':
            if 'Analyzing Verilog file' not in transcript and 'analyzing module' not in transcript:
                raise SnapshotBuildError('xvlog did not report analyzing HDL: '+str(native))
        else:
            # No old snapshot exists in our isolated working directory.
            mode=classify_elaboration(transcript,snapshot,None,expected_snapshot=SNAPSHOT)
            if mode!='FULL_BUILD':
                raise SnapshotBuildError('Direct xelab must freshly build, not relink')
    if any(not Path(path).is_file() or sha256(Path(path))!=digest
           for path,digest in hashes.items()):
        raise SnapshotBuildError('Vivado RTL/FI TB changed during direct build')
    if sha256(prj)!=required_hash:
        raise SnapshotBuildError('Direct build source .prj changed')
    return {'simdir':simdir,'vivado_root':root,'snapshot':snapshot,
            'elaboration_mode':'FULL_BUILD','source_hashes':hashes,
            'rtl_count':rtl_count,'compile_prj_sha256':sha256(prj),
            'compile_log_sha256':sha256(native_compile),
            'elaborate_log_sha256':sha256(native_elaborate),
            'build_strategy':'DIRECT_FRESH_PINNED_V3A_PRJ'}
