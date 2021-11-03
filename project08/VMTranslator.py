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
        command = self.text.split(" ")
        if command[0] in Command.arithmetics:
            self.type = CommandType.C_ARITHMETIC
            self.arg1 = command[0]
            return
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
    indirect_segments = ["THIS", "THAT", "LOCAL", "ARGUMENT"]

    def __init__(self, output, overwrite=False):
        self.out_stream = sys.stdout
        self.do_close = False
        if output is not None:
            assert overwrite or not os.path.exists(output), "output file already exists and overwrite flag not given!"
            self.out_stream = open(output, "w")
            self.do_close = True
        self.label_count = -1
        self.current_file = None
        self.current_function = None
        self.return_idx = 0
        self.line_count = 0

    def close(self):
        if self.do_close:
            self.out_stream.close()

    def get_bool_label(self):
        self.label_count += 1
        return f".bool_label{self.label_count}"

    # TODO: implement the label / symbol getting for pushpop, and allow for hard-coded ram locations
    def write_arithmetic(self, command: str):
        self.write(f"//{command}")
        # pop first argument
        self.write_pushpop("pop", "RAM", 14)
        # pop second argument if it's not unary
        if command not in ["neg", "not"]:
            self.write_pushpop("pop", "RAM", 13)
            self.write("@R13")
            self.write("D=M")
        self.write("@R14")
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
            self.write("@R14")
            self.write("M=0")
            self.write(f"@{label_done}")
            self.write("0;JMP")
            self.write(f"({label_true})")
            self.write("@R14")
            self.write("M=-1")
            self.write(f"({label_done})")
        self.write_pushpop("push", "RAM", 14)

    def write_address(self, segment, index):
        segment = segment.upper()
        if segment == "CONSTANT":
            raise ValueError("CONSTANT is not a real address")
        elif segment == "RAM":
            self.write(f"@R{index}")
        elif segment == "POINTER":
            if index > 1:
                warnings.warn("Assigning to a pointer >1 (not this or that)")
            self.write(f"@R{3 + index}")
        elif segment == "TEMP":
            self.write(f"@R{5 + index}")
        elif segment == "STATIC":
            self.write(f"@{self.current_file}.{index}")
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

    def write_pop_into_d(self):
        self.write("@SP")
        self.write("AM=M-1")
        self.write("D=M")

    def write_pushpop(self, push_or_pop="push", segment="", index=0, push_straight_from_d=False):
        if push_straight_from_d:
            self.write("// push D")
        else:
            self.write(f"//{push_or_pop}, {segment}[{index}]")
        if push_or_pop == "push":
            if segment.upper() == "CONSTANT":
                self.write(f"@{index}")
                self.write("D=A")
            elif not push_straight_from_d:
                self.write_address(segment, index)
                self.write("D=M")
            self.write("@SP")
            self.write("AM=M+1")
            self.write("A=A-1")
            self.write("M=D")
        else:
            if segment.upper() not in CodeWriter.indirect_segments:
                # the simple case won't overwrite the D-register, so we can safely call write_address()
                self.write_pop_into_d()
                self.write_address(segment, index)
                self.write("M=D")
            else:
                # calling write_address will overwrite the D register so we need to be careful and roundabout
                # get the address we want to push into
                self.write_address(segment, index)
                # save it in D
                self.write("D=A")
                # then save that into R15
                self.write("@R15")
                self.write("M=D")
                # now pop the stack into D
                self.write_pop_into_d()
                # now point to the address we saved into R15
                self.write("@R15")
                self.write("A=M")
                # and finally deposit the data from the stack into it
                self.write("M=D")

    def write_command(self, command: Command):
        if command.type == CommandType.C_NONE:
            return
        if command.type == CommandType.C_LABEL:
            self.write_label(command.arg1)
        if command.type == CommandType.C_ARITHMETIC:
            return self.write_arithmetic(command.arg1)
        elif command.type in (CommandType.C_PUSH, CommandType.C_POP):
            return self.write_pushpop(command.type == CommandType.C_POP and "pop" or "push", command.arg1, command.arg2)
        elif command.type == CommandType.C_GOTO:
            self.write_goto(command.arg1)
        elif command.type == CommandType.C_IF:
            self.write_if(command.arg1)
        elif command.type == CommandType.C_CALL:
            self.write_call(command.arg1, command.arg2)
        elif command.type == CommandType.C_RETURN:
            self.write_return()
        elif command.type == CommandType.C_FUNCTION:
            self.write_function(command.arg1, command.arg2)

    def write_goto(self, label):
        self.write(f"// goto {label}")
        self.write(self.get_label(label))
        self.write("0;JMP")

    def write_if(self, label):
        self.write(f"// if-goto {label}")
        self.write_pop_into_d()
        self.write(self.get_label(label))
        self.write("D;JNE")

    def write_return(self):
        self.write("// return")
        # store the current LCL in R13 (and D for now)
        self.write("@R1")
        self.write("D=M")
        self.write("@R13")
        self.write("M=D")

        # store return address in R14
        self.write("@5")
        self.write("A=D-A")
        self.write("D=M")
        self.write("@R14")
        self.write("M=D")

        # restore caller's return value
        self.write_pop_into_d()
        self.write("@ARG")
        self.write("A=M")
        self.write("M=D")

        # restore caller's SP
        self.write("@ARG")
        self.write("D=M+1")
        self.write("@SP")
        self.write("M=D")

        # restore that, this, arg and lcl
        for i in ("THAT", "THIS", "ARG", "LCL"):
            self.write("@R13")
            self.write("AM=M-1")
            self.write("D=M")
            self.write(f"@{i}")
            self.write("M=D")

        # goto ret
        self.write("@R14")
        self.write("A=M")
        self.write("0;JMP")

    def write_function(self, name, num_locals):
        self.current_function = name
        if not name.split(".")[:-2] == self.current_file.split(".")[:-2]:
            raise NameError(f"function {name} declared in {self.current_file} illegally")
        self.return_idx = 0
        self.write(f"// function {name} {num_locals}")
        # create entrypoint
        self.write(f"({self.current_function})")
        # initialise locals
        for i in range(num_locals):
            self.write_pushpop("push", "CONSTANT", 0)

    def get_label(self, label, is_declare=False):
        label = f"{self.current_function}${label}"
        if is_declare:
            return f"({label})"
        return f"@{label}"

    def write_label(self, label):
        self.write(self.get_label(label, True))

    def write_call(self, function, num_args):
        self.write(f"// call {function} {num_args}")
        return_label = self.get_label(f"$return_{self.return_idx}")[1:]
        self.return_idx += 1
        # push return-address
        self.write(f"@{return_label}")
        self.write("D=A")
        self.write_pushpop(push_straight_from_d=True)
        # push lcl, arg, this and that
        self.write_pushpop("push", "RAM", 1)
        self.write_pushpop("push", "RAM", 2)
        self.write_pushpop("push", "RAM", 3)
        self.write_pushpop("push", "RAM", 4)
        # reposition arg
        self.write("@SP")
        self.write("D=M")
        self.write(f"@{num_args}")
        self.write("D=D-A")
        self.write("@5")
        self.write("D=D-A")
        self.write("@ARG")
        self.write("M=D")
        # reposition LCL
        self.write("@SP")
        self.write("D=M")
        self.write("@LCL")
        self.write("M=D")
        # goto f
        self.write(f"@{function}")
        self.write("0;JMP")
        # declare return label
        self.write(f"({return_label})")

    def write_init(self, sys_init):
        self.write("//init Stack pointer to STACK[0] (RAM[256])")
        self.write("@256")
        self.write("D=A")
        self.write("@0")
        self.write("M=D")
        if sys_init:
            self.write_call("Sys.init", 0)

    def write(self, text):
        if text[:1] not in "(/":
            self.out_stream.write(text + f" //{self.line_count}\n")
            self.line_count += 1
        else:
            self.out_stream.write(text + "\n")

    def do_compile(self, filenames):
        sys_init = False
        for filename in filenames:
            if os.path.basename(filename)[:-3] == "Sys":
                sys_init = True
                break
        self.write_init(sys_init)
        for filename in filenames:
            print(f"Compiling {filename}")
            self.current_file = os.path.basename(filename)[:-3]
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
        sys.argv.append("Sys.vm")
    arg_parser = argparse.ArgumentParser(description="Compiles a .vm file or directory of .vm files",
                                         prog="vm_compiler.py")
    arg_parser.add_argument("vm", help="the vm file to assemble")
    # arg_parser.add_argument("--write", help="write a file, if --no-write just echo output",
    #                         action=argparse.BooleanOptionalAction, default=True)
    # arg_parser.add_argument("--overwrite", help="Overwrite file if it exists",
    #                         action=argparse.BooleanOptionalAction, default=True)
    _args = arg_parser.parse_args()
    _filename = _args.vm
    if os.path.isdir(_filename):
        search = os.path.join(_filename, "*.vm")
        _filenames = glob.glob(search)
        assert len(_filenames), "Directory must contain at least one .vm file"
        _outfile = os.path.join(_filename, os.path.basename(_filename) + ".asm")
    else:
        assert (_filename[-3:] == ".vm"), "vm must be a directory or .vm file"
        _filenames = [_filename]
        _outfile = _filename[:-3] + ".asm"

    # if not _args.write:
    #     _outfile = None
    _writer = CodeWriter(_outfile, True)  # _args.overwrite)
    _writer.do_compile(_filenames)

# 8.2.1 Program Flow Commands - page 187
