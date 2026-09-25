from pathlib import Path

from helpers.bench import note_deep_probe


def test_parse_args_accepts_question_and_json_paths(tmp_path):
    questions = tmp_path / "questions.json"
    output = tmp_path / "results.json"

    args = note_deep_probe._parse_args(["--questions", str(questions), "--json", str(output)])

    assert args.questions == questions
    assert args.json == output


def test_default_questions_path_is_stable():
    args = note_deep_probe._parse_args([])

    assert args.questions == Path(note_deep_probe.QUESTIONS_PATH)
    assert args.json is None
