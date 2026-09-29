module conv1_layer (
    input clk, input rst_n, input start, input valid_in,
    input [7:0] pixel_in,
    output busy, output valid_out, output [31:0] data_out, output done
);
    // TODO: compose Conv1 + Quant/ReLU + Pool1.
endmodule
