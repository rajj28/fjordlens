"""Exact character spans of JSON values in raw response text (evidence quotes).

index() maps RFC 6901 pointers to (member_start, value_start, value_end) so a claim can quote
the exact bytes of its source, e.g. '"navn":"DIPS AS"'. Verified by a 90k-pointer property test.
"""
import json as _json

_WS = " \t\r\n"
_MAX_DEPTH = 512
_MAX_NODES = 2_000_000
_MAX_POINTER_CHARS = 64_000_000  # Bounds memory for adversarial documents.


def _escape(key):
    return key.replace("~", "~0").replace("/", "~1")


def index(text):
    """Map every JSON value in `text` to (member_start, value_start, value_end)."""
    L = text
    n = len(L)
    result = {}
    budget = [0]

    def charge(pointer):
        budget[0] += len(pointer) + 1
        if budget[0] > _MAX_POINTER_CHARS or len(result) > _MAX_NODES:
            raise ValueError("document too large to index")

    def skip_ws(i):
        while i < n and L[i] in _WS:
            i += 1
        return i

    def scan_string(i):
        # i at opening quote; returns index just past closing quote.
        i += 1
        while i < n:
            c = L[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return i + 1
            if ord(c) < 0x20:
                raise ValueError("control character in string")
            i += 1
        raise ValueError("unterminated string")

    def parse_scalar(i):
        c = L[i]
        if c == '"':
            end = scan_string(i)
            _json.loads(L[i:end])  # validates escapes
            return end
        if c == "t":
            if L.startswith("true", i):
                return i + 4
        elif c == "f":
            if L.startswith("false", i):
                return i + 5
        elif c == "n":
            if L.startswith("null", i):
                return i + 4
        elif c == "-" or "0" <= c <= "9":
            j = i
            if L[j] == "-":
                j += 1
                if j >= n:
                    raise ValueError("bad number")
            if L[j] == "0":
                j += 1
            elif "1" <= L[j] <= "9":
                while j < n and L[j].isdigit():
                    j += 1
            else:
                raise ValueError("bad number")
            if j < n and L[j] == ".":
                j += 1
                if j >= n or not L[j].isdigit():
                    raise ValueError("bad number")
                while j < n and L[j].isdigit():
                    j += 1
            if j < n and L[j] in "eE":
                j += 1
                if j < n and L[j] in "+-":
                    j += 1
                if j >= n or not L[j].isdigit():
                    raise ValueError("bad number")
                while j < n and L[j].isdigit():
                    j += 1
            return j
        raise ValueError(f"unexpected character {c!r}")

    # read a key (string), colon, and position at the value
    def read_key(i):
        i = skip_ws(i)
        if i >= n or L[i] != '"':
            raise ValueError("expected object key")
        key_start = i
        key_end = scan_string(i)
        # validate the key decodes (json.loads raises on bad escapes)
        key = _json.loads(L[key_start:key_end])
        j = skip_ws(key_end)
        if j >= n or L[j] != ":":
            raise ValueError("expected colon")
        j = skip_ws(j + 1)
        if j >= n:
            raise ValueError("unexpected end of input")
        return key, key_start, j

    i = skip_ws(0)
    if i >= n:
        raise ValueError("empty input")

    # stacks: obj frames are ["{", ptr], array frames ["[", ptr, next_index]
    stack = []
    pending_ms = i
    pending_ptr = ""
    need_value = True

    while True:
        if need_value:
            c = L[i]
            vs = i
            if c == "{":
                if len(stack) >= _MAX_DEPTH:
                    raise ValueError("nesting too deep")
                charge(pending_ptr)
                result[pending_ptr] = [pending_ms, vs, -1]
                stack.append(["{", pending_ptr])
                i += 1
                j = skip_ws(i)
                if j >= n:
                    raise ValueError("unexpected end of input")
                if L[j] == "}":
                    result[pending_ptr][2] = j + 1
                    i = j + 1
                    stack.pop()
                    need_value = False
                    continue
                key, key_start, i = read_key(i)
                pending_ms = key_start
                pending_ptr = stack[-1][1] + "/" + _escape(key)
                need_value = True
                continue
            if c == "[":
                if len(stack) >= _MAX_DEPTH:
                    raise ValueError("nesting too deep")
                charge(pending_ptr)
                result[pending_ptr] = [pending_ms, vs, -1]
                stack.append(["[", pending_ptr, 0])
                i = skip_ws(i + 1)
                if i >= n:
                    raise ValueError("unexpected end of input")
                if L[i] == "]":
                    result[pending_ptr][2] = i + 1
                    i += 1
                    stack.pop()
                    need_value = False
                    continue
                pending_ms = i
                pending_ptr = pending_ptr + "/0"
                need_value = True
                continue
            end = parse_scalar(i)
            charge(pending_ptr)
            result[pending_ptr] = (pending_ms, vs, end)
            i = end
            need_value = False
            continue
        # need_value == False: a value just completed; expect , or closer
        if not stack:
            i = skip_ws(i)
            if i != n:
                raise ValueError("trailing garbage")
            return {pointer: tuple(span) for pointer, span in result.items()}
        frame = stack[-1]
        j = skip_ws(i)
        if j >= n:
            raise ValueError("unexpected end of input")
        ch = L[j]
        if frame[0] == "{":
            if ch == ",":
                key, key_start, i = read_key(j + 1)
                pending_ms = key_start
                pending_ptr = frame[1] + "/" + _escape(key)
                need_value = True
                continue
            if ch == "}":
                result[frame[1]][2] = j + 1
                i = j + 1
                stack.pop()
                continue
            raise ValueError("expected ',' or '}'")
        else:
            if ch == ",":
                frame[2] += 1
                i = skip_ws(j + 1)
                if i >= n:
                    raise ValueError("unexpected end of input")
                pending_ms = i
                pending_ptr = frame[1] + "/" + str(frame[2])
                need_value = True
                continue
            if ch == "]":
                result[frame[1]][2] = j + 1
                i = j + 1
                stack.pop()
                continue
            raise ValueError("expected ',' or ']'")


def locate(text, pointer, spans=None):
    """Span for one pointer; pass a precomputed index() as `spans` to avoid re-parsing."""
    return (spans if spans is not None else index(text)).get(pointer)


def quote(text, pointer, *, with_key=True, max_len=4000, spans=None):
    """Raw slice for evidence: '"key":value' (or the value alone). None if the pointer is absent
    or the slice exceeds max_len."""
    span = locate(text, pointer, spans)
    if span is None:
        return None
    ms, vs, ve = span
    if with_key:
        s = text[ms:ve]
        if len(s) <= max_len:
            return s
    s = text[vs:ve]
    if len(s) <= max_len:
        return s
    return None
