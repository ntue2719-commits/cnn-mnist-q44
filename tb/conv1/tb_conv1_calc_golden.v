`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/10/2026 09:42:34 AM
// Design Name: 
// Module Name: tb_conv1_calc_golden
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////
`timescale 1ns / 1ps

`default_nettype none

module tb_conv1_calc_golden;

    // =========================================================================
    // Parameters
    // =========================================================================

    localparam IMG_W       = 28;
    localparam IMG_H       = 28;

    localparam OUT_W       = 26;
    localparam OUT_H       = 26;

    localparam NUM_PIXELS  = 784;
    localparam NUM_WINDOWS = 676;

    localparam NUM_WEIGHTS = 36;
    localparam NUM_BIASES  = 4;

    localparam NUM_GOLDEN  = 2704;


    // =========================================================================
    // DUT signals
    // =========================================================================

    reg         i_clk;
    reg         i_rst_n;
    reg         i_valid;

    reg  [71:0]  i_window;
    reg  [287:0] i_weights;
    reg  [31:0]  i_biases;

    wire         o_valid;
    wire [127:0] o_acc;


    // =========================================================================
    // Memories
    // =========================================================================

    reg signed [7:0] input_mem [0:NUM_PIXELS-1];

    reg signed [7:0] weight_mem [0:NUM_WEIGHTS-1];
    reg signed [7:0] bias_mem   [0:NUM_BIASES-1];

    reg signed [31:0] golden_mem [0:NUM_GOLDEN-1];


    // =========================================================================
    // Counters
    // =========================================================================

    integer x;
    integer y;
    integer k;
    integer oc;

    integer window_index;
    integer checked_windows;
    integer errors;

    integer pixel_index;
    integer golden_index;


    // =========================================================================
    // Temporary comparison values
    // =========================================================================

    reg signed [31:0] dut_acc;
    reg signed [31:0] expected_acc;


    // =========================================================================
    // DUT
    // =========================================================================

    conv1_calc dut (
        .i_clk     (i_clk),
        .i_rst_n   (i_rst_n),

        .i_valid   (i_valid),
        .i_window  (i_window),
        .i_weights (i_weights),
        .i_biases  (i_biases),

        .o_valid   (o_valid),
        .o_acc     (o_acc)
    );


    // =========================================================================
    // Clock
    // =========================================================================

    initial begin
        i_clk = 1'b0;
        forever #5 i_clk = ~i_clk;
    end


    // =========================================================================
    // Pack weights and biases
    // =========================================================================

    task pack_parameters;
        integer i;
        begin

            i_weights = 288'd0;
            i_biases  = 32'd0;

            for (i = 0; i < NUM_WEIGHTS; i = i + 1)
                i_weights[i*8 +: 8] = weight_mem[i];

            for (i = 0; i < NUM_BIASES; i = i + 1)
                i_biases[i*8 +: 8] = bias_mem[i];

        end
    endtask


    // =========================================================================
    // Construct one 3x3 window
    //
    // P0 P1 P2
    // P3 P4 P5
    // P6 P7 P8
    //
    // Base coordinate = (base_y, base_x)
    // =========================================================================

    task build_window;
        input integer base_y;
        input integer base_x;

        integer ky;
        integer kx;
        integer p;
        integer index;

        begin

            i_window = 72'd0;

            p = 0;

            for (ky = 0; ky < 3; ky = ky + 1) begin
                for (kx = 0; kx < 3; kx = kx + 1) begin

                    index =
                        (base_y + ky) * IMG_W
                        + (base_x + kx);

                    i_window[p*8 +: 8] =
                        input_mem[index];

                    p = p + 1;

                end
            end

        end
    endtask


    // =========================================================================
    // Compare current output
    // =========================================================================

    task compare_window;
        input integer win_idx;

        integer c;
        integer g_idx;

        begin

            if (!o_valid) begin

                $display(
                    "[FAIL] win=%0d : o_valid=0",
                    win_idx
                );

                errors = errors + 1;

            end
            else begin

                for (c = 0; c < 4; c = c + 1) begin

                    dut_acc =
                        $signed(o_acc[c*32 +: 32]);

                    // Golden tensor:
                    // [channel][y][x]
                    //
                    // each channel has 26*26 = 676 values
                    g_idx =
                        c * NUM_WINDOWS
                        + win_idx;

                    expected_acc =
                        golden_mem[g_idx];

                    if (dut_acc !== expected_acc) begin

                        $display(
                            "[FAIL] win=%0d ch=%0d golden_idx=%0d expected=%0d got=%0d",
                            win_idx,
                            c,
                            g_idx,
                            expected_acc,
                            dut_acc
                        );

                        errors = errors + 1;

                    end

                end

                checked_windows = checked_windows + 1;

            end

        end
    endtask


    // =========================================================================
    // Main
    // =========================================================================

    initial begin

        // ---------------------------------------------------------------------
        // Init
        // ---------------------------------------------------------------------

        i_rst_n = 1'b0;
        i_valid = 1'b0;

        i_window  = 72'd0;
        i_weights = 288'd0;
        i_biases  = 32'd0;

        errors          = 0;
        checked_windows = 0;
        window_index    = 0;


        // ---------------------------------------------------------------------
        // Load files
        //
        // If Vivado cannot find these files, add them to Simulation Sources
        // or change the paths below.
        // ---------------------------------------------------------------------

    $readmemh("D:/Share_Data_ubuntu_window/NKKH/data/input.hex",
              input_mem);

    $readmemh("D:/Share_Data_ubuntu_window/NKKH/data/conv1_weight.hex",
              weight_mem);

    $readmemh("D:/Share_Data_ubuntu_window/NKKH/data/conv1_bias.hex",
              bias_mem);

    $readmemh("D:/Share_Data_ubuntu_window/NKKH/data/golden_conv1.hex",
              golden_mem);


        // ---------------------------------------------------------------------
        // Pack Conv1 parameters
        // ---------------------------------------------------------------------

        pack_parameters();


        // ---------------------------------------------------------------------
        // Reset
        // ---------------------------------------------------------------------

        repeat (3) @(posedge i_clk);

        i_rst_n = 1'b1;

        @(posedge i_clk);


        // ---------------------------------------------------------------------
        // Send all 676 windows
        // ---------------------------------------------------------------------

        window_index = 0;

        for (y = 0; y < OUT_H; y = y + 1) begin

            for (x = 0; x < OUT_W; x = x + 1) begin

                // Build stable input before rising edge
                @(negedge i_clk);

                build_window(y, x);

                i_valid = 1'b1;


                // DUT captures this window
                @(posedge i_clk);


                // Output corresponding to this transaction is registered.
                @(negedge i_clk);

                i_valid = 1'b0;

                #1;

                compare_window(window_index);


                // Optional debug
                if ((window_index == 0)   ||
                    (window_index == 338) ||
                    (window_index == 675)) begin

                    $display(
                        "win %0d: acc0=%0d acc1=%0d acc2=%0d acc3=%0d",
                        window_index,
                        $signed(o_acc[31:0]),
                        $signed(o_acc[63:32]),
                        $signed(o_acc[95:64]),
                        $signed(o_acc[127:96])
                    );

                end

                window_index = window_index + 1;

            end
        end


        // ---------------------------------------------------------------------
        // Report
        // ---------------------------------------------------------------------

        $display("");
        $display("Checked windows : %0d", checked_windows);
        $display("Checked acc     : %0d", checked_windows * 4);
        $display("Errors          : %0d", errors);
        $display("");


        if ((errors == 0) &&
            (checked_windows == NUM_WINDOWS)) begin

            $display(
                "========== CONV1_CALC GOLDEN TEST: PASS =========="
            );

        end
        else begin

            $display(
                "========== CONV1_CALC GOLDEN TEST: FAIL =========="
            );

        end


        $finish;

    end

endmodule

`default_nettype wire