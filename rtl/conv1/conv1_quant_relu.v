module conv1_quant_relu (
    input clk, input rst_n, input valid_in,
    input [127:0] acc_in,
    output valid_out, output [31:0] data_out
);
    // TODO: round-even -> saturation -> ReLU.
endmodule
