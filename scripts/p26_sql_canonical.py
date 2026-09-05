#!/usr/bin/env python3
"""Restore-stable canonicalisation of PostgreSQL expression text.

Why this module exists
----------------------

``pg_get_constraintdef()`` and ``pg_get_indexdef()`` do not return the SQL the
operator wrote. They *deparse* the expression tree the server stored. The shape
of that tree depends on how the original statement was parsed and type-coerced,
not only on what the expression means.

A dump/restore cycle re-parses the deparsed text, so the tree is rebuilt from a
different starting point and deparses to different bytes. The observed case:

    source   ... = ANY ((ARRAY['a'::character varying])::text[])
    restored ... = ANY (ARRAY[('a'::character varying)::text])

Both denote the same predicate. Only the placement of the cast differs: the
source carries one cast on the whole array, the restored form carries one cast
per element. A fingerprint taken over raw deparse text therefore changes across
a restore even when the schema is byte-for-byte equivalent, which defeats the
purpose of using that fingerprint to verify a restore.

What this module does
---------------------

It parses the expression into a small structural tree and rewrites that tree
with rules that preserve meaning, then re-serialises it deterministically. It
does **not** perform textual substitution: no ``str.replace``, no regular
expression rewriting of SQL fragments. Fragile substitution is precisely what
would silently erase a real difference.

The output is a canonical *representation* used only as fingerprint input. It
is deliberately not required to be executable SQL. Raw definitions stay in the
snapshot artefact for audit.

Guarantees
----------

* Two semantically equivalent definitions canonicalise to the same string.
* Two genuinely different definitions canonicalise to different strings. Every
  rule below is meaning-preserving in both directions; none discards a token,
  a literal, an identifier, or a type that could carry a difference.
* The function is total: text it cannot parse is returned normalised only for
  whitespace, never silently emptied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = ["canonicalise_expression", "CanonicalisationError"]


class CanonicalisationError(ValueError):
    """Raised when the tokeniser meets input it cannot represent."""


# ---------------------------------------------------------------------------
# Type names
# ---------------------------------------------------------------------------
# PostgreSQL accepts several spellings for one type. Two schemas that differ
# only in spelling are the same schema, so spellings collapse to one form.

_TYPE_ALIASES = {
    "character varying": "varchar",
    "varchar": "varchar",
    "character": "bpchar",
    "char": "bpchar",
    "bpchar": "bpchar",
    "text": "text",
    "integer": "int4",
    "int": "int4",
    "int4": "int4",
    "bigint": "int8",
    "int8": "int8",
    "smallint": "int2",
    "int2": "int2",
    "boolean": "bool",
    "bool": "bool",
    "double precision": "float8",
    "float8": "float8",
    "real": "float4",
    "float4": "float4",
    "numeric": "numeric",
    "decimal": "numeric",
    "timestamp with time zone": "timestamptz",
    "timestamptz": "timestamptz",
    "timestamp without time zone": "timestamp",
    "timestamp": "timestamp",
    "time with time zone": "timetz",
    "time without time zone": "time",
    "date": "date",
    "uuid": "uuid",
    "jsonb": "jsonb",
    "json": "json",
}

# Casting through one of these, with no length modifier, cannot change the
# value: they are variable-length and perform no padding or truncation. A cast
# chain that passes through one of them may collapse to its outermost type.
#
# ``bpchar`` is deliberately absent: it pads to the declared length, so
# collapsing a chain through it would discard a real difference.
_LOSSLESS_STRING_TYPES = frozenset({"text", "varchar"})


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_PUNCTUATION = frozenset("(),[]")
_OPERATOR_CHARS = frozenset("+-*/<>=~!@#%^&|`?")
_IDENT_START = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)
_IDENT_BODY = _IDENT_START | frozenset("0123456789$")


@dataclass(frozen=True)
class Token:
    kind: str   # 'string' | 'quoted_ident' | 'number' | 'word' | 'op' | 'punct' | 'cast'
    text: str


def tokenise(source: str) -> list[Token]:
    """Split ``source`` into tokens, keeping literals intact."""
    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]

        if ch.isspace():
            i += 1
            continue

        # Single-quoted literal, '' is an escaped quote.
        if ch == "'":
            j = i + 1
            buf = []
            while True:
                if j >= n:
                    raise CanonicalisationError("unterminated string literal")
                if source[j] == "'":
                    if j + 1 < n and source[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(source[j])
                j += 1
            tokens.append(Token("string", "".join(buf)))
            i = j
            continue

        # Double-quoted identifier, "" is an escaped quote.
        if ch == '"':
            j = i + 1
            buf = []
            while True:
                if j >= n:
                    raise CanonicalisationError("unterminated quoted identifier")
                if source[j] == '"':
                    if j + 1 < n and source[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(source[j])
                j += 1
            tokens.append(Token("quoted_ident", "".join(buf)))
            i = j
            continue

        if ch == ":" and i + 1 < n and source[i + 1] == ":":
            tokens.append(Token("cast", "::"))
            i += 2
            continue

        if ch in _PUNCTUATION:
            tokens.append(Token("punct", ch))
            i += 1
            continue

        if ch.isdigit() or (
            ch == "." and i + 1 < n and source[i + 1].isdigit()
        ):
            j = i
            while j < n and (source[j].isdigit() or source[j] in ".eE"):
                if source[j] in "eE" and j + 1 < n and source[j + 1] in "+-":
                    j += 2
                    continue
                j += 1
            tokens.append(Token("number", source[i:j]))
            i = j
            continue

        if ch in _IDENT_START:
            j = i
            while j < n and source[j] in _IDENT_BODY:
                j += 1
            tokens.append(Token("word", source[i:j]))
            i = j
            continue

        if ch in _OPERATOR_CHARS:
            j = i
            while j < n and source[j] in _OPERATOR_CHARS:
                j += 1
            tokens.append(Token("op", source[i:j]))
            i = j
            continue

        if ch in ".;":
            tokens.append(Token("punct", ch))
            i += 1
            continue

        raise CanonicalisationError(f"unexpected character {ch!r}")

    return tokens


# ---------------------------------------------------------------------------
# Node tree
# ---------------------------------------------------------------------------

@dataclass
class Node:
    def render(self) -> str:                      # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class Leaf(Node):
    token: Token

    def render(self) -> str:
        t = self.token
        if t.kind == "string":
            return "'" + t.text.replace("'", "''") + "'"
        if t.kind == "quoted_ident":
            return '"' + t.text.replace('"', '""') + '"'
        if t.kind == "word":
            # Keywords and unquoted identifiers are case insensitive in
            # PostgreSQL and are folded, so case carries no meaning here.
            return t.text.lower()
        return t.text


@dataclass
class Group(Node):
    """A parenthesised or bracketed sequence of comma separated items."""
    open: str                       # '(' or '['
    items: list[list[Node]] = field(default_factory=list)

    def render(self) -> str:
        close = ")" if self.open == "(" else "]"
        inner = ",".join(render_sequence(item) for item in self.items)
        return f"{self.open}{inner}{close}"


@dataclass
class ArrayCtor(Node):
    """An ``array[...]`` constructor."""
    items: list[list[Node]] = field(default_factory=list)

    def render(self) -> str:
        inner = ",".join(render_sequence(item) for item in self.items)
        return f"array[{inner}]"


@dataclass
class TypeName:
    base: str          # canonical base name
    typmod: str | None # e.g. '30' for varchar(30)
    array_depth: int   # number of trailing []

    def render(self) -> str:
        out = self.base
        if self.typmod is not None:
            out += f"({self.typmod})"
        out += "[]" * self.array_depth
        return out

    @property
    def is_array(self) -> bool:
        return self.array_depth > 0

    def element(self) -> "TypeName":
        if self.array_depth == 0:
            raise CanonicalisationError("element() on a non array type")
        return TypeName(self.base, self.typmod, self.array_depth - 1)


@dataclass
class Cast(Node):
    value: Node
    type_name: TypeName

    def render(self) -> str:
        return f"{self.value.render()}::{self.type_name.render()}"


def render_sequence(nodes: Sequence[Node]) -> str:
    return " ".join(node.render() for node in nodes)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
# Multi word type names are matched longest first so that
# 'timestamp with time zone' wins over 'timestamp'.
_MULTIWORD_TYPES = sorted(
    (name for name in _TYPE_ALIASES if " " in name),
    key=lambda name: -len(name.split()),
)


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> Token | None:
        index = self.pos + offset
        if index < len(self.tokens):
            return self.tokens[index]
        return None

    def parse_sequence(self, terminators: frozenset[str]) -> list[Node]:
        nodes: list[Node] = []
        while True:
            token = self.peek()
            if token is None:
                break
            if token.kind == "punct" and token.text in terminators:
                break
            nodes.append(self.parse_term())
        return nodes

    def parse_term(self) -> Node:
        node = self.parse_primary()
        # Postfix casts bind to the term immediately to their left.
        while True:
            token = self.peek()
            if token is not None and token.kind == "cast":
                self.pos += 1
                node = Cast(node, self.parse_type_name())
                continue
            break
        return node

    def parse_primary(self) -> Node:
        token = self.peek()
        if token is None:
            raise CanonicalisationError("unexpected end of expression")

        if token.kind == "punct" and token.text in "([":
            return self.parse_group(token.text)

        if token.kind == "word" and token.text.lower() == "array":
            nxt = self.peek(1)
            if nxt is not None and nxt.kind == "punct" and nxt.text == "[":
                self.pos += 1
                group = self.parse_group("[")
                return ArrayCtor(group.items)

        self.pos += 1
        return Leaf(token)

    def parse_group(self, opener: str) -> Group:
        closer = ")" if opener == "(" else "]"
        self.pos += 1  # consume opener
        items: list[list[Node]] = []
        terminators = frozenset({",", closer})
        while True:
            token = self.peek()
            if token is None:
                raise CanonicalisationError(f"unbalanced {opener!r}")
            if token.kind == "punct" and token.text == closer:
                self.pos += 1
                break
            if token.kind == "punct" and token.text == ",":
                self.pos += 1
                continue
            items.append(self.parse_sequence(terminators))
        return Group(opener, items)

    def parse_type_name(self) -> TypeName:
        words: list[str] = []
        while True:
            token = self.peek()
            if token is None or token.kind != "word":
                break
            candidate = " ".join(words + [token.text.lower()])
            # Keep consuming while the accumulated phrase is still the prefix
            # of a known multi word type, or is itself a known type.
            still_useful = candidate in _TYPE_ALIASES or any(
                phrase.startswith(candidate + " ") for phrase in _MULTIWORD_TYPES
            )
            if not words:
                still_useful = True  # always take at least one word
            if not still_useful:
                break
            words.append(token.text.lower())
            self.pos += 1

        if not words:
            token = self.peek()
            if token is not None and token.kind == "quoted_ident":
                words = [token.text]
                self.pos += 1
            else:
                raise CanonicalisationError("cast without a type name")

        phrase = " ".join(words)
        base = _TYPE_ALIASES.get(phrase, phrase)

        typmod: str | None = None
        token = self.peek()
        if token is not None and token.kind == "punct" and token.text == "(":
            group = self.parse_group("(")
            typmod = ",".join(render_sequence(item) for item in group.items)

        depth = 0
        while True:
            token = self.peek()
            nxt = self.peek(1)
            if (
                token is not None
                and nxt is not None
                and token.kind == "punct"
                and token.text == "["
                and nxt.kind == "punct"
                and nxt.text == "]"
            ):
                depth += 1
                self.pos += 2
                continue
            break

        return TypeName(base, typmod, depth)


# ---------------------------------------------------------------------------
# Meaning preserving rewrites
# ---------------------------------------------------------------------------

def _rewrite_sequence(nodes: list[Node]) -> list[Node]:
    return [_rewrite(node) for node in nodes]


def _rewrite(node: Node) -> Node:
    """Apply every rule bottom up until the node stops changing."""
    # Children first, so a rule always sees rewritten operands.
    if isinstance(node, Group):
        node = Group(node.open, [_rewrite_sequence(i) for i in node.items])
    elif isinstance(node, ArrayCtor):
        node = ArrayCtor([_rewrite_sequence(i) for i in node.items])
    elif isinstance(node, Cast):
        node = Cast(_rewrite(node.value), node.type_name)

    for _ in range(64):  # a fixpoint is reached in one or two passes
        rewritten = _apply_rules(node)
        if rewritten is node:
            return node
        node = _rewrite(rewritten)
    return node


def _apply_rules(node: Node) -> Node:
    # R1 - a parenthesised group holding exactly one item, and that item a
    # single node, adds no grouping. '(status)::text' and 'status::text' are
    # the same expression. Multi node items keep their parentheses, because
    # removing them could change operator precedence.
    if isinstance(node, Group) and node.open == "(":
        if len(node.items) == 1 and len(node.items[0]) == 1:
            return node.items[0][0]

    if isinstance(node, Cast):
        inner = node.value
        target = node.type_name

        # R2 - a cast applied to an array constructor is exactly a cast
        # applied to each element. This is the rule that reconciles the two
        # deparse forms: one cast on the whole array becomes one cast per
        # element, which is the form a restore produces.
        if isinstance(inner, ArrayCtor) and target.is_array:
            element_type = target.element()
            items = [
                [Cast(_as_single(item), element_type)] for item in inner.items
            ]
            return ArrayCtor(items)

        if isinstance(inner, Cast):
            # R3 - a cast of a cast of a literal collapses to the outer type,
            # provided the inner type cannot change the value. Restricted to
            # literals: on an arbitrary expression an inner cast may shorten
            # the value, and discarding it would erase a real difference.
            if (
                isinstance(inner.value, Leaf)
                and inner.value.token.kind in ("string", "number")
                and inner.type_name.typmod is None
                and inner.type_name.array_depth == 0
                and inner.type_name.base in _LOSSLESS_STRING_TYPES
            ):
                return Cast(inner.value, target)

            # R4 - casting twice to the very same type is a no-op.
            if inner.type_name.render() == target.render():
                return inner

    return node


def _as_single(item: list[Node]) -> Node:
    """Collapse a one element sequence, or wrap a longer one in a group."""
    if len(item) == 1:
        return item[0]
    return Group("(", [item])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def canonicalise_expression(source: str | None) -> str | None:
    """Return a restore-stable canonical form of ``source``.

    ``None`` maps to ``None`` so an absent default stays absent. Text that
    cannot be tokenised is returned with its whitespace collapsed rather than
    discarded: an unparsable definition must still contribute its difference
    to the fingerprint.
    """
    if source is None:
        return None
    if not isinstance(source, str):
        source = str(source)

    try:
        tokens = tokenise(source)
        parser = _Parser(tokens)
        nodes = parser.parse_sequence(frozenset())
        if parser.pos != len(tokens):
            raise CanonicalisationError("trailing tokens")
        return render_sequence(_rewrite_sequence(nodes))
    except CanonicalisationError:
        return " ".join(source.split())
