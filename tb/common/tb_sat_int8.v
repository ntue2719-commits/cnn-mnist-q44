`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/08/2026 01:17:26 PM
// Design Name: 
// Module Name: tb_sat_int8
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module tb_sat_int8;

    reg  signed [31:0] i_value;
    wire signed [7:0]  o_value;

    integer errors;

    sat_int8 dut (
        .i_value(i_value),
        .o_value(o_value)
    );

    task check;
        input signed [31:0] test_value;
        input signed [7:0]  expected;
        begin
            i_value = test_value;
            #1;

            if (o_value !== expected) begin
                $display(
                    "[FAIL] i_value=%0d expected=%0d got=%0d",
                    test_value, expected, o_value
                );
                errors = errors + 1;
            end
            else begin
                $display(
                    "[PASS] i_value=%0d -> %0d",
                    test_value, o_value
                );
            end
        end
    endtask

    initial begin
        errors  = 0;
        i_value = 0;

        #1;

        // Normal range
        check(32'sd0,    8'sd0);
        check(32'sd1,    8'sd1);
        check(32'sd50,   8'sd50);
        check(32'sd126,  8'sd126);
        check(32'sd127,  8'sd127);

        check(-32'sd1,    -8'sd1);
        check(-32'sd50,   -8'sd50);
        check(-32'sd127,  -8'sd127);
        check(-32'sd128,  -8'sd128);

        // Positive saturation
        check(32'sd128,   8'sd127);
        check(32'sd200,   8'sd127);
        check(32'sd1000,  8'sd127);
        check(32'sd32767, 8'sd127);

        // Negative saturation
        check(-32'sd129,   -8'sd128);
        check(-32'sd200,   -8'sd128);
        check(-32'sd1000,  -8'sd128);
        check(-32'sd32768, -8'sd128);

        if (errors == 0)
            $display("\n========== SAT_INT8 TEST: PASS ==========");
        else
            $display("\n========== SAT_INT8 TEST: FAIL (%0d errors) ==========", errors);

        $finish;
    end

endmodule

`default_nettype wire