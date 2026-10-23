`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/08/2026 01:19:08 PM
// Design Name: 
// Module Name: tb_relu_int8
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

module tb_relu_int8;

    reg  signed [7:0] i_value;
    wire signed [7:0] o_value;

    integer errors;

    relu_int8 dut (
        .i_value(i_value),
        .o_value(o_value)
    );

    task check;
        input signed [7:0] test_value;
        input signed [7:0] expected;
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

        // Negative inputs -> zero
        check(-8'sd128, 8'sd0);
        check(-8'sd127, 8'sd0);
        check(-8'sd64,  8'sd0);
        check(-8'sd10,  8'sd0);
        check(-8'sd1,   8'sd0);

        // Zero
        check(8'sd0, 8'sd0);

        // Positive inputs pass through
        check(8'sd1,   8'sd1);
        check(8'sd10,  8'sd10);
        check(8'sd64,  8'sd64);
        check(8'sd126, 8'sd126);
        check(8'sd127, 8'sd127);

        if (errors == 0)
            $display("\n========== RELU_INT8 TEST: PASS ==========");
        else
            $display("\n========== RELU_INT8 TEST: FAIL (%0d errors) ==========", errors);

        $finish;
    end

endmodule

`default_nettype wire