import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _reports_support import COURSE_ID, CONTENTS, build, config, load_pool
from studai.api import server
from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.study import calendar
from studai.features.study import service as study

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)  # quinta-feira, 12h em São Paulo
TODAY = date(2026, 10, 22)
WEIGHTS = {"Matemática": 0.6, "Física": 0.4}


def demand_item(identity: str, subject: str, questions: int, priority: float, gap: bool = False) -> dict:
    return {"id": identity, "name": identity, "subject": subject, "questions": questions, "gap": gap,
            "depth": 0.0, "priority": priority}


def content_of(content_id: str) -> str:
    return CONTENT_INDEX[content_id]["path_ids"][2]


class ScheduleTests(unittest.TestCase):
    def demand(self, per_content=6):
        return [demand_item(f"m{i}", "Matemática", per_content, 1 - i / 10) for i in range(4)] + \
               [demand_item(f"f{i}", "Física", per_content, 0.5 - i / 10) for i in range(3)]

    def schedule(self, days=30, demand=None, simulados=None):
        review = {"Matemática": [{"id": "m0", "name": "m0"}], "Física": [{"id": "f0", "name": "f0"}]}
        return calendar.build_schedule(TODAY, TODAY + timedelta(days=days), self.demand() if demand is None else demand,
                                       WEIGHTS, review, simulados if simulados is not None else
                                       [{"sourceId": "p1", "label": "Prova 1", "questions": 90, "answered": False}])

    def test_layout_has_weekly_simulados_final_stretch_eve_and_exam(self):
        days, _ = self.schedule()
        self.assertEqual(days[-1]["kind"], "exam")
        self.assertEqual(days[-2]["kind"], "eve")
        for entry in days:
            left = entry["daysLeft"]
            weekday = date.fromisoformat(entry["date"]).weekday()
            self.assertEqual(entry["phase"], "reta_final" if left <= calendar.FINAL_STRETCH_DAYS else "construcao")
            if left > calendar.FINAL_STRETCH_DAYS:
                self.assertEqual(entry["kind"] == "simulado", weekday == 6)
            elif left > 1:
                self.assertEqual(entry["kind"] == "simulado", left % 3 == 1)
        simulado = next(entry for entry in days if entry["kind"] == "simulado")
        self.assertEqual((simulado["simulado"]["sourceId"], simulado["target"]), ("p1", 90))

    def test_every_planned_content_is_scheduled_and_subjects_alternate(self):
        days, load = self.schedule()
        self.assertTrue(load["feasible"])
        self.assertEqual(load["planned"], 42)
        focus = [block for entry in days for block in entry["blocks"] if block["kind"] == "focus"]
        self.assertEqual(sum(block["questions"] for block in focus), 42)
        self.assertEqual({content for block in focus for content in block["contentIds"]},
                         {item["id"] for item in self.demand()})
        first_subjects = [entry["blocks"][0]["subject"] for entry in days[:3] if entry["kind"] == "study"]
        self.assertEqual(len(set(first_subjects)), 2)
        # Fila vazia: os dias seguintes viram revisão, sempre com a carga do dia.
        for entry in days:
            if entry["kind"] == "study":
                self.assertEqual(entry["target"], load["questionsPerDay"])

    def test_schedule_is_deterministic(self):
        self.assertEqual(self.schedule(), self.schedule())

    def test_load_rises_when_time_is_short_and_cuts_low_priority_contents(self):
        _, relaxed = self.schedule(days=60)
        big = [demand_item(f"m{i}", "Matemática", 6, 1 - i / 1000) for i in range(100)]
        days, tight = self.schedule(days=5, demand=big)
        self.assertEqual(relaxed["questionsPerDay"], calendar.MIN_DAILY)
        self.assertEqual(tight["questionsPerDay"], calendar.MAX_DAILY)
        self.assertFalse(tight["feasible"])
        self.assertEqual(tight["planned"], calendar.MAX_DAILY * tight["studyDays"])
        planned = {content for entry in days for block in entry["blocks"] if block["kind"] == "focus"
                   for content in block["contentIds"]}
        self.assertIn("m0", planned)
        self.assertNotIn("m99", planned)

    def test_heavy_days_split_between_two_subjects(self):
        big = [demand_item(f"m{i}", "Matemática", 6, 1 - i / 100) for i in range(30)] + \
              [demand_item(f"f{i}", "Física", 6, 0.5 - i / 100) for i in range(30)]
        days, load = self.schedule(days=10, demand=big)
        first = next(entry for entry in days if entry["kind"] == "study")
        self.assertGreater(load["questionsPerDay"], 2 * calendar.MIN_DAILY)
        self.assertEqual({block["subject"] for block in first["blocks"]}, {"Matemática", "Física"})
        self.assertTrue(all(len(block["contentIds"]) == len(set(block["contentIds"])) for block in first["blocks"]))

    def test_daily_load_bounds(self):
        self.assertEqual(calendar.daily_load(0, 10)["questionsPerDay"], calendar.MIN_DAILY)
        self.assertEqual(calendar.daily_load(300, 10)["questionsPerDay"], 30)
        self.assertEqual(calendar.daily_load(10_000, 10),
                         {"questionsPerDay": calendar.MAX_DAILY, "demand": 10_000, "studyDays": 10, "feasible": False})

    def test_without_exams_in_the_archive_simulado_days_become_mixed_training(self):
        days, _ = self.schedule(simulados=[])
        sunday = next(entry for entry in days if entry["kind"] == "simulado")
        self.assertIsNone(sunday["simulado"])
        self.assertTrue(sunday["blocks"])

    def test_exam_subjects_use_fase2_of_the_course(self):
        profile = {"pesos": {"Matemática": 3, "Física": 2, "Química": 1},
                   "fase2": {"dia1": ["Língua Portuguesa"], "dia2": ["Física", "Matemática"]}}
        self.assertEqual(calendar.exam_subjects(profile, "fase1"), ["Matemática", "Física", "Química"])
        self.assertEqual(calendar.exam_subjects(profile, "fase2"), ["Física", "Matemática"])
        self.assertEqual(calendar.exam_subjects({"pesos": {"Física": 1}}, "fase2"), ["Física"])


class CalendarUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.pool = load_pool(self.db, self.processed)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def overview(self, now=NOW, cfg=None):
        return calendar.calendar_overview(self.db, self.pool, cfg or config(), COURSE_ID, now)

    def answer_today(self, now, subject=None, correct=True, limit=None):
        session = study.plan_today(self.db, self.pool, config(), COURSE_ID, now)
        answered = 0
        for item in session["items"]:
            if subject and item["subject"] != subject or item["answered"] or answered == limit:
                continue
            raw = next(i for i in json.loads(self.db.execute("SELECT plan_json FROM study_sessions WHERE id=?",
                                                              (session["id"],)).fetchone()[0])["items"]
                       if i["id"] == item["id"])
            answer = raw["correctAnswer"] if correct else next(l for l in "ABCDE" if l != raw["correctAnswer"])
            study.record_study_answer(self.db, session["id"], item["id"], answer,
                                      None if correct else "unknown_content", now, self.pool)
            answered += 1
        return answered

    def test_exam_crud_validation_and_next_exam(self):
        later = calendar.add_exam(self.db, COURSE_ID, "FUVEST 2ª fase", "2026-12-13", "fase2", NOW)
        first = calendar.add_exam(self.db, COURSE_ID, " FUVEST   1ª fase ", "2026-11-22", "fase1", NOW)
        self.assertEqual((first["name"], first["daysLeft"], first["phaseLabel"]), ("FUVEST 1ª fase", 31, "1ª fase"))
        exams = calendar.list_exams(self.db, COURSE_ID, TODAY)
        self.assertEqual([exam["id"] for exam in exams], [first["id"], later["id"]])
        self.assertEqual(calendar.next_exam(exams)["id"], first["id"])
        self.assertEqual(calendar.next_exam(calendar.list_exams(self.db, COURSE_ID, date(2026, 11, 23)))["id"],
                         later["id"])
        for args in (("", "2026-11-22", "fase1"), ("X", "22/11/2026", "fase1"), ("X", "2026-10-01", "fase1"),
                     ("X", "2026-11-22", "fase3"), ("X", "2030-01-01", "fase1")):
            with self.assertRaises(ValueError):
                calendar.add_exam(self.db, COURSE_ID, *args, NOW)
        calendar.delete_exam(self.db, later["id"])
        with self.assertRaises(calendar.ExamNotFound):
            calendar.delete_exam(self.db, later["id"])

    def test_without_exam_shows_readiness_but_no_plan(self):
        data = self.overview()
        self.assertIsNone(data["target"])
        self.assertEqual(data["days"], [])
        self.assertEqual([item["subject"] for item in data["readiness"]["subjects"]], ["Matemática", "Física"])
        self.assertEqual(data["readiness"]["overall"], 0.0)

    def test_overview_plans_until_the_exam_with_weeks_and_months(self):
        exam = calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW)
        data = self.overview()
        self.assertEqual(data["target"]["id"], exam["id"])
        self.assertEqual(data["days"][0]["date"], "2026-10-01")  # início do mês, antes de hoje
        upcoming = [entry for entry in data["days"] if entry["kind"] != "past"]
        self.assertEqual((upcoming[0]["date"], upcoming[-1]["kind"]), ("2026-10-22", "exam"))
        self.assertTrue(upcoming[0]["frozen"])
        self.assertEqual([month["key"] for month in data["months"]], ["2026-10", "2026-11"])
        self.assertEqual(data["weeks"][0]["key"], "2026-10-19")
        planned = {block["subject"] for entry in upcoming for block in entry["blocks"]}
        self.assertEqual(planned, {"Matemática", "Física"})
        simulado = next(entry for entry in upcoming if entry["kind"] == "simulado")
        self.assertEqual(simulado["simulado"]["label"], "Prova FUVEST 2025 (V1)")

    def test_fase2_target_keeps_only_its_subjects(self):
        cfg = config()
        cfg["cursos"][COURSE_ID]["fase2"] = {"dia1": ["Língua Portuguesa"], "dia2": ["Física"]}
        calendar.add_exam(self.db, COURSE_ID, "2ª fase", "2026-12-13", "fase2", NOW)
        data = self.overview(cfg=cfg)
        self.assertEqual(data["subjects"], ["Física"])
        self.assertEqual({block["subject"] for entry in data["days"] for block in entry["blocks"]}, {"Física"})

    def test_fase1_target_weights_every_question_the_same(self):
        cfg = config()
        cfg["cursos"][COURSE_ID].update(formula="fuvest-2027", fase2={"dia1": [], "dia2": ["Matemática"]})
        weights = lambda data: {item["subject"]: item["weight"] for item in data["readiness"]["subjects"]}
        # Sem prova cadastrada vale a nota final, em que a 2ª fase pesa Matemática.
        self.assertEqual(weights(self.overview(cfg=cfg)), {"Matemática": 0.75, "Física": 0.25})
        calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW)
        self.assertEqual(weights(self.overview(cfg=cfg)), {"Matemática": 0.5, "Física": 0.5})

    def test_daily_plan_uses_the_phase_of_the_next_exam(self):
        self.assertIsNone(study.target_phase(self.db, COURSE_ID, NOW))
        calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW)
        self.assertEqual(study.target_phase(self.db, COURSE_ID, NOW), "fase1")
        self.assertIsNone(study.target_phase(self.db, COURSE_ID, NOW + timedelta(days=32)))
        with patch.object(study, "resolve_weights", wraps=study.resolve_weights) as resolve:
            study.plan_today(self.db, self.pool, config(), COURSE_ID, NOW)
        self.assertEqual(resolve.call_args.args[2], "fase1")

    def test_today_is_frozen_while_progress_updates(self):
        calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW)
        before = next(entry for entry in self.overview()["days"] if entry["date"] == "2026-10-22")
        subject = before["blocks"][0]["subject"]
        answered = self.answer_today(NOW + timedelta(minutes=5), subject=subject, limit=3)
        self.assertEqual(answered, 3)
        after = next(entry for entry in self.overview(NOW + timedelta(hours=1))["days"]
                     if entry["date"] == "2026-10-22")
        self.assertEqual([(b["subject"], b["contentIds"], b["questions"]) for b in after["blocks"]],
                         [(b["subject"], b["contentIds"], b["questions"]) for b in before["blocks"]])
        self.assertEqual(after["blocks"][0]["done"], 3)
        self.assertEqual(after["status"], "partial")
        # No dia seguinte, ontem aparece com o plano congelado e o que foi feito.
        tomorrow = self.overview(NOW + timedelta(days=1))
        yesterday = next(entry for entry in tomorrow["days"] if entry["date"] == "2026-10-22")
        self.assertEqual((yesterday["status"], yesterday["done"]), ("partial", 3))

    def test_day_activity_uses_the_local_day(self):
        late_night = datetime(2026, 10, 23, 2, 30, tzinfo=timezone.utc)  # 22/10 23h30 em São Paulo
        self.answer_today(late_night, limit=2)
        self.assertEqual(calendar.day_activity(self.db, COURSE_ID, date(2026, 10, 22))["total"], 2)
        self.assertEqual(calendar.day_activity(self.db, COURSE_ID, date(2026, 10, 23))["total"], 0)

    def test_training_block_can_focus_on_calendar_contents(self):
        session = study.plan_today(self.db, self.pool, config(), COURSE_ID, NOW)
        target = content_of(CONTENTS["Física"][2])
        updated = study.append_training_item(self.db, session["id"], self.pool, config(), NOW,
                                             subjects=["Física"], count=3, content_ids=[target])
        extra = [item for item in updated["items"] if item["id"].startswith("t")]
        self.assertEqual(len(extra), 3)
        self.assertTrue(all(target in CONTENT_INDEX[item["contentId"]]["path_ids"] for item in extra))
        # Conteúdo sem questões no acervo: o bloco usa a matéria inteira.
        fallback = study.append_training_item(self.db, session["id"], self.pool, config(), NOW,
                                              subjects=["Física"], count=1, content_ids=["matematica"])
        self.assertEqual(fallback["items"][-1]["subject"], "Física")

    def test_reset_learning_context_keeps_exam_dates(self):
        calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW)
        self.overview()
        study.reset_learning_context(self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calendar_days").fetchone()[0], 0)
        self.assertEqual(len(calendar.list_exams(self.db, COURSE_ID, TODAY)), 1)


class CalendarApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        processed, config_path, db_path = build(Path(self.temp.name))
        web = Path(self.temp.name) / "web"
        web.mkdir()
        (web / "calendar.js").write_text("window.ok = true;\n", encoding="utf-8")
        self.patches = [
            patch.object(server, "WEB_ROOT", web),
            patch.object(server, "DEFAULT_QUESTIONS_DIR", processed),
            patch.object(server, "DEFAULT_DIAGNOSTICS_DB", db_path),
            patch.object(server, "DEFAULT_COURSE_CONFIG", config_path),
            patch.dict(server.POOL_CACHE, clear=True),
        ]
        for active in self.patches:
            active.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        for active in reversed(self.patches):
            active.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers={"Content-Type": "application/json"} if body else {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_calendar_routes(self):
        date_ = (datetime.now(timezone.utc).date() + timedelta(days=40)).isoformat()
        status, exam = self.request("/api/calendar/exams", "POST",
                                    {"courseId": COURSE_ID, "name": "FUVEST", "date": date_, "phase": "fase1"})
        self.assertEqual(status, 201)
        status, data = self.request(f"/api/calendar?courseId={COURSE_ID}")
        self.assertEqual((status, data["target"]["id"]), (200, exam["id"]))
        self.assertEqual(data["days"][-1]["kind"], "exam")
        status, today = self.request(f"/api/calendar/today?courseId={COURSE_ID}")
        self.assertEqual((status, today["today"]["date"]), (200, data["today"]))
        self.assertEqual(self.request("/api/calendar/exams", "POST",
                                      {"courseId": COURSE_ID, "name": "X", "date": "amanhã"})[0], 400)
        self.assertEqual(self.request("/api/calendar/exams", "POST",
                                      {"courseId": "nao-existe", "name": "X", "date": date_})[0], 404)
        self.assertEqual(self.request("/api/calendar?courseId=nao-existe")[0], 404)
        self.assertEqual(self.request(f"/api/calendar/exams/{exam['id']}", "DELETE")[0], 200)
        self.assertEqual(self.request(f"/api/calendar/exams/{exam['id']}", "DELETE")[0], 404)

    def test_calendar_js_is_served(self):
        with urllib.request.urlopen(self.base + "/calendar.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.headers["Content-Type"])

    def test_training_next_accepts_content_ids(self):
        status, session = self.request(f"/api/study/today?courseId={COURSE_ID}")
        self.assertEqual(status, 200)
        target = content_of(CONTENTS["Matemática"][0])
        status, updated = self.request("/api/study/training/next", "POST",
                                       {"sessionId": session["id"], "subjects": ["Matemática"], "count": 2,
                                        "contentIds": [target]})
        self.assertEqual(status, 200)
        extra = [item for item in updated["items"] if item["id"].startswith("t")]
        self.assertTrue(extra and all(target in CONTENT_INDEX[item["contentId"]]["path_ids"] for item in extra))
        self.assertEqual(self.request("/api/study/training/next", "POST",
                                      {"sessionId": session["id"], "contentIds": "x"})[0], 400)


if __name__ == "__main__":
    unittest.main()


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "h.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def demand(self):
        return [demand_item(f"m{i}", "Matemática", 6, 1 - i / 100) for i in range(30)]

    def schedule(self, availability, days=27):
        review = {"Matemática": [{"id": "m0", "name": "m0"}]}
        simulados = [{"sourceId": "p1", "label": "Prova 1", "questions": 90, "answered": False}]
        return calendar.build_schedule(TODAY, TODAY + timedelta(days=days), self.demand(), {"Matemática": 1.0},
                                       review, simulados, availability)

    def test_days_follow_the_time_available(self):
        # Seg–sex 1 h (15 questões), sábado 2 h (30), domingo livre 4 h: simulado.
        availability = {0: 60, 1: 60, 2: 60, 3: 60, 4: 60, 5: 120, 6: 240}
        days, load = self.schedule(availability)
        construction = [d for d in days if d["phase"] == "construcao"]
        for entry in construction:
            weekday = date.fromisoformat(entry["date"]).weekday()
            if weekday == 6:
                self.assertEqual(entry["kind"], "simulado")
            else:
                self.assertEqual(entry["kind"], "study")
                self.assertLessEqual(entry["target"], availability[weekday] // calendar.QUESTION_MINUTES)
        saturdays = [d["target"] for d in construction if date.fromisoformat(d["date"]).weekday() == 5]
        weekdays = [d["target"] for d in construction if date.fromisoformat(d["date"]).weekday() == 0]
        self.assertGreater(saturdays[0], weekdays[0])
        self.assertTrue(load["byAvailability"])

    def test_day_without_time_is_rest_and_short_sunday_has_no_simulado(self):
        days, load = self.schedule({0: 0, 1: 60, 2: 60, 3: 60, 4: 60, 5: 60, 6: 60})
        construction = [d for d in days if d["phase"] == "construcao"]
        mondays = [d for d in construction if date.fromisoformat(d["date"]).weekday() == 0]
        sundays = [d for d in construction if date.fromisoformat(d["date"]).weekday() == 6]
        self.assertTrue(all(d["kind"] == "rest" and d["target"] == 0 for d in mondays))
        self.assertTrue(all(d["kind"] == "study" for d in sundays))
        self.assertTrue(load["feasible"])
        # Com 10 dias e 1 h por dia (15 questões), 30 conteúdos × 6 questões não cabem.
        _, short = self.schedule({0: 0, 1: 60, 2: 60, 3: 60, 4: 60, 5: 60, 6: 60}, days=10)
        self.assertFalse(short["feasible"])
        rest = calendar.attach_progress(dict(mondays[0]), {"subjects": {}, "exam": 0, "total": 0}, TODAY)
        self.assertEqual(rest["status"], "rest")

    def test_store_and_validate(self):
        self.assertIsNone(calendar.get_availability(self.db, "curso"))
        saved = calendar.set_availability(self.db, "curso", {"0": 60, "6": "240"})
        self.assertEqual(saved, {0: 60, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 240})
        self.assertEqual(calendar.get_availability(self.db, "curso"), saved)
        for value in ({}, {"0": -1}, {"0": 800}, {"0": "x"}, "60"):
            with self.assertRaises(ValueError):
                calendar.set_availability(self.db, "curso", value)
        self.assertIsNone(calendar.set_availability(self.db, "curso", None))
        self.assertIsNone(calendar.get_availability(self.db, "curso"))
