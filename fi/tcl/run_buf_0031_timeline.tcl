# Dedicated FI proof for FI_BUF_0005; does not modify P0 RTL.
set project_file {D:/Share_Data_ubuntu_window/Github/Simulation/Simulation.xpr}
set script_dir [file dirname [file normalize [info script]]]
set tb_file [file normalize [file join $script_dir .. tb tb_cnn_mnist_fi_timeline.v]]
if {![file isfile $project_file] || ![file isfile $tb_file]} {error "Missing project or timeline TB source"}
open_project $project_file
set sim [get_filesets sim_1]
if {[llength $sim] != 1} {error "sim_1 missing"}
if {[llength [get_files -quiet $tb_file]] == 0} {
    add_files -fileset sim_1 $tb_file
}
catch {close_sim -force}
set_property top tb_cnn_mnist_fi_timeline $sim
set_property xsim.compile.incremental false $sim
update_compile_order -fileset $sim
set_property -name {xsim.simulate.xsim.more_options} -value {-testplusarg FI_OFFSET=1049 -testplusarg SCENARIO_ID=FI_BUF_0005 -testplusarg TARGET_ID=BUF_0031 -testplusarg FI_BIT=0 -testplusarg SAMPLE_ID=IMG0000} -objects $sim
if {[catch {launch_simulation; run all} err opts]} {
    catch {puts stderr "FI_TIMELINE_TCL_ERROR: $err"}
    exit 1
}
catch {close_sim -force}
close_project
exit 0
