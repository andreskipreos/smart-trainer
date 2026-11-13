from pathlib import Path

import pytest

from smart_trainer.workout import load_workout, parse_duration, parse_erg_mrc, parse_text, parse_zwo

BUNDLED = Path(__file__).parent.parent / "smart_trainer" / "workouts"

ZWO = """<workout_file><name>T</name><workout>
<Warmup Duration="300" PowerLow="0.5" PowerHigh="0.7"/>
<IntervalsT Repeat="2" OnDuration="60" OffDuration="30" OnPower="1.2" OffPower="0.5" Cadence="100">
  <textevent timeoffset="0" message="go"/>
  <textevent timeoffset="95" message="again"/>
</IntervalsT>
<FreeRide Duration="120"/>
<SteadyState Duration="60" Power="0.8"/>
</workout></workout_file>"""


def test_zwo_parsing():
    w = parse_zwo(ZWO)
    assert w.name == "T"
    assert [s.kind for s in w.segments] == ["warmup", "on", "off", "on", "off", "free", "steady"]
    assert w.total_duration == 300 + 2 * 90 + 120 + 60
    assert w.segments[1].cadence == 100
    assert w.segments[1].messages == [(0, "go")]
    assert w.segments[3].messages == [(5, "again")]
    assert w.watts_at(150, 200) == pytest.approx(120)  # middle of the warmup ramp
    assert w.watts_at(310, 200) == pytest.approx(240)
    assert w.watts_at(300 + 180 + 10, 200) is None  # free ride
    assert w.watts_at(10_000, 200) is None


def test_segment_index_lookup():
    w = parse_zwo(ZWO)
    assert w.segment_index_at(0) == 0
    assert w.segment_index_at(299.9) == 0
    assert w.segment_index_at(300) == 1
    assert w.segment_index_at(w.total_duration - 0.1) == len(w.segments) - 1
    assert w.segment_index_at(w.total_duration) is None


def test_erg_absolute_watts():
    txt = "[COURSE HEADER]\nMINUTES WATTS\n[END COURSE HEADER]\n[COURSE DATA]\n0 100\n10 200\n10 250\n15 250\n[END COURSE DATA]\n"
    w = parse_erg_mrc(txt, "x.erg")
    assert len(w.segments) == 2
    assert w.segments[0].absolute and w.segments[0].is_ramp
    assert w.watts_at(300, ftp=999) == pytest.approx(150)
    assert w.watts_at(700, ftp=999) == pytest.approx(250)


def test_mrc_percent():
    txt = "[COURSE HEADER]\nMINUTES PERCENT\n[END COURSE HEADER]\n[COURSE DATA]\n0 50\n5 50\n5 100\n10 100\n[END COURSE DATA]\n"
    w = parse_erg_mrc(txt, "x.mrc")
    assert w.watts_at(400, 300) == pytest.approx(300)


@pytest.mark.parametrize("tok,secs", [("10m", 600), ("30s", 30), ("1m30s", 90), ("1h", 3600),
                                      ("1:30", 90), ("5", 300), ("2min", 120)])
def test_parse_duration(tok, secs):
    assert parse_duration(tok) == secs


def test_text_format():
    w = parse_text("""
name: Mi workout
# comentario
10m 50-75% "calentando"
3x 1m 120% @100, 1m 50%
5m 200w
5m free
""")
    assert w.name == "Mi workout"
    assert len(w.segments) == 1 + 6 + 1 + 1
    assert w.segments[0].is_ramp and w.segments[0].messages == [(0.0, "calentando")]
    assert w.segments[1].kind == "on" and w.segments[1].cadence == 100
    assert w.segments[2].kind == "off"
    assert w.segments[7].absolute and w.watts_at(600 + 360 + 10, 300) == 200
    assert w.segments[8].is_free


def test_text_format_errors_have_line_numbers():
    with pytest.raises(ValueError, match="línea 2"):
        parse_text("10m 50%\n10m banana")


@pytest.mark.parametrize("path", sorted(BUNDLED.iterdir()))
def test_bundled_workouts_load(path):
    w = load_workout(path)
    assert w.total_duration > 600
    st = w.stats(250)
    assert 0.4 < st["if"] < 1.2
