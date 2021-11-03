// This file is part of www.nand2tetris.org
// and the book "The Elements of Computing Systems"
// by Nisan and Schocken, MIT Press.
// File name: projects/04/Fill.asm

// Runs an infinite loop that listens to the keyboard input.
// When a key is pressed (any key), the program blackens the screen,
// i.e. writes "black" in every pixel;
// the screen should remain fully black as long as the key is pressed. 
// When no key is pressed, the program clears the screen, i.e. writes
// "white" in every pixel;
// the screen should remain fully clear as long as no key is pressed.

// Put your code here.
// read the keyboard
(READ)
	@KBD
	D=M
	//; if @KBD > 0 then jump to BLACK
	@BLACK
	D;JGT
	//; else load 0 into R0
	@R0
	M=A
	//; and jump ahead to FILL
	@FILL
	0;JMP
(BLACK)
	//; load -1 into R0 for a full black row
	@R0
	M=-1
(FILL)
	//; fill the screen with the value in R0
	//; initialise R1 to the first screen row
	@SCREEN
	D=A
	@R1
	M=D
(DRAW)
	//; load the brush with the selected colour
	@R0
	D=M
	//; load the row location into A
	@R1
	A=M
	//; paint it
	M=D
	//; increment the pointer
	@R1
	MD=M+1
	//; test if we've got more to paint
	@24576
	D=A-D
	//; if so draw the next row
	@DRAW
	D;JGT
	//; else jump back to read
	@READ
	0;JMP