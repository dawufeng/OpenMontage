"""Deterministic reference contracts; creative and visual decisions remain with agents.

Object tracking has a replay-verified assets adapter. Capability names never
prove execution; masking, 3D and trajectory-driven composition fail closed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import time
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from schemas.artifacts import validate_artifact

ASPECTS = ('subject', 'subject_motion', 'scene', 'spatial_framing', 'camera', 'typography', 'interaction')
_MISSING = object()
PLANNING_STAGES = {'proposal', 'idea', 'script', 'scene_plan', 'assets', 'edit', 'compose', 'publish'}


def _require(condition, message):
    if not condition:
        raise ValueError('Reference contract: ' + message)


def _schema(name, value):
    try:
        validate_artifact(name, value)
    except Exception as exc:
        raise ValueError(f'Reference contract: invalid {name}: {exc}') from exc


def project_path(project_dir, value, *, exists=False):
    root = Path(project_dir).resolve()
    path = Path(value)
    lexical = path if path.is_absolute() else root / path
    for component in (lexical, *lexical.parents):
        _require(not component.is_symlink() and not (getattr(component, 'is_junction', lambda: False)()), 'links/junctions are not allowed in reference project paths')
        if component == root:
            break
    path = lexical.resolve()
    _require(path.is_relative_to(root) and path != root, 'path escapes project directory')
    if exists:
        _require(path.is_file(), f'evidence file missing: {path.name}')
    return path


def _time(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_visual_analysis(brief, *, require_ready=True):
    """Check seven-aspect analysis against real sample times and source shots."""
    _require(isinstance(brief, dict), 'missing video_analysis_brief')
    visual = brief.get('visual_analysis')
    _schema('visual_analysis', visual)
    if require_ready:
        _require(visual['status'] == 'ready' and not visual['issues'], 'visual analysis is incomplete')
    scenes = brief.get('structure_analysis', {}).get('scenes', [])
    _require(bool(scenes), 'no reference source shots')
    _require(len(visual['shots']) == len(scenes), 'all source shots must be covered')
    samples = brief.get('sampled_frames', [])
    seen = set()
    for index, (shot, source) in enumerate(zip(visual['shots'], scenes)):
        sid = shot['id']
        _require(sid not in seen, 'duplicate reference shot id')
        seen.add(sid)
        start, end = shot['start_seconds'], shot['end_seconds']
        _require(_time(start) and _time(end) and start < end, 'invalid shot interval')
        _require(abs(start - source.get('start_time', -1)) < .001 and abs(end - source.get('end_time', -1)) < .001, 'source shot interval mismatch')
        times = {frame.get('timestamp_seconds') for frame in samples if frame.get('shot_id') == sid and _time(frame.get('timestamp_seconds')) and start <= frame['timestamp_seconds'] <= end and frame.get('path')}
        event_ids = set()
        for aspect in shot['aspects'].values():
            if require_ready:
                _require(aspect['status'] != 'unknown', 'unknown aspect cannot support ready analysis')
            evidence = aspect['evidence_timestamps']
            _require(all(_time(t) and t in times for t in evidence), 'aspect evidence must use sampled times inside shot')
            if aspect['status'] != 'unknown':
                _require(bool(aspect['description'].strip()) and bool(evidence), 'observed/absent needs description and sampled evidence')
        event_types = {event['type'] for event in shot['events']}
        for aspect_name, event_type in [('typography', 'text'), ('interaction', 'interaction'), ('subject_motion', 'subject_motion')]:
            if shot['aspects'][aspect_name]['status'] == 'observed':
                _require(event_type in event_types, f'observed {aspect_name} requires an explicit event')
        for event in shot['events']:
            if event.get('anchor', {}).get('binding') == 'subject':
                _require('object_tracking' in event['required_capabilities'], 'subject-bound overlay requires object_tracking execution')
            if event['type'] == 'interaction':
                anchor = event['anchor']
                if anchor['interaction_mode'] == 'subject_follow':
                    _require(anchor['binding'] == 'subject', 'subject_follow requires a typed subject anchor')
                    _require('object_tracking' in event['required_capabilities'], 'subject_follow requires real object_tracking execution')
                if anchor['interaction_mode'] == 'occlusion':
                    _require('masking' in event['required_capabilities'], 'occlusion requires real masking execution')
            if event.get('anchor', {}).get('render_mode') == '3d':
                _require('3d_rendering' in event['required_capabilities'], '3d anchor requires real 3d_rendering execution')
            _require(event['id'] not in event_ids, 'duplicate event id')
            event_ids.add(event['id'])
            a, b = event['start_seconds'], event['end_seconds']
            _require(_time(a) and _time(b) and start <= a <= b <= end, 'event interval outside reference shot')
            evidence = event['evidence_timestamps']
            _require(all(_time(t) and t in times and a <= t <= b for t in evidence), 'event evidence must use sampled times within event')
            if event['type'] in {'interaction', 'subject_motion'}:
                _require(len(set(evidence)) >= 2, 'motion/interaction requires temporal evidence')


def validate_reference_style_plan(brief, plan, scene_plan=None, *, require_execution=False, project_dir=None, asset_manifest=None, edit_decisions=None, execution_stage="assets"):
    validate_visual_analysis(brief)
    _schema('reference_style_plan', plan)
    shots = {s['id']: s for s in brief['visual_analysis']['shots']}
    mappings = plan['shots']
    ids = [s['reference_shot_id'] for s in mappings]
    _require(len(ids) == len(set(ids)) and set(ids) == set(shots), 'each source shot needs exactly one plan mapping')
    scenes = {s['id'] for s in (scene_plan or {}).get('scenes', [])}
    for mapping in mappings:
        if scene_plan is not None:
            _require(mapping['scene_id'] in scenes, 'plan maps to unknown target scene')
        events = {e['id']: e for e in shots[mapping['reference_shot_id']]['events']}
        _require(set(mapping['preserve_event_ids']) == set(events), 'every reference event must be preserved')
        overlays = mapping['overlays']
        _require(all(o['event_id'] in events for o in overlays), 'overlay references unknown event')
        text_ids = {e['id'] for e in events.values() if e['type'] == 'text'}
        _require(text_ids <= {o['event_id'] for o in overlays}, 'text events require a text overlay layer')
        required = {c for e in events.values() for c in e['required_capabilities']}
        _require(required <= set(mapping['required_capabilities']), 'event capability requirements were dropped')
        if require_execution and mapping['required_capabilities']:
            from lib.reference_execution import validate_mapping_execution
            validate_mapping_execution(project_dir, brief, shots[mapping['reference_shot_id']], mapping,
                                       asset_manifest, edit_decisions, stage=execution_stage)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _frame_pixels(path, timestamp=None):
    command = ['ffmpeg', '-v', 'error', '-threads', '1']
    if timestamp is not None:
        command += ['-ss', str(timestamp)]
    command += ['-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1']
    result = subprocess.run(command, capture_output=True, timeout=15)
    _require(result.returncode == 0 and bool(result.stdout), 'cannot decode target evidence frame')
    return hashlib.sha256(result.stdout).digest()


def validate_reference_style_review(brief, plan, review, project_dir, output_path):
    """Validate coverage and frame provenance, not visual similarity semantics.

    Each evidence_paths item is a JSON receipt containing source_video,
    source_sha256, timestamp_seconds, frame_path. The source must have exactly
    the target video's bytes, and the supplied frame must decode identically
    to that video's timestamp. A real visual reviewer must decide the status.
    """
    _schema('reference_style_review', review)
    output = project_path(project_dir, output_path, exists=True)
    _require(project_path(project_dir, review['output_path']) == output, 'review does not refer to actual target output')
    expected = {(s['reference_shot_id'], eid) for s in plan['shots']
                for eid in (s['preserve_event_ids'] or ['__shot__'])}
    checks = review['checks']
    keys = [(c['reference_shot_id'], c['event_id']) for c in checks]
    _require(len(keys) == len(set(keys)) and set(keys) == expected, 'every preserved event needs exactly one review')
    target_hash = _sha(output)
    for check in checks:
        _require(check['status'] == 'passed', 'review failed or unknown')
        receipts = check['evidence_paths']
        _require(1 <= len(receipts) <= 8, 'passed review requires 1..8 target evidence receipts')
        times = set()
        for value in receipts:
            receipt_path = project_path(project_dir, value, exists=True)
            _require(receipt_path.suffix.lower() == '.json', 'target frame requires a provenance receipt')
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            source = project_path(project_dir, receipt['source_video'], exists=True)
            _require(receipt['source_sha256'] == target_hash and _sha(source) == target_hash, 'review evidence source is not the target video')
            frame = project_path(project_dir, receipt['frame_path'], exists=True)
            timestamp = receipt['timestamp_seconds']
            _require(_time(timestamp) and timestamp >= 0, 'invalid target evidence time')
            _require(_frame_pixels(output, timestamp) == _frame_pixels(frame), 'evidence frame is not from target video')
            times.add(timestamp)
        events = {e['id']: e for s in brief['visual_analysis']['shots'] if s['id'] == check['reference_shot_id'] for e in s['events']}
        if events.get(check['event_id'], {}).get('type') in {'subject_motion', 'interaction'}:
            _require(len(times) >= 2, 'motion review requires target temporal evidence')


def _load(project_dir, name, artifacts):
    if name in artifacts:
        return artifacts[name]
    path = project_path(project_dir, f'artifacts/{name}.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    # Stage checkpoints also carry canonical/supplementary artifacts.
    candidates = []
    for checkpoint in Path(project_dir).glob('checkpoint_*.json'):
        project_path(project_dir, checkpoint, exists=True)
        payload = json.loads(checkpoint.read_text(encoding='utf-8'))
        if name in payload.get('artifacts', {}):
            candidates.append((checkpoint.stat().st_mtime_ns, payload['artifacts'][name]))
    return max(candidates, key=lambda item:item[0])[1] if candidates else _MISSING


def _validate_reference_source(project_dir, brief):
    source = brief.get('source', {})
    _require(source.get('local_path') and source.get('sha256') and source.get('size_bytes'), 'reference source fingerprint missing; reanalyze legacy brief')
    path = project_path(project_dir, source['local_path'], exists=True)
    fingerprint = _sha(path)
    _require(path.stat().st_size == source['size_bytes'] and fingerprint == source['sha256'], 'reference source changed; reanalysis required')
    samples = brief.get('sampled_frames', [])
    _require(1 <= len(samples) <= 120, 'reference sampled frame budget exceeded')
    decoded = {}
    for sample in samples:
        frame = project_path(project_dir, sample['path'], exists=True)
        stamp = sample['timestamp_seconds']
        if stamp not in decoded:
            decoded[stamp] = _frame_pixels(path, stamp)
        _require(decoded[stamp] == _frame_pixels(frame), 'sampled frame is not grounded in current reference source')
    _require(_sha(path) == fingerprint, 'reference source changed during evidence validation')


def _reference_intent(project_dir, inputs=None):
    """Recognize only explicit reference markers and conventional reference files.

    Ordinary uploaded/source videos carry no implied style-reference intent.
    This is deterministic input identification, not pipeline/creative routing.
    """
    def declared(payload):
        return isinstance(payload, dict) and (
            payload.get('reference_style_required') is True or bool(payload.get('reference_video'))
        )

    if declared(inputs):
        return True
    marker = project_path(project_dir, 'project.json')
    if marker.is_file() and declared(json.loads(marker.read_text(encoding='utf-8'))):
        return True
    for directory in ('inputs', 'input'):
        folder = Path(project_dir) / directory
        if folder.is_dir():
            for candidate in folder.glob('reference_video.*'):
                if candidate.is_file():
                    project_path(project_dir, candidate, exists=True)
                    return True
    return False


def validate_reference_stage(project_dir, stage, artifacts, *, output_path=None, require_review=False):
    """Return False for legacy projects, raise on incomplete reference contracts."""
    brief = _load(project_dir, 'video_analysis_brief', artifacts)
    if brief is _MISSING:
        if not _reference_intent(project_dir, artifacts):
            return False
        _require(stage not in PLANNING_STAGES, 'explicit reference intent requires completed reference analysis')
        return True
    if stage not in PLANNING_STAGES:
        return True
    validate_visual_analysis(brief)
    _validate_reference_source(project_dir, brief)
    if output_path is not None:
        project_path(project_dir, output_path)
    if stage in {'scene_plan', 'assets', 'edit', 'compose', 'publish'}:
        plan = _load(project_dir, 'reference_style_plan', artifacts)
        scene_plan = _load(project_dir, 'scene_plan', artifacts)
        _require(scene_plan is not _MISSING, 'target scene_plan missing')
        asset_manifest = _load(project_dir, 'asset_manifest', artifacts)
        edit_decisions = _load(project_dir, 'edit_decisions', artifacts)
        validate_reference_style_plan(
            brief, plan, scene_plan, require_execution=stage in {'assets','edit','compose','publish'},
            project_dir=project_dir, execution_stage=stage,
            asset_manifest=None if asset_manifest is _MISSING else asset_manifest,
            edit_decisions=None if edit_decisions is _MISSING else edit_decisions,
        )
        if require_review or stage == 'publish':
            review = _load(project_dir, 'reference_style_review', artifacts)
            _require(review is not _MISSING, 'reference_style_review missing; candidate is not approved')
            target = output_path or review.get('output_path')
            _require(bool(target), 'target output missing')
            validate_reference_style_review(brief, plan, review, project_dir, target)
    return True


def find_reference_project(inputs):
    """Locate the workspace from explicit context or the output's ancestors."""
    explicit = inputs.get('project_dir')
    if explicit:
        return Path(explicit).resolve()
    if inputs.get('project_id'):
        from lib.paths import PROJECTS_DIR
        return project_path(PROJECTS_DIR, inputs['project_id'])
    output = Path(inputs.get('output_path', '.')).resolve()
    for ancestor in output.parents:
        if (ancestor / 'artifacts' / 'video_analysis_brief.json').exists() or any(ancestor.glob('checkpoint_*.json')) or _reference_intent(ancestor):
            return ancestor
    if 'video_analysis_brief' in inputs or inputs.get('reference_style_required') is True or inputs.get('reference_video'):
        raise ValueError('Reference contract: explicit project_dir required')
    return None


@contextmanager
def _publication_lock(project_dir, *, timeout=10):
    """Serialize validation/promotion/compensation across production processes."""
    _require(_time(timeout) and 0 < timeout <= 60, 'invalid publication lock timeout')
    path = project_path(project_dir, 'artifacts/.reference_publish.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('Reference publication lock timeout; another publisher is active')
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def publish_reference_candidate(project_dir, candidate_path, output_path, review=None, *, lock_timeout=10):
    """Publish a reviewed candidate under a bounded cross-process project lock."""
    with _publication_lock(project_dir, timeout=lock_timeout):
        return _publish_reference_candidate_locked(project_dir, candidate_path, output_path, review)


def _publish_reference_candidate_locked(project_dir, candidate_path, output_path, review=None):
    """Validate a reviewed candidate, then atomically replace the deliverable.

    Keep the candidate for frame provenance and retries. Unreviewed renders must
    never overwrite the existing playable output. The caller supplies a real
    visual review; this utility makes no creative or approval decision.
    """
    candidate = project_path(project_dir, candidate_path, exists=True)
    output = project_path(project_dir, output_path)
    _require(candidate != output, 'candidate must be separate from deliverable')
    artifacts = {'reference_style_review': review} if review is not None else {}
    candidate_hash = _sha(candidate)
    _require(validate_reference_stage(project_dir, 'compose', artifacts, output_path=candidate, require_review=True), 'candidate has no reference contract')
    _require(_sha(candidate) == candidate_hash, 'candidate changed during review verification')
    result = dict(review or _load(project_dir, 'reference_style_review', {}))
    result['output_path'] = str(output)
    path = project_path(project_dir, 'artifacts/reference_style_review.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    pending = output.with_name('.' + output.name + '.' + token + '.publish')
    review_pending = path.with_name(path.name + '.' + token + '.tmp')
    backup = output.with_name('.' + output.name + '.' + token + '.backup')
    had_output = output.exists()
    replaced_output = False
    keep_backup = False
    try:
        # Prepare both durable payloads before touching the current deliverable.
        shutil.copyfile(candidate, pending)
        _require(_sha(pending) == candidate_hash, 'candidate changed while preparing publication')
        review_pending.write_text(json.dumps(result, indent=2), encoding='utf-8')
        if had_output:
            shutil.copyfile(output, backup)
        os.replace(pending, output)
        replaced_output = True
        os.replace(review_pending, path)
    except BaseException:
        # Filesystem and JSON metadata cannot be replaced in one transaction.
        # Compensate if the second replace fails, preserving the previous movie.
        if replaced_output:
            if had_output:
                try:
                    os.replace(backup, output)
                except BaseException:
                    keep_backup = True
                    raise
            else:
                output.unlink(missing_ok=True)
        raise
    finally:
        pending.unlink(missing_ok=True)
        review_pending.unlink(missing_ok=True)
        if not keep_backup:
            backup.unlink(missing_ok=True)
    return result
