"""Deep research tools (research.py runs the jobs).

`research_tools(service, uid)`: one person's (uid None: the owner).

- `start_research(question, depth)`: "research the best internships for
  physics majors this summer", "do a deep dive on X". Always asks first,
  with the time and how many searches it may do; with outside text in the
  conversation the question is shown in full (a page could have written
  it). Nothing is asked when it would be refused anyway (one running, the
  day's count, history off): the result says why. Returns at once.
- `research_status`: read-only, the person's recent jobs ("is my research
  done?").

The question is the person's own words: both tools are private (no inputs
or results in the log)."""

from __future__ import annotations

from kwil.tools import UNTRUSTED_CONTEXT, Tool

SHORT = 120  # characters of the question in an untainted conversation's question


def research_tools(service, uid: str | None = None) -> list[Tool]:
    from kwil import i18n
    from kwil import research as R
    from kwil.tools.memory_tools import _chat

    def lang() -> str:
        return "zh" if getattr(_chat(), "language", "en") == "zh" else "en"

    def say(key: str) -> str:
        return i18n.t(key, lang(), per_day=service.settings.per_day,
                      chars=R.MAX_QUESTION)

    def depth_of(i: dict) -> str:
        depth = i.get("depth") or "standard"
        return depth if depth in R.DEPTHS else "standard"

    def ask(i: dict) -> str | None:
        try:
            question = R.clean_question(i.get("question"))
        except R.ResearchRefused:
            return None  # nothing to ask: the tool says why
        chat = _chat()
        if getattr(chat, "incognito", False) is True or service.refusal(uid):
            return None
        depth = depth_of(i)
        low, high = service.settings.minutes(depth)
        # outside text in the conversation: the whole question, so a page
        # can't hide what it would make Kwil research behind a cut
        shown = question if UNTRUSTED_CONTEXT.get() else R.cut(question, SHORT)
        return i18n.t("research_confirm", lang(), question=shown, low=low, high=high,
                      searches=service.settings.budgets[depth].searches,
                      cost=i18n.t(f"research_cost_{depth}", lang()))

    def start_research(question: str, depth: str = "standard") -> dict:
        chat = _chat()
        if getattr(chat, "incognito", False) is True:
            return {"status": "refused", "message": say("research_off")}
        try:
            job = service.start(uid, question, depth_of({"depth": depth}),
                                tainted=bool(UNTRUSTED_CONTEXT.get()))
        except R.ResearchRefused as e:
            return {"status": "refused", "reason": e.key, "message": say(e.key)}
        low, high = service.settings.minutes(job["depth"])
        return {"status": "started", "id": job["id"],
                "message": "The research runs in the background now. Tell the user "
                           "in one short sentence that you'll send a notification "
                           f"when it's ready (about {low} to {high} minutes) and "
                           "that it shows in the chat. Don't research it yourself now."}

    def research_status() -> dict:
        jobs = service.jobs_of(uid) or []
        return {"jobs": [{k: j[k] for k in ("question", "status", "searches",
                                             "max_searches", "partial", "created")}
                         for j in jobs[:5]]}

    return [
        Tool("start_research",
             "Start deep research in the background: several rounds of web "
             "search and reading, then a written report with sources, saved to "
             "History, with a notification when it's ready. Use it when the user "
             "asks to research something, for a deep dive, or a thorough "
             "comparison; for a quick fact, search the web yourself instead. The "
             "user is asked to confirm automatically; just call it. depth "
             "'standard' (about 5-10 minutes) unless they ask for a deep or "
             "thorough one ('deep', about 10-20 minutes). Only the question goes "
             "to the research; put everything it needs in it.",
             {"type": "object",
              "properties": {
                  "question": {"type": "string",
                               "description": "What to research, complete and self-"
                                              f"contained (at most {R.MAX_QUESTION} "
                                              "characters), in the user's words."},
                  "depth": {"type": "string", "enum": list(R.DEPTHS)}},
              "required": ["question"]},
             start_research, confirm_prompt=ask, private=True),
        Tool("research_status",
             "The user's recent research jobs: running (with searches so far) or "
             "finished. Finished reports are in History.",
             {"type": "object", "properties": {}},
             research_status, private=True),
    ]
