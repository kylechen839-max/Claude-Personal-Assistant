"""Phone app features on top of the web app (webapp.py): the lecture
recorder and the homework tutor. Every /api route here needs the owner's
session (`require_owner`: a family member gets 404); /static holds the
page code for these screens and the vendored KaTeX (no CDN).

  GET  /api/lecture/courses            Course options from Notion
  POST /api/lecture/start              {course, mime, started_at} -> {id, ...}
  GET  /api/lecture/{id}               upload status and job state
  POST /api/lecture/{id}/chunk?offset= raw bytes; 409 {received} on a gap
  POST /api/lecture/{id}/finish        {total} -> starts the job
  POST /api/lecture/{id}/retry         re-run a failed job from its last stage
  POST /api/lecture/{id}/assignments   {index, name?, due?, type?} -> adds one
  GET  /api/lectures                   recent jobs
  POST /api/tutor                      form: image?, question, new?, full?
"""

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def lecture_to_history(job, notes: dict, meta: dict) -> bool:
    """Add the filed lecture's summary to the phone conversation it was
    recorded from, through the history module's public function (nothing
    if history is off or there was no conversation)."""
    from kwil import history

    cid = meta.get("conversation_id") or None
    if not cid:
        return False
    minutes = f", {round(job.duration_seconds / 60)} min" if job.duration_seconds else ""
    summary = " ".join((notes.get("summary") or "").split())[:800]
    text = (f"Lecture notes filed in Notion: {job.title or 'Lecture'} "
            f"({job.course or 'no course'}{minutes}). {summary} {job.notion_url}").strip()
    return history.record_event(cid, "lecture", text)


class Features:
    """The objects behind the routes, built lazily from config (each part
    can be passed in instead, for tests)."""

    def __init__(self, config: dict, assistant=None, client=None,
                 assignments=None, pipeline=None):
        self.config = config
        self.assistant = assistant  # the WebAssistant (TTS, transcriber)
        self._client = client
        self._assignments = assignments
        self._fixed_assignments = assignments is not None  # (tests)
        self._pipeline = pipeline
        self._courses: tuple[float, list[str]] | None = None
        self.tutors: dict[str, object] = {}  # web session id -> TutorSession
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            import anthropic

            a = self.config["assistant"]
            self._client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                               timeout=max(60, a["api_timeout"]))
        return self._client

    @property
    def assignments(self):
        """The owner's NotionAssignments, or None while his Notion isn't set
        (NOTION_TOKEN, or Connected services in the app: asked each time,
        so a connect or disconnect applies at once)."""
        if self._fixed_assignments:
            return self._assignments
        from kwil import owner_connect

        if not self.config["notion"]["enabled"] \
                or owner_connect.managed(self.config, "notion") == "none":
            return None
        if self._assignments is None:
            # with an app sign-in its client looks the token up per request
            self._assignments = owner_connect.owner_assignments(self.config)
            if self._pipeline is not None:  # built while Notion wasn't set
                self._pipeline.notes_db.assignments = self._assignments
        return self._assignments

    @property
    def pipeline(self):
        if self._pipeline is None:
            from kwil.lecture import (ChunkedUploads, LectureNotesDB,
                                           LecturePipeline)

            lec, stt = self.config["lecture"], self.config["stt"]
            root = Path(lec["upload_dir"])
            uploads = ChunkedUploads(
                root, max_bytes=int(float(lec["max_upload_mb"]) * 1_000_000),
                max_chunk_bytes=int(float(lec["chunk_mb"]) * 1_000_000) + 65536)
            notes_db = LectureNotesDB(self.assignments, lec["notes_database_id"],
                                      state_file=root / "notion.json")
            shared = getattr(getattr(self.assistant, "transcriber", None),
                             "model_name", None)

            def whisper(name):
                if name == shared:  # same model as voice turns: load it once
                    return self.assistant.transcriber.model
                from faster_whisper import WhisperModel
                return WhisperModel(name, device=stt["device"],
                                    compute_type=stt["compute_type"])

            uploads.purge_stale()
            self._pipeline = LecturePipeline(
                uploads, self.client, notes_db,
                model=self.config["assistant"]["model"], whisper_model=lec["model"],
                keep_audio=bool(lec["keep_audio"]), whisper_factory=whisper,
                on_filed=lecture_to_history)
        return self._pipeline

    def resume_jobs(self) -> int:
        """Re-queue lecture jobs cut off by a restart or redeploy (only if
        there have ever been recordings: nothing is created otherwise)."""
        lec = self.config["lecture"]
        if not lec["enabled"] or not Path(lec["upload_dir"]).is_dir():
            return 0
        return self.pipeline.resume_pending()

    def courses(self) -> list[str]:
        if self.assignments is None:
            return []
        now = time.monotonic()
        if self._courses is None or now - self._courses[0] > 600:
            names = [o["name"] for o in self.assignments.course_options()]
            self._courses = (now, names)
        return self._courses[1]

    def tutor(self, sid: str, new: bool = False):
        from kwil.tutor import TutorSession

        with self._lock:
            if new or sid not in self.tutors:
                if len(self.tutors) > 50:
                    self.tutors.pop(next(iter(self.tutors)))
                self.tutors[sid] = TutorSession(
                    client=self.client, model=self.config["assistant"]["model"],
                    max_tokens=self.config["tutor"]["max_tokens"])
            return self.tutors[sid]


def check_limits(config: dict) -> list[str]:
    """Lecture pieces and tutor photos must fit under the web app's request
    size cap ([webapp] max_upload_mb; 4 on the cloud server), or every
    upload would fail with 413. Values that don't fit are lowered to just
    under the cap, with a warning. Returns the warnings."""
    cap = float(config["webapp"]["max_upload_mb"])
    room = max(0.5, cap - 0.1)  # headroom for the request's own overhead
    warnings = []
    for section, key in (("lecture", "chunk_mb"), ("tutor", "max_image_mb")):
        value = float(config[section][key])
        if value > room:
            warnings.append(f"[{section}] {key} = {value:g} is over [webapp] "
                            f"max_upload_mb ({cap:g}); using {room:g}")
            config[section][key] = room
    for w in warnings:
        log.warning(w)
    return warnings


def install(app, features: Features) -> None:
    """Add the feature routes and /static to a create_app() app."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.concurrency import run_in_threadpool

    from kwil.lecture import UploadError, UploadGap
    from kwil.tutor import ImageRejected, prepare_image
    # owner only: lecture notes go to Kyle's Notion; a family member gets 404
    from kwil.webapp import require_owner

    lec_cfg, tutor_cfg = features.config["lecture"], features.config["tutor"]
    check_limits(features.config)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def error(message: str, status: int, **extra):
        return JSONResponse({"error": message, **extra}, status)

    def upload_error(e: UploadError):
        if isinstance(e, UploadGap):
            return error("resend from the server's offset", 409, received=e.received)
        return error(str(e), getattr(e, "status", 400))

    async def json_body(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def lecture_on():
        return None if lec_cfg["enabled"] else error("lecture recorder is off", 404)

    @app.get("/api/lecture/courses")
    async def lecture_courses(session=Depends(require_owner)):
        try:
            names = await run_in_threadpool(features.courses)
        except Exception:
            log.exception("Reading Course options from Notion failed")
            names = []
        return {"courses": names, "chunk_bytes":
                int(float(lec_cfg["chunk_mb"]) * 1_000_000),
                "max_bytes": int(float(lec_cfg["max_upload_mb"]) * 1_000_000)}

    @app.post("/api/lecture/start")
    async def lecture_start(request: Request, session=Depends(require_owner)):
        if (off := lecture_on()) is not None:
            return off
        if features.assignments is None:
            return error("Notion isn't connected (NOTION_TOKEN, or Connected "
                         "services in Settings), so lecture notes "
                         "have nowhere to go", 400)
        body = await json_body(request)
        uploads = features.pipeline.uploads
        try:
            upload_id = await run_in_threadpool(
                uploads.start, str(body.get("course") or ""),
                str(body.get("mime") or ""), str(body.get("started_at") or ""),
                getattr(getattr(session, "chat", None), "conversation_id", None) or "")
        except UploadError as e:
            return upload_error(e)
        return {**uploads.status(upload_id),
                "chunk_bytes": int(float(lec_cfg["chunk_mb"]) * 1_000_000)}

    @app.get("/api/lecture/{upload_id}")
    async def lecture_status(upload_id: str, session=Depends(require_owner)):
        p = features.pipeline
        try:
            status = p.uploads.status(upload_id)
            job = p.job(upload_id).public() if (
                p.uploads.directory(upload_id) / "job.json").exists() else None
        except UploadError as e:
            return upload_error(e)
        return {**status, "job": job}

    @app.api_route("/api/lecture/{upload_id}/chunk", methods=["POST", "PUT"])
    async def lecture_chunk(upload_id: str, offset: int, request: Request,
                            session=Depends(require_owner)):
        if (off := lecture_on()) is not None:
            return off
        data = await request.body()
        try:
            received = await run_in_threadpool(features.pipeline.uploads.append,
                                               upload_id, offset, data)
        except UploadError as e:
            return upload_error(e)
        return {"received": received}

    @app.post("/api/lecture/{upload_id}/finish")
    async def lecture_finish(upload_id: str, request: Request,
                             session=Depends(require_owner)):
        if (off := lecture_on()) is not None:
            return off
        body = await json_body(request)
        total = body.get("total")
        p = features.pipeline
        try:
            p.uploads.finish(upload_id, int(total) if total is not None else None)
            job = p.submit(upload_id)
        except UploadError as e:
            return upload_error(e)
        return job.public()

    @app.post("/api/lecture/{upload_id}/retry")
    async def lecture_retry(upload_id: str, session=Depends(require_owner)):
        if (off := lecture_on()) is not None:
            return off
        try:
            return features.pipeline.submit(upload_id).public()
        except UploadError as e:
            return upload_error(e)

    @app.post("/api/lecture/{upload_id}/assignments")
    async def lecture_add_assignment(upload_id: str, request: Request,
                                     session=Depends(require_owner)):
        body = await json_body(request)
        if features.assignments is None:
            return error("Notion isn't set up", 400)
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            return error("bad request", 400)
        overrides = {k: str(body[k])[:200] for k in ("name", "due", "type", "course")
                     if body.get(k) is not None}
        try:
            return await run_in_threadpool(features.pipeline.add_candidate,
                                           upload_id, index, features.assignments,
                                           overrides)
        except (UploadError, ValueError) as e:
            return error(str(e), 400)

    @app.get("/api/lectures")
    async def lectures(session=Depends(require_owner)):
        return {"jobs": [j.public() for j in features.pipeline.jobs()[:20]]}

    @app.post("/api/tutor")
    async def tutor(request: Request, session=Depends(require_owner)):
        if not tutor_cfg["enabled"]:
            return error("tutor is off", 404)
        max_bytes = int(float(tutor_cfg["max_image_mb"]) * 1_000_000)
        try:
            form = await request.form(max_files=1, max_fields=5)
        except Exception:
            return error("send a form", 400)
        question = str(form.get("question") or "")[:4000]
        upload = form.get("image")
        image = None
        if upload is not None and hasattr(upload, "read"):
            data = await upload.read(max_bytes + 1)
            try:
                image = await run_in_threadpool(prepare_image, data,
                                                upload.content_type, max_bytes)
            except ImageRejected as e:
                status = 413 if "MB" in str(e) else 415
                return error(str(e), status)
        new = form.get("new") == "1" or image is not None
        ts = features.tutor(session.sid, new=new)
        if not ts.history and image is None:
            return error("take or choose a photo of the problem first", 400)
        try:
            reply = await run_in_threadpool(ts.ask, question, image,
                                            form.get("full") == "1")
        except Exception:
            log.exception("Tutor request failed")
            return error("Claude couldn't answer right now", 502)
        out = {"text": reply.text, "spoken": reply.spoken}
        if features.assistant is not None and form.get("speak") == "1":
            rid = features.assistant.remember(reply.spoken, session)
            out["audio"] = f"/api/audio/{rid}"
        return out
