import json
import shutil
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tools.analysis.reference_analyzer import ReferenceAnalyzer, ASPECTS
from tools.analysis.video_analyzer import VideoAnalyzer
from tools.base_tool import ToolResult


def test_short_scene_sampling_stays_inside():
    times = VideoAnalyzer()._compute_keyframe_timestamps([
        {'start_seconds': 0, 'end_seconds': .02},
        {'start_seconds': .02, 'end_seconds': .04}], 6, 'deep')
    assert len(times) >= 4
    assert all(0 <= t < .04 for t in times)
    assert any(0 <= t < .02 for t in times)
    assert any(.02 <= t < .04 for t in times)


@pytest.fixture
def video(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=160x120:r=20:d=1', '-y', str(source)], check=True)
    return tmp_path, source


def test_prepare_is_incomplete_and_real_frames(video):
    root, source = video
    result = ReferenceAnalyzer().execute({'project_dir': str(root), 'source': str(source), 'operation': 'prepare', 'max_frames': 4})
    assert result.success
    brief = result.data
    assert brief['visual_analysis']['status'] == 'incomplete'
    assert 2 <= len(brief['sampled_frames']) <= 4
    assert all((root / f['path']).is_file() for f in brief['sampled_frames'])


def test_backend_receives_images_and_parses_json(video):
    root, source = video
    class Backend:
        def analyze_reference_frames(self, *, image_paths, timestamps, query, model):
            assert len(image_paths) >= 2
            assert all(p.is_file() and p.read_bytes().startswith(b'\x89PNG') for p in image_paths)
            assert model == 'local-test'
            return ToolResult(success=True, data={'response': json.dumps({
                'aspects': {k: {'status': 'absent', 'description': 'Not visible in sampled sequence', 'confidence': .9, 'evidence_timestamps': timestamps} for k in ASPECTS}, 'events': []})})
    tool = ReferenceAnalyzer()
    tool._backend = lambda name: Backend()
    result = tool.execute({'project_dir': str(root), 'source': str(source), 'model': 'local-test'})
    assert result.success
    assert result.data['visual_analysis']['status'] == 'ready'


@pytest.mark.parametrize('response', ['not json', '{}', '{"aspects":{},"events":[]}'])
def test_bad_backend_stays_incomplete(video, response):
    root, source = video
    class Backend:
        def analyze_reference_frames(self, **kwargs):
            return ToolResult(success=True, data={'response': response})
    tool = ReferenceAnalyzer()
    tool._backend = lambda name: Backend()
    result = tool.execute({'project_dir': str(root), 'source': str(source), 'model': 'local-test'})
    assert result.data['visual_analysis']['status'] == 'incomplete'
    assert result.data['visual_analysis']['issues']


def test_source_escape_rejected(video, tmp_path_factory):
    root, source = video
    other = tmp_path_factory.mktemp('other')
    assert not ReferenceAnalyzer().execute({'project_dir': str(other), 'source': str(source)}).success


def test_zero_call_budget_never_invokes_backend(video):
    root, source = video
    tool = ReferenceAnalyzer()
    tool._backend = lambda name: pytest.fail('backend must not load')
    result = tool.execute({'project_dir': str(root), 'source': str(source), 'max_calls': 0, 'model': 'local-test'})
    assert result.data['visual_analysis']['status'] == 'incomplete'


def test_registry_discovers_tool():
    from tools.tool_registry import ToolRegistry
    registry = ToolRegistry()
    assert 'reference_analyzer' in registry.discover('tools.analysis')


def test_actual_frame_timestamps_match_stream(video):
    root, source = video
    result = ReferenceAnalyzer().execute({'project_dir': str(root), 'source': str(source), 'operation': 'prepare', 'frames_per_shot': 7})
    pts = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(source)], capture_output=True, text=True, check=True).stdout)
    actual = [float(f['best_effort_timestamp_time']) for f in pts['frames']]
    assert all(any(abs(f['timestamp_seconds'] - t) < 1e-6 for t in actual) for f in result.data['sampled_frames'])


def test_enrichment_cannot_invent_evidence(video):
    root, source = video
    tool = ReferenceAnalyzer()
    prepared = tool.execute({'project_dir': str(root), 'source': str(source), 'operation': 'prepare'})
    visual = prepared.data['visual_analysis']
    for shot in visual['shots']:
        shot['aspects'] = {k: {'status': 'absent', 'description': 'not visible', 'confidence': .9, 'evidence_timestamps': [.123456789]} for k in ASPECTS}
    result = tool.execute({'project_dir': str(root), 'operation': 'enrich', 'visual_analysis': visual})
    assert result.data['visual_analysis']['status'] == 'incomplete'


def test_no_local_model_never_downloads(video):
    from tools.analysis.video_understand import VideoUnderstand
    root, source = video
    result = VideoUnderstand().analyze_reference_frames(image_paths=[source, source], timestamps=[0., .5], query='describe', model=str(root / 'missing'))
    assert not result.success
    assert 'not installed' in result.error


def test_processor_receives_real_labelled_sequence_pixels(tmp_path, monkeypatch):
    from PIL import Image
    from tools.analysis.video_understand import VideoUnderstand
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{}')
    paths = [tmp_path / 'a.png', tmp_path / 'b.png']
    for path, color in zip(paths, ('red', 'blue')):
        Image.new('RGB', (16, 16), color).save(path)
    seen = {}
    class Processor:
        def __call__(self, **kwargs):
            seen.update(kwargs)
            return {}
        def batch_decode(self, *args, **kwargs):
            return ['{"aspects":{},"events":[]}']
    class Loader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            assert kwargs == {'local_files_only': True, 'trust_remote_code': False}
            return Processor()
    class ModelLoader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            assert kwargs['local_files_only'] and not kwargs['trust_remote_code']
            return SimpleNamespace(config=SimpleNamespace(is_encoder_decoder=True), generate=lambda **kw: [[1]])
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(AutoProcessor=Loader, AutoModelForVision2Seq=ModelLoader))
    result = VideoUnderstand().analyze_reference_frames(image_paths=paths, timestamps=[.1, .9], query='inspect sequence', model=str(model))
    assert result.success
    assert seen['images'].getpixel((0, 28)) == (255, 0, 0)
    assert seen['images'].getpixel((512, 28)) == (0, 0, 255)
    assert 'Ordered timestamps: [0.1, 0.9]' in seen['text']


def test_event_resampling_obeys_both_budgets(video):
    root, source = video
    calls = []
    class Backend:
        def analyze_reference_frames(self, **kwargs):
            ts = kwargs['timestamps']
            calls.append(ts)
            aspects = {k: {'status': 'absent', 'description': 'not visible', 'confidence': .9, 'evidence_timestamps': ts} for k in ASPECTS}
            aspects['subject_motion']['status'] = 'observed'
            event = {'id': 'shot_000_move', 'type': 'subject_motion', 'start_seconds': min(ts), 'end_seconds': max(ts), 'description': 'movement across sequence', 'evidence_timestamps': ts, 'required_capabilities': ['video_generation']}
            return ToolResult(success=True, data={'response': json.dumps({'aspects': aspects, 'events': [event]})})
    tool = ReferenceAnalyzer()
    tool._backend = lambda name: Backend()
    result = tool.execute({'project_dir': str(root), 'source': str(source), 'model': 'local', 'max_calls': 2, 'max_frames': 7})
    assert result.success and result.data['visual_analysis']['status'] == 'ready'
    assert len(calls) == 2
    assert len(calls[1]) > len(calls[0])
    assert len(result.data['sampled_frames']) <= 7


def test_output_cannot_overwrite_script_or_final(video):
    root, source = video
    for output in ('renders/final.mp4', 'artifacts/script.json', '../other/video_analysis_brief.json'):
        result = ReferenceAnalyzer().execute({'project_dir': str(root), 'source': str(source), 'output_path': output})
        assert not result.success


def test_symlink_source_rejected(video):
    root, source = video
    linked = root / 'link.mp4'
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable')
    result = ReferenceAnalyzer().execute({'project_dir': str(root), 'source': str(linked)})
    assert not result.success


def test_enrich_rejects_changed_source_and_keeps_brief(video):
    import hashlib
    root, source = video
    tool = ReferenceAnalyzer()
    result = tool.execute({'project_dir': str(root), 'source': str(source), 'operation': 'prepare'})
    assert result.data['source']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.data['source']['size_bytes'] == source.stat().st_size
    output = root / 'artifacts/video_analysis_brief.json'
    before = output.read_bytes()
    with source.open('ab') as handle:
        handle.write(b'changed-source')
    enriched = tool.execute({'project_dir': str(root), 'operation': 'enrich', 'visual_analysis': result.data['visual_analysis']})
    assert not enriched.success
    assert 'source' in enriched.error.lower()
    assert output.read_bytes() == before


def test_failed_frame_extraction_attempts_are_globally_bounded(tmp_path, monkeypatch):
    from tools.analysis.scene_detect import SceneDetect
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'fake-media-decoding-is-mocked')
    monkeypatch.setattr(VideoAnalyzer, '_get_duration', lambda self, path: 1.)
    monkeypatch.setattr(SceneDetect, '_has_pyscenedetect', lambda self: False)
    monkeypatch.setattr(SceneDetect, '_detect_ffmpeg', lambda self, inputs: [
        {'start_seconds': i / 25, 'end_seconds': (i + 1) / 25} for i in range(25)])
    attempts = []
    def extract(self, command, **kwargs):
        attempts.append(command)
        return SimpleNamespace(stderr='')  # no decoded frame, no PNG
    monkeypatch.setattr(ReferenceAnalyzer, 'run_command', extract)
    result = ReferenceAnalyzer().execute({'project_dir': str(tmp_path), 'source': str(source),
        'operation': 'prepare', 'max_frames': 10, 'max_sample_attempts': 4})
    assert result.success
    assert len(attempts) == 4
    assert result.data['_analysis_meta']['sample_attempts'] == 4
    assert result.data['_analysis_meta']['max_sample_attempts'] == 4
    assert result.data['visual_analysis']['status'] == 'incomplete'
    assert any('sampling attempt budget' in issue.lower() for issue in result.data['visual_analysis']['issues'])


def test_duplicate_sample_requests_share_attempt_budget(tmp_path, monkeypatch):
    tool = ReferenceAnalyzer()
    attempts = []
    def extract(command, **kwargs):
        attempts.append(command)
        return SimpleNamespace(stderr='')
    monkeypatch.setattr(tool, 'run_command', extract)
    sampling = {'attempts': 0, 'limit': 3, 'requested': set(), 'exhausted': False}
    shot = {'id': 'shot_000', 'start_seconds': 0., 'end_seconds': 1.}
    records = []
    requests = [.2, .2, .20000000001, .3]
    tool._sample(tmp_path / 'source.mp4', tmp_path, shot, requests, records, 10, tmp_path / 'frames', sampling)
    tool._sample(tmp_path / 'source.mp4', tmp_path, shot, [.2, .3, .4, .5, .6], records, 10, tmp_path / 'frames', sampling)
    assert len(attempts) == sampling['attempts'] == 3
    assert sampling['exhausted']


@pytest.mark.parametrize('budget', [0, -1, 241, True, 1.5])
def test_invalid_sample_attempt_budget_rejected(video, budget):
    root, source = video
    result = ReferenceAnalyzer().execute({'project_dir': str(root), 'source': str(source), 'max_sample_attempts': budget})
    assert not result.success


@pytest.mark.parametrize('mode,binding,capabilities,expected', [
    ('temporal_sync', 'screen', [], 'ready'),
    ('subject_follow', 'subject', ['object_tracking'], 'ready'),
    ('occlusion', 'screen', ['masking'], 'ready'),
    ('occlusion', 'subject', ['masking', 'object_tracking'], 'ready'),
    ('subject_follow', 'screen', ['object_tracking'], 'incomplete'),
    ('subject_follow', 'subject', [], 'incomplete'),
    ('occlusion', 'screen', [], 'incomplete'),
    ('temporal_sync', 'subject', [], 'incomplete'),
    (None, 'subject', ['object_tracking'], 'incomplete'),
])
def test_interaction_modes_preserve_actual_required_capabilities(video, mode, binding, capabilities, expected):
    root, source = video
    class Backend:
        def analyze_reference_frames(self, **kwargs):
            ts = kwargs['timestamps']
            aspects = {k: {'status': 'absent', 'description': 'not visible', 'confidence': .9, 'evidence_timestamps': ts} for k in ASPECTS}
            aspects['interaction']['status'] = 'observed'
            anchor = {'render_mode': 'flat', 'binding': binding}
            if mode is not None:
                anchor['interaction_mode'] = mode
            event = {'id': 'shot_000_interaction', 'type': 'interaction', 'start_seconds': min(ts),
                'end_seconds': max(ts), 'description': 'Large type changes with actor movement',
                'evidence_timestamps': ts, 'required_capabilities': capabilities, 'anchor': anchor}
            return ToolResult(success=True, data={'response': json.dumps({'aspects': aspects, 'events': [event]})})
    tool = ReferenceAnalyzer()
    tool._backend = lambda name: Backend()
    result = tool.execute({'project_dir': str(root), 'source': str(source), 'model': 'local', 'max_calls': 1})
    assert result.success
    assert result.data['visual_analysis']['status'] == expected
