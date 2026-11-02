"""Bounded, evidence-bearing reference analysis; never downloads a vision model."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
import tempfile
import uuid

from tools.base_tool import BaseTool, ToolResult, ToolTier
from tools.analysis.video_analyzer import VideoAnalyzer

ASPECTS = ('subject', 'subject_motion', 'scene', 'spatial_framing', 'camera', 'typography', 'interaction')


def project_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Inspect the original path, before resolve erases symlink components.
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Symbolic links/junctions are not allowed')
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root):
        raise ValueError('Path must remain inside project_dir')
    return candidate


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as handle:
            name = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def source_identity(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
    return {'sha256': digest.hexdigest(), 'size_bytes': size}


class ReferenceAnalyzer(BaseTool):
    name = 'reference_analyzer'
    tier = ToolTier.ANALYZE
    capability = 'analysis'
    capabilities = ['reference_visual_analysis', 'reference_sequence_sampling']
    dependencies = ['cmd:ffmpeg', 'cmd:ffprobe']
    input_schema = {'type': 'object', 'required': ['project_dir'], 'properties': {
        'project_dir': {'type': 'string'}, 'source': {'type': 'string'},
        'operation': {'enum': ['analyze', 'prepare', 'enrich'], 'default': 'analyze'},
        'model': {'type': 'string', 'description': 'Explicit existing local vision model directory'},
        'backend_tool': {'type': 'string', 'default': 'video_understand'},
        'brief_path': {'type': 'string'}, 'visual_analysis': {'type': 'object'},
        'max_frames': {'type': 'integer', 'minimum': 1, 'maximum': 120},
        'max_sample_attempts': {'type': 'integer', 'minimum': 1, 'maximum': 240,
            'description': 'Shared FFmpeg extraction attempt budget; default max_frames * 2'},
        'frames_per_shot': {'type': 'integer', 'minimum': 2, 'maximum': 12},
        'max_calls': {'type': 'integer', 'minimum': 0, 'maximum': 50},
        'output_path': {'type': 'string'}}}
    side_effects = ['writes bounded reference frames and atomic analysis artifact under project_dir/artifacts']

    def _backend(self, name):
        from tools.tool_registry import registry
        tool = registry.get(name)
        if tool is None:
            registry.discover('tools.analysis')
            tool = registry.get(name)
        return tool

    @staticmethod
    def _check(brief):
        visual = brief['visual_analysis']
        if visual.get('version') != '1.0' or not visual.get('shots'):
            raise ValueError('Missing visual analysis shots/version')
        expected = brief['structure_analysis']['scenes']
        if len(expected) != len(visual['shots']):
            raise ValueError('All source shots must be covered')
        seen_events = set()
        for index, shot in enumerate(visual['shots']):
            scene = expected[index]
            if shot.get('id') != f'shot_{index:03d}' or shot.get('start_seconds') != scene['start_time'] or shot.get('end_seconds') != scene['end_time']:
                raise ValueError('Shot identity/bounds mismatch')
            sampled = {f['timestamp_seconds'] for f in brief['sampled_frames'] if f['shot_id'] == shot['id']}
            if any(not math.isfinite(t) or not shot['start_seconds'] <= t < shot['end_seconds'] for t in sampled):
                raise ValueError('Sample timestamp outside shot')
            if set(shot.get('aspects', {})) != set(ASPECTS):
                raise ValueError('Seven aspects are required')
            for aspect in shot['aspects'].values():
                evidence = aspect.get('evidence_timestamps', [])
                if aspect.get('status') not in ('observed', 'absent') or not isinstance(aspect.get('description'), str) or not aspect['description'].strip():
                    raise ValueError('Unknown or missing aspect evidence')
                confidence = aspect.get('confidence')
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError('Invalid confidence')
                if not evidence or not set(evidence) <= sampled:
                    raise ValueError('Aspect evidence must use actual sampled timestamps')
            if not isinstance(shot.get('events'), list):
                raise ValueError('Events must be an array')
            for event in shot['events']:
                ev = event.get('evidence_timestamps', [])
                if event.get('type') not in ('subject_motion', 'text', 'interaction') or not event.get('id') or event['id'] in seen_events:
                    raise ValueError('Invalid or duplicate event')
                seen_events.add(event['id'])
                if not ev or not set(ev) <= sampled or not shot['start_seconds'] <= event.get('start_seconds', -1) <= event.get('end_seconds', -1) <= shot['end_seconds']:
                    raise ValueError('Event time/evidence mismatch')
                if any(not event['start_seconds'] <= t <= event['end_seconds'] for t in ev):
                    raise ValueError('Event evidence outside event')
                if event['type'] in ('interaction', 'subject_motion') and len(set(ev)) < 2:
                    raise ValueError('Temporal event requires multiple sampled frames')
                if not isinstance(event.get('description'), str) or not event['description'].strip() or not isinstance(event.get('required_capabilities'), list) or any(not isinstance(c, str) or not c for c in event['required_capabilities']):
                    raise ValueError('Missing event description/capabilities')
                if event['type'] in ('text', 'interaction'):
                    anchor = event.get('anchor', {})
                    if anchor.get('render_mode') not in ('flat', '3d') or anchor.get('binding') not in ('screen', 'subject'):
                        raise ValueError('Text/interaction requires typed anchor')
                    if event['type'] == 'interaction':
                        mode = anchor.get('interaction_mode')
                        if mode not in ('temporal_sync', 'subject_follow', 'occlusion'):
                            raise ValueError('Interaction requires explicit interaction_mode')
                        if mode == 'subject_follow' and anchor['binding'] != 'subject':
                            raise ValueError('Subject-follow interaction requires subject binding')
                        if mode == 'occlusion' and 'masking' not in event['required_capabilities']:
                            raise ValueError('Occlusion interaction requires masking')
                    if anchor['binding'] == 'subject' and 'object_tracking' not in event['required_capabilities']:
                        raise ValueError('Subject-bound event requires object_tracking')
                if event.get('anchor', {}).get('render_mode') == '3d' and '3d_rendering' not in event['required_capabilities']:
                    raise ValueError('3d anchor requires 3d_rendering')
            for aspect, event_type in (('typography', 'text'), ('interaction', 'interaction'), ('subject_motion', 'subject_motion')):
                if shot['aspects'][aspect]['status'] == 'observed' and not any(e['type'] == event_type for e in shot['events']):
                    raise ValueError(f'Observed {aspect} requires corresponding event')

    def _sample(self, source, root, shot, timestamps, records, limit, frame_dir, sampling):
        for timestamp in sorted(set(timestamps)):
            if len(records) >= limit:
                break
            # Requested times and successful frame times are different when the
            # decoder rounds to a real frame PTS. Remember even failed requests
            # across initial/near-event sampling, with nanosecond deduplication.
            request = (shot['id'], round(timestamp, 9))
            if request in sampling['requested']:
                continue
            if any(r['shot_id'] == shot['id'] and r['timestamp_seconds'] == timestamp for r in records):
                continue
            if sampling['attempts'] >= sampling['limit']:
                sampling['exhausted'] = True
                return
            sampling['requested'].add(request)
            sampling['attempts'] += 1
            path = project_path(root, frame_dir / f'{shot["id"]}_{len(records):04d}.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            staging = path.with_name(path.stem + '.tmp.png')
            try:
                result = self.run_command(['ffmpeg', '-v', 'info', '-copyts', '-ss', str(timestamp), '-i', str(source), '-vf', 'showinfo', '-frames:v', '1', '-fps_mode', 'passthrough', '-y', str(staging)], timeout=60)
                match = re.search(r'\bpts_time:([\d.eE+-]+)', result.stderr)
                actual = float(match.group(1)) if match else None
                if staging.is_file() and staging.stat().st_size and actual is not None and shot['start_seconds'] <= actual < shot['end_seconds'] and not any(r['shot_id'] == shot['id'] and abs(r['timestamp_seconds'] - actual) < 1e-7 for r in records):
                    os.replace(staging, path)
                    records.append({'shot_id': shot['id'], 'timestamp_seconds': actual, 'path': str(path.relative_to(root))})
            finally:
                staging.unlink(missing_ok=True)

    def execute(self, inputs):
        try:
            root_raw = Path(inputs['project_dir']).absolute()
            root = root_raw.resolve()
            project_path(root, root_raw)
            if not root.is_dir():
                raise ValueError('project_dir must exist')
            output = project_path(root, inputs.get('output_path', 'artifacts/video_analysis_brief.json'))
            if output.name != 'video_analysis_brief.json' or output.parent != root / 'artifacts':
                raise ValueError('Analysis output must be JSON directly under project artifacts')
            operation = inputs.get('operation', 'analyze')
            if operation not in ('prepare', 'analyze', 'enrich'):
                raise ValueError('Unknown operation')
            max_frames = inputs.get('max_frames', 24)
            max_sample_attempts = inputs.get('max_sample_attempts', max_frames * 2)
            count = inputs.get('frames_per_shot', 3)
            max_calls = inputs.get('max_calls', 8)
            for value, low, high in ((max_frames, 1, 120), (max_sample_attempts, 1, 240), (count, 2, 12), (max_calls, 0, 50)):
                if type(value) is not int or not low <= value <= high:
                    raise ValueError('Invalid sampling/call budget')
            sampling = {'attempts': 0, 'limit': max_sample_attempts,
                'requested': set(), 'exhausted': False}
            if operation == 'enrich':
                path = project_path(root, inputs.get('brief_path', 'artifacts/video_analysis_brief.json'))
                brief = json.loads(path.read_text(encoding='utf-8'))
                source = project_path(root, brief['source']['local_path'])
                identity = source_identity(source)
                if any(brief['source'].get(key) != value for key, value in identity.items()):
                    raise ValueError('Reference source identity changed or missing; prepare again before enrichment')
                for record in brief['sampled_frames']:
                    if not project_path(root, record['path']).is_file():
                        raise ValueError('Sample evidence file missing')
                brief['visual_analysis'] = copy.deepcopy(inputs['visual_analysis'])
                brief['visual_analysis']['status'] = 'incomplete'
                brief['visual_analysis']['issues'] = []
            else:
                source = project_path(root, inputs['source'])
                if not source.is_file():
                    raise ValueError('Source file missing')
                identity = source_identity(source)
                duration = VideoAnalyzer()._get_duration(source)
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError('Invalid video duration')
                # SceneDetect remains optional; FFmpeg whole-clip fallback is explicit.
                scenes = []
                detection_issue = None
                try:
                    from tools.analysis.scene_detect import SceneDetect
                    detector = SceneDetect()
                    params = {'input_path': str(source), 'min_scene_length_seconds': .1}
                    # Reuse detector without its non-atomic sidecar persistence.
                    scenes = detector._detect_pyscenedetect(params) if detector._has_pyscenedetect() else detector._detect_ffmpeg(params)
                except Exception as exc:
                    detection_issue = f'Scene detection unavailable: {type(exc).__name__}'
                if not scenes:
                    scenes = [{'start_seconds': 0., 'end_seconds': duration}]
                shots = []
                for i, scene in enumerate(scenes):
                    start = max(0., float(scene.get('start_seconds', 0)))
                    end = min(duration, float(scene.get('end_seconds', duration)))
                    if not 0 <= start < end <= duration:
                        raise ValueError('Invalid scene boundaries')
                    shots.append({'id': f'shot_{i:03d}', 'start_seconds': start, 'end_seconds': end,
                        'aspects': {k: {'status': 'unknown', 'description': '', 'confidence': 0., 'evidence_timestamps': []} for k in ASPECTS}, 'events': []})
                brief = {'version': '1.0', 'source': {'type': 'local_file', 'local_path': str(source.relative_to(root)), 'duration_seconds': duration, **identity},
                    'content_analysis': {'summary': '', 'topics': [], 'target_audience': 'general'},
                    'structure_analysis': {'total_scenes': len(shots), 'scenes': [{'scene_index': i, 'start_time': s['start_seconds'], 'end_time': s['end_seconds'], 'description': ''} for i, s in enumerate(shots)], 'pacing_profile': {}},
                    'sampled_frames': [], 'visual_analysis': {'version': '1.0', 'status': 'incomplete', 'shots': shots, 'issues': []},
                    '_analysis_meta': {'visual_calls': 0, 'max_frames': max_frames, 'max_calls': max_calls,
                        'sample_attempts': 0, 'max_sample_attempts': max_sample_attempts,
                        'scene_detection_issue': detection_issue}}
                frame_dir = Path('artifacts/reference_frames') / uuid.uuid4().hex
                # First allocate up to two frames to every shot, then denser coverage.
                for fractions in ((.1, .9), tuple((i + 1) / (count - 1) for i in range(count - 2))):
                    for shot in shots:
                        span = shot['end_seconds'] - shot['start_seconds']
                        self._sample(source, root, shot, [shot['start_seconds'] + span * f for f in fractions], brief['sampled_frames'], max_frames, frame_dir, sampling)
                issues = brief['visual_analysis']['issues']
                if detection_issue:
                    issues.append(detection_issue)
                if operation == 'prepare':
                    issues.append('Prepared frames only; native agent vision enrichment is required')
                elif not inputs.get('model'):
                    issues.append('Explicit existing local vision model is required; no model downloaded')
                elif not max_calls:
                    issues.append('Visual call budget exhausted')
                else:
                    backend = self._backend(inputs.get('backend_tool', 'video_understand'))
                    if backend is None or not callable(getattr(backend, 'analyze_reference_frames', None)):
                        issues.append('Selected backend lacks structured temporal visual capability')
                    elif getattr(backend, 'runtime', 'local') not in ('local', 'local_gpu'):
                        issues.append('Automatic reference analysis only allows local offline vision backends')
                    else:
                        for shot in shots:
                            records = [r for r in brief['sampled_frames'] if r['shot_id'] == shot['id']]
                            if len(records) < 2 or brief['_analysis_meta']['visual_calls'] >= max_calls:
                                issues.append(f'{shot["id"]}: insufficient sequence evidence or call budget')
                                continue
                            def infer():
                                seq = sorted([r for r in brief['sampled_frames'] if r['shot_id'] == shot['id']], key=lambda r: r['timestamp_seconds'])
                                brief['_analysis_meta']['visual_calls'] += 1
                                query = ('Return ONLY JSON {"aspects":{...},"events":[]}. Seven aspect keys: ' + ','.join(ASPECTS) + '. Each aspect: status observed/absent/unknown, description, confidence 0..1, evidence_timestamps. Events: id (prefix '+shot['id']+'),type subject_motion/text/interaction,start_seconds,end_seconds,description,evidence_timestamps,required_capabilities. Observed typography, subject_motion, interaction require corresponding text, subject_motion, interaction events. Text/interaction need anchor {render_mode:flat or 3d,binding:screen or subject}. Interaction additionally requires interaction_mode: temporal_sync (screen text timed with action; tracking not required), subject_follow (must bind subject and require object_tracking), or occlusion (must require masking; screen or subject binding allowed). Choose from actual evidence; do not assume every interaction follows a hand or subject. Every subject-bound event requires object_tracking; render_mode=3d requires 3d_rendering. Do not omit 3D treatment visible in reference. Use actual timestamps in supplied ordered images. Unknown when uncertain. Temporal events need >=2 timestamps. Never infer AI origin from motion. Shot bounds: ' + str((shot['start_seconds'], shot['end_seconds'])))
                                result = backend.analyze_reference_frames(image_paths=[project_path(root, r['path']) for r in seq], timestamps=[r['timestamp_seconds'] for r in seq], query=query, model=inputs['model'])
                                if not result.success:
                                    raise ValueError(result.error or 'Visual backend failed')
                                response = result.data.get('response')
                                parsed = json.loads(response) if isinstance(response, str) else response
                                if not isinstance(parsed, dict) or set(parsed) != {'aspects', 'events'}:
                                    raise ValueError('Malformed structured visual response')
                                shot.update(parsed)
                            try:
                                infer()
                                # Revisit both sides of candidate events if frame/call budgets permit.
                                nearby = []
                                for event in shot['events']:
                                    for t in (event['start_seconds'], event['end_seconds']):
                                        nearby.extend(max(shot['start_seconds'], min(math.nextafter(shot['end_seconds'], shot['start_seconds']), float(t) + delta)) for delta in (-.05, .05))
                                before = len(brief['sampled_frames'])
                                if nearby and brief['_analysis_meta']['visual_calls'] < max_calls:
                                    self._sample(source, root, shot, nearby, brief['sampled_frames'], max_frames, frame_dir, sampling)
                                    if len(brief['sampled_frames']) > before:
                                        infer()
                            except Exception as exc:
                                issues.append(f'{shot["id"]}: {type(exc).__name__}: {exc}')
                brief['_analysis_meta']['sample_attempts'] = sampling['attempts']
                if sampling['exhausted']:
                    issues.append('Sampling attempt budget exhausted; sequence/event coverage is incomplete')
            visual = brief['visual_analysis']
            try:
                self._check(brief)
                if not visual['issues']:
                    visual['status'] = 'ready'
                    # The shared validator is also used at checkpoint/compose.
                    try:
                        from lib.reference_style import validate_visual_analysis
                    except ImportError:
                        validate_visual_analysis = None
                    if validate_visual_analysis is not None:
                        validate_visual_analysis(brief)
            except (ValueError, KeyError, TypeError) as exc:
                visual['status'] = 'incomplete'
                visual['issues'].append(str(exc))
            if source_identity(source) != identity:
                raise ValueError('Reference source changed during analysis; previous brief preserved')
            atomic_json(output, brief)
            return ToolResult(success=True, data=brief, artifacts=[str(output)])
        except Exception as exc:
            return ToolResult(success=False, error=f'{type(exc).__name__}: {exc}')
