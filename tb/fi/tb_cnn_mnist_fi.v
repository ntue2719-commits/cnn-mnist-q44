`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name: tb_cnn_mnist_fi
//
// Purpose:
//   Deterministic RTL fault-injection smoke test for CNN_PEP.
//
// Scenario:
//   FI_SMOKE_0001
//
// Target:
//   dut.u_top_fsm.state[2]
//
// Fault model:
//   Single transient bit upset (SEU), one injected bit, one inference.
//
// Injection:
//   - Start a normal continuous-input inference.
//   - At START + 100 cycles, while top_fsm is ST_CONV1 = 3'b001,
//     force state[2] from 0 -> 1.
//   - Visible faulted state becomes 3'b101 = ST_DONE.
//   - Hold the override only through the next active clock edge.
//   - Release immediately afterward so normal RTL control resumes.
//
// Expected effect:
//   Premature top-level DONE before all 784 input pixels are accepted.
//   Final classification: PROTOCOL_VIOLATION.
//
// Test structure:
//   1. Fault-free control run:
//        - prediction matches golden
//        - latency = 1342 cycles
//   2. Reset
//   3. FI_SMOKE_0001:
//        - verify pre-state 001
//        - verify faulted state 101
//        - verify injection consumed at exact cycle
//        - verify premature DONE
//        - verify classification = PROTOCOL_VIOLATION
//
// Notes:
//   This testbench does NOT modify the RTL source.
//   Injection is performed hierarchically in simulation only.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module tb_cnn_mnist_fi;

    // =========================================================================
    // Parameters
    // =========================================================================

    localparam integer INPUT_PIXELS          = 784;
    localparam integer BASELINE_LATENCY      = 1342;
    localparam integer MAX_WAIT_CYCLES       = 3000;

    // Injection is defined relative to the accepted top-level start.
    localparam integer FI_INJECT_OFFSET      = 100;

    localparam [2:0] ST_IDLE                 = 3'b000;
    localparam [2:0] ST_CONV1                = 3'b001;
    localparam [2:0] ST_DONE                 = 3'b101;

    // Outcome encoding used only inside this smoke test.
    localparam integer OUTCOME_NONE          = 0;
    localparam integer OUTCOME_FINAL_MATCH   = 1;
    localparam integer OUTCOME_WRONG_LOGITS  = 2;
    localparam integer OUTCOME_WRONG_LABEL   = 3;
    localparam integer OUTCOME_PROTOCOL      = 4;
    localparam integer OUTCOME_NONCOMPLETION = 5;

    // =========================================================================
    // Files
    // =========================================================================

    localparam INPUT_FILE =
        "D:/Share_Data_ubuntu_window/NKKH/data/input.hex";

    localparam PREDICTION_FILE =
        "D:/Share_Data_ubuntu_window/NKKH/data/prediction.hex";

    localparam FC_WEIGHT_FILE =
        "D:/Share_Data_ubuntu_window/NKKH/data/fc_weight.hex";

    localparam FC_BIAS_FILE =
        "D:/Share_Data_ubuntu_window/NKKH/data/fc_bias.hex";

    // =========================================================================
    // Clock
    // =========================================================================

    reg i_clk = 1'b0;

    always #5 i_clk = ~i_clk;

    // =========================================================================
    // DUT inputs
    // =========================================================================

    reg       i_rst_n = 1'b1;
    reg       i_start = 1'b0;
    reg       i_valid = 1'b0;
    reg [7:0] i_pixel = 8'd0;

    // =========================================================================
    // DUT outputs
    // =========================================================================

    wire       o_busy;
    wire       o_valid;
    wire       o_done;
    wire [3:0] o_digit;

    // =========================================================================
    // DUT
    // =========================================================================

    cnn_mnist_top #(
        .FC_WEIGHT_FILE (FC_WEIGHT_FILE),
        .FC_BIAS_FILE   (FC_BIAS_FILE)
    ) dut (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),

        .i_start (i_start),

        .i_valid (i_valid),
        .i_pixel (i_pixel),

        .o_busy  (o_busy),
        .o_valid (o_valid),
        .o_done  (o_done),

        .o_digit (o_digit)
    );

    // =========================================================================
    // Test data
    // =========================================================================

    reg [7:0] input_mem      [0:INPUT_PIXELS-1];
    reg [7:0] prediction_mem [0:0];

    // =========================================================================
    // Global scoreboard
    // =========================================================================

    integer errors;
    integer checks;

    integer global_cycle;

    // =========================================================================
    // Per-run state
    // =========================================================================

    integer monitor_enable;
    integer fault_mode;

    integer accepted_pixels;

    integer start_cycle;
    integer first_done_cycle;
    integer first_done_pixel_count;

    integer done_count;
    integer conv1_done_count;
    integer conv2_done_count;
    integer fc_done_count;
    integer argmax_valid_count;

    integer baseline_latency;

    // =========================================================================
    // FI bookkeeping
    // =========================================================================

    integer fi_requested_cycle;
    integer fi_actual_cycle;

    integer fi_applied;
    integer fi_released;

    reg [2:0] fi_pre_state;
    reg [2:0] fi_fault_state;
    reg [2:0] fi_post_release_state;

    integer outcome;

    // =========================================================================
    // Global cycle counter
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n)
            global_cycle = 0;
        else
            global_cycle = global_cycle + 1;

    end

    // =========================================================================
    // Internal event monitor
    //
    // #2 lets DUT NBAs and the input-accept bookkeeping settle first.
    // =========================================================================

    always @(posedge i_clk) begin

        #2;

        if (monitor_enable) begin

            if (dut.conv1_done)
                conv1_done_count = conv1_done_count + 1;

            if (dut.conv2_done)
                conv2_done_count = conv2_done_count + 1;

            if (dut.fc_done)
                fc_done_count = fc_done_count + 1;

            if (dut.argmax_valid)
                argmax_valid_count = argmax_valid_count + 1;

            if (o_done) begin

                done_count = done_count + 1;

                if (first_done_cycle < 0) begin

                    first_done_cycle =
                        global_cycle;

                    first_done_pixel_count =
                        accepted_pixels;

                end

            end

        end

    end

    // =========================================================================
    // Reset per-run counters
    // =========================================================================

    task clear_run_state;

        begin

            accepted_pixels       = 0;

            start_cycle           = -1;
            first_done_cycle      = -1;
            first_done_pixel_count = -1;

            done_count            = 0;
            conv1_done_count      = 0;
            conv2_done_count      = 0;
            fc_done_count         = 0;
            argmax_valid_count    = 0;

            fi_requested_cycle    = -1;
            fi_actual_cycle       = -1;

            fi_applied            = 0;
            fi_released           = 0;

            fi_pre_state          = 3'bxxx;
            fi_fault_state        = 3'bxxx;
            fi_post_release_state = 3'bxxx;

            outcome               = OUTCOME_NONE;

        end

    endtask

    // =========================================================================
    // Apply reset
    // =========================================================================

    task apply_reset;

        begin

            monitor_enable = 0;

            @(negedge i_clk);

            i_rst_n = 1'b0;
            i_start = 1'b0;
            i_valid = 1'b0;
            i_pixel = 8'd0;

            repeat (5)
                @(posedge i_clk);

            @(negedge i_clk);

            i_rst_n = 1'b1;

            repeat (2)
                @(posedge i_clk);

            #2;

            checks = checks + 1;

            if (o_busy !== 1'b0) begin
                $display("FAIL reset: o_busy=%b expected=0", o_busy);
                errors = errors + 1;
            end

            checks = checks + 1;

            if (o_valid !== 1'b0) begin
                $display("FAIL reset: o_valid=%b expected=0", o_valid);
                errors = errors + 1;
            end

            checks = checks + 1;

            if (o_done !== 1'b0) begin
                $display("FAIL reset: o_done=%b expected=0", o_done);
                errors = errors + 1;
            end

            checks = checks + 1;

            if (dut.u_top_fsm.state !== ST_IDLE) begin
                $display(
                    "FAIL reset: FSM state=%03b expected ST_IDLE=000",
                    dut.u_top_fsm.state
                );
                errors = errors + 1;
            end

        end

    endtask

    // =========================================================================
    // Start one inference
    // =========================================================================

    task start_frame;

        begin

            while (o_busy !== 1'b0)
                @(posedge i_clk);

            @(negedge i_clk);

            i_start = 1'b1;
            i_valid = 1'b0;
            i_pixel = 8'd0;

            @(posedge i_clk);
            #2;

            start_cycle = global_cycle;

            checks = checks + 1;

            if (o_busy !== 1'b1) begin
                $display("FAIL: i_start was not accepted");
                errors = errors + 1;
            end

            checks = checks + 1;

            if (dut.u_top_fsm.state !== ST_CONV1) begin
                $display(
                    "FAIL: state after start=%03b expected ST_CONV1=001",
                    dut.u_top_fsm.state
                );
                errors = errors + 1;
            end

            $display(
                "START accepted @ cycle %0d, FSM=%03b",
                start_cycle,
                dut.u_top_fsm.state
            );

            @(negedge i_clk);

            i_start = 1'b0;

            // Preserve the verified Phase-1 input protocol.
            @(posedge i_clk);
            #2;

        end

    endtask

    // =========================================================================
    // Continuous image sender
    // =========================================================================

    task send_continuous_image;

        integer p;

        begin

            for (
                p = 0;
                p < INPUT_PIXELS;
                p = p + 1
            ) begin

                @(negedge i_clk);

                i_valid = 1'b1;
                i_pixel = input_mem[p];

                @(posedge i_clk);
                #1;

                accepted_pixels =
                    accepted_pixels + 1;

            end

            @(negedge i_clk);

            i_valid = 1'b0;
            i_pixel = 8'd0;

        end

    endtask

    // =========================================================================
    // Wait for normal completion
    // =========================================================================

    task wait_for_normal_done;

        integer wait_cycles;

        begin

            wait_cycles = 0;

            while (
                (first_done_cycle < 0)
                &&
                (wait_cycles < MAX_WAIT_CYCLES)
            ) begin

                @(posedge i_clk);
                #2;

                wait_cycles =
                    wait_cycles + 1;

            end

            if (first_done_cycle < 0) begin

                outcome = OUTCOME_NONCOMPLETION;

                $display(
                    "FAIL: no completion within %0d cycles",
                    MAX_WAIT_CYCLES
                );

                errors = errors + 1;

            end

        end

    endtask

    // =========================================================================
    // FI_SMOKE_0001
    //
    // Target:
    //   dut.u_top_fsm.state[2]
    //
    // Expected:
    //   ST_CONV1 001
    //     XOR 100
    //   ST_DONE  101
    //
    // The force is established on the falling edge immediately before the
    // requested injection rising edge. The FSM therefore consumes the faulted
    // state on exactly fi_requested_cycle.
    // =========================================================================

    task inject_fi_smoke_0001;

        begin

            fi_requested_cycle =
                start_cycle + FI_INJECT_OFFSET;

            // Wait on falling edges so global_cycle is already stable.
            //
            // At the falling edge after cycle (fi_requested_cycle - 1),
            // global_cycle == fi_requested_cycle - 1.  Force the bit here,
            // then the immediately following rising edge is exactly the
            // requested injection cycle.
            //
            // Using @(posedge i_clk) here creates a simulator scheduling race
            // with the global_cycle counter and can shift the recorded event
            // by one cycle.
            while (
                global_cycle <
                (fi_requested_cycle - 1)
            )
                @(negedge i_clk);

            #1;

            fi_pre_state =
                dut.u_top_fsm.state;

            checks = checks + 1;

            if (fi_pre_state !== ST_CONV1) begin

                $display(
                    "FAIL FI_SMOKE_0001: pre-state=%03b expected 001",
                    fi_pre_state
                );

                errors = errors + 1;

            end

            // -------------------------------------------------------------
            // Single-bit upset:
            //
            // state = 001
            // flip state[2] 0 -> 1
            // state = 101
            // -------------------------------------------------------------

            force dut.u_top_fsm.state[2] = 1'b1;

            #1;

            fi_fault_state =
                dut.u_top_fsm.state;

            fi_applied = 1;

            checks = checks + 1;

            if (fi_fault_state !== ST_DONE) begin

                $display(
                    "FAIL FI_SMOKE_0001: faulted state=%03b expected 101",
                    fi_fault_state
                );

                errors = errors + 1;

            end

            $display("");
            $display("============================================================");
            $display("FI_SMOKE_0001 INJECTION");
            $display("============================================================");
            $display("Target          : dut.u_top_fsm.state[2]");
            $display("Class           : CTL");
            $display("Fault type      : SEU_SINGLE_BIT");
            $display("Requested cycle : %0d", fi_requested_cycle);
            $display("Pre-state       : %03b", fi_pre_state);
            $display("Faulted state   : %03b", fi_fault_state);
            $display("Bit flip        : state[2] 0 -> 1");

            // The faulted state must be consumed by the FSM on this edge.
            @(posedge i_clk);
            #2;

            fi_actual_cycle =
                global_cycle;

            checks = checks + 1;

            if (fi_actual_cycle != fi_requested_cycle) begin

                $display(
                    "FAIL FI_SMOKE_0001: actual cycle=%0d expected=%0d",
                    fi_actual_cycle,
                    fi_requested_cycle
                );

                errors = errors + 1;

            end

            // ST_DONE behavior should have been executed.
            checks = checks + 1;

            if (o_done !== 1'b1) begin

                $display(
                    "FAIL FI_SMOKE_0001: expected premature o_done=1"
                );

                errors = errors + 1;

            end

            checks = checks + 1;

            if (o_valid !== 1'b1) begin

                $display(
                    "FAIL FI_SMOKE_0001: expected premature o_valid=1"
                );

                errors = errors + 1;

            end

            checks = checks + 1;

            if (o_busy !== 1'b0) begin

                $display(
                    "FAIL FI_SMOKE_0001: expected o_busy=0 in ST_DONE"
                );

                errors = errors + 1;

            end

            // Remove the simulator override immediately after the faulted
            // state has affected one active clock edge.
            release dut.u_top_fsm.state[2];

            #1;

            fi_released = 1;

            fi_post_release_state =
                dut.u_top_fsm.state;

            $display("Actual cycle    : %0d", fi_actual_cycle);
            $display("Accepted pixels : %0d", accepted_pixels);
            $display("After release   : %03b", fi_post_release_state);
            $display("============================================================");

        end

    endtask

    // =========================================================================
    // Fault-free control experiment
    // =========================================================================

    task run_baseline_control;

        begin

            $display("");
            $display("============================================================");
            $display("CONTROL RUN: FI DISABLED");
            $display("============================================================");

            clear_run_state();
            fault_mode = 0;

            apply_reset();

            monitor_enable = 1;

            start_frame();

            fork

                send_continuous_image();

                wait_for_normal_done();

            join

            baseline_latency =
                first_done_cycle - start_cycle;

            checks = checks + 1;

            if (accepted_pixels != INPUT_PIXELS) begin
                $display(
                    "FAIL control: accepted pixels=%0d expected=%0d",
                    accepted_pixels,
                    INPUT_PIXELS
                );
                errors = errors + 1;
            end

            checks = checks + 1;

            if (done_count != 1) begin
                $display(
                    "FAIL control: done_count=%0d expected=1",
                    done_count
                );
                errors = errors + 1;
            end

            checks = checks + 1;

            if (baseline_latency != BASELINE_LATENCY) begin
                $display(
                    "FAIL control: latency=%0d expected=%0d",
                    baseline_latency,
                    BASELINE_LATENCY
                );
                errors = errors + 1;
            end

            checks = checks + 1;

            if (o_digit !== prediction_mem[0][3:0]) begin
                $display(
                    "FAIL control: prediction expected=%0d got=%0d",
                    prediction_mem[0][3:0],
                    o_digit
                );
                errors = errors + 1;
            end

            if (errors == 0) begin
                $display(
                    "PASS control: latency=%0d cycles prediction=%0d",
                    baseline_latency,
                    o_digit
                );
            end

            monitor_enable = 0;

        end

    endtask

    // =========================================================================
    // FI experiment
    // =========================================================================

    task run_fi_smoke;

        begin

            $display("");
            $display("============================================================");
            $display("FAULT RUN: FI_SMOKE_0001");
            $display("============================================================");

            clear_run_state();
            fault_mode = 1;

            apply_reset();

            monitor_enable = 1;

            start_frame();

            fork

                send_continuous_image();

                inject_fi_smoke_0001();

            join

            // Allow all late internal events caused by the already-running
            // Conv1 block to become visible to the monitor.
            repeat (20)
                @(posedge i_clk);

            #2;

            // -------------------------------------------------------------
            // Classify according to FI_SPEC precedence.
            // -------------------------------------------------------------

            if (first_done_cycle < 0) begin

                outcome =
                    OUTCOME_NONCOMPLETION;

            end
            else if (
                (first_done_pixel_count < INPUT_PIXELS)
                ||
                (conv2_done_count == 0)
                ||
                (fc_done_count == 0)
                ||
                (argmax_valid_count == 0)
            ) begin

                outcome =
                    OUTCOME_PROTOCOL;

            end
            else if (
                o_digit !==
                prediction_mem[0][3:0]
            ) begin

                outcome =
                    OUTCOME_WRONG_LABEL;

            end
            else begin

                outcome =
                    OUTCOME_FINAL_MATCH;

            end

            // -------------------------------------------------------------
            // Smoke-test acceptance checks
            // -------------------------------------------------------------

            checks = checks + 1;

            if (!fi_applied) begin
                $display("FAIL FI_SMOKE_0001: injection was not applied");
                errors = errors + 1;
            end

            checks = checks + 1;

            if (!fi_released) begin
                $display("FAIL FI_SMOKE_0001: force was not released");
                errors = errors + 1;
            end

            checks = checks + 1;

            if (first_done_cycle < 0) begin
                $display(
                    "FAIL FI_SMOKE_0001: expected premature DONE but none observed"
                );
                errors = errors + 1;
            end

            checks = checks + 1;

            if (
                first_done_pixel_count >=
                INPUT_PIXELS
            ) begin
                $display(
                    "FAIL FI_SMOKE_0001: DONE was not premature; pixel_count=%0d",
                    first_done_pixel_count
                );
                errors = errors + 1;
            end

            checks = checks + 1;

            if (outcome != OUTCOME_PROTOCOL) begin
                $display(
                    "FAIL FI_SMOKE_0001: outcome=%0d expected PROTOCOL_VIOLATION",
                    outcome
                );
                errors = errors + 1;
            end

            monitor_enable = 0;

        end

    endtask

    // =========================================================================
    // Final FI report
    // =========================================================================

    task print_fi_report;

        begin

            $display("");
            $display("============================================================");
            $display("FI_SMOKE_0001 RESULT");
            $display("============================================================");

            $display("Scenario ID       : FI_SMOKE_0001");
            $display("Target ID         : CTL_0001");
            $display("Target            : dut.u_top_fsm.state[2]");
            $display("Target class      : CTL");
            $display("Fault type        : SEU_SINGLE_BIT");
            $display("Fault events      : 1");

            $display("");

            $display("Start cycle       : %0d", start_cycle);
            $display("Requested FI cycle: %0d", fi_requested_cycle);
            $display("Actual FI cycle   : %0d", fi_actual_cycle);

            $display("");

            $display("Pre-state         : %03b", fi_pre_state);
            $display("Faulted state     : %03b", fi_fault_state);
            $display("Post-release state: %03b", fi_post_release_state);

            $display("");

            $display("Pixels driven     : %0d", accepted_pixels);
            $display("First DONE cycle  : %0d", first_done_cycle);
            $display(
                "Pixels @ first DONE: %0d / %0d",
                first_done_pixel_count,
                INPUT_PIXELS
            );

            $display("");

            $display("DONE events       : %0d", done_count);
            $display("Conv1 done events : %0d", conv1_done_count);
            $display("Conv2 done events : %0d", conv2_done_count);
            $display("FC done events    : %0d", fc_done_count);
            $display("ArgMax valid      : %0d", argmax_valid_count);

            $display("");

            if (outcome == OUTCOME_NONCOMPLETION)
                $display("OUTCOME            : NONCOMPLETION");
            else if (outcome == OUTCOME_PROTOCOL)
                $display("OUTCOME            : PROTOCOL_VIOLATION");
            else if (outcome == OUTCOME_WRONG_LABEL)
                $display("OUTCOME            : WRONG_LABEL");
            else if (outcome == OUTCOME_WRONG_LOGITS)
                $display("OUTCOME            : WRONG_LOGITS_SAME_LABEL");
            else if (outcome == OUTCOME_FINAL_MATCH)
                $display("OUTCOME            : FINAL_OUTPUT_MATCH");
            else
                $display("OUTCOME            : UNKNOWN");

            $display("============================================================");

        end

    endtask

    // =========================================================================
    // Main
    // =========================================================================

    initial begin

        errors = 0;
        checks = 0;

        global_cycle = 0;

        monitor_enable = 0;
        fault_mode     = 0;

        clear_run_state();

        i_rst_n = 1'b1;
        i_start = 1'b0;
        i_valid = 1'b0;
        i_pixel = 8'd0;

        $display("============================================================");
        $display("CNN_MNIST RTL FAULT-INJECTION SMOKE TEST");
        $display("============================================================");

        $display("Loading %s", INPUT_FILE);
        $readmemh(
            INPUT_FILE,
            input_mem
        );

        $display("Loading %s", PREDICTION_FILE);
        $readmemh(
            PREDICTION_FILE,
            prediction_mem
        );

        // ---------------------------------------------------------------------
        // Gate 1: FI-disabled control run must reproduce the locked P0 baseline.
        // ---------------------------------------------------------------------

        run_baseline_control();

        if (errors != 0) begin

            $display("");
            $display("FI TEST ABORTED: fault-free control run failed.");
            $display("Errors: %0d", errors);

            $fatal(
                1,
                "FI-disabled equivalence gate failed"
            );

        end

        // ---------------------------------------------------------------------
        // Gate 2: deterministic known-bit injection.
        // ---------------------------------------------------------------------

        run_fi_smoke();

        print_fi_report();

        $display("");

        if (errors == 0) begin

            $display("============================================================");
            $display("CNN_MNIST FI SMOKE TEST: PASS");
            $display("Control baseline          : PASS");
            $display("Deterministic injection   : PASS");
            $display("Expected classification   : PROTOCOL_VIOLATION");
            $display("Checks                    : %0d", checks);
            $display("============================================================");

        end
        else begin

            $display("============================================================");
            $display("CNN_MNIST FI SMOKE TEST: FAIL");
            $display("Errors : %0d", errors);
            $display("Checks : %0d", checks);
            $display("============================================================");

            $fatal(
                1,
                "FI_SMOKE_0001 failed"
            );

        end

        repeat (3)
            @(posedge i_clk);

        $finish;

    end

endmodule

`default_nettype wire
