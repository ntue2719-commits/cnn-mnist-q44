`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:52:45 PM
// Design Name: Nguyen Tri Tue
// Module Name: argmax
//
// Description:
//   Find the class index with maximum signed INT32 logit.
//
// Input packing:
//
//   class 0 -> i_logits[31:0]
//   class 1 -> i_logits[63:32]
//   ...
//   class 9 -> i_logits[319:288]
//
// Comparison:
//   signed INT32
//
// Tie-breaking:
//   lower class index wins.
//
// Implementation:
//
//   Start with class 0.
//   Compare classes 1..9 using STRICT:
//
//       if (candidate > current_max)
//
//   Because equality does not replace current winner,
//   the first / lowest class index is preserved.
//
// Latency:
//   1 registered cycle from i_valid to o_valid.
//
// No quantization or arithmetic modification of logits.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module argmax (
    input  wire         i_clk,
    input  wire         i_rst_n,

    input  wire         i_valid,
    input  wire [319:0] i_logits,

    output reg          o_valid,
    output reg  [3:0]   o_digit
);

    // =========================================================================
    // Combinational argmax
    // =========================================================================

    integer class_idx;

    reg signed [31:0] current_max;
    reg signed [31:0] candidate;

    reg [3:0] max_index;

    always @(*) begin

        // ---------------------------------------------------------------------
        // Initialize from class 0.
        // ---------------------------------------------------------------------

        current_max =
            $signed(i_logits[31:0]);

        max_index =
            4'd0;

        // ---------------------------------------------------------------------
        // Strict signed comparison.
        //
        // STRICT > guarantees lower-index tie breaking.
        // ---------------------------------------------------------------------

        for (
            class_idx = 1;
            class_idx < 10;
            class_idx = class_idx + 1
        ) begin

            candidate =
                $signed(
                    i_logits[
                        (class_idx*32) +: 32
                    ]
                );

            if (candidate > current_max) begin

                current_max =
                    candidate;

                max_index =
                    class_idx[3:0];

            end

        end

    end

    // =========================================================================
    // Registered output
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            o_valid <= 1'b0;
            o_digit <= 4'd0;

        end
        else begin

            o_valid <= i_valid;

            if (i_valid) begin

                o_digit <= max_index;

            end

        end

    end

endmodule

`default_nettype wire