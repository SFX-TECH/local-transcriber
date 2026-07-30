"""
Unit tests for diarize: the local speaker-diarization glue.

The heavy ONNX inference is not exercised here (it needs the downloaded models);
these tests cover the pure, model-free logic: assigning diarization turns to
transcript segments and words by time-overlap, labeling, and the graceful
"unavailable" path when the models are missing.
"""

import diarize


TURNS = [
    {"start": 0.0, "end": 5.0, "speaker": 0},
    {"start": 5.0, "end": 10.0, "speaker": 1},
    {"start": 10.0, "end": 15.0, "speaker": 0},
]


def test_assign_speakers_by_overlap():
    segs = [
        {"start": 0.5, "end": 4.5, "text": "a"},
        {"start": 5.5, "end": 9.5, "text": "b"},
        {"start": 10.5, "end": 14.5, "text": "c"},
    ]
    diarize.assign_speakers(segs, TURNS)
    assert [s["speaker"] for s in segs] == [0, 1, 0]


def test_assign_speakers_straddling_boundary_picks_max_overlap():
    # 4.0 to 6.5 overlaps turn0 (1.0s) and turn1 (1.5s) -> turn1 wins
    segs = [{"start": 4.0, "end": 6.5, "text": "x"}]
    diarize.assign_speakers(segs, TURNS)
    assert segs[0]["speaker"] == 1


def test_assign_speakers_labels_words_too():
    segs = [{"start": 0.0, "end": 10.0, "text": "two turns",
             "words": [{"start": 1.0, "end": 2.0, "word": "one"},
                       {"start": 6.0, "end": 7.0, "word": "two"}]}]
    diarize.assign_speakers(segs, TURNS)
    assert segs[0]["words"][0]["speaker"] == 0
    assert segs[0]["words"][1]["speaker"] == 1


def test_assign_no_turns_is_noop():
    segs = [{"start": 0.0, "end": 1.0, "text": "a"}]
    diarize.assign_speakers(segs, [])
    assert "speaker" not in segs[0]


def test_best_speaker_nearest_when_no_overlap():
    # a segment entirely in a gap after all turns -> nearest by midpoint (turn2, spk 0)
    assert diarize._best_speaker(20.0, 21.0, TURNS) == 0


def test_label_for_is_one_indexed():
    assert diarize.label_for(0) == "Speaker 1"
    assert diarize.label_for(4) == "Speaker 5"


def test_speaker_count():
    assert diarize.speaker_count(TURNS) == 2
    assert diarize.speaker_count([]) == 0


def test_available_false_when_models_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DIARIZATION_MODELS_DIR", str(tmp_path))  # empty dir
    assert diarize.available() is False


def test_model_paths_follow_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIARIZATION_MODELS_DIR", str(tmp_path))
    seg, emb = diarize.model_paths()
    assert seg == tmp_path / "segmentation.onnx"
    assert emb == tmp_path / "embedding.onnx"
