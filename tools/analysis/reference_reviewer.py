"""Compare staged output to reference events with actual, timestamped visual input.

This tool records model observations, not a guarantee of perceptual equivalence.
Promotion additionally requires the deterministic reference contract validator.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from tools.base_tool import BaseTool, ToolResult, ToolTier


def _inside(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Reference review paths cannot contain links or junctions')
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('Reference review path must remain within this project')
    return path


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class ReferenceReviewer(BaseTool):
    name = 'reference_reviewer'
    tier = ToolTier.ANALYZE
    capability = 'analysis'
    capabilities = ['reference_event_review']
    dependencies = ['cmd:ffmpeg', 'cmd:ffprobe']
    agent_skills = ['video-understand']
    side_effects = ['writes candidate-specific frame evidence and reference_style_review under project_dir/artifacts']
    input_schema = {'type': 'object', 'required': ['project_dir', 'output_path'], 'properties': {
        'project_dir': {'type': 'string'}, 'output_path': {'type': 'string'},
        'backend_tool': {'type': 'string', 'default': 'video_understand'},
        'model': {'type': 'string', 'description': 'Explicit installed local visual model directory'},
        'max_calls': {'type': 'integer', 'minimum': 0, 'maximum': 50, 'default': 8},
        'min_confidence': {'type': 'number', 'minimum': 0, 'maximum': 1, 'default': .8}}}

    def _backend(self, name):
        from tools.tool_registry import registry
        tool = registry.get(name)
        if tool is None:
            registry.discover('tools.analysis')
            tool = registry.get(name)
        return tool

    @staticmethod
    def _validate_plan(root, brief, plan, scenes):
        from lib.reference_style import validate_reference_style_plan
        validate_reference_style_plan(brief, plan, scenes, project_dir=root)

    @staticmethod
    def _duration(path):
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                 '-of', 'json', str(path)], check=True, capture_output=True,
                                text=True, timeout=15)
        duration = float(json.loads(result.stdout)['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Invalid video duration')
        return duration

    @staticmethod
    def _frame(video, timestamp, path):
        result = subprocess.run(['ffmpeg', '-v', 'info', '-copyts', '-y', '-ss', str(timestamp),
                                 '-i', str(video), '-vf', 'showinfo', '-frames:v', '1',
                                 '-fps_mode', 'passthrough', str(path)],
                                check=True, capture_output=True, text=True, timeout=15)
        if not path.is_file():
            raise ValueError('No frame at requested timestamp')
        match = re.search(r'\bpts_time:([\d.eE+-]+)', result.stderr)
        if match is None:
            raise ValueError('Actual evidence timestamp unavailable')
        return float(match.group(1))

    def execute(self, inputs):
        try:
            return self._execute(inputs)
        except Exception as exc:
            # Do not disclose provider response bodies or local credentials in errors.
            return ToolResult(success=False, error=f'Reference review could not complete: {type(exc).__name__}')

    def _execute(self, inputs):
        root = Path(inputs['project_dir']).resolve(strict=True)
        artifacts = _inside(root, 'artifacts')
        candidate = _inside(root, inputs['output_path'])
        if not candidate.is_file() or not candidate.is_relative_to(_inside(root, 'renders')):
            raise ValueError('Review requires a staged project render')
        def read(name):
            return json.loads(_inside(root, artifacts / f'{name}.json').read_text(encoding='utf-8'))
        brief, plan, scene_plan = read('video_analysis_brief'), read('reference_style_plan'), read('scene_plan')
        self._validate_plan(root, brief, plan, scene_plan)
        source = _inside(root, brief['source']['local_path'])
        source_hash = _hash(source)
        if brief['source'].get('sha256') != source_hash or brief['source'].get('size_bytes') != source.stat().st_size:
            raise ValueError('Reference source changed; re-analysis required')
        if source == candidate or _hash(source) == _hash(candidate):
            raise ValueError('Reference input cannot stand in for a new candidate')
        source_duration, target_duration = self._duration(source), self._duration(candidate)
        digest = _hash(candidate)
        max_calls, threshold = inputs.get('max_calls', 8), inputs.get('min_confidence', .8)
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or not 0 <= max_calls <= 50:
            raise ValueError('Invalid review call budget')
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise ValueError('Invalid confidence threshold')
        backend = self._backend(inputs.get('backend_tool', 'video_understand'))
        visual_shots = {s['id']: s for s in brief['visual_analysis']['shots']}
        scenes = {s['id']: s for s in scene_plan['scenes']}
        evidence_dir = _inside(root, artifacts / 'reference_review_frames' / uuid.uuid4().hex)
        evidence_dir.mkdir(parents=True)
        checks, calls = [], 0
        for mapped in plan['shots']:
            shot, target = visual_shots[mapped['reference_shot_id']], scenes[mapped['scene_id']]
            events = {e['id']: e for e in shot['events']}
            review_ids = mapped['preserve_event_ids'] or ['__shot__']
            if not mapped['preserve_event_ids']:
                events['__shot__'] = {
                    'description': 'Whole shot subject, scene, composition, camera and visual treatment: '
                                   + json.dumps(shot.get('aspects', {}), ensure_ascii=False),
                    'evidence_timestamps': [frame['timestamp_seconds'] for frame in brief.get('sampled_frames', [])
                                            if frame.get('shot_id') == shot['id']]
                }
            for event_id in review_ids:
                event = events[event_id]
                check = {'reference_shot_id': shot['id'], 'event_id': event_id, 'status': 'unknown',
                         'evidence_paths': [], 'notes': 'Visual review unavailable or call budget exhausted'}
                checks.append(check)
                if calls >= max_calls or not inputs.get('model') or not callable(getattr(backend, 'analyze_reference_frames', None)):
                    continue
                # Review the whole target shot: do not assume generated gesture timing
                # matches reference timing. This sampling is QA, never an anchor track.
                start, end = target['start_seconds'], target['end_seconds']
                if not 0 <= start < end <= target_duration + .001:
                    check['notes'] = 'Target shot is outside candidate duration'
                    continue
                reference_times = sorted(set(event['evidence_timestamps']))[:4]
                if not reference_times or any(not 0 <= t < source_duration for t in reference_times):
                    check['notes'] = 'Reference event lacks usable temporal evidence'
                    continue
                target_times = [start + (end - start) * fraction for fraction in (.1, .35, .65, .9)]
                image_paths, labels, sidecars = [], [], []
                for group, video, times in [('REFERENCE', source, reference_times), ('CANDIDATE', candidate, target_times)]:
                    for timestamp in times:
                        stem = uuid.uuid4().hex
                        frame = evidence_dir / f'{stem}.png'
                        timestamp = self._frame(video, timestamp, frame)
                        if not 0 <= timestamp < (source_duration if group == 'REFERENCE' else target_duration):
                            raise ValueError('Evidence timestamp outside media')
                        image_paths.append(frame)
                        labels.append(timestamp)
                        if group == 'CANDIDATE':
                            sidecar = evidence_dir / f'{stem}.json'
                            _write(sidecar, {'source_video': str(candidate), 'source_sha256': digest,
                                             'timestamp_seconds': timestamp, 'frame_path': str(frame)})
                            sidecars.append(sidecar.relative_to(root).as_posix())
                query = (
                    f'Compare event {event_id}: {event["description"]}. First {len(reference_times)} images '
                    f'are REFERENCE evidence, remaining {len(target_times)} images are CANDIDATE output. '
                    'Inspect exact typography, visible text depth, gesture order, motion and occlusion. '
                    'Do not infer unseen motion between samples; report unknown when temporal coverage is insufficient. '
                    'Return only JSON {"status":"passed|failed|unknown","confidence":0.0,"notes":"visible evidence"}. '
                    'passed requires all visible requirements; a subtitle is not 3D interactive typography.'
                )
                calls += 1
                try:
                    answer = backend.analyze_reference_frames(image_paths=image_paths, timestamps=labels,
                                                              query=query, model=inputs['model'])
                    if not answer.success:
                        raise ValueError('Visual backend failed')
                    response = answer.data['response'].strip()
                    if response.startswith('```'):
                        response = response.split('\n', 1)[1].rsplit('```', 1)[0]
                    verdict = json.loads(response)
                    confidence = verdict.get('confidence')
                    status = verdict.get('status')
                    notes = verdict.get('notes')
                    if (status not in {'passed', 'failed', 'unknown'} or not isinstance(notes, str) or not notes.strip()
                            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                        raise ValueError('Invalid visual verdict')
                    check.update(status=status if confidence >= threshold else 'unknown',
                                 evidence_paths=sidecars, notes=notes)
                except Exception:
                    check['notes'] = 'Visual response failed or did not provide a valid grounded verdict'
        if _hash(candidate) != digest or _hash(source) != source_hash:
            raise ValueError('Candidate or reference changed during review')
        report = {'version': '1.0', 'output_path': str(candidate), 'checks': checks}
        _write(artifacts / 'reference_style_review.json', report)
        passed = bool(checks) and all(c['status'] == 'passed' for c in checks)
        return ToolResult(success=passed, data=report,
                          error=None if passed else 'Reference event review is incomplete or found differences',
                          artifacts=[str(artifacts / 'reference_style_review.json')])
