"""Study mode tools (study.py).

`study_tools(store)`: `start_study` (the model calls it when the person asks
to be quizzed or to study; `review` true starts from what they missed) and
`end_study` act on the running conversation (chat.current_chat). With the
person's memory store (memory_store.py; None when memory is off),
`note_missed` counts a concept they got wrong and `list_missed` reads the
list back.

start_study puts the course and topic into the next turn's system prompt,
so it asks first while outside text is in the conversation
(confirm_if_untrusted). note_missed doesn't ask: a study session grounded
in course files is always "tainted" (their text is others'), so a question
after every wrong answer would make it unusable; instead a concept noted
then is stored marked untrusted, it never reaches the system prompt (only
these tools' results, as data), and the person sees and deletes the list in
Settings, Memory (SECURITY.md). Incognito notes nothing; "Pause memory"
saves nothing new. Everything here is private: inputs and results stay
out of the log.
"""

from __future__ import annotations

from kwil.tools import Tool, mark_untrusted
from kwil.tools.memory_tools import _chat, _from_outside, _incognito

ENDED = ("Study mode is off from the next turn. Say so in a few words and go "
         "back to normal; offer a short recap of what to review if it helps.")


def _short(state: dict) -> str:
    return state.get("course") or state.get("topic") or "now"


def study_tools(store=None) -> list[Tool]:
    from kwil.memory_store import MAX_CONCEPT_CHARS, MemoryPaused
    from kwil.study import MAX_REVIEW, block, new_state

    def start_study(course: str = "", topic: str = "", review: bool = False) -> dict:
        chat = _chat()
        if chat is None or not hasattr(chat, "start_study"):
            return {"status": "error", "message": "Study mode needs a conversation."}
        state = chat.start_study(course=course, topic=topic, review=bool(review))
        # this turn's system prompt stays as it was (the cache rule): the
        # rules ride in this result, so the first question follows them
        rules = block(state, getattr(chat, "tools", None))
        about = getattr(chat, "_about_user", None)  # a family member's name, not Kyle's
        if callable(about):
            rules = about([rules])[0]
        result = {"status": "started", "course": state["course"],
                  "topic": state["topic"], "instructions": rules}
        if review:
            if store is None:
                result["to_review"] = []
                result["message"] = ("Missed concepts aren't kept here (memory is "
                                     "off): quiz on the course instead.")
            elif store.paused:  # "Pause memory": the list isn't used either
                result["to_review"] = []
                result["status_review"] = "paused"
                result["message"] = ("Memory is paused in Settings, so the missed list "
                                     "isn't used: say so and quiz on the course instead.")
            else:
                # only that course ("PHYS361" matches "PHYS 361"), never others
                found = store.missed(state["course"] or None)
                result["to_review"] = [
                    {k: m[k] for k in ("course", "concept", "count")}
                    for m in found[:MAX_REVIEW]]
                if not found:
                    where = f" for {state['course']}" if state["course"] else ""
                    result["message"] = (f"Nothing to review{where} yet: say so and "
                                         "quiz on the course instead.")
                mark_untrusted(result, any(m["untrusted"] for m in found[:MAX_REVIEW]))
        return result

    def end_study() -> dict:
        chat = _chat()
        if chat is None or not hasattr(chat, "end_study"):
            return {"status": "error", "message": "Study mode needs a conversation."}
        was = chat.end_study()
        return {"status": "ended" if was else "not_on", "message": ENDED}

    def note_missed(course: str, concept: str) -> dict:
        chat = _chat()
        refused = _incognito(chat)
        if refused:
            return refused
        try:
            saved = store.note_missed(course, concept, untrusted=_from_outside(chat))
        except MemoryPaused:
            return {"status": "paused",
                    "message": "Memory is paused in Settings, so nothing was noted. "
                               "Carry on with the quiz."}
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "noted", "id": saved["id"], "count": saved["count"]}

    def list_missed(course: str = "") -> dict:
        if store.paused:
            return {"status": "paused", "missed": [],
                    "message": "Memory is paused in Settings, so the missed list "
                               "isn't used. Say so in one short sentence."}
        found = store.missed(str(course or "")[:100] or None)
        result = {"missed": [{k: m[k] for k in ("id", "course", "concept", "count",
                                                "last_seen")} for m in found[:100]],
                  "total": len(found)}
        return mark_untrusted(result, any(m["untrusted"] for m in found[:100]))

    tools = [
        Tool("start_study",
             "Turn on study mode: you become a Socratic tutor who asks one "
             "question at a time. Call it when the user asks to be quizzed, "
             "tested or to study ('quiz me on PHYS 361', 'help me study for the "
             "midterm'), or with review true for 'review what I missed'. Its "
             "result has the rules to follow from now on.",
             {"type": "object", "properties": {
                 "course": {"type": "string",
                            "description": "The course, as the user named it (e.g. PHYS 361)"},
                 "topic": {"type": "string",
                           "description": "The topic or exam, if they said one"},
                 "review": {"type": "boolean",
                            "description": "Start from the concepts they missed before"}}},
             start_study,
             confirm_if_untrusted=lambda i: f"Start studying {_short(new_state(i.get('course'), i.get('topic')))}?",
             private=True),
        Tool("end_study",
             "Turn study mode off, when the user says 'stop studying', 'I'm done', "
             "'that's enough' or similar.",
             {"type": "object", "properties": {}}, end_study),
    ]
    if store is not None:
        tools += [
            Tool("note_missed",
                 "In study mode, after a wrong or partly right answer: note the "
                 "concept the user missed, so 'review what I missed' can come back "
                 f"to it. The concept in a few words (at most {MAX_CONCEPT_CHARS} "
                 "characters), not the question or their answer.",
                 {"type": "object", "properties": {
                     "course": {"type": "string"},
                     "concept": {"type": "string",
                                 "description": "e.g. Gauss's law for a charged shell"}},
                  "required": ["course", "concept"]},
                 note_missed, private=True),
            Tool("list_missed",
                 "List the concepts the user missed in study mode, most missed "
                 "first, optionally for one course.",
                 {"type": "object", "properties": {
                     "course": {"type": "string",
                                "description": "Only this course (part of its name)"}}},
                 list_missed, private=True),
        ]
    return tools
