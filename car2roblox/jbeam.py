"""Lenient parser for BeamNG ``.jbeam`` / ``.pc`` / ``materials.json`` files.

JBeam is "JSON with tolerance": ``//`` and ``/* */`` comments, trailing commas and *missing*
commas between values are all accepted by the game. This parser accepts the same.
"""
from __future__ import annotations

import re
from typing import Any

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$.\-]*")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", "\\": "\\", '"': '"'}


class JBeamError(ValueError):
    pass


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)

    def err(self, msg: str) -> JBeamError:
        line = self.s.count("\n", 0, self.i) + 1
        col = self.i - (self.s.rfind("\n", 0, self.i) + 1) + 1
        return JBeamError(f"{msg} at line {line}, column {col}")

    def ws(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n﻿":
                self.i += 1
            elif c == "/" and s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            elif c == "/" and s.startswith("/*", self.i):
                j = s.find("*/", self.i + 2)
                self.i = n if j < 0 else j + 2
            else:
                break

    def value(self) -> Any:
        self.ws()
        if self.i >= self.n:
            raise self.err("unexpected end of file")
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c == '"':
            return self.string()
        m = _NUM.match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group()
            try:
                return int(t) if re.fullmatch(r"[-+]?\d+", t) else float(t)
            except ValueError:
                return float(t)
        m = _WORD.match(self.s, self.i)
        if m:
            self.i = m.end()
            w = m.group()
            return {"true": True, "false": False, "null": None}.get(w, w)
        raise self.err(f"unexpected character {c!r}")

    def string(self) -> str:
        s = self.s
        self.i += 1
        out = []
        while self.i < self.n:
            c = s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < self.n:
                e = s[self.i + 1]
                if e == "u" and self.i + 5 < self.n:
                    try:
                        out.append(chr(int(s[self.i + 2:self.i + 6], 16)))
                        self.i += 6
                        continue
                    except ValueError:
                        pass
                out.append(_ESC.get(e, e))
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        raise self.err("unterminated string")

    def comma(self) -> None:
        self.ws()
        if self.i < self.n and self.s[self.i] == ",":
            self.i += 1
            self.ws()

    def arr(self) -> list:
        self.i += 1
        out = []
        while True:
            self.ws()
            if self.i >= self.n:
                raise self.err("unterminated array")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.comma()

    def obj(self) -> dict:
        self.i += 1
        out = {}
        while True:
            self.ws()
            if self.i >= self.n:
                raise self.err("unterminated object")
            if self.s[self.i] == "}":
                self.i += 1
                return out
            k = self.value()
            if not isinstance(k, str):
                raise self.err("object keys must be strings")
            self.ws()
            if self.i >= self.n or self.s[self.i] != ":":
                raise self.err("expected ':'")
            self.i += 1
            out[k] = self.value()
            self.comma()


def loads(text: str) -> Any:
    p = _Parser(text)
    v = p.value()
    p.ws()
    return v


def load_bytes(data: bytes) -> Any:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return loads(data.decode(enc))
        except UnicodeDecodeError:
            continue
    raise JBeamError("cannot decode file")
