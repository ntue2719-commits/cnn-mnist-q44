`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:20:06 PM
// Design Name: Nguyen Tri Tue
// Module Name: pool2_reorder
//
// Description:
//   Reorder Pool2 spatial-major output into canonical channel-major order.
//
// Input:
//   25 spatial transactions.
//
//   i_data[7:0]   = ch0[pos]
//   i_data[15:8]  = ch1[pos]
//   ...
//   i_data[63:56] = ch7[pos]
//
// Canonical byte order:
//   ch0[0..24]
//   ch1[0..24]
//   ...
//   ch7[0..24]
//
// Total:
//   200 bytes
//
// Output:
//   25 x 64-bit words
//   8 consecutive canonical bytes per word.
//
// No padding between channel boundaries.
//
// Architecture:
//   IDLE -> CAPTURE -> DRAIN_WAIT -> DRAIN
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module pool2_reorder (
    input  wire        i_clk,
    input  wire        i_rst_n,
    input  wire        i_start,

    input  wire        i_valid,
    input  wire [63:0] i_data,

    output reg         o_valid,
    output reg  [63:0] o_data,
    output reg         o_done
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
    // Canonical temporary memory
    //
    // ch0 :   0 .. 24
    // ch1 :  25 .. 49
    // ch2 :  50 .. 74
    // ch3 :  75 .. 99
    // ch4 : 100 .. 124
    // ch5 : 125 .. 149
    // ch6 : 150 .. 174
    // ch7 : 175 .. 199
    // =========================================================================

    reg [7:0] reorder_mem [0:199];

    // =========================================================================
    // Capture position
    // =========================================================================

    reg [4:0] capture_count;

    // =========================================================================
    // Drain word counter
    //
    // 25 words:
    // 0..24
    // =========================================================================

    reg [4:0] drain_word_count;

    // =========================================================================
    // Base canonical byte address
    //
    // base = drain_word_count * 8
    //
    // max = 24*8 = 192
    // =========================================================================

    wire [7:0] drain_base;

    assign drain_base = {
        drain_word_count,
        3'b000
    };

    // =========================================================================
    // Main control
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            state <= STATE_IDLE;

            capture_count    <= 5'd0;
            drain_word_count <= 5'd0;

            o_valid <= 1'b0;
            o_data  <= 64'd0;
            o_done  <= 1'b0;

        end
        else begin

            o_valid <= 1'b0;
            o_done  <= 1'b0;

            case (state)

                // =============================================================
                // IDLE
                // =============================================================

                STATE_IDLE: begin

                    if (i_start) begin

                        capture_count    <= 5'd0;
                        drain_word_count <= 5'd0;

                        state <= STATE_CAPTURE;

                    end

                end

                // =============================================================
                // CAPTURE
                //
                // Convert spatial-major words directly into canonical
                // channel-major byte addresses.
                // =============================================================

                STATE_CAPTURE: begin

                    if (i_valid) begin

                        reorder_mem[  0 + capture_count] <= i_data[7:0];
                        reorder_mem[ 25 + capture_count] <= i_data[15:8];
                        reorder_mem[ 50 + capture_count] <= i_data[23:16];
                        reorder_mem[ 75 + capture_count] <= i_data[31:24];

                        reorder_mem[100 + capture_count] <= i_data[39:32];
                        reorder_mem[125 + capture_count] <= i_data[47:40];
                        reorder_mem[150 + capture_count] <= i_data[55:48];
                        reorder_mem[175 + capture_count] <= i_data[63:56];

                        if (capture_count == 5'd24) begin

                            capture_count    <= 5'd0;
                            drain_word_count <= 5'd0;

                            state <= STATE_DRAIN_WAIT;

                        end
                        else begin

                            capture_count <= capture_count + 5'd1;

                        end

                    end

                end

                // =============================================================
                // DRAIN_WAIT
                // =============================================================

                STATE_DRAIN_WAIT: begin

                    drain_word_count <= 5'd0;

                    state <= STATE_DRAIN;

                end

                // =============================================================
                // DRAIN
                //
                // Emit 8 consecutive canonical bytes per cycle.
                // =============================================================

                STATE_DRAIN: begin

                    o_valid <= 1'b1;

                    o_data <= {
                        reorder_mem[drain_base + 8'd7],
                        reorder_mem[drain_base + 8'd6],
                        reorder_mem[drain_base + 8'd5],
                        reorder_mem[drain_base + 8'd4],
                        reorder_mem[drain_base + 8'd3],
                        reorder_mem[drain_base + 8'd2],
                        reorder_mem[drain_base + 8'd1],
                        reorder_mem[drain_base + 8'd0]
                    };

                    if (drain_word_count == 5'd24) begin

                        o_done <= 1'b1;

                        drain_word_count <= 5'd0;

                        state <= STATE_IDLE;

                    end
                    else begin

                        drain_word_count <= drain_word_count + 5'd1;

                    end

                end

                // =============================================================
                // Recovery
                // =============================================================

                default: begin

                    state <= STATE_IDLE;

                    capture_count    <= 5'd0;
                    drain_word_count <= 5'd0;

                    o_valid <= 1'b0;
                    o_data  <= 64'd0;
                    o_done  <= 1'b0;

                end

            endcase

        end

    end

endmodule

`default_nettype wire