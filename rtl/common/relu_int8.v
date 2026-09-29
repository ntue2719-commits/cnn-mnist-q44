module relu_int8 (
    input  signed [7:0] value_in,
    output signed [7:0] value_out
);
    assign value_out = (value_in < 0) ? 8'sd0 : value_in;
endmodule
