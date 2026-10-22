import json
import os

import pytest

import shaper

HERE = os.path.dirname(os.path.abspath(__file__))
FEA = "/app/features.fea"
HELD = json.load(open(os.path.join(HERE, "heldout.json")))


@pytest.fixture(scope="session")
def engine():
    assert os.path.isfile(FEA), "/app/features.fea is missing"
    assert os.path.getsize(FEA) < 4 * 1024 * 1024, "feature file is unreasonably large"
    font = shaper.compile_font(os.path.join(HERE, "font.ttf"), FEA)
    return shaper.Engine(font, shaper.load_marks(os.path.join(HERE, "classes.json")))


@pytest.mark.parametrize("n", range(len(HELD)))
def test_held_out_line(engine, n):
    p = HELD[n]
    want = [tuple(x) for x in p["output"]]
    got = engine.shape([shaper.parse_cp(a) for a in p["input"]])
    assert got == want, "line %d: expected %s, got %s" % (n + 1, want, got)
