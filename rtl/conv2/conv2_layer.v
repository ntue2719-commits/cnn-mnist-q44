module conv2_layer (
    input clk, input rst_n, input start, input valid_in,
    input [31:0] data_in,
    output busy, output valid_out, output [63:0] data_out, output done
);
    // TODO: compose Conv2 + Quant/ReLU + Pool2.
endmodule
