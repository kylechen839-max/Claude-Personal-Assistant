"""Phone app features on top of the web app (webapp.py): the lecture
recorder and the homework tutor. Every /api route here needs the same
session as the rest of the app (`require_session`); /static holds the
page code for these screens and the vendored KaTeX (no CDN).

  GET  /api/lecture/courses            Course options from Notion
  POST /api/lecture/start              {course, mime, started_at} -> {id, ...}
  GET  /api/lecture/{id}               upload status and job state
  PUT  /api/lecture/{id}/chunk?offset= raw bytes; 409 {received} on a gap
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


class Features:
    """The objects behind the routes, built lazily from config (each part
    can be passed in instead, for tests)."""

    def __init__(self, config: dict, assistant=None, client=None,
                 assignments=None, pipeline=None):
        self.config = config
        self.assistant = assistant  # the WebAssistant (TTS, transcriber)
        self._client = client
        self._assignments = assignments
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
        import os

        if self._assignments is None and self.config["notion"]["enabled"] \
                and os.environ.get("NOTION_TOKEN"):
            from assistant.tools.notion import NotionAssignments

            self._assignments = NotionAssignments(
                self.config["notion"]["database_id"],
                timezone=self.config["assistant"]["timezone"] or None)
        return self._assignments

    @property
    def pipeline(self):
        if self._pipeline is None:
            from assistant.lecture import (ChunkedUploads, LectureNotesDB,
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
                keep_audio=bool(lec["keep_audio"]), whisper_factory=whisper)
        return self._pipeline

    def courses(self) -> list[str]:
        if self.assignments is None:
            return []
        now = time.monotonic()
        if self._courses is None or now - self._courses[0] > 600:
            names = [o["name"] for o in self.assignments.course_options()]
            self._courses = (now, names)
        return self._courses[1]

    def tutor(self, sid: str, new: bool = False):
        from assistant.tutor import TutorSession

        with self._lock:
            if new or sid not in self.tutors:
                if len(self.tutors) > 50:
                    self.tutors.pop(next(iter(self.tutors)))
                self.tutors[sid] = TutorSession(
                    client=self.client, model=self.config["assistant"]["model"],
                    max_tokens=self.config["tutor"]["max_tokens"])
            return self.tutors[sid]


def install(app, features: Features) -> None:
    """Add the feature routes and /static to a create_app() app."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.concurrency import run_in_threadpool

    from assistant.lecture import UploadError, UploadGap
    from assistant.tutor import ImageRejected, prepare_image
    from assistant.webapp import require_session

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    lec_cfg, tutor_cfg = features.config["lecture"], features.config["tutor"]

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
    async def lecture_courses(session=Depends(require_session)):
        try:
            names = await run_in_threadpool(features.courses)
        except Exception:
            log.exception("Reading Course options from Notion failed")
            names = []
        return {"courses": names, "chunk_bytes":
                int(float(lec_cfg["chunk_mb"]) * 1_000_000),
                "max_bytes": int(float(lec_cfg["max_upload_mb"]) * 1_000_000)}

    @app.post("/api/lecture/start")
    async def lecture_start(request: Request, session=Depends(require_session)):
        if (off := lecture_on()) is not None:
            return off
        if features.assignments is None:
            return error("Notion isn't set up (NOTION_TOKEN), so lecture notes "
                         "have nowhere to go", 400)
        body = await json_body(request)
        uploads = features.pipeline.uploads
        upload_id = uploads.start(course=str(body.get("course") or ""),
                                  mime=str(body.get("mime") or ""),
                                  started_at=str(body.get("started_at") or ""))
        return {**uploads.status(upload_id),
                "chunk_bytes": int(float(lec_cfg["chunk_mb"]) * 1_000_000)}

    @app.get("/api/lecture/{upload_id}")
    async def lecture_status(upload_id: str, session=Depends(require_session)):
        p = features.pipeline
        try:
            status = p.uploads.status(upload_id)
            job = p.job(upload_id).public() if (
                p.uploads.directory(upload_id) / "job.json").exists() else None
        except UploadError as e:
            return upload_error(e)
        return {**status, "job": job}

    @app.put("/api/lecture/{upload_id}/chunk")
    async def lecture_chunk(upload_id: str, offset: int, request: Request,
                            session=Depends(require_session)):
        data = await request.body()
        try:
            received = await run_in_threadpool(features.pipeline.uploads.append,
                                               upload_id, offset, data)
        except UploadError as e:
            return upload_error(e)
        return {"received": received}

    @app.post("/api/lecture/{upload_id}/finish")
    async def lecture_finish(upload_id: str, request: Request,
                             session=Depends(require_session)):
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
    async def lecture_retry(upload_id: str, session=Depends(require_session)):
        try:
            return features.pipeline.submit(upload_id).public()
        except UploadError as e:
            return upload_error(e)

    @app.post("/api/lecture/{upload_id}/assignments")
    async def lecture_add_assignment(upload_id: str, request: Request,
                                     session=Depends(require_session)):
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
    async def lectures(session=Depends(require_session)):
        return {"jobs": [j.public() for j in features.pipeline.jobs()[:20]]}

    @app.post("/api/tutor")
    async def tutor(request: Request, session=Depends(require_session)):
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
            out["audio"] = f"/api/audio/{features.assistant.remember(reply.spoken)}"
        return out
