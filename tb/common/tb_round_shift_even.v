`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/08/2026 01:15:27 PM
// Design Name: 
// Module Name: tb_round_shift_even
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

module tb_round_shift_even;

    reg  signed [31:0] i_value;
    wire signed [31:0] o_value;

    integer errors;

    round_shift_even dut (
        .i_value(i_value),
        .o_value(o_value)
    );

    task check;
        input signed [31:0] test_value;
        input signed [31:0] expected;
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

        // Basic positive cases
        check(32'sd0,  32'sd0);
        check(32'sd1,  32'sd0);
        check(32'sd7,  32'sd0);
        check(32'sd8,  32'sd0);  // +0.5 -> 0
        check(32'sd9,  32'sd1);

        check(32'sd15, 32'sd1);
        check(32'sd16, 32'sd1);
        check(32'sd17, 32'sd1);

        check(32'sd23, 32'sd1);
        check(32'sd24, 32'sd2);  // +1.5 -> 2
        check(32'sd25, 32'sd2);

        check(32'sd39, 32'sd2);
        check(32'sd40, 32'sd2);  // +2.5 -> 2
        check(32'sd41, 32'sd3);

        // Negative cases
        check(-32'sd1,  32'sd0);
        check(-32'sd7,  32'sd0);
        check(-32'sd8,  32'sd0);   // -0.5 -> 0
        check(-32'sd9, -32'sd1);

        check(-32'sd15, -32'sd1);
        check(-32'sd16, -32'sd1);
        check(-32'sd17, -32'sd1);

        check(-32'sd23, -32'sd1);
        check(-32'sd24, -32'sd2);  // -1.5 -> -2
        check(-32'sd25, -32'sd2);

        check(-32'sd39, -32'sd2);
        check(-32'sd40, -32'sd2);  // -2.5 -> -2
        check(-32'sd41, -32'sd3);

        // Extra even/odd tie tests
        check(32'sd56,  32'sd4);   // 3.5 -> 4
        check(32'sd72,  32'sd4);   // 4.5 -> 4

        check(-32'sd56, -32'sd4);  // -3.5 -> -4
        check(-32'sd72, -32'sd4);  // -4.5 -> -4

        if (errors == 0)
            $display("\n========== ROUND_SHIFT_EVEN TEST: PASS ==========");
        else
            $display("\n========== ROUND_SHIFT_EVEN TEST: FAIL (%0d errors) ==========", errors);

        $finish;
    end

endmodule

`default_nettype wire