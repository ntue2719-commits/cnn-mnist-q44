module round_shift_even (
    input  signed [31:0] acc_in,
    output signed [31:0] value_out
);
    integer shifted;
    integer remainder;

    always @* begin
        shifted  = acc_in >>> 4;
        remainder = acc_in - (shifted <<< 4);

        if (remainder > 8)
            shifted = shifted + 1;
        else if ((remainder == 8) && (shifted[0] == 1'b1))
            shifted = shifted + 1;

        value_out = shifted;
    end
endmodule
