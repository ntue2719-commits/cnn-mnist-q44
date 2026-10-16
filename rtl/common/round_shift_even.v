
`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/08/2026 11:46:26 AM
// Design Name: Nguyen Tri Tue
// Module Name: round_shift_even
// Description:
//   Signed round-to-nearest-even with arithmetic right shift by 4.
//   Converts INT32 Q8.8-scale accumulator to an integer at Q4.4 scale.
//   No saturation is performed in this module.
//////////////////////////////////////////////////////////////////////////////////
         
`default_nettype none
module round_shift_even (
    input  signed [31:0] i_value,
    output signed [31:0] o_value
);
    wire signed [31:0]  shifted;
    wire        [3:0]   remainder;
    wire                round_up;

    // Arithmetic floor division by 16.
    assign shifted = i_value >>> 4;
    // Remainder is always in the range 0..15.
    assign remainder = i_value - (shifted <<< 4);

    // Round-to-nearest-even:
    // remainder > 8  -> round upward
    // remainder == 8 -> round upward only when shifted is odd
    assign round_up = (remainder > 4'd8) || ((remainder == 4'd8) && shifted[0] == 1'b1);

    assign o_value = shifted + (round_up ? 32'sd1 : 32'sd0);

endmodule
`default_nettype wire