"""V4d-B direct-build negative tests. No Vivado or XSim runs here."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pinned_direct_snapshot_v4db as m
from pilot400_common import sha256
from pilot400_direct_xsim import SNAPSHOT


class PinnedDirectTest(unittest.TestCase):
    def make_case(self, base: Path):
        root=base/'Vivado'; (root/'bin').mkdir(parents=True)
        for name in ('vivado.bat','xvlog.bat','xelab.bat','xsim.bat'):
            (root/'bin'/name).write_text('@echo off\n',encoding='ascii')
        repo=base/'repo';repo.mkdir()
        project=base/'Simulation'/'Simulation.xpr';project.parent.mkdir()
        project.write_text('project',encoding='ascii')
        v3a=base/'v3a';v3a.mkdir()
        prj=v3a/'source_vlog.prj';prj.write_text('verilog xil_defaultlib "C:/mock/source.v"\n',encoding='ascii')
        source=base/'live.v';source.write_text('module test; endmodule\n',encoding='ascii')
        pins={str(source):sha256(source)}
        (v3a/'validation_manifest.json').write_text(json.dumps({
            'snapshot_build': {'compile_prj_sha256':sha256(prj),'source_hashes':pins}
        }),encoding='utf-8')
        out=base/'out';out.mkdir()
        return root,repo,project,v3a,out,source,pins

    def build(self,base,*,alter_source=False,alter_project=False,elab_error=False):
        root,repo,project,v3a,out,source,pins=self.make_case(base)
        if alter_source:
            source.write_text('changed',encoding='ascii')
        if alter_project:
            (v3a/'source_vlog.prj').write_text('changed',encoding='ascii')
        def fake_cmd(cmd,cwd,wrapper,timeout,stage):
            wrapper.write_text('RC=0',encoding='ascii')
            native=out/(cmd.name.replace('_snapshot.cmd','_snapshot.log'))
            if 'compile' in stage:
                native.write_text('INFO: [VRFC] Analyzing Verilog file "C:/mock/source.v"\n')
            else:
                log=('Vivado Simulator v2022.2\nRunning: C:/Vivado/xelab.exe --relax\n'
                     'Starting static elaboration\nCompleted static elaboration\n')
                if not elab_error:
                    log+=f'Built simulation snapshot {SNAPSHOT}\n'
                native.write_text(log)
                snap=cwd/'xsim.dir'/SNAPSHOT;snap.mkdir(parents=True)
                (snap/'kernel.dat').write_text('kernelfresh',encoding='ascii')
        with patch.object(m,'ensure_windows',return_value=None),\
             patch.object(m,'project_source_hashes',side_effect=lambda *a, **kw: ({str(source):sha256(source)},25)),\
             patch.object(m,'call_cmd',side_effect=fake_cmd):
            return m.build_pinned_direct_snapshot(
                repo,project,str(root/'bin/vivado.bat'),'sim_1',out,900,v3a)

    def test_build_proves_new_snapshot_and_hashes(self):
        with TemporaryDirectory() as d:
            got=self.build(Path(d))
            self.assertEqual(got['build_strategy'],'DIRECT_FRESH_PINNED_V3A_PRJ')
            self.assertEqual(got['elaboration_mode'],'FULL_BUILD')
            self.assertTrue((got['simdir']/'xsim.dir'/SNAPSHOT/'kernel.dat').is_file())
            self.assertEqual(len(got['compile_prj_sha256']),64)
            self.assertEqual(len(got['elaborate_log_sha256']),64)

    def test_refuses_modified_hdl(self):
        with TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'HDL content differs'):
                self.build(Path(d),alter_source=True)

    def test_refuses_modified_source_list(self):
        with TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'source|prj hash mismatch'):
                self.build(Path(d),alter_project=True)

    def test_refuses_elaboration_without_full_build_marker(self):
        with TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'snapshot-built|incremental-relink'):
                self.build(Path(d),elab_error=True)

    def test_script_uses_isolated_direct_compilers(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=p/'Vivado';(root/'bin').mkdir(parents=True)
            (root/'bin/xvlog.bat').write_text('x')
            cmd=m.cmd_stage(p,root,'xvlog.bat',[
                '--relax','-prj','C:/Users/Tri Tue/locked.prj'],p/'compile.log')
            self.assertIn('xvlog.bat',cmd)
            self.assertIn('"C:/Users/Tri Tue/locked.prj"',cmd)
            self.assertNotIn('launch_simulation',cmd)

    def test_refuses_unknown_stage(self):
        with TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Unsupported'):
                m.cmd_stage(Path(d),Path(d),'vivado.bat',[],Path(d)/'console')

    def test_refuses_cmd_injection(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=p/'Vivado';(root/'bin').mkdir(parents=True)
            (root/'bin/xvlog.bat').write_text('x')
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                m.cmd_stage(p,root,'xvlog.bat',['--relax','evil & exit 0'],p/'compile.log')

    def test_refuses_error_in_compiler_transcript(self):
        with TemporaryDirectory() as d:
            path=Path(d)/'compile.log';path.write_text('something\nERROR: model compilation failed\n')
            with self.assertRaisesRegex(ValueError,'logged error'):
                m._strict_log(path,'compile')

    def test_refuses_reusing_simdir(self):
        with TemporaryDirectory() as d:
            root,repo,project,v3a,out,source,pins=self.make_case(Path(d))
            (out/'direct_sim').mkdir()
            with patch.object(m,'ensure_windows',return_value=None),\
                 patch.object(m,'project_source_hashes',return_value=(pins,25)):
                with self.assertRaisesRegex(ValueError,'Refusing to reuse'):
                    m.build_pinned_direct_snapshot(repo,project,str(root/'bin/vivado.bat'),'sim_1',out,900,v3a)

    def test_original_v4db_default_still_uses_scripts_only(self):
        import qualify_temporal_v4db as temporal
        self.assertEqual(temporal.SCHEMA,'CNN_PEP_V4D_B_TEN_SAMPLE_TEMPORAL_V1')
        self.assertIn('pinned-direct-prj',Path(temporal.__file__).read_text())
        self.assertIn("getattr(args,'build_mode','vivado-scripts')",Path(temporal.__file__).read_text())


if __name__=='__main__':
    unittest.main()
