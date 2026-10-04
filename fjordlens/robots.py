"""RFC 9309 robots.txt policy: status handling, group selection, longest-match precedence, * and $."""
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Rule:
    path: str
    normalized_path: str
    allow: bool
    pattern_length: int


@dataclass
class Group:
    user_agents: list[str]
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: Optional[float] = None


class Robots:
    MAX_BODY_SIZE = 500 * 1024  # 500 KiB

    def __init__(self, state: str, groups: list[Group]):
        self._state = state
        self._groups = groups

    @classmethod
    def from_response(cls, status: int | None, body: bytes | None, *, error: bool = False) -> "Robots":
        if error or status is None:
            return cls("unreachable", [])

        if 200 <= status <= 299:
            if body is None:
                body = b""
            body = body[:cls.MAX_BODY_SIZE]
            text = body.decode("utf-8", errors="replace")
            if text.startswith("\ufeff"):
                text = text[1:]
            groups = cls._parse(text)
            if not groups:
                return cls("parsed", [])
            return cls("parsed", groups)

        if 400 <= status <= 499 and status != 429:
            return cls("unavailable", [])

        return cls("unreachable", [])

    @staticmethod
    def _parse(text: str) -> list[Group]:
        lines = text.splitlines()
        groups: list[Group] = []
        current_group: Optional[Group] = None
        current_user_agents: list[str] = []

        for line in lines:
            line = line.split("#", 1)[0].strip()  # "#" starts a comment anywhere on a line (RFC 9309 2.2).
            if not line:
                continue

            colon_idx = line.find(":")
            if colon_idx == -1:
                continue

            field = line[:colon_idx].strip().lower()
            value = line[colon_idx + 1:].strip()

            if field == "user-agent":
                if current_group is not None and (current_group.rules or current_group.crawl_delay is not None):
                    groups.append(current_group)
                    current_user_agents = [value.lower()]
                    current_group = Group(user_agents=current_user_agents.copy())
                elif current_group is not None:
                    current_user_agents.append(value.lower())
                    current_group.user_agents = current_user_agents.copy()
                else:
                    current_user_agents = [value.lower()]
                    current_group = Group(user_agents=current_user_agents.copy())
            elif field in ("allow", "disallow") and current_group is not None:
                if value == "":
                    continue
                pattern_length = len(value)
                normalized = Robots._normalize_path_static(value)
                rule = Rule(
                    path=value,
                    normalized_path=normalized,
                    allow=(field == "allow"),
                    pattern_length=pattern_length
                )
                current_group.rules.append(rule)
            elif field == "crawl-delay" and current_group is not None:
                try:
                    current_group.crawl_delay = float(value)
                except ValueError:
                    current_group.crawl_delay = None

        if current_group is not None and (current_group.rules or current_group.crawl_delay is not None):
            groups.append(current_group)

        return groups

    @staticmethod
    def _normalize_path_static(path: str) -> str:
        result = []
        i = 0
        while i < len(path):
            if path[i] == "%" and i + 2 < len(path):
                try:
                    hex_val = path[i+1:i+3].upper()
                    char_code = int(hex_val, 16)
                    char = chr(char_code)
                    if char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~":
                        result.append(char)
                    else:
                        result.append(f"%{hex_val}")
                    i += 3
                    continue
                except ValueError:
                    pass
            char = path[i]
            # Non-ASCII characters compare in their UTF-8 percent-encoded form, as URLs are fetched.
            result.append(char if ord(char) < 128 else "".join(f"%{b:02X}" for b in char.encode("utf-8")))
            i += 1
        return "".join(result)

    def _normalize_path(self, path: str) -> str:
        return self._normalize_path_static(path)

    @property
    def state(self) -> str:
        return self._state

    def _extract_path_and_query(self, url_or_path: str) -> str:
        if "://" in url_or_path:
            parsed = urllib.parse.urlparse(url_or_path)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            return self._normalize_path(path)
        return self._normalize_path(url_or_path or "/")

    def _match_token(self, token: str, user_agent: str) -> bool:
        token_lower = token.lower()
        match = re.match(r"^[a-zA-Z_-]+", user_agent)
        if match:
            ua_prefix = match.group(0).lower()
            return ua_prefix == token_lower
        return False

    def _get_matching_groups(self, user_agent_token: str) -> list[Group]:
        matching = []
        star_groups = []
        for group in self._groups:
            for ua in group.user_agents:
                if ua == "*":
                    star_groups.append(group)
                    break
                if self._match_token(user_agent_token, ua):
                    matching.append(group)
                    break
        if matching:
            return matching
        return star_groups

    def _match_rule(self, rule: Rule, path: str) -> bool:
        pattern = rule.normalized_path
        if pattern.endswith("$"):
            pattern = pattern[:-1]
            anchored = True
        else:
            anchored = False

        regex_pattern = "^"
        i = 0
        while i < len(pattern):
            if pattern[i] == "*":
                regex_pattern += ".*"
            elif pattern[i] == "$":
                pass
            else:
                regex_pattern += re.escape(pattern[i])
            i += 1

        if anchored:
            regex_pattern += "$"

        return bool(re.match(regex_pattern, path))

    def allowed(self, user_agent_token: str, url_or_path: str) -> bool:
        path = self._extract_path_and_query(url_or_path)

        if path == "/robots.txt" or path.startswith("/robots.txt?"):
            return True

        if self._state != "parsed":
            return self._state == "unavailable"

        groups = self._get_matching_groups(user_agent_token)
        if not groups:
            return True

        all_rules: list[Rule] = []
        for group in groups:
            all_rules.extend(group.rules)

        matching_rules = [r for r in all_rules if self._match_rule(r, path)]
        if not matching_rules:
            return True

        matching_rules.sort(key=lambda r: (-r.pattern_length, 0 if r.allow else 1))
        return matching_rules[0].allow

    def crawl_delay(self, user_agent_token: str) -> float | None:
        if self._state != "parsed":
            return None

        groups = self._get_matching_groups(user_agent_token)
        if not groups:
            return None

        for group in groups:
            if group.crawl_delay is not None:
                return group.crawl_delay
        return None