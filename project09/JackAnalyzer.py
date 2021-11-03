import os
import re
import sys
import argparse
import glob
from typing import IO, Union

keywords = "class constructor function method field static var int char boolean " \
           "void true false null this let do if else while return".split(" ")

symbols = "{}()[].,;+-*=&|<>=~/"
word_breaks = symbols[:-1] + " \t\n"
digits = "0123456789"
identifier_pattern = re.compile("[A-Za-z0-9_]+")
symbols_to_xml = {"<": "&lt;", ">": "&gt;", "\"": "&quot;", "&": "&amp;"}


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
        self.out_stream = out_stream
        self.tokens = analyser.tokens
        self.idx = -1
        self.token = None
        self.indent_level = 0
        self.labels = []

    def peek_next_token(self):
        return self.tokens[self.idx + 1].value

    def next_token(self):
        self.idx += 1
        self.token = self.tokens[self.idx]

    def token_is(self, value):
        if isinstance(value, type(())):
            return self.token.value in value
        return self.token.value == value

    def token_is_type(self, token_type):
        return self.token.type == token_type

    def assert_token_is(self, value, error=None, do_write=True):
        if not self.token_is(value):
            if error is None:
                error = f"Expected {value} got {self.token.value} "
            raise ParseError(error)
        if do_write:
            self.write_token()

    def assert_token_is_type(self, token_type, error=None, do_write=True):
        if not self.token_is_type(token_type):
            if error is None:
                error = f"Expected {token_type} got {self.token.type} "
            raise ParseError(error)
        if do_write:
            self.write_token()

    def write(self, text):
        self.out_stream.write(self.indent_level * "  " + text + "\n")

    def write_token(self, advance=True):
        self.write(self.token.xml())
        if advance:
            try:
                self.next_token()
            except IndexError:
                pass

    def begin(self, label):
        self.write(f"<{label}>")
        self.labels.append(label)
        self.indent_level += 1

    def end(self):
        self.indent_level -= 1
        self.write(f"</{self.labels.pop()}>")

    def compile_class(self):
        self.begin("class")
        self.next_token()

        # class
        self.assert_token_is("class")

        # className
        self.assert_token_is_type("IDENTIFIER", "Expected a class name")

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

        # all done
        self.end()

    def compile_type(self, allow_void=False):
        # type
        if self.token_is(("int", "char", "boolean")) or self.token_is_type("IDENTIFIER") \
                or allow_void and self.token_is("void"):
            self.write_token()
        else:
            raise ParseError(f"type {allow_void and 'or void '}expected, got {self.token.value}")

    def compile_var_list(self):
        # type
        self.compile_type()

        # varName
        self.assert_token_is_type("IDENTIFIER")

        # (, varName)*
        while self.token_is(","):
            self.write_token()
            self.assert_token_is_type("IDENTIFIER")

        # ;
        self.assert_token_is(";")

    def compile_class_var_dec(self):
        self.begin("classVarDec")
        # static | field
        self.write_token()

        # type varName (, varName)*;
        self.compile_var_list()

        # done
        self.end()

    def compile_subroutine(self):
        self.begin("subroutineDec")

        # constructor | function | method
        self.assert_token_is(("constructor", "function", "method"))

        # type
        self.compile_type(True)

        # subroutineName
        self.assert_token_is_type("IDENTIFIER")

        # ( parameterList )
        self.assert_token_is("(")
        self.compile_parameter_list()
        self.assert_token_is(")")

        # subroutineBody
        self.begin("subroutineBody")

        # { varDec* statements }
        self.assert_token_is("{")
        while self.token_is("var"):
            self.compile_var_dec()
        self.compile_statements()
        self.assert_token_is("}")
        # end body
        self.end()

        # end dec
        self.end()

    def compile_parameter_list(self):
        self.begin("parameterList")
        while not self.token_is(")"):
            # type
            self.compile_type()

            # varName
            self.assert_token_is_type("IDENTIFIER")

            # if , then read another parameter, if it's neither , nor ) that's a syntax error
            if self.token_is(","):
                self.write_token()
            elif not self.token_is(")"):
                raise ParseError(f"Expected ) or , got {self.token.value}")

        self.end()

    def compile_var_dec(self):
        self.begin("varDec")
        # var
        self.write_token()

        # type varName (, varName)*;
        self.compile_var_list()

        # done
        self.end()

    def compile_statements(self):
        self.begin("statements")
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
        self.end()

    def compile_expression_list(self):
        self.begin("expressionList")

        # (expression (, expression)*)?
        while not self.token_is(")"):
            self.compile_expression()
            if self.token_is(","):
                self.write_token()
            else:
                if not self.token_is(")"):
                    raise ParseError(f"expected , or ) got {self.token.value}")
        self.end()

    def compile_subroutine_call(self):
        # subroutineName | className | varName
        self.write_token()

        # .subroutineName ?
        if self.token_is("."):
            self.write_token()
            self.assert_token_is_type("IDENTIFIER")

        # ( expressionList )
        self.assert_token_is("(")
        self.compile_expression_list()
        self.assert_token_is(")")

    def compile_do(self):
        self.begin("doStatement")
        # do
        self.write_token()

        # subroutineCall
        self.compile_subroutine_call()

        #;
        self.assert_token_is(";")
        self.end()

    def compile_let(self):
        self.begin("letStatement")
        # let
        self.write_token()
        # varName
        self.assert_token_is_type("IDENTIFIER")

        # [expression] ?
        if self.token_is("["):
            self.write_token()
            self.compile_expression()
            self.assert_token_is("]")

        self.assert_token_is("=")

        # expression
        self.compile_expression()

        # ;
        self.assert_token_is(";")

        self.end()

    def compile_while(self):
        self.begin("whileStatement")
        # while
        self.write_token()

        # (
        self.assert_token_is("(")

        # expression
        self.compile_expression()

        # )
        self.assert_token_is(")")

        # {
        self.assert_token_is("{")

        # statements
        self.compile_statements()

        # }
        self.assert_token_is("}")
        self.end()

    def compile_return(self):
        self.begin("returnStatement")
        # return
        self.write_token()

        # expression?
        if not self.token_is(";"):
            self.compile_expression()

        # ;
        self.assert_token_is(";")
        self.end()

    def compile_if(self):
        self.begin("ifStatement")
        # if
        self.write_token()

        # ( expression )
        self.assert_token_is("(")
        self.compile_expression()
        self.assert_token_is(")")

        # { statements }
        self.assert_token_is("{")
        self.compile_statements()
        self.assert_token_is("}")

        # (else { statements })?
        if self.token_is("else"):
            self.write_token()
            self.assert_token_is("{")
            self.compile_statements()
            self.assert_token_is("}")

        self.end()

    def compile_expression(self):
        self.begin("expression")
        # term
        self.compile_term()

        # (op term)*
        while self.token_is(("+", "-", "*", "/", "&", "|", "<", ">", "=")):
            self.write_token()
            self.compile_term()

        self.end()

    def compile_term(self):
        self.begin("term")

        # integerConstant
        if self.token_is_type("INT_CONST"):
            self.write_token()

        # stringConstant
        elif self.token_is_type("STRING_CONST"):
            self.write_token()

        # keywordConstant
        elif self.token_is(("true", "false", "null", "this")):
            self.write_token()

        # varName | varName[expression] | subroutineCall
        elif self.token_is_type("IDENTIFIER"):
            # subroutineCall
            if self.peek_next_token() == ".":
                self.compile_subroutine_call()
            # varName ([expression])?
            else:
                self.write_token()
                if self.token_is("["):
                    self.write_token()
                    self.compile_expression()
                    self.assert_token_is("]")

        # ( expression )
        elif self.token_is("("):
            self.write_token()
            self.compile_expression()
            self.assert_token_is(")")

        # unaryOp term
        else:
            self.assert_token_is(("~", "-"), f"invalid term {self.token.value}")
            self.compile_term()

        self.end()


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
        _outfile = _filename[:-5] + ".xml"
        with open(_filename) as in_stream:  # type: IO[str]
            print(f"Compiling {_filename}")
            _analyser = Analyser(in_stream)
            with open(_outfile, "w") as _out_stream:
                _compiler = CompilationEngine(_analyser, _out_stream)
                _compiler.compile_class()
