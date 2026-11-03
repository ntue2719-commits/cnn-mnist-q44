`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 09:52:49 PM
// Design Name: 
// Module Name: conv2_buf
// Description:
//   Phase-1 Conv2 input buffer / window generator.
//
// Input:
//   169 x 32-bit Mem1 words.
//   Each word contains four CONSECUTIVE bytes from the canonical
//   channel-major Pool1 stream:
//
//     ch0[0..168]
//     ch1[0..168]
//     ch2[0..168]
//     ch3[0..168]
//
//   IMPORTANT:
//   A 32-bit Mem1 word is NOT four channels at one spatial position.
//
// Architecture:
//   CAPTURE
//      169 x 32-bit
//      -> unpack 676 canonical bytes
//
//   DRAIN_WAIT
//      -> one cycle separating final capture write and reads
//
//   DRAIN
//      reconstruct feature[c][y][x]
//      -> generate 3x3x4 windows
//      -> 11x11 = 121 windows
//
// Tensor mapping:
//   canonical_index = c*169 + y*13 + x
//
// Window packing:
//   ch0 -> o_window[71:0]
//   ch1 -> o_window[143:72]
//   ch2 -> o_window[215:144]
//   ch3 -> o_window[287:216]
//
// Within each channel:
//   P0 P1 P2
//   P3 P4 P5
//   P6 P7 P8
//
//   P0 -> lowest byte of that channel's 72-bit region.
//
// Control:
//   - active-low synchronous reset
//   - i_start begins a new capture
//   - valid gaps during capture are legal
//   - exactly 121 o_valid pulses are produced per completed capture
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module conv2_buf (
    input  wire         i_clk,
    input  wire         i_rst_n,
    input  wire         i_start,

    input  wire         i_valid,
    input  wire [31:0]  i_data,

    output reg          o_valid,
    output reg  [287:0] o_window
);

    // =========================================================================
    // State encoding
    // =========================================================================

    localparam STATE_IDLE       = 2'd0;
    localparam STATE_CAPTURE    = 2'd1;
    localparam STATE_DRAIN_WAIT = 2'd2;
    localparam STATE_DRAIN      = 2'd3;

    reg [1:0] state;

    // =========================================================================
    // Canonical Pool1 byte memory
    //
    // byte 0..168   : ch0
    // byte 169..337 : ch1
    // byte 338..506 : ch2
    // byte 507..675 : ch3
    // =========================================================================

    reg [7:0] feature_mem [0:675];

    // =========================================================================
    // Capture counter
    //
    // 169 input words:
    //   word 0 .. word 168
    //
    // Each word contributes four canonical bytes.
    // =========================================================================

    reg [7:0] capture_word_count;

    wire [9:0] capture_base;

    assign capture_base = {
        capture_word_count,
        2'b00
    };

    // =========================================================================
    // Output spatial position
    //
    // Conv2 input = 13x13
    // kernel      = 3x3
    // output      = 11x11
    //
    // out_y = 0..10
    // out_x = 0..10
    // =========================================================================

    reg [3:0] out_y;
    reg [3:0] out_x;

    // =========================================================================
    // Integer temporaries used for window assembly
    // =========================================================================

    integer c;
    integer ky;
    integer kx;

    integer tensor_index;
    integer window_byte_index;

    // =========================================================================
    // Main sequential logic
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            state <= STATE_IDLE;

            capture_word_count <= 8'd0;

            out_y <= 4'd0;
            out_x <= 4'd0;

            o_valid  <= 1'b0;
            o_window <= 288'd0;

        end
        else begin

            // Default output-valid pulse behavior.
            o_valid <= 1'b0;

            case (state)

                // =============================================================
                // IDLE
                //
                // Wait for a new transaction.
                //
                // If i_start and i_valid are asserted together, word 0 is
                // accepted on this same rising edge.
                // =============================================================

                STATE_IDLE: begin

                    if (i_start) begin

                        capture_word_count <= 8'd0;

                        out_y <= 4'd0;
                        out_x <= 4'd0;

                        if (i_valid) begin

                            feature_mem[0] <= i_data[7:0];
                            feature_mem[1] <= i_data[15:8];
                            feature_mem[2] <= i_data[23:16];
                            feature_mem[3] <= i_data[31:24];

                            capture_word_count <= 8'd1;

                        end

                        state <= STATE_CAPTURE;

                    end

                end

                // =============================================================
                // CAPTURE
                //
                // Store four consecutive canonical bytes for every valid
                // Mem1 word.
                //
                // Counters advance ONLY when i_valid=1.
                // =============================================================

                STATE_CAPTURE: begin

                    if (i_valid) begin

                        feature_mem[
                            capture_base + 10'd0
                        ] <= i_data[7:0];

                        feature_mem[
                            capture_base + 10'd1
                        ] <= i_data[15:8];

                        feature_mem[
                            capture_base + 10'd2
                        ] <= i_data[23:16];

                        feature_mem[
                            capture_base + 10'd3
                        ] <= i_data[31:24];

                        if (capture_word_count == 8'd168) begin

                            capture_word_count <= 8'd0;

                            out_y <= 4'd0;
                            out_x <= 4'd0;

                            state <= STATE_DRAIN_WAIT;

                        end
                        else begin

                            capture_word_count <=
                                capture_word_count + 8'd1;

                        end

                    end

                end

                // =============================================================
                // DRAIN_WAIT
                //
                // Explicitly separate the final nonblocking memory write
                // from the first window read.
                // =============================================================

                STATE_DRAIN_WAIT: begin

                    out_y <= 4'd0;
                    out_x <= 4'd0;

                    state <= STATE_DRAIN;

                end

                // =============================================================
                // DRAIN
                //
                // Generate one complete 3x3x4 window every cycle.
                //
                // feature_mem index:
                //
                //   c*169 + (out_y+ky)*13 + (out_x+kx)
                //
                // o_window byte:
                //
                //   c*9 + ky*3 + kx
                // =============================================================

                STATE_DRAIN: begin

                    o_valid <= 1'b1;

                    for (c = 0; c < 4; c = c + 1) begin

                        for (ky = 0; ky < 3; ky = ky + 1) begin

                            for (kx = 0; kx < 3; kx = kx + 1) begin

                                tensor_index =
                                    (c * 169)
                                    +
                                    ((out_y + ky) * 13)
                                    +
                                    (out_x + kx);

                                window_byte_index =
                                    (c * 9)
                                    +
                                    (ky * 3)
                                    +
                                    kx;

                                o_window[
                                    (window_byte_index * 8) +: 8
                                ] <= feature_mem[tensor_index];

                            end

                        end

                    end

                    // ---------------------------------------------------------
                    // Advance 11x11 output position.
                    // ---------------------------------------------------------

                    if (out_x == 4'd10) begin

                        out_x <= 4'd0;

                        if (out_y == 4'd10) begin

                            // Final window = output position (10,10).
                            out_y <= 4'd0;

                            state <= STATE_IDLE;

                        end
                        else begin

                            out_y <= out_y + 4'd1;

                        end

                    end
                    else begin

                        out_x <= out_x + 4'd1;

                    end

                end

                // =============================================================
                // Recovery
                // =============================================================

                default: begin

                    state <= STATE_IDLE;

                    capture_word_count <= 8'd0;

                    out_y <= 4'd0;
                    out_x <= 4'd0;

                    o_valid  <= 1'b0;
                    o_window <= 288'd0;

                end

            endcase

        end

    end

endmodule

`default_nettype wire
