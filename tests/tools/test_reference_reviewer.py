"""Reference review is grounded in candidate frames, never a supplied passed flag."""
import json
import hashlib
from pathlib import Path
import subprocess

from tools.base_tool import ToolResult
from tools.analysis.reference_reviewer import ReferenceReviewer


def fixture_project(tmp_path):
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'renders').mkdir()
    # Different sources ensure reference-frame evidence cannot stand in for output.
    for name, color in [('inputs/reference.mp4', 'red'), ('renders/candidate.mp4', 'blue')]:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=64x64:r=10:d=1', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(tmp_path / name)], check=True, timeout=15)
    reference = tmp_path / 'inputs/reference.mp4'
    brief = {'source': {'local_path': 'inputs/reference.mp4', 'duration_seconds': 1,
                       'sha256': hashlib.sha256(reference.read_bytes()).hexdigest(), 'size_bytes': reference.stat().st_size},
             'visual_analysis': {'shots': [{'id': 'shot_000', 'start_seconds': 0,
                 'end_seconds': 1, 'events': [{'id': 'text_01', 'type': 'text',
                     'description': 'Large readable text', 'start_seconds': .1,
                     'end_seconds': .8, 'evidence_timestamps': [.1, .5]}]}]}}
    plan = {'shots': [{'reference_shot_id': 'shot_000', 'scene_id': 's1',
                      'preserve_event_ids': ['text_01']}]}
    scenes = {'scenes': [{'id': 's1', 'start_seconds': 0, 'end_seconds': 1}]}
    for name, data in [('video_analysis_brief', brief), ('reference_style_plan', plan),
                       ('scene_plan', scenes)]:
        (tmp_path / 'artifacts' / f'{name}.json').write_text(json.dumps(data))
    return {'project_dir': str(tmp_path), 'output_path': 'renders/candidate.mp4', 'model': 'fake-local'}


class Backend:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def analyze_reference_frames(self, **kwargs):
        self.calls.append(kwargs)
        assert all(Path(p).is_file() for p in kwargs['image_paths'])
        assert 'REFERENCE' in kwargs['query'] and 'CANDIDATE' in kwargs['query']
        return ToolResult(success=True, data={'response': json.dumps(self.answer)})


def tool(monkeypatch, backend):
    instance = ReferenceReviewer()
    monkeypatch.setattr(instance, '_backend', lambda name: backend)
    # Plan validation is covered by its own suite; these fixtures focus on review input/output.
    monkeypatch.setattr(instance, '_validate_plan', lambda *args: None)
    return instance


def test_review_passes_actual_reference_and_candidate_images_and_writes_provenance(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    backend = Backend({'status': 'passed', 'confidence': .9, 'notes': 'Exact text and style visible'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert result.success
    assert len(backend.calls) == 1
    assert len(backend.calls[0]['image_paths']) >= 4
    report = json.loads((tmp_path / 'artifacts/reference_style_review.json').read_text())
    assert report['checks'][0]['status'] == 'passed'
    evidence = json.loads((tmp_path / report['checks'][0]['evidence_paths'][0]).read_text())
    assert Path(evidence['source_video']) == (tmp_path / 'renders/candidate.mp4').resolve()
    assert len(evidence['source_sha256']) == 64
    assert Path(evidence['frame_path']).is_file()


def test_unavailable_vision_stays_unknown(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    result = tool(monkeypatch, None).execute(inputs)
    assert not result.success
    assert result.data['checks'][0]['status'] == 'unknown'


def test_low_confidence_and_malformed_answers_cannot_pass(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    backend = Backend({'status': 'passed', 'confidence': .1, 'notes': 'Unsure'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert not result.success
    assert result.data['checks'][0]['status'] == 'unknown'
    backend.answer = {'status': 'passed'}
    result = tool(monkeypatch, backend).execute(inputs)
    assert not result.success


def test_budget_exhaustion_does_not_drop_unchecked_events(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    inputs['max_calls'] = 0
    backend = Backend({'status': 'passed', 'confidence': 1, 'notes': 'ok'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert not result.success
    assert not backend.calls
    assert len(result.data['checks']) == 1
    assert result.data['checks'][0]['status'] == 'unknown'


def test_output_outside_project_rejected_without_backend_call(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    inputs['output_path'] = '../foreign.mp4'
    backend = Backend({'status': 'passed', 'confidence': 1, 'notes': 'ok'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert not result.success
    assert not backend.calls


def test_reference_cannot_be_reviewed_as_new_candidate(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    inputs['output_path'] = 'inputs/reference.mp4'
    backend = Backend({'status': 'passed', 'confidence': 1, 'notes': 'ok'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert not result.success
    assert not backend.calls


def test_extracted_evidence_uses_actual_pts_instead_of_requested_time(tmp_path):
    fixture_project(tmp_path)
    timestamp = ReferenceReviewer._frame(tmp_path / 'renders/candidate.mp4', .13, tmp_path / 'frame.png')
    assert timestamp == .2


def test_changed_reference_cannot_reuse_old_analysis(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    (tmp_path / 'inputs/reference.mp4').write_bytes(b'changed reference')
    backend = Backend({'status': 'passed', 'confidence': 1, 'notes': 'ok'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert not result.success
    assert not backend.calls


def test_no_retained_events_still_requires_whole_shot_visual_review(tmp_path, monkeypatch):
    inputs = fixture_project(tmp_path)
    plan_path = tmp_path / 'artifacts/reference_style_plan.json'
    plan = json.loads(plan_path.read_text())
    plan['shots'][0]['preserve_event_ids'] = []
    plan_path.write_text(json.dumps(plan))
    brief_path = tmp_path / 'artifacts/video_analysis_brief.json'
    brief = json.loads(brief_path.read_text())
    brief['sampled_frames'] = [{'shot_id': 'shot_000', 'timestamp_seconds': .1},
                               {'shot_id': 'shot_000', 'timestamp_seconds': .5}]
    brief_path.write_text(json.dumps(brief))
    result = tool(monkeypatch, None).execute(inputs)
    assert not result.success
    backend = Backend({'status': 'passed', 'confidence': .95, 'notes': 'Whole shot matches'})
    result = tool(monkeypatch, backend).execute(inputs)
    assert result.success
    assert len(backend.calls) == 1
    assert result.data['checks'][0]['event_id'] == '__shot__'
