// This file is part of www.nand2tetris.org
// and the book "The Elements of Computing Systems"
// by Nisan and Schocken, MIT Press.
// File name: projects/04/Mult.asm

// Multiplies R0 and R1 and stores the result in R2.
// (R0, R1, R2 refer to RAM[0], RAM[1], and RAM[2], respectively.)
//
// This program only needs to handle arguments that satisfy
// R0 >= 0, R1 >= 0, and R0*R1 < 32768.

// Test for R1 > R0 and if so swap them around
	@R0
	D=M
	@R1
	D=M-D //; D = R1 - R0
	@MAIN
	D;JLT // skip swapping if (r1-r0) < 0 i.e. if R0 >= R1
	@R1
	D=M //; D = R1
	@R2
	M=D //; R2 = D
	@R0
	D=M //; D = R0
	@R1
	M=D //; R1 = D
	@R2
	D=M //; D = R2
	@R0
	M=D //; R0 = D
(MAIN)
	//; initialise R2 to 0
	@0
	D=A
	@R2
	M=D
	
	//; Test R0 is 0, if so quit
	@R0
	D=M
	@END
	D;JLE
	
	//; Test R1 is 0, if so quit
	@R1
	D=M
	@END
	D;JLE
	
(LOOP) //; R0 >= R1
	@R0
	D=M //; D=R0
	@R2
	M=M+D //; R2+=D
	@R1
	MD=M-1 //; R1-=1
	@LOOP
	D;JGT // Jump back to loop if R1 > 0
(END)
	@END
	0;JMP