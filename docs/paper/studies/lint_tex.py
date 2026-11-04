"""
Structural checks on the LaTeX source, since there is no TeX here to compile.

This catches the mistakes that waste an Overleaf compile: unbalanced
environments or braces, a \\cite with no matching \\bibitem, a \\ref with no
\\label, a referenced graphic that is not in the repository, and the special
characters that silently change meaning outside maths or \\texttt.

Run:  python docs/paper/studies/lint_tex.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TEX = Path("docs/paper/voxplay-paper.tex")
FIGS = Path("docs/paper/figures")


def strip_verbatimish(text: str) -> str:
    """
    Remove comments, maths and \\texttt/\\url bodies before scanning.

    Escaped specials go first. An escaped dollar such as US\\$0.06 otherwise
    opens a maths run as far as the naive pairing is concerned, and every
    subsequent $ lands on the wrong side of it -- which produced a confident
    report of an unescaped underscore that did not exist.
    """
    text = re.sub(r"\\[$%&#_{}]", " ", text)
    text = re.sub(r"(?<!\\)%.*", "", text)
    text = re.sub(r"\$[^$]*\$", " ", text)
    text = re.sub(r"\\begin\{(equation|align)\*?\}.*?\\end\{\1\*?\}", " ",
                  text, flags=re.S)
    text = re.sub(r"\\(?:texttt|url|verb)\{[^}]*\}", " ", text)
    return text


def main() -> int:
    src = TEX.read_text(encoding="utf-8")
    problems: list[str] = []

    # Environments balance.
    opens = re.findall(r"\\begin\{([a-zA-Z*]+)\}", src)
    closes = re.findall(r"\\end\{([a-zA-Z*]+)\}", src)
    for name in set(opens) | set(closes):
        if opens.count(name) != closes.count(name):
            problems.append("environment %s: %d begin, %d end"
                            % (name, opens.count(name), closes.count(name)))

    # Braces balance, ignoring escaped ones.
    bare = re.sub(r"\\[{}]", "", re.sub(r"(?<!\\)%.*", "", src))
    if bare.count("{") != bare.count("}"):
        problems.append("braces: %d open, %d close"
                        % (bare.count("{"), bare.count("}")))

    # Every \cite resolves to a \bibitem, and every \bibitem is cited.
    cited = {k.strip() for group in re.findall(r"\\cite\{([^}]*)\}", src)
             for k in group.split(",")}
    defined = set(re.findall(r"\\bibitem\{([^}]*)\}", src))
    for key in sorted(cited - defined):
        problems.append("\\cite{%s} has no \\bibitem" % key)
    for key in sorted(defined - cited):
        problems.append("\\bibitem{%s} is never cited" % key)

    # Every \ref resolves to a \label.
    refs = set(re.findall(r"\\ref\{([^}]*)\}", src))
    labels = set(re.findall(r"\\label\{([^}]*)\}", src))
    for key in sorted(refs - labels):
        problems.append("\\ref{%s} has no \\label" % key)

    # Graphics exist.
    for name in re.findall(r"\\includegraphics\[[^\]]*\]\{([^}]*)\}", src):
        if not (FIGS / name).exists():
            problems.append("missing graphic: %s" % (FIGS / name))

    # Specials that must be escaped in running text.
    body = strip_verbatimish(src)
    for sym, name in (("%", "percent"), ("&", "ampersand"),
                      ("#", "hash"), ("_", "underscore")):
        for match in re.finditer(r"(?<!\\)\%s" % re.escape(sym), body):
            line = body[:match.start()].count("\n") + 1
            problems.append("unescaped %s near line %d" % (name, line))

    # Characters the Type 1 fonts cannot set without extra packages.
    for ch in sorted({c for c in src if ord(c) > 127}):
        problems.append("non-ASCII U+%04X %r -- prefer a LaTeX command"
                        % (ord(ch), ch))

    if problems:
        print("%d problem(s):" % len(problems))
        for item in problems:
            print("  -", item)
        return 1
    print("OK: environments and braces balance, every cite and ref resolves, "
          "graphics present, no unescaped specials, pure ASCII")
    return 0


if __name__ == "__main__":
    sys.exit(main())
