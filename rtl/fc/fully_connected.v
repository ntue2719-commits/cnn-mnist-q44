module fully_connected (
    input clk, input rst_n, input start, input valid_in,
    input [63:0] data_in,
    input [15999:0] fc_weights,
    input [79:0] fc_bias,
    output busy, output valid_out, output [319:0] logits_out, output done
);
    // TODO: 10 parallel accumulators, 8 features/clock.
endmodule
