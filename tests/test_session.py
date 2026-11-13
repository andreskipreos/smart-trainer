from smart_trainer.devices import Trainer, speed_from_power
from smart_trainer.ftms import BikeData
from smart_trainer.recorder import Recorder
from smart_trainer.session import Session
from smart_trainer.workout import parse_text


class FakeTrainer(Trainer):
    name = "fake"

    def __init__(self):
        super().__init__()
        self.connected = True
        self.commands = []

    def set_erg(self, watts):
        super().set_erg(watts)
        self.commands.append(("erg", watts))

    def set_sim(self, grade):
        super().set_sim(grade)
        self.commands.append(("sim", grade))


def ride(session, trainer, seconds, start=0.0, step=0.25, power=200, cadence=90):
    t = start
    while t < start + seconds - 1e-9:
        trainer.data = BikeData(power_w=power, cadence_rpm=cadence, speed_kmh=30)
        t += step
        session.update(t)
    return t


def make(text="5m 50%\n2m 100%\n3m 60-80%", **kw):
    tr = FakeTrainer()
    s = Session(tr, ftp=200, workout=parse_text(text), **kw)
    return tr, s


def test_waits_for_pedaling_then_runs_and_sends_erg():
    tr, s = make()
    s.update(0)
    s.update(5)
    assert not s.started and s.workout_time == 0
    t = ride(s, tr, 10, start=5)
    assert s.started
    assert s.workout_time == 10 - 0.25 or abs(s.workout_time - 9.75) < 0.3
    assert tr.commands[-1] == ("erg", 100)


def test_segment_change_and_ramp_commands():
    tr, s = make()
    t = ride(s, tr, 301)
    assert s.segment_index == 1
    assert tr.commands[-1] == ("erg", 200)
    t = ride(s, tr, 120 + 90, start=t)  # into the middle of the ramp
    assert s.segment_index == 2
    assert 130 < tr.commands[-1][1] < 150


def test_auto_pause_and_resume():
    tr, s = make()
    t = ride(s, tr, 20)
    wt = s.workout_time
    t = ride(s, tr, 10, start=t, power=0, cadence=0)
    assert s.auto_paused
    assert s.workout_time < wt + 4
    ride(s, tr, 2, start=t)
    assert not s.auto_paused


def test_keys_skip_bias_pause_and_quit():
    tr, s = make()
    t = ride(s, tr, 10)
    s.handle_key("n")
    assert s.segment_index == 1
    s.handle_key("+")
    assert s.bias == 1.05
    assert tr.commands[-1] == ("erg", 210)
    s.handle_key(" ")
    assert s.paused and tr.mode == "sim"
    s.handle_key(" ")
    assert not s.paused and tr.mode == "erg"
    s.handle_key("e")  # ERG off -> slope mode, + changes grade
    s.handle_key("+")
    assert s.grade == 0.5 and tr.commands[-1] == ("sim", 0.5)
    s.handle_key("q")
    assert not s.quit
    s.handle_key("q")
    assert s.quit


def test_finishes_and_switches_to_free_ride():
    tr, s = make("1m 50%")
    ride(s, tr, 70)
    assert s.finished
    assert tr.mode == "sim"
    assert len(s.recorder.samples) >= 69


def test_free_ride_without_workout():
    tr = FakeTrainer()
    s = Session(tr, ftp=250, workout=None, start_grade=2.0)
    ride(s, tr, 5)
    assert tr.commands[-1] == ("sim", 2.0)
    s.handle_key("UP")
    assert tr.commands[-1] == ("sim", 2.5)


def test_recorder_exports(tmp_path):
    r = Recorder()
    for i in range(120):
        r.add(elapsed=i + 1, power=200 + (i % 2) * 20, cadence=90, speed_kmh=36, heart_rate=140,
              target=210)
    summ = r.summary(250)
    assert 205 < summ["avg_power"] < 215
    assert abs(summ["distance_km"] - 1.2) < 0.01
    tcx, csvp = r.save(tmp_path, "Mi rodada")
    import xml.etree.ElementTree as ET
    root = ET.parse(tcx).getroot()
    ns = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    assert len(root.findall(".//t:Trackpoint", ns)) == 120
    assert csvp.read_text().count("\n") == 121


def test_speed_from_power_reasonable():
    v = speed_from_power(200, 0) * 3.6
    assert 30 < v < 38
    assert speed_from_power(200, 8) * 3.6 < 15
