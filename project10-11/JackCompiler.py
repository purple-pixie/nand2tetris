import os
import re
import sys
import argparse
import glob
import token
from typing import IO, Union, Dict

keywords = "class constructor function method field static var int char boolean " \
           "void true false null this let do if else while return".split(" ")

symbols = "{}()[].,;+-*&|<=>~/"
ops_to_vm = {
    "+": "add",
    "-": "sub",
    "=": "eq",
    "<": "lt",
    ">": "gt",
    "|": "or",
    "&": "and",
    "~": "not"
}
word_breaks = symbols[:-1] + " \t\n"
digits = "0123456789"
identifier_pattern = re.compile("[A-Za-z0-9_]+")
symbols_to_xml = {"<": "&lt;", ">": "&gt;", "\"": "&quot;", "&": "&amp;"}
kinds_to_segments = {
    "VAR": "local",
    "FIELD": "this",
    "STATIC": "static",
    "ARG": "argument"
}


class ParseError(Exception):
    pass


class Token:
    def __init__(self, text):
        self.type = None
        self.value: Union[str, int] = text

        if not len(text):
            return
        if text[0] in " \n\t":
            return
        if text[0] == "\"":
            self.type = "STRING_CONST"
            self.value = text[1:-1]
        elif text in keywords:
            self.type = "KEYWORD"
        elif text in symbols:
            self.type = "SYMBOL"
        elif text[0] in digits:
            try:
                self.value = int(text)
            except ValueError:
                raise ParseError(f"Invalid Int literal: {text}")
            if self.value > 32767:
                raise ParseError(f"Invalid Int literal for base 16: {text}")
            self.type = "INT_CONST"
        else:
            if identifier_pattern.match(text) is None:
                raise ParseError(f"Invalid identifier: {text}")
            self.type = "IDENTIFIER"

    def __repr__(self):
        return f"{self.type}: {self.value}"

    def xml(self):
        xml_type = self.type.lower()
        if "_" in xml_type:
            if xml_type[:3] == "int":
                xml_type = "integerConstant"
            else:
                xml_type = "stringConstant"

        xml_text = str(self.value)
        if xml_type in ("symbol", "stringConstant"):
            xml_text = "".join(symbols_to_xml.get(char, char) for char in xml_text)
        return f"<{xml_type}> {xml_text} </{xml_type}>"


class Analyser:
    def __init__(self, in_file: IO[str]):
        self.tokens = []
        data = in_file.read()
        word = ""
        idx = 0
        while idx < len(data):
            char = data[idx]
            idx += 1
            # if we've hit a symbol or whitespace, stop building the current token and record it and this new symbol
            if char in word_breaks:
                self.add_token(word)
                self.add_token(char)
                word = ""
            # if we hit a /, see if it's followed by * or / indicating a comment, or if we should just parse it as a /
            elif char == "/":
                # ideally there should be a space before a comment, but try to parse the current word just in case
                self.add_token(word)
                word = ""
                # single line comment, skip until EOL
                if data[idx] == "/":
                    while data[idx] != '\n':
                        idx += 1
                    idx += 1
                # multi-line comment / API doc - read until */
                elif data[idx] == "*":
                    idx += 1
                    while data[idx:idx + 2] != "*/":
                        idx += 1
                    # skip over the */ and continue parsing from after it
                    idx += 2
                # otherwise it's just a divide symbol, record it
                else:
                    self.add_token(char)
            # if it's a ", read until the next " and make a token from the string
            elif char == "\"":
                word = char
                while data[idx] != "\"":
                    if data[idx] == "\n":
                        raise ValueError("Newline found within a StringConstant")
                    word = word + data[idx]
                    idx += 1
                word = word + "\""
                idx += 1
                self.add_token(word)
                word = ""
            # otherwise it's an identifier or a constant, so just add it to the current word
            else:
                word = word + char

    def add_token(self, text):
        token = Token(text)
        if token.type is not None:
            self.tokens.append(token)

    def write_xml(self, out_stream: IO[str]):
        out_stream.write("<tokens>\n")
        for token in self.tokens:
            out_stream.write(token.xml() + "\n")
        out_stream.write("</tokens>\n")


class CompilationEngine:
    def __init__(self, analyser, out_stream):
        self.vm_writer = VMWriter(out_stream)
        self.tokens = analyser.tokens
        self.idx = -1
        self.token = None
        self.indent_level = 0
        self.labels = []
        self.is_define = False
        self.kind = None
        self.type = None
        self.class_name = None
        self.func_name = None
        self.while_count = 0
        self.if_count = 0
        self.symbol_table = SymbolTable()
        self.class_or_sub = "class"

    # peek at the next token without advancing
    def peek_next_token(self):
        return self.tokens[self.idx + 1].value

    # advance to the next token and return it
    def next_token(self):
        self.idx += 1
        try:
            self.token = self.tokens[self.idx]
        except IndexError:
            pass
        return self.token

    def token_is(self, value):
        if isinstance(value, type(())):
            return self.token.value in value
        return self.token.value == value

    def token_is_type(self, token_type):
        return self.token.type == token_type

    # assert that the token's value is value, or is in value if value is a tuple
    # returns the token's value in case that's needed, and advances to the next token
    def assert_token_is(self, value, error=None):
        if not self.token_is(value):
            if error is None:
                error = f"Expected {value} got {self.token.value} "
            raise ParseError(error)
        value = self.token.value
        self.next_token()
        return value

    # assert that the token's type is token_type
    # returns the token's value in case that's needed, and advances to the next token
    def assert_token_is_type(self, token_type, error=None):
        if not self.token_is_type(token_type):
            if error is None:
                error = f"Expected {token_type} got {self.token.type} "
            raise ParseError(error)
        value = self.token.value
        self.next_token()
        return value

    def read_var(self):
        name = self.assert_token_is_type("IDENTIFIER")
        if self.is_define:
            self.symbol_table.define(name, self.type, self.kind)
            return None, None
        v_type = self.symbol_table.type_of(name)
        if v_type is None:
            return False, name
        else:
            return True, self.symbol_table.get_var(name)

    def write(self, text):
        self.vm_writer.write(text)

    def compile_class(self):
        # read "class" and go onto the next token
        self.next_token()
        self.assert_token_is("class", "Expected a class definition")

        # className
        self.class_name = self.assert_token_is_type("IDENTIFIER", "Expected a class name")

        # {
        self.assert_token_is("{")

        # classVarDec*
        while self.token.value in ("static", "field"):
            self.compile_class_var_dec()

        # subroutineDec*
        while self.token.value in ("constructor", "function", "method"):
            self.compile_subroutine()

        # }
        self.assert_token_is("}")

    def compile_type(self, allow_void=False):
        # type
        self.class_or_sub = "class"
        if self.token_is(("int", "char", "boolean")) or self.token_is_type("IDENTIFIER") \
                or allow_void and self.token_is("void"):
            self.type = self.token.value
            self.next_token()
        else:
            raise ParseError(f"type {allow_void and 'or void '}expected, got {self.token.value}")

    def compile_var_list(self):
        # type
        self.compile_type()
        self.is_define = True

        # varName
        self.read_var()

        # (, varName)*
        while self.token_is(","):
            self.next_token()
            self.read_var()

        # ;
        self.assert_token_is(";")
        self.is_define = False

    def compile_class_var_dec(self):
        # static | field
        self.kind = self.token.value.upper()
        self.next_token()

        # type varName (, varName)*;
        self.compile_var_list()

    def compile_subroutine(self):
        self.symbol_table.start_subroutine()
        self.while_count = 0
        self.if_count = 0
        # constructor | function | method
        func_type = self.assert_token_is(("constructor", "function", "method"))
        if func_type == "method":
            self.symbol_table.define("this", None, "ARG")

        # type
        self.compile_type(True)

        # subroutineName
        self.class_or_sub = "subroutine"
        self.func_name = name = self.token.value
        self.assert_token_is_type("IDENTIFIER")

        # ( parameterList )
        self.assert_token_is("(")
        self.compile_parameter_list()
        self.assert_token_is(")")

        self.assert_token_is("{")
        # parse var statements
        while self.token_is("var"):
            self.compile_var_dec()

        # declare the function, and parse its body
        self.vm_writer.write_function(f"{self.class_name}.{name}", self.symbol_table.var_count("VAR"))

        # this = Memory.alloc(field_count) for contructors
        if func_type == "constructor":
            self.vm_writer.write_push("constant", self.symbol_table.var_count("FIELD"))
            self.vm_writer.write_call("Memory.alloc", 1)
            self.vm_writer.write_pop("pointer", 0)
        # this = argument[0] for methods
        elif func_type == "method":
            self.vm_writer.write_push("argument", 0)
            self.vm_writer.write_pop("pointer", 0)

        self.compile_statements()
        self.assert_token_is("}")

    def compile_parameter_list(self):
        self.kind = "ARG"
        self.class_or_sub = "class"
        while not self.token_is(")"):
            # type
            self.compile_type()

            # varName
            self.is_define = True
            self.read_var()
            self.is_define = False

            # if , then read another parameter, if it's neither , nor ) that's a syntax error
            if self.token_is(","):
                self.next_token()
            elif not self.token_is(")"):
                raise ParseError(f"Expected ) or , got {self.token.value}")

    def compile_var_dec(self):
        # var
        self.kind = "VAR"
        self.next_token()

        # type varName (, varName)*;
        self.compile_var_list()

    def compile_statements(self):
        while self.token_is(("let", "if", "while", "do", "return")):
            if self.token_is("let"):
                self.compile_let()
            elif self.token_is("if"):
                self.compile_if()
            elif self.token_is("while"):
                self.compile_while()
            elif self.token_is("do"):
                self.compile_do()
            elif self.token_is("return"):
                self.compile_return()

    def compile_expression_list(self):
        # (expression (, expression)*)?
        count = 0
        while not self.token_is(")"):
            self.compile_expression()
            count += 1
            if self.token_is(","):
                self.next_token()
            else:
                if not self.token_is(")"):
                    raise ParseError(f"expected , or ) got {self.token.value}")
        return count

    def compile_subroutine_call(self):
        # (className. | varName.) ?
        is_method = False
        if self.peek_next_token() == ".":
            self.class_or_sub = "class"
            # read the className / varName and determine which it was
            is_var, var = self.read_var()
            # if it's in the symbol table, it's being called as a method
            if is_var:
                is_method = True
                # get the type of the variable as the class on which to call the method
                class_name = var.type
                # and push the var's value as the first argument (this)
                self.vm_writer.write_push(var.kind, var.idx)
            else:
                # otherwise it's a class reference, so not a method (so no implicit first argument)
                class_name = var
            # skip the .
            self.next_token()
        else:
            # if no class or var given, we want to call it as a method on this, so push pointer[0]
            is_method = True
            self.vm_writer.write_push("pointer", 0)
            # and we know it belongs to our own class
            class_name = self.class_name

        # subroutineName
        self.class_or_sub = "subroutine"
        routine_name = self.assert_token_is_type("IDENTIFIER")

        # ( expressionList )
        self.assert_token_is("(")
        arg_count = self.compile_expression_list()
        self.assert_token_is(")")

        self.vm_writer.write_call(f"{class_name}.{routine_name}", arg_count + is_method)

    def compile_do(self):
        # do
        self.next_token()

        # subroutineCall
        self.compile_subroutine_call()

        # trash the returned value
        self.vm_writer.write_pop("temp", 0)
        # ;
        self.assert_token_is(";")

    def compile_let(self):
        # let
        self.next_token()
        # varName
        is_var, var = self.read_var()

        # make sure we're assigning to an actual variable
        if not is_var:
            raise ParseError(f"Cannot assign to {var}")

        kind = var.kind
        index = var.idx
        direct = True
        # [expression] ?
        if self.token_is("["):
            direct = False
            # this is an array-style indirect address, so first push the base address
            self.vm_writer.write_push(kind, index)
            # then evaluate expression
            self.next_token()
            self.compile_expression()
            self.assert_token_is("]")
            # and finally add together
            self.vm_writer.write_arithmetic("+")

        self.assert_token_is("=")

        # expression
        self.compile_expression()

        # for direct assignments, just pop into the given location
        if direct:
            self.vm_writer.write_pop(kind, index)
        else:
            # store the value in temp[0] and pop the next value into pointer[1]
            self.vm_writer.write_pop("temp", 0)
            self.vm_writer.write_pop("pointer", 1)
            # now retrieve the temp value and store it in that
            self.vm_writer.write_push("temp", 0)
            self.vm_writer.write_pop("that", 0)
        # ;
        self.assert_token_is(";")

    def compile_while(self):
        label_idx = self.while_count
        self.while_count += 1
        eval_label = f"WHILE_EXP{label_idx}"
        done_label = f"WHILE_END{label_idx}"

        # advance past the while token and write the label for evaluating cond
        self.next_token()
        self.vm_writer.write_label(eval_label)

        # evaluate condition
        self.assert_token_is("(")
        self.compile_expression()
        self.assert_token_is(")")

        # goto done label if condition is not true
        self.vm_writer.write_arithmetic("~")
        self.vm_writer.write_if(done_label)

        # run the while body
        self.assert_token_is("{")
        self.compile_statements()
        self.assert_token_is("}")

        # jump back to eval
        self.vm_writer.write_goto(eval_label)

        # and finally label the end of the loop
        self.vm_writer.write_label(done_label)

    def compile_return(self):
        # advance past return token
        self.next_token()

        # if there's an expression, push it
        if not self.token_is(";"):
            self.compile_expression()
        else:
            # otherwise push null
            self.vm_writer.write_push("constant", 0)

        # ;
        self.assert_token_is(";")
        self.vm_writer.write_return()

    def compile_if(self):
        # advance past if token
        self.next_token()

        label_idx = self.if_count
        self.if_count += 1
        true_label = f"IF_TRUE{label_idx}"
        false_label = f"IF_FALSE{label_idx}"
        done_label = f"IF_END{label_idx}"

        # evaluate expression
        self.assert_token_is("(")
        self.compile_expression()
        self.assert_token_is(")")

        # if expression, do true
        self.vm_writer.write_if(true_label)
        self.vm_writer.write_goto(false_label)

        self.vm_writer.write_label(true_label)
        self.assert_token_is("{")
        self.compile_statements()
        self.assert_token_is("}")

        # (else { statements })?
        if self.token_is("else"):
            # jump past else if we fell through from
            self.vm_writer.write_goto(done_label)
            # label the start of the else block
            self.vm_writer.write_label(false_label)
            self.next_token()
            # compile the else code-block
            self.assert_token_is("{")
            self.compile_statements()
            self.assert_token_is("}")
            # and mark the done label (which won't be needed if there's no else)
            self.vm_writer.write_label(done_label)
        else:
            # if there's no else we still need a false label, because we couldn't have known that when we
            # were writing the branching logic, so we've already included a (possibly unreachable) if-goto to it
            self.vm_writer.write_label(false_label)

    def compile_expression(self):
        # push the first term
        self.compile_term()

        # (op term)*
        while self.token_is(("+", "-", "*", "/", "&", "|", "<", ">", "=")):
            # push the second term, then perform op
            op = self.token.value
            self.next_token()
            self.compile_term()
            self.vm_writer.write_arithmetic(op)

    def compile_term(self):
        value = self.token.value

        # integerConstant
        if self.token_is_type("INT_CONST"):
            self.vm_writer.write_push("constant", value)
            self.next_token()

        # stringConstant
        elif self.token_is_type("STRING_CONST"):
            string = self.token.value
            # create the string object of the right size
            self.vm_writer.write_push("constant", len(string))
            self.vm_writer.write_call("String.new", 1)

            # write each character
            for char in string:
                self.vm_writer.write_push("constant", ord(char))
                self.vm_writer.write_call("String.appendChar", 2)

            # and advance to the next token
            self.next_token()

        # keywordConstant
        elif self.token_is(("true", "false", "null", "this")):
            # for true, false and null push 0
            if value != "this":
                self.vm_writer.write_push("constant", 0)
                # and for true negate it to get -1
                if value == "true":
                    self.vm_writer.write_arithmetic("~")
            else:
                # for this push pointer[0]
                self.vm_writer.write_push("pointer", 0)
            self.next_token()

        # varName | varName[expression] | subroutineCall
        elif self.token_is_type("IDENTIFIER"):
            # subroutineCall
            if self.peek_next_token() == ".":
                self.compile_subroutine_call()
            # varName ([expression])?
            else:
                is_decl, var = self.read_var()
                if not is_decl:
                    raise ParseError(f"{var} is unknown in this scope")

                # if it's an indirect access:
                if self.token_is("["):
                    self.next_token()
                    # resolve expression
                    self.compile_expression()
                    self.assert_token_is("]")
                    # add expression to var
                    self.vm_writer.write_push(var.kind, var.idx)
                    self.vm_writer.write_arithmetic("+")
                    # store the value as that
                    self.vm_writer.write_pop("pointer", 1)
                    # and retrieve the value that points to
                    self.vm_writer.write_push("that", 0)
                else:
                    # it would be easier just to write this before testing the next token, but for some reason
                    # the provided compiler does it this way around, so this lets us do a textcompare more easily
                    self.vm_writer.write_push(var.kind, var.idx)

        # ( expression )
        elif self.token_is("("):
            self.next_token()
            self.compile_expression()
            self.assert_token_is(")")

        # unaryOp term
        else:
            # save the operator
            op = self.assert_token_is(("~", "-"), f"invalid term {self.token.value}")
            # evaluate term and then apply op
            self.compile_term()
            if op == "-":
                self.vm_writer.write("neg")
            else:
                self.vm_writer.write_arithmetic(op)


class VMWriter:
    def __init__(self, out_stream):
        self.out_stream = out_stream

    def write(self, string):
        self.out_stream.write(f"{string}\n")

    def write_push(self, segment, index):
        segment = kinds_to_segments.get(segment, segment)
        self.write(f"push {segment} {index}")

    def write_pop(self, segment, index):
        segment = kinds_to_segments.get(segment, segment)
        self.write(f"pop {segment} {index}")

    def write_arithmetic(self, command):
        if command in ops_to_vm:
            self.write(ops_to_vm[command])
        else:
            if command == "*":
                func = "Math.multiply"
            else:
                func = "Math.divide"
            self.write_call(func, 2)

    def write_label(self, label):
        self.write(f"label {label}")

    def write_goto(self, label):
        self.write(f"goto {label}")

    def write_if(self, label):
        self.write(f"if-goto {label}")

    def write_call(self, name, n_args):
        self.write(f"call {name} {n_args}")

    def write_function(self, name, n_locals):
        self.write(f"function {name} {n_locals}")

    def write_return(self):
        self.write("return")


class Variable:
    def __init__(self, v_name, v_type, v_kind, v_idx):
        self.name = v_name
        self.type = v_type
        self.kind = v_kind
        self.idx = v_idx


class SymbolTable:
    def __init__(self):
        self.classTable: Dict[str, Variable] = {}
        self.subroutineTable: Dict[str, Variable] = {}
        self.counts = {
            "STATIC": 0,
            "FIELD": 0,
            "ARG": 0,
            "VAR": 0
        }

    def start_subroutine(self):
        self.subroutineTable.clear()
        self.counts["ARG"] = 0
        self.counts["VAR"] = 0

    def get_var(self, name):
        if name in self.subroutineTable:
            return self.subroutineTable[name]
        else:
            return self.classTable.get(name, None)

    def get_table(self, kind):
        if kind in ("STATIC", "FIELD"):
            return self.classTable
        else:
            return self.subroutineTable

    def define(self, v_name, v_type, v_kind):
        table = self.get_table(v_kind)
        idx = self.counts[v_kind]
        self.counts[v_kind] += 1
        table[v_name] = Variable(v_name, v_type, v_kind, idx)

    def var_count(self, kind):
        return self.counts[kind]

    def kind_of(self, name):
        if name in self.subroutineTable:
            return self.subroutineTable[name].kind
        elif name in self.classTable:
            return self.classTable[name].kind
        return None

    def type_of(self, name):
        kind = self.kind_of(name)
        if kind is None:
            return None
        table = self.get_table(kind)
        return table[name].type

    def idx_of(self, name):
        kind = self.kind_of(name)
        if kind is None:
            return None
        table = self.get_table(kind)
        return table[name].idx


if __name__ == "__main__":
    if sys.argv[0][0] == "C":
        sys.argv.append(".")
    arg_parser = argparse.ArgumentParser(description="Compiles a .jack file or directory of .jack files",
                                         prog="JackAnalyzer.py")
    arg_parser.add_argument("jack", help="the jack file or directory to compile")
    # arg_parser.add_argument("--write", help="write a file, if --no-write just echo output",
    #                         action=argparse.BooleanOptionalAction, default=True)
    # arg_parser.add_argument("--overwrite", help="Overwrite file if it exists",
    #                         action=argparse.BooleanOptionalAction, default=True)
    _args = arg_parser.parse_args()
    _filename = _args.jack
    if os.path.isdir(_filename):
        search = os.path.join(_filename, "*.jack")
        _filenames = glob.glob(search)
        assert len(_filenames), "Directory must contain at least one .jack file"
    else:
        assert (_filename[-5:] == ".jack"), "jack must be a directory or .jack file"
        _filenames = [_filename]
    for _filename in _filenames:
        _outfile = _filename[:-5] + ".vm"
        with open(_filename) as in_stream:  # type: IO[str]
            print(f"Compiling {_filename}")
            _analyser = Analyser(in_stream)
            with open(_outfile, "w") as _out_stream:
                _compiler = CompilationEngine(_analyser, _out_stream)
                _compiler.compile_class()


# page 261 compiler