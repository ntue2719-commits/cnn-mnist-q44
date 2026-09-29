module sat_int8 (
    input  signed [31:0] value_in,
    output signed [7:0] value_out
);
    always @* begin
        if (value_in > 127)
            value_out = 8'sd127;
        else if (value_in < -128)
            value_out = -8'sd128;
        else
            value_out = value_in[7:0];
    end
endmodule
