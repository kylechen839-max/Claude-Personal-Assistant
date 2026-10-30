"""Deep research tools (research.py runs the jobs).

`research_tools(service, uid)`: one person's (uid None: the owner).

- `start_research(question, depth)`: "research the best internships for
  physics majors this summer", "do a deep dive on X". Always asks first,
  with the time, how many searches it may do and the whole question (it
  is all the research sees). Nothing is asked when it would be refused
  anyway (one running, the day's count, history off): that call then
  refuses with the reason, even if it cleared meanwhile (never starts
  unasked). It starts only the question that was confirmed. Returns at
  once.
- `research_status`: read-only, the person's recent jobs ("is my research
  done?"). Its result counts as outside text (a question may have been
  written while a web page was in a conversation).

The question is the person's own words: both tools are private (no inputs
or results in the log)."""

from __future__ import annotations

import threading

from kwil.tools import UNTRUSTED_CONTEXT, Tool, mark_untrusted

_DECIDED = threading.local()  # this thread's last ask(): the question asked, or a refusal


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
        """The question to confirm, or None when it would be refused: then
        the refusal is kept for this call, and the tool refuses with it
        (never starts unasked, even if the refusal clears meanwhile)."""
        _DECIDED.value = None
        try:
            question = R.clean_question(i.get("question"))
        except R.ResearchRefused as e:
            _DECIDED.value = ("refused", e.key)
            return None  # nothing to ask: the tool says why
        chat = _chat()
        why = "research_off" if getattr(chat, "incognito", False) is True \
            else service.refusal(uid)
        if why:
            _DECIDED.value = ("refused", why)
            return None
        _DECIDED.value = ("asked", question)
        depth = depth_of(i)
        low, high = service.settings.minutes(depth)
        # always the whole question: it is all the research sees, so the
        # person confirms exactly what leaves (at most MAX_QUESTION characters)
        return i18n.t("research_confirm", lang(), question=question, low=low, high=high,
                      searches=service.settings.budgets[depth].searches,
                      cost=i18n.t(f"research_cost_{depth}", lang()))

    def start_research(question: str, depth: str = "standard") -> dict:
        decided, _DECIDED.value = getattr(_DECIDED, "value", None), None
        if decided is None or decided[0] != "asked":
            key = decided[1] if decided else "research_bad"
            return {"status": "refused", "reason": key, "message": say(key)}
        try:
            if R.clean_question(question) != decided[1]:  # not what was confirmed
                raise R.ResearchRefused("research_bad")
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

    # its questions may have been written while outside text was in a
    # conversation (a job's `tainted`): always outside text here
    def research_status() -> dict:
        jobs = service.jobs_of(uid) or []
        result = {"jobs": [{k: j[k] for k in ("question", "status", "searches",
                                               "max_searches", "partial", "created")}
                           for j in jobs[:5]]}
        person = service.person(uid)
        tainted = person is not None and any(
            j.get("tainted") for j in person.store.list()[:5])
        return mark_untrusted(result, tainted or bool(jobs))

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
             "to the research; put everything it needs in it. Don't include "
             "personal details (names, contact details, schedule, grades, "
             "addresses) unless the user asked for them in this research.",
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
             research_status, untrusted_output=True, private=True),
    ]
