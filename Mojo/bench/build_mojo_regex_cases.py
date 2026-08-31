#!/usr/bin/env python3
"""Build Mojo/bench/mojo_regex_cases.json — the regex interop battery as data.

Authors the case list (patterns/inputs/modes) and computes each `expected`
with the reference `regex` module, freezing a golden file both sides must
match. Re-run after editing CASES below (idempotent):
    .venv/bin/python3 Mojo/bench/build_mojo_regex_cases.py [--check]

--check exits 1 when the committed JSON no longer matches the authored
cases (regenerated expected values differ) — run it after a `regex`
module upgrade.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from mojo_regex_battery import run_case  # noqa: E402

OUT = pathlib.Path(__file__).with_name("mojo_regex_cases.json")

# 50 cases: feature coverage (groups, lookaround, Unicode classes,
# possessive/atomic, recursion, backrefs, modes) + realistically BIG
# patterns (RFC-5322-ish mailbox, URI, Apache log line, 26-word
# alternation).
CASES = [
    # --- ported from the original 21-check battery ---
    dict(
        name="named_groups",
        mode="search_groupdict",
        pattern=r"(?<word>\w+)\s+(?<num>\d+)",
        input="abc 42",
    ),
    dict(
        name="casei_fullmatch",
        mode="fullmatch_bool",
        pattern=r"abc",
        input="aBc",
        flags=["IGNORECASE"],
    ),
    dict(
        name="casei_search",
        mode="match_bool",
        pattern=r"hello",
        input="HELLO world",
        flags=["IGNORECASE"],
    ),
    dict(name="non_greedy_star", mode="findall", pattern=r"<.*?>", input="<a><b><c>"),
    dict(name="non_greedy_plus", mode="search_group", pattern=r"a+?", input="aaaa", group=0),
    dict(name="non_greedy_opt", mode="match_bool", pattern=r"colou?r", input="color"),
    dict(
        name="word_boundary", mode="findall", pattern=r"\bcat\b", input="cat cathedral cat! thecat"
    ),
    dict(name="non_boundary", mode="findall", pattern=r"\Bcat", input="thecat scatter"),
    dict(
        name="unicode_L",
        mode="findall",
        pattern=r"\p{L}+",
        input="Hello 123 \u03b1\u03b2\u03b3 XYZ",
    ),
    dict(name="unicode_N", mode="findall", pattern=r"\p{N}+", input="abc123def456"),
    dict(
        name="unicode_Greek",
        mode="findall",
        pattern=r"\p{Greek}+",
        input="Hello \u03b1\u03b2\u03b3 World",
    ),
    dict(
        name="multiline",
        mode="findall",
        pattern=r"^Line\d",
        input="Line1\nLine2\nxLine3",
        flags=["MULTILINE"],
    ),
    dict(name="dotall", mode="match_bool", pattern=r"a.b", input="a\nb", flags=["DOTALL"]),
    dict(name="no_dotall", mode="fullmatch_bool", pattern=r"a.b", input="a\nb"),
    dict(
        name="lookahead_pos",
        mode="findall",
        pattern=r"\d+(?=\s*dollars)",
        input="100 dollars 200 euros 300 dollars",
    ),
    dict(name="lookbehind_pos", mode="findall", pattern=r"(?<=\$)\d+", input="$50 and $60 but 70"),
    dict(name="lookahead_neg", mode="findall", pattern=r"foo(?!bar)", input="foobar foobaz foo"),
    dict(name="S_class", mode="findall", pattern=r"\S+", input="a b\tc\nd"),
    dict(name="D_class", mode="findall", pattern=r"\D+", input="a1b2c3"),
    dict(name="W_class", mode="findall", pattern=r"\W+", input="a!b@c#"),
    dict(
        name="complex_unicode_email",
        mode="finditer_groups",
        pattern=r"(?i)(?<name>[\p{L}.]+)@(?<domain>[\p{L}\d.\-]+\.[\p{L}]{2,})",
        input="Contact: \u0391\u03bb\u03af\u03ba\u03b7.Papa@\u0395\u03bb\u03bb\u03ac\u03b4\u03b1.GR and bob@Example.com",
    ),
    # --- bigger patterns + wider feature coverage ---
    dict(
        name="rfc5322_mailbox",
        mode="findall",
        pattern=(
            r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9]"
            r"(?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9]"
            r"(?:[A-Z0-9-]{0,61}[A-Z0-9])?)+"
        ),
        input="clean@example.com bad@@nope x.y+z@sub.domain.co.uk missing@",
        flags=["IGNORECASE"],
    ),
    dict(
        name="uri_named_groups",
        mode="search_groupdict",
        pattern=(
            r"^(?<scheme>[a-z][a-z0-9+.-]*):\/\/(?:(?<userinfo>[^@\/]*)@)?"
            r"(?<host>[^:\/\?#]+)(?::(?<port>\d+))?"
            r"(?<path>[^\?#]*)(?:\?(?<query>[^#]*))?(?:#(?<fragment>.*))?$"
        ),
        input="https://user:pw@api.example.co.uk:8443/v1/notes/42?tag=alpha&z=1#frag",
    ),
    dict(
        name="apache_log_line",
        mode="search_groupdict",
        pattern=(
            r"^(?<ip>\S+) (?<ident>\S+) (?<user>\S+) \[(?<time>[^\]]+)\] "
            r"\"(?<method>\S+) (?<path>\S+) (?<proto>[^\"]+)\" "
            r"(?<status>\d{3}) (?<bytes>\d+|-)(?: \"(?<ref>[^\"]*)\" "
            r"\"(?<ua>[^\"]*)\")?$"
        ),
        input='10.0.0.1 - arun [29/Aug/2026:10:00:00 +0000] "GET /api/notes HTTP/1.1" 200 512 "https://ref" "Mozilla/5.0"',
    ),
    dict(
        name="markdown_links",
        mode="finditer_groups",
        pattern=r"\[([^\]]+)\]\(([^)\s]+)\)",
        input="see [Mojo docs](https://docs.modular.com/mojo) and [repo](https://github.com/x/y) now",
    ),
    dict(
        name="iso_datetime",
        mode="findall",
        pattern=(
            r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})"
            r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)?"
        ),
        input="at 2026-08-29 and 2026-08-29T10:11:12.5Z plus 2025-01-02 03:04:05+05:30 end",
    ),
    dict(
        name="currency_amounts",
        mode="findall",
        pattern=r"(?<!\d)\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?!\d)",
        input="cost 1,234.50 vs 987654.00 and $12.75 or 42",
    ),
    dict(name="possessive_star", mode="findall", pattern=r"\d*+9", input="1239 999 x9"),
    dict(name="atomic_group", mode="findall", pattern=r"(?>\d+)bar", input="123bar 1bar xbar"),
    dict(
        name="varwidth_lookbehind",
        mode="findall",
        pattern=r"(?<=\d{3})kg",
        input="100kg 50kg 1000kg",
    ),
    dict(
        name="backreference_word",
        mode="findall",
        pattern=r"(\w+) \1",
        input="hello hello world world fail ok",
    ),
    dict(
        name="nested_parens_recursion",
        mode="findall",
        pattern=r"(?<rec>\((?:[^()]|(?&rec))*\))",
        input="f(a(b(c)d)e) g((h)) i(",
    ),
    dict(
        name="keepout_k",
        mode="findall",
        pattern=r"\bkey\K\w*",
        input="keyword keynote lock keyboard",
    ),
    dict(name="conditional_group", mode="findall", pattern=r"(a)?b(?(1)c|d)", input="abc bd abd"),
    dict(
        name="branch_reset",
        mode="finditer_groups",
        pattern=r"(?|(?<a>xy)|(?<b>z))\d",
        input="xy1 z2 q3",
    ),
    dict(
        name="devanagari_script",
        mode="findall",
        pattern=r"\p{Devanagari}+",
        input="Hindi \u0928\u092e\u0938\u094d\u0924\u0947 word \u0926\u0941\u0928\u093f\u092f\u093e end",
    ),
    dict(
        name="han_cjk",
        mode="findall",
        pattern=r"[\p{Han}\p{Hiragana}\p{Katakana}]+",
        input="mix \u6f22\u5b57 text \u3072\u0930\u304c\u306a and \u30ab\u30bf\u30ab\u30ca",
    ),
    dict(
        name="currency_symbols",
        mode="findall",
        pattern=r"\p{Sc}",
        input="\u20b9 99 $5 \u00a53 \u00a51000 \u20ac2",
    ),
    dict(
        name="posix_class_alnum", mode="findall", pattern=r"[[:alnum:]]+", input="foo-bar_99 baz!"
    ),
    dict(
        name="hex_and_named_escape",
        mode="findall",
        pattern=r"\u03b1\u03b2|\N{GREEK SMALL LETTER GAMMA}",
        input="\u03b1\u03b2\u03b3\u03b4",
    ),
    dict(
        name="unicode_word_accented",
        mode="findall",
        pattern=r"\w+",
        input="caf\u00e9 \u00e9migr\u00e9 na\u00efve r\u00e9sum\u00e9",
    ),
    dict(
        name="verbose_flag",
        mode="findall",
        pattern=r"""\d{3}  # area
                    -       # dash
                    \d{4}  # number""",
        input="call 555-1234 or 555-5678",
        flags=["VERBOSE"],
    ),
    dict(
        name="scoped_flags",
        mode="findall",
        pattern=r"(?i:todo)|fixme",
        input="TODO fixme todo FIXME",
    ),
    dict(
        name="alternation_26_words",
        mode="findall",
        pattern=(
            r"alpha|bravo|charlie|delta|echo|foxtrot|golf|hotel|india|"
            r"juliet|kilo|lima|mike|november|oscar|papa|quebec|romeo|"
            r"sierra|tango|uniform|victor|whiskey|xray|yankee|zulu"
        ),
        input="alpha said tango to zulu; mike and xray met victor",
    ),
    dict(name="split_csv_fields", mode="split", pattern=r"\s*,\s*", input="a, b ,c,  d"),
    dict(name="sub_numbers", mode="sub", pattern=r"\d+", repl="N", input="a1 b22 c333"),
    dict(name="subn_numbers", mode="subn", pattern=r"\d+", repl="N", input="a1 b22 c333"),
    dict(
        name="date_groups",
        mode="finditer_groups",
        pattern=r"(\d{4})-(\d{2})-(\d{2})",
        input="2026-08-29, 2025-12-31, 2024-02-29",
    ),
    dict(
        name="lazy_optional_combo",
        mode="findall",
        pattern=r"colou??r",
        input="color colour colouur colr",
    ),
    dict(
        name="capture_repeat_last", mode="search_group", pattern=r"(a+)(\w*)", input="aaaa", group=1
    ),
    dict(
        name="greedy_vs_lazy_difference",
        mode="finditer_groups",
        pattern=r"a(.*)b|a(.*?)b",
        input="aXbYb aZb",
    ),
]


def main() -> int:
    for c in CASES:
        c["expected"] = run_case(c)  # golden from the reference module
    payload = {"cases": CASES}
    text = json.dumps(payload, indent=1) + "\n"
    if "--check" in sys.argv:
        if OUT.read_text() != text:
            print(f"STALE: {OUT.name} differs from authored cases")
            return 1
        print(f"OK: {len(CASES)} cases match committed file")
        return 0
    OUT.write_text(text)
    print(f"wrote {len(CASES)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
