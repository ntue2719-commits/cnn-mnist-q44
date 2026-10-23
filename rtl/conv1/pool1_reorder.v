`timescale 1ns / 1ps

//////////////////////////////////////////////////////////////////////////////////
// Design Name: Nguyen Tri Tue
// Module Name: pool1_reorder
// Description:
//   Reorders Pool1 spatial-major output into canonical channel-major order.
//
//   Input:
//     169 spatial transactions.
//
//     i_data[7:0]   = ch0[pos]
//     i_data[15:8]  = ch1[pos]
//     i_data[23:16] = ch2[pos]
//     i_data[31:24] = ch3[pos]
//
//   Canonical byte order:
//     ch0[0..168]
//     ch1[0..168]
//     ch2[0..168]
//     ch3[0..168]
//
//   Total:
//     676 bytes
//
//   Output:
//     169 x 32-bit words
//     4 consecutive canonical bytes per word.
//
//   No padding between channel boundaries.
//
//   Architecture:
//     IDLE -> CAPTURE -> DRAIN_WAIT -> DRAIN
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module pool1_reorder (
    input  wire        i_clk,
    input  wire        i_rst_n,
    input  wire        i_start,

    input  wire        i_valid,
    input  wire [31:0] i_data,

    output reg         o_valid,
    output reg  [31:0] o_data,
    output reg         o_done
);

    // -------------------------------------------------------------------------
    // State encoding
    // -------------------------------------------------------------------------

    localparam STATE_IDLE       = 2'd0;
    localparam STATE_CAPTURE    = 2'd1;
    localparam STATE_DRAIN_WAIT = 2'd2;
    localparam STATE_DRAIN      = 2'd3;

    reg [1:0] state;

    // -------------------------------------------------------------------------
    // Canonical temporary memory
    //
    // Address mapping:
    //
    // ch0 :   0 .. 168
    // ch1 : 169 .. 337
    // ch2 : 338 .. 506
    // ch3 : 507 .. 675
    //
    // canonical_index = channel * 169 + spatial_position
    // -------------------------------------------------------------------------

    reg [7:0] reorder_mem [0:675];

    // -------------------------------------------------------------------------
    // Capture position
    //
    // Range: 0..168
    // -------------------------------------------------------------------------

    reg [7:0] capture_count;

    // -------------------------------------------------------------------------
    // Output word counter
    //
    // 169 output words:
    // 0..168
    // -------------------------------------------------------------------------

    reg [7:0] drain_word_count;

    // -------------------------------------------------------------------------
    // Base canonical byte address for current output word
    //
    // base = drain_word_count * 4
    //
    // Maximum:
    // 168 * 4 = 672
    // -------------------------------------------------------------------------

    wire [9:0] drain_base;

    assign drain_base = {
        drain_word_count,
        2'b00
    };

    // -------------------------------------------------------------------------
    // Main control
    // -------------------------------------------------------------------------

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            state <= STATE_IDLE;

            capture_count   <= 8'd0;
            drain_word_count <= 8'd0;

            o_valid <= 1'b0;
            o_data  <= 32'd0;
            o_done  <= 1'b0;

        end
        else begin

            // Default pulse outputs
            o_valid <= 1'b0;
            o_done  <= 1'b0;

            case (state)

                // =============================================================
                // IDLE
                // =============================================================

                STATE_IDLE: begin

                    if (i_start) begin

                        capture_count    <= 8'd0;
                        drain_word_count <= 8'd0;

                        state <= STATE_CAPTURE;

                    end
                end

                // =============================================================
                // CAPTURE
                //
                // Convert incoming spatial-major words directly into
                // canonical channel-major memory addresses.
                // =============================================================

                STATE_CAPTURE: begin

                    if (i_valid) begin

                        // ch0
                        reorder_mem[
                            capture_count
                        ] <= i_data[7:0];

                        // ch1
                        reorder_mem[
                            10'd169 + capture_count
                        ] <= i_data[15:8];

                        // ch2
                        reorder_mem[
                            10'd338 + capture_count
                        ] <= i_data[23:16];

                        // ch3
                        reorder_mem[
                            10'd507 + capture_count
                        ] <= i_data[31:24];

                        // -----------------------------------------------------
                        // Last spatial position
                        // -----------------------------------------------------

                        if (capture_count == 8'd168) begin

                            capture_count    <= 8'd0;
                            drain_word_count <= 8'd0;

                            state <= STATE_DRAIN_WAIT;

                        end
                        else begin

                            capture_count <=
                                capture_count + 8'd1;

                        end

                    end
                end

                // =============================================================
                // DRAIN_WAIT
                //
                // Separates final capture write from first read transaction.
                // =============================================================

                STATE_DRAIN_WAIT: begin

                    drain_word_count <= 8'd0;

                    state <= STATE_DRAIN;

                end

                // =============================================================
                // DRAIN
                //
                // Read four consecutive canonical bytes per clock.
                // =============================================================

                STATE_DRAIN: begin

                    o_valid <= 1'b1;

                    o_data <= {
                        reorder_mem[drain_base + 10'd3],
                        reorder_mem[drain_base + 10'd2],
                        reorder_mem[drain_base + 10'd1],
                        reorder_mem[drain_base + 10'd0]
                    };

                    // ---------------------------------------------------------
                    // Last output word
                    // ---------------------------------------------------------

                    if (drain_word_count == 8'd168) begin

                        o_done <= 1'b1;

                        drain_word_count <= 8'd0;

                        state <= STATE_IDLE;

                    end
                    else begin

                        drain_word_count <=
                            drain_word_count + 8'd1;

                    end

                end

                // =============================================================
                // Recovery
                // =============================================================

                default: begin

                    state <= STATE_IDLE;

                    capture_count    <= 8'd0;
                    drain_word_count <= 8'd0;

                    o_valid <= 1'b0;
                    o_data  <= 32'd0;
                    o_done  <= 1'b0;

                end

            endcase
        end
    end

endmodule

`default_nettype wire