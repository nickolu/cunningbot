"""Smoke-test every model in the catalog against the live chat-completions API.

Being in the account's /v1/models listing does not mean a model works here: the
`-pro` and `-codex` variants are listed but only serve /v1/responses, and
deprecated ids linger in the listing. Both kinds shipped in the `/chat` picker
as options that could only ever error.

The test suite cannot catch this -- it runs with no network and no key. Run this
by hand after touching bot/domain/llm/models.py:

    OPENAI_API_KEY=... python3 scripts/check_models.py

Costs a few tokens per model. Exits non-zero if any model fails.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openai import AsyncOpenAI  # noqa: E402

from bot.domain.llm.models import MODELS, request_arguments  # noqa: E402


async def check(client, model):
    try:
        await client.chat.completions.create(
            messages=[{"role": "user", "content": "Reply OK"}],
            **request_arguments(model.id),
        )
        return None
    except Exception as e:  # noqa: BLE001 - reporting, not handling
        return str(e).split("\n")[0][:120]


async def main():
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set.")
    client = AsyncOpenAI()
    failures = []
    for model in MODELS:
        error = await check(client, model)
        flag = " (hidden)" if model.deprecated else ""
        print("%-18s %s%s" % (model.id, "FAIL: " + error if error else "ok", flag))
        if error:
            failures.append(model.id)
    if failures:
        sys.exit("\n%d model(s) unusable: %s" % (len(failures), ", ".join(failures)))
    print("\nAll %d models usable." % len(MODELS))


if __name__ == "__main__":
    asyncio.run(main())
