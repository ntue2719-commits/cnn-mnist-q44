`default_nettype none
module pool1_buf (
    input wire i_clk,
    input wire i_rst_n,
    input wire i_start,

    input wire i_valid,
    input wire [31:0] i_data,

    output reg o_valid,
    output reg [127:0] o_window
);
    reg [4:0] x_count;
    reg [4:0] y_count;

    //Previous row: line_buf[x] = data from row y-1, column x
    reg [31:0] line_buf [0:25];

    //Horizontal delays
    reg [31:0] prev_top; //(y-1, x-1)
    reg [31:0] prev_curr //(y  , x-1)

    always @(posedge i_clk) begin
        if(!i_rst_n) begin
            x_count <= 5'd0;
            y_count <= 5'd0;

            prev_top <= 32'd0;
            prev_curr <= 32'd0;

            o_valid <= 1'b0;
            o_window <= 128'd0;
        end
        else begin
            o_valid <= 1'b0;

            if(i_valid) begin
                //Current input is D(y,x)
                //Valid stride-2 windows ends at odd x and odd y
                //x,y = 1,3,5,....25 -> 13x13 = 169 windows

                if((y_count[0] == 1'b1) && (x_count[0] == 1'b1)) begin
                    o_valid <= 1'b1;
                    o_window <= {
                        i_data,             //D= (y, x)
                        pre_curr,           //C= (y, x-1)
                        line_buf[x_count],  //B= (y-1, x)
                        pre_top             //A= (y-1, x-1)   
                    };
                end

                //Horizontal state for next accepted spatial position
                prev_top <= line_buf[x_count];
                pre_curr <= i_data;

                //Current row becomes the previous row for the next row
                line_buf[x_count] <= i_data;

                //Advanced only on accepted valid transactions.
                if(x_count == 5'd25) begin
                    x_count <= 5'd0;

                    if(y_count == 5'd25) 
                        y_count <= 5'd0;
                    else 
                        y_count <= y_count + 5'd1;
                end
                else begin
                    x_count <= x_count + 5'd1;
                end
            end
        end
    end
endmodule
`default_nettype wire