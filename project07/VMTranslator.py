import glob
import os.path
import argparse
import warnings
from enum import Enum, auto
import sys
from typing import Optional


class CommandType(Enum):
    C_NONE = auto()
    C_ARITHMETIC = auto()
    C_PUSH = auto()
    C_POP = auto()
    C_LABEL = auto()
    C_GOTO = auto()
    C_IF = auto()
    C_FUNCTION = auto()
    C_RETURN = auto()
    C_CALL = auto()


class Command:
    line_count = 0

    arithmetics = ["add", "sub", "neg", "eq", "gt", "lt", "and", "or", "not"]
    non_arithmetics = {
        "push": CommandType.C_PUSH,
        "pop": CommandType.C_POP,
        "label": CommandType.C_LABEL,
        "goto": CommandType.C_GOTO,
        "if-goto": CommandType.C_IF,
        "function": CommandType.C_FUNCTION,
        "return": CommandType.C_RETURN,
        "call": CommandType.C_CALL
    }

    def parse(self):
        if self.text in Command.arithmetics:
            self.type = CommandType.C_ARITHMETIC
            self.arg1 = self.text
            return
        command = self.text.split(" ")
        self.type = Command.non_arithmetics[command[0]]
        try:
            self.arg1 = command[1]
        except IndexError:
            return
        try:
            self.arg2 = int(command[2])
        except IndexError:
            return
        except ValueError:
            self.arg2 = command[2]

    def __init__(self, text):
        self.line_idx = Command.line_count
        self.type = CommandType.C_NONE
        self.text = text.strip().split("//")[0]
        if len(self.text) == 0:
            return
        self.arg1 = ""
        self.arg2 = -1
        self.parse()


class Parser:
    def __init__(self, input_stream):
        self.data = input_stream.readlines()
        self.currentCommand: Optional[Command] = None
        self.commands = []
        self.symbol_table = {"SP": 0,
                             "LCL": 1,
                             "ARG": 2,
                             "THIS": 3,
                             "THAT": 4,
                             "SCREEN": 16384,
                             "KBD": 24576}
        for i in range(16):
            self.symbol_table[f'R{i}'] = i
        self.read_commands()

    def has_more_commands(self):
        return len(self.commands) > 0

    def read_commands(self):
        while len(self.data) > 0:
            command = Command(self.data.pop(0))
            if command.type == CommandType.C_NONE:
                continue
            if command.type == CommandType.C_LABEL:
                pass
            else:
                self.commands.append(command)
        # # loop through the symbols and build symbol table
        # symbol_idx = 16
        # for command in self.commands:
        #     if command.is_symbol:
        #         if command.symbol not in self.symbol_table:
        #             self.symbol_table[command.symbol] = symbol_idx
        #             symbol_idx += 1

    def advance(self):
        self.currentCommand = self.commands.pop(0)

    def command_type(self):
        return self.currentCommand.type

    def arg1(self):
        return self.currentCommand.arg1

    def arg2(self):
        return self.currentCommand.arg2


class CodeWriter:
    def __init__(self, output, overwrite=False):
        self.out_stream = sys.stdout
        self.do_close = False
        if output is not None:
            assert overwrite or not os.path.exists(output), "output file already exists and overwrite flag not given!"
            self.out_stream = open(output, "w")
            self.do_close = True
        self.label_count = -1

    def close(self):
        if self.do_close:
            self.out_stream.close()

    def get_bool_label(self):
        self.label_count += 1
        return f".bool_label{self.label_count}"

    # TODO: implement the label / symbol getting for pushpop, and allow for hard-coded ram locations
    def write_arithmetic(self, command: Command):
        command = command.arg1
        self.write(f"//{command}")
        # pop first argument
        self.write_pushpop("pop", "RAM", 14)
        # pop second argument if it's not unary
        if command not in ["neg", "not"]:
            self.write_pushpop("pop", "RAM", 13)
            self.write("@13")
            self.write("D=M")
        self.write("@14")
        if command == "neg":
            self.write("M=-M")
        elif command == "not":
            self.write("M=!M")
        elif command == "and":
            self.write("M=D&M")
        elif command == "or":
            self.write("M=D|M")
        elif command == "add":
            self.write("M=M+D")
        elif command == "sub":
            self.write("M=D-M")
        else:
            label_base = self.get_bool_label()
            label_true = label_base + "_is_true"
            label_done = label_base + "_all_done"
            self.write("D=D-M")
            self.write(f"@{label_true}")
            if command == "eq":
                self.write("D;JEQ")
            if command == "lt":
                self.write("D;JLT")
            if command == "gt":
                self.write("D;JGT")
            self.write("@14")
            self.write("M=0")
            self.write(f"@{label_done}")
            self.write("0;JMP")
            self.write(f"({label_true})")
            self.write("@14")
            self.write("M=-1")
            self.write(f"({label_done})")
        self.write_pushpop("push", "RAM", 14)

    indirect_segments = ["THIS", "THAT", "LOCAL", "ARGUMENT"]

    def write_address(self, segment, index):
        segment = segment.upper()
        if segment == "CONSTANT":
            raise ValueError("CONSTANT is not a real address")
        elif segment == "RAM":
            self.write(f"@{index}")
        elif segment == "POINTER":
            if index > 1:
                warnings.warn("Assigning to a pointer >1 (not this or that)")
            self.write(f"@{3+index}")
        elif segment == "TEMP":
            self.write(f"@{5+index}")
        elif segment == "STATIC":
            self.write(f"@{16+index}")
        elif segment == "LOCAL":
            self.write("@LCL")
        elif segment == "ARGUMENT":
            self.write("@ARG")
        elif segment == "THIS":
            self.write("@THIS")
        elif segment == "THAT":
            self.write("@THAT")
        else:
            raise ValueError(f"{segment} is not a valid segment")
        if segment in CodeWriter.indirect_segments:
            self.write("D=M")
            self.write(f"@{index}")
            self.write("A=D+A")

    def write_pushpop(self, push_or_pop, segment, index):
        self.write(f"//{push_or_pop}, {segment}[{index}]")
        if push_or_pop == "push":
            if segment.upper() == "CONSTANT":
                self.write(f"@{index}")
                self.write("D=A")
            else:
                self.write_address(segment, index)
                self.write("D=M")
            self.write("@0")
            self.write("AM=M+1")
            self.write("A=A-1")
            self.write("M=D")
        else:
            if segment.upper() not in CodeWriter.indirect_segments:
                # the simple case won't overwrite the D-register, so we can safely call write_address()
                self.write("@0")
                self.write("AM=M-1")
                self.write("D=M")
                self.write_address(segment, index)
                self.write("M=D")
            else:
                # calling write_address will overwrite the D register so we need to be careful and roundabout
                # get the address we want to push into
                self.write_address(segment, index)
                # save it in D
                self.write("D=A")
                # then save that into R15
                self.write("@15")
                self.write("M=D")
                # now get (and decrement) the stack pointer
                self.write("@0")
                self.write("AM=M-1")
                # and pop the stack into D
                self.write("D=M")
                # now point to the address we saved into R15
                self.write("@15")
                self.write("A=M")
                # and finally deposit the data from the stack into it
                self.write("M=D")

    def write_command(self, command: Command):
        if command.type == CommandType.C_NONE:
            return
        if command.type == CommandType.C_ARITHMETIC:
            return self.write_arithmetic(command)
        if command.type in (CommandType.C_PUSH, CommandType.C_POP):
            return self.write_pushpop(command.type == CommandType.C_POP and "pop" or "push", command.arg1, command.arg2)

    def write(self, text):
        self.out_stream.write(text + "\n")

    def do_init(self):
        self.write("//init Stack pointer to STACK[0] (RAM[256])")
        self.write("@256")
        self.write("D=A")
        self.write("@0")
        self.write("M=D")
        self.write("//init LCL pointer to 300")
        self.write("@300")
        self.write("D=A")
        self.write("@1")
        self.write("M=D")
        self.write("//init ARG pointer to 400")
        self.write("@400")
        self.write("D=A")
        self.write("@2")
        self.write("M=D")
        self.write("//init THIS pointer to 3000")
        self.write("@3000")
        self.write("D=A")
        self.write("@3")
        self.write("M=D")
        self.write("//init THAT pointer to 3010")
        self.write("@3010")
        self.write("D=A")
        self.write("@4")
        self.write("M=D")

    def do_compile(self, filenames):
        # self.do_init()
        for filename in filenames:
            print(f"Compiling {filename}")
            with open(filename) as in_stream:
                parser = Parser(in_stream)
                while parser.has_more_commands():
                    parser.advance()
                    self.write_command(parser.currentCommand)
            # self.write("@.END")
            # self.write("(.END)")
            # self.write("0;JMP")
        self.close()


if __name__ == "__main__":
    if sys.argv[0][0] == "C":
        sys.argv.append(".")
    arg_parser = argparse.ArgumentParser(description="Compiles a .vm file or directory of .vm files",
                                         prog="vm_compiler.py")
    arg_parser.add_argument("vm", help="the vm file to assemble")
    # arg_parser.add_argument("--write", help="write a file, if --no-write just echo output",
    #                         action=argparse.BooleanOptionalAction, default=True)
    # arg_parser.add_argument("--overwrite", help="Overwrite file if it exists",
    #                         action=argparse.BooleanOptionalAction, default=True)
    _args = arg_parser.parse_args()
    _filename = _args.vm
    _outfile = None
    _filenames = None
    if os.path.isdir(_filename):
        search = os.path.join(_filename, "*.vm")
        _filenames = glob.glob(search)
        assert len(_filenames), "Directory must contain at least one .vm file"
        if len(_filenames) > 1:
            _outfile = os.path.basename(_filename) + ".asm"
        else:
            _filename = _filenames[0]
    if _outfile is None:
        assert (_filename[-3:] == ".vm"), "vm must be a directory or .vm file"
        _filenames = [_filename]
        _outfile = _filename[:-3] + ".asm"

    # if not _args.write:
    #     _outfile = None
    _writer = CodeWriter(_outfile, True)  # _args.overwrite)
    _writer.do_compile(_filenames)