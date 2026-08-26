"""Renders published pages.

Callers supply Markdown, not HTML: it is what an LLM writes most reliably, and
``markdown_it`` escapes embedded raw HTML by default, so a page can't smuggle
script onto the public host. The document shell below supplies all the styling
so every published page looks like it came from the same place.
"""

from __future__ import annotations

import html as html_lib
from typing import Optional

from markdown_it import MarkdownIt

# html=False escapes raw HTML instead of passing it through. The "commonmark"
# preset turns it ON by default, so it must be set explicitly here.
# Do not enable it — see the module docstring.
_md = MarkdownIt(
    "commonmark", {"html": False, "linkify": True, "typographer": True}
).enable("table")

_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #fbfbfa; --fg: #1c1f23; --muted: #6b7280;
    --rule: #e5e7eb; --accent: #2563eb; --code-bg: #f3f4f6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16181a; --fg: #e8e6e3; --muted: #9ca3af;
      --rule: #2c3034; --accent: #7aa2f7; --code-bg: #1f2225;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 3rem 1.25rem 4rem;
    background: var(--bg); color: var(--fg);
    font: 16px/1.65 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-text-size-adjust: 100%;
  }}
  main {{ max-width: 44rem; margin: 0 auto; }}
  h1 {{ font-size: 1.75rem; line-height: 1.25; margin: 0 0 1.5rem; letter-spacing: -0.01em; }}
  h2 {{ font-size: 1.25rem; margin: 2.25rem 0 .75rem; letter-spacing: -0.01em; }}
  h3 {{ font-size: 1.05rem; margin: 1.75rem 0 .5rem; }}
  p, li {{ overflow-wrap: anywhere; }}
  ul, ol {{ padding-left: 1.35rem; }}
  li {{ margin: .3rem 0; }}
  a {{ color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }}
  hr {{ border: 0; border-top: 1px solid var(--rule); margin: 2.5rem 0; }}
  blockquote {{
    margin: 1.25rem 0; padding: .25rem 0 .25rem 1rem;
    border-left: 3px solid var(--rule); color: var(--muted);
  }}
  code {{
    background: var(--code-bg); padding: .15em .4em; border-radius: 4px;
    font: .875em/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  pre {{ background: var(--code-bg); padding: 1rem; border-radius: 8px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.25rem 0; font-size: .95rem; }}
  th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid var(--rule); }}
  th {{ font-weight: 600; }}
  footer {{
    max-width: 44rem; margin: 3.5rem auto 0; padding-top: 1.25rem;
    border-top: 1px solid var(--rule); color: var(--muted); font-size: .8125rem;
  }}
</style>
</head>
<body>
<main>
<h1>{heading}</h1>
{body}
</main>
<footer>{footer}</footer>
</body>
</html>
"""


def render_page(title: str, markdown: str, footer: Optional[str] = None) -> str:
    """Render ``markdown`` into a complete, self-contained HTML document."""
    body = _md.render(markdown or "")

    # markdown-it emits bare <table>; wrap so wide tables scroll instead of
    # forcing the whole page sideways on a phone.
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>"
    )

    safe_title = html_lib.escape(title or "Untitled")
    safe_footer = html_lib.escape(footer or "Published by CunningBot")

    return _SHELL.format(
        title=safe_title, heading=safe_title, body=body, footer=safe_footer
    )
