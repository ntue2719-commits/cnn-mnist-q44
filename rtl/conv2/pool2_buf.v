`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:09:04 PM
// Design Name: Nguyen Tri Tue
// Module Name: pool2_buf
//
// Description:
//   Phase-1 Pool2 2x2 window generator.
//
// Input:
//   11 x 11 = 121 spatial positions.
//   Each position contains 8 x INT8 Q4.4 channels:
//
//     i_data[7:0]   = ch0
//     i_data[15:8]  = ch1
//     ...
//     i_data[63:56] = ch7
//
// Pool:
//   2x2
//   stride = 2
//
// Input size:
//   11 x 11
//
// Output size:
//   floor(11/2) x floor(11/2)
//   = 5 x 5
//   = 25 windows
//
// IMPORTANT:
//   Input row 10 and column 10 are discarded.
//
// Window:
//
//        A B
//        C D
//
// Packing:
//
//   o_window[63:0]    = A
//   o_window[127:64]  = B
//   o_window[191:128] = C
//   o_window[255:192] = D
//
// Architecture:
//   IDLE
//     -> CAPTURE 121 accepted words
//     -> DRAIN_WAIT
//     -> DRAIN 25 pooling windows
//
// Valid gaps:
//   Capture position advances only when i_valid=1.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module pool2_buf (
    input  wire         i_clk,
    input  wire         i_rst_n,
    input  wire         i_start,

    input  wire         i_valid,
    input  wire [63:0]  i_data,

    output reg          o_valid,
    output reg [255:0]  o_window
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
    // Spatial temporary memory
    //
    // Address:
    //   addr = y*11 + x
    //
    // Each entry:
    //   8 channels x INT8 = 64 bits
    // =========================================================================

    reg [63:0] spatial_mem [0:120];

    // =========================================================================
    // Capture counter
    //
    // 0..120
    // =========================================================================

    reg [6:0] capture_count;

    // =========================================================================
    // Pool output coordinates
    //
    // pool_y = 0..4
    // pool_x = 0..4
    // =========================================================================

    reg [2:0] pool_y;
    reg [2:0] pool_x;

    // =========================================================================
    // Address calculations
    //
    // Top-left input coordinate of current pool:
    //
    //   iy = 2*pool_y
    //   ix = 2*pool_x
    //
    // Addresses:
    //
    //   A = iy*11 + ix
    //   B = A + 1
    //   C = A + 11
    //   D = A + 12
    // =========================================================================

    integer base_addr;

    // =========================================================================
    // Main sequential logic
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            state <= STATE_IDLE;

            capture_count <= 7'd0;

            pool_y <= 3'd0;
            pool_x <= 3'd0;

            o_valid  <= 1'b0;
            o_window <= 256'd0;

        end
        else begin

            // Default pulse behavior.
            o_valid <= 1'b0;

            case (state)

                // =============================================================
                // IDLE
                // =============================================================

                STATE_IDLE: begin

                    if (i_start) begin

                        capture_count <= 7'd0;

                        pool_y <= 3'd0;
                        pool_x <= 3'd0;

                        // -----------------------------------------------------
                        // Support i_start + i_valid in same cycle.
                        // -----------------------------------------------------

                        if (i_valid) begin

                            spatial_mem[0] <= i_data;

                            capture_count <= 7'd1;

                        end

                        state <= STATE_CAPTURE;

                    end

                end

                // =============================================================
                // CAPTURE
                //
                // Store accepted 11x11 spatial stream.
                // Counter advances only on i_valid.
                // =============================================================

                STATE_CAPTURE: begin

                    if (i_valid) begin

                        spatial_mem[capture_count] <= i_data;

                        // -----------------------------------------------------
                        // Last spatial input = word 120.
                        // -----------------------------------------------------

                        if (capture_count == 7'd120) begin

                            capture_count <= 7'd0;

                            pool_y <= 3'd0;
                            pool_x <= 3'd0;

                            state <= STATE_DRAIN_WAIT;

                        end
                        else begin

                            capture_count <=
                                capture_count + 7'd1;

                        end

                    end

                end

                // =============================================================
                // DRAIN_WAIT
                //
                // Separate final memory write from first memory read.
                // =============================================================

                STATE_DRAIN_WAIT: begin

                    pool_y <= 3'd0;
                    pool_x <= 3'd0;

                    state <= STATE_DRAIN;

                end

                // =============================================================
                // DRAIN
                //
                // Emit one 2x2 pooling window per cycle.
                // =============================================================

                STATE_DRAIN: begin

                    o_valid <= 1'b1;

                    // ---------------------------------------------------------
                    // Input top-left address:
                    //
                    // ((2*pool_y) * 11) + (2*pool_x)
                    // ---------------------------------------------------------

                    base_addr =
                        ((pool_y << 1) * 11)
                        +
                        (pool_x << 1);

                    // ---------------------------------------------------------
                    // Window packing:
                    //
                    // [63:0]    A
                    // [127:64]  B
                    // [191:128] C
                    // [255:192] D
                    // ---------------------------------------------------------

                    o_window[63:0] <=
                        spatial_mem[base_addr];

                    o_window[127:64] <=
                        spatial_mem[base_addr + 1];

                    o_window[191:128] <=
                        spatial_mem[base_addr + 11];

                    o_window[255:192] <=
                        spatial_mem[base_addr + 12];

                    // ---------------------------------------------------------
                    // Advance output coordinates.
                    //
                    // 5 x 5:
                    //
                    // (0,0)...(0,4)
                    // ...
                    // (4,0)...(4,4)
                    // ---------------------------------------------------------

                    if (pool_x == 3'd4) begin

                        pool_x <= 3'd0;

                        if (pool_y == 3'd4) begin

                            pool_y <= 3'd0;

                            state <= STATE_IDLE;

                        end
                        else begin

                            pool_y <= pool_y + 3'd1;

                        end

                    end
                    else begin

                        pool_x <= pool_x + 3'd1;

                    end

                end

                // =============================================================
                // Recovery
                // =============================================================

                default: begin

                    state <= STATE_IDLE;

                    capture_count <= 7'd0;

                    pool_y <= 3'd0;
                    pool_x <= 3'd0;

                    o_valid  <= 1'b0;
                    o_window <= 256'd0;

                end

            endcase

        end

    end

endmodule

`default_nettype wire