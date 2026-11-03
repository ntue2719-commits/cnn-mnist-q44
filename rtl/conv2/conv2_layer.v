`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:29:09 PM
// Design Name: Nguyen Tri Tue
// Module Name: conv2_layer
//
// Description:
//   Phase-1 Conv2 integration wrapper.
//
// Datapath:
//
//   canonical Mem1 stream
//          |
//          v
//      conv2_buf
//          |
//          v
//      conv2_calc
//          |
//          v
//   conv2_quant_relu
//          |
//          v
//      pool2_buf
//          |
//          v
//        pool2
//          |
//          v
//    pool2_reorder
//          |
//          v
//   canonical Mem2 stream
//
// Input:
//   169 x 32-bit Mem1 words
//   = 676 canonical Pool1 bytes.
//
// Output:
//   25 x 64-bit Mem2 words
//   = 200 canonical Pool2 bytes.
//
// Control:
//   - i_start accepted only while idle.
//   - o_busy remains asserted throughout operation.
//   - o_done coincides with final output word.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module conv2_layer (
    input  wire        i_clk,
    input  wire        i_rst_n,

    input  wire        i_start,

    input  wire        i_valid,
    input  wire [31:0] i_data,

    output wire        o_busy,

    output wire        o_valid,
    output wire [63:0] o_data,

    output wire        o_done
);

    // =========================================================================
    // Layer control
    // =========================================================================

    reg r_busy;

    wire start_accept;

    assign start_accept =
        i_start && !r_busy;

    // Allow first Mem1 word on same cycle as accepted start.
    wire input_valid;

    assign input_valid =
        i_valid && (r_busy || start_accept);

    // =========================================================================
    // Conv2 parameter ROM
    // =========================================================================

    wire [2303:0] conv2_weights;
    wire [63:0]   conv2_biases;

    conv2_rom #(
    .WEIGHT_FILE(
        "D:/Share_Data_ubuntu_window/NKKH/data/conv2_weight.hex"
    ),
    .BIAS_FILE(
        "D:/Share_Data_ubuntu_window/NKKH/data/conv2_bias.hex"
    )
) u_conv2_rom (
    .o_weights (conv2_weights),
    .o_biases  (conv2_biases)
);

    // =========================================================================
    // Conv2 buffer
    //
    // 169 x 32-bit canonical Mem1 words
    // -> reconstruct 4x13x13
    // -> 121 x 3x3x4 windows
    // =========================================================================

    wire         conv2_buf_valid;
    wire [287:0] conv2_window;

    conv2_buf u_conv2_buf (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),
        .i_start  (start_accept),

        .i_valid  (input_valid),
        .i_data   (i_data),

        .o_valid  (conv2_buf_valid),
        .o_window (conv2_window)
    );

    // =========================================================================
    // Conv2 MAC
    //
    // 121 windows
    // -> 8 x INT32 Q8.8 raw accumulators
    // =========================================================================

    wire         conv2_calc_valid;
    wire [255:0] conv2_acc;

    conv2_calc u_conv2_calc (
        .i_clk     (i_clk),
        .i_rst_n   (i_rst_n),

        .i_valid   (conv2_buf_valid),

        .i_window  (conv2_window),
        .i_weights (conv2_weights),
        .i_biases  (conv2_biases),

        .o_valid   (conv2_calc_valid),
        .o_acc     (conv2_acc)
    );

    // =========================================================================
    // Quantization + ReLU
    //
    // 8 x INT32 Q8.8
    // -> round-even
    // -> sat INT8
    // -> ReLU
    // -> 8 x INT8 Q4.4
    // =========================================================================

    wire        quant_valid;
    wire [63:0] quant_data;

    conv2_quant_relu u_conv2_quant_relu (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),

        .i_valid (conv2_calc_valid),
        .i_acc   (conv2_acc),

        .o_valid (quant_valid),
        .o_data  (quant_data)
    );

    // =========================================================================
    // Pool2 window generator
    //
    // 11x11 x 8
    // -> 25 x 2x2x8 windows
    // =========================================================================

    wire         pool2_buf_valid;
    wire [255:0] pool2_window;

    pool2_buf u_pool2_buf (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),
        .i_start  (start_accept),

        .i_valid  (quant_valid),
        .i_data   (quant_data),

        .o_valid  (pool2_buf_valid),
        .o_window (pool2_window)
    );

    // =========================================================================
    // Pool2 signed maximum
    //
    // IMPORTANT:
    // The actual verified pool2.v in this project has registered output
    // and therefore requires clock/reset.
    // =========================================================================

    wire        pool2_valid;
    wire [63:0] pool2_data;

    pool2 u_pool2 (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),

        .i_valid  (pool2_buf_valid),
        .i_window (pool2_window),

        .o_valid  (pool2_valid),
        .o_data   (pool2_data)
    );

    // =========================================================================
    // Pool2 reorder
    //
    // 25 spatial words x 8 channels
    // -> canonical [channel][y][x]
    // -> 25 x 64-bit Mem2 words
    // =========================================================================

    wire        reorder_valid;
    wire [63:0] reorder_data;
    wire        reorder_done;

    pool2_reorder u_pool2_reorder (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),
        .i_start (start_accept),

        .i_valid (pool2_valid),
        .i_data  (pool2_data),

        .o_valid (reorder_valid),
        .o_data  (reorder_data),
        .o_done  (reorder_done)
    );

    // =========================================================================
    // Layer outputs
    // =========================================================================

    assign o_valid = reorder_valid;
    assign o_data  = reorder_data;
    assign o_done  = reorder_done;

    assign o_busy =
        r_busy && !reorder_done;

    // =========================================================================
    // Busy state
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            r_busy <= 1'b0;

        end
        else begin

            if (start_accept)
                r_busy <= 1'b1;

            if (reorder_done)
                r_busy <= 1'b0;

        end

    end

endmodule

`default_nettype wire