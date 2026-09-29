module conv2_quant_relu (
    input clk, input rst_n, input valid_in,
    input [255:0] acc_in,
    output valid_out, output [63:0] data_out
);
    // TODO: round-even -> saturation -> ReLU.
endmodule
