`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 09:35:37 PM
// Design Name: 
// Module Name: conv1_layer
//   Phase-1 Conv1 integration wrapper.
//
// Datapath:
//
//   i_pixel
//      |
//      v
//   conv1_buf
//      |
//      v
//   conv1_calc
//      |
//      v
//   conv1_quant_relu
//      |
//      v
//   pool1_buf
//      |
//      v
//   pool1
//      |
//      v
//   pool1_reorder
//      |
//      v
//   canonical Pool1 stream
//
// Input:
//   28 x 28 = 784 signed INT8 Q4.4 pixels.
//
// Intermediate:
//   Conv1 raw       : 4 x 26 x 26 INT32 Q8.8
//   ReLU1           : 4 x 26 x 26 INT8 Q4.4
//   Pool1           : 4 x 13 x 13 INT8 Q4.4
//
// Output:
//   169 x 32-bit words
//   = 676 canonical INT8 values
//
// Ordering:
//   [channel][row][column]
//
// Control:
//   i_start accepted only when idle.
//   o_busy remains high during entire Conv1 operation.
//   o_done asserted with final reordered output word.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module conv1_layer (
    input  wire        i_clk,
    input  wire        i_rst_n,

    input  wire        i_start,

    input  wire        i_valid,
    input  wire [7:0]  i_pixel,

    output wire        o_busy,

    output wire        o_valid,
    output wire [31:0] o_data,

    output wire        o_done
);

    // =========================================================================
    // Layer control
    // =========================================================================

    reg r_busy;

    wire start_accept;

    assign start_accept =
        i_start && !r_busy;

    // -------------------------------------------------------------------------
    // Allow first pixel to be accepted on the same cycle as start_accept.
    // -------------------------------------------------------------------------

    wire pixel_valid;

    assign pixel_valid =
        i_valid && (r_busy || start_accept);

    // =========================================================================
    // Conv1 buffer
    //
    // 28x28 input stream
    // -> 676 valid 3x3 windows
    // =========================================================================

    wire        conv1_buf_valid;
    wire [71:0] conv1_window;

    conv1_buf u_conv1_buf (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),

        .i_start  (start_accept),

        .i_valid  (pixel_valid),
        .i_pixel  (i_pixel),

        .o_valid  (conv1_buf_valid),
        .o_window (conv1_window)
    );

    // =========================================================================
    // Conv1 ROM
    // =========================================================================

    wire [287:0] conv1_weights;
    wire [31:0]  conv1_biases;

    conv1_rom u_conv1_rom (
        .o_weights (conv1_weights),
        .o_biases  (conv1_biases)
    );

    // =========================================================================
    // Conv1 MAC
    //
    // 4 output channels x 9 products
    // -> 4 x signed INT32 raw accumulators
    // =========================================================================

    wire         conv1_calc_valid;
    wire [127:0] conv1_acc;

    conv1_calc u_conv1_calc (
        .i_clk     (i_clk),
        .i_rst_n   (i_rst_n),

        .i_valid   (conv1_buf_valid),
        .i_window  (conv1_window),

        .i_weights (conv1_weights),
        .i_biases  (conv1_biases),

        .o_valid   (conv1_calc_valid),
        .o_acc     (conv1_acc)
    );

    // =========================================================================
    // Quantization + ReLU
    //
    // raw INT32
    // -> round-to-nearest-even
    // -> saturation INT8
    // -> ReLU
    // =========================================================================

    wire        quant_valid;
    wire [31:0] quant_data;

    conv1_quant_relu u_conv1_quant_relu (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),

        .i_valid  (conv1_calc_valid),
        .i_acc    (conv1_acc),

        .o_valid  (quant_valid),
        .o_data   (quant_data)
    );

    // =========================================================================
    // Pool1 window generator
    //
    // Input:
    //   26 x 26 spatial positions
    //
    // Output:
    //   169 x 2x2 pooling windows
    // =========================================================================

    wire         pool1_buf_valid;
    wire [127:0] pool1_window;

    pool1_buf u_pool1_buf (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),

        .i_start  (start_accept),

        .i_valid  (quant_valid),
        .i_data   (quant_data),

        .o_valid  (pool1_buf_valid),
        .o_window (pool1_window)
    );

    // =========================================================================
    // Pool1 datapath
    //
    // IMPORTANT:
    // pool1.v is REGISTERED.
    //
    // Therefore clock and reset MUST be connected.
    //
    // pool1_buf_valid at cycle N
    // -> pool1_valid / pool1_data registered by pool1
    // =========================================================================

    wire        pool1_valid;
    wire [31:0] pool1_data;

    pool1 u_pool1 (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),

        .i_valid  (pool1_buf_valid),
        .i_window (pool1_window),

        .o_valid  (pool1_valid),
        .o_data   (pool1_data)
    );

    // =========================================================================
    // Pool1 reorder
    //
    // spatial-major:
    //
    //   pos0: ch0 ch1 ch2 ch3
    //   pos1: ch0 ch1 ch2 ch3
    //   ...
    //
    // ->
    //
    // canonical channel-major:
    //
    //   ch0[0..168]
    //   ch1[0..168]
    //   ch2[0..168]
    //   ch3[0..168]
    //
    // ->
    //
    // 169 x 32-bit words.
    // =========================================================================

    wire        reorder_valid;
    wire [31:0] reorder_data;
    wire        reorder_done;

    pool1_reorder u_pool1_reorder (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),

        // Reorder enters CAPTURE at beginning of layer operation
        // and waits for pool1_valid transactions.
        .i_start (start_accept),

        .i_valid (pool1_valid),
        .i_data  (pool1_data),

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

    // Busy goes low on the final output transaction.
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

            if (start_accept) begin

                r_busy <= 1'b1;

            end

            if (reorder_done) begin

                r_busy <= 1'b0;

            end

        end

    end

endmodule

`default_nettype wire