"""Local, bounded translation tracking of an explicitly observed pixel region.

This is not semantic object/hand detection, segmentation, or an overlay renderer.
The pure-read core is also used to verify receipts by deterministic replay.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from tools.base_tool import BaseTool, ToolResult, ToolTier
from tools.analysis.reference_analyzer import project_path, source_identity


def _commit_receipt(path: Path, receipt: dict) -> None:
    """Publish complete bytes atomically, refusing any existing destination.

    The same-directory temporary file keeps the hard-link operation on one
    filesystem. Link creation is atomic and cannot replace another writer's
    receipt. Unsupported filesystems fail closed; never fall back to replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as handle:
            temporary = handle.name
            json.dump(receipt, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected finite numeric parameter')
    return float(value)


def _probe(path, frames=False):
    entries = ('frame=best_effort_timestamp_time' if frames else
               'stream=width,height,nb_frames,avg_frame_rate,start_time,duration:format=duration,start_time')
    command = ['ffprobe', '-v', 'error', '-select_streams', 'v:0']
    if frames:
        # Bound probing even if container metadata lies about frame count.
        command += ['-read_intervals', '%+#305']
    command += ['-show_entries', entries, '-of', 'json', str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=35, check=True)
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError('Unable to probe bounded local video') from exc


def track_region(project_dir, source_path, params) -> dict:
    """Read source only; return an unsigned receipt suitable for independent replay.

    Supported sources are whole clips <=30 seconds, <=300 decoded frames,
    <=1920x1080 pixels (either orientation), <=150M total decoded pixels,
    and <=512MiB, with a declared container frame count. Unknown frame counts
    fail closed rather than trigger an unbounded scan.
    All PTS in [start_seconds, end_seconds) are processed;
    exceeding max_frames rejects rather than silently truncating a trajectory.
    """
    import cv2
    import numpy as np

    root_input = Path(project_dir).absolute()
    root = root_input.resolve(strict=True)
    project_path(root, root_input)  # reject symlink/junction project ancestors
    source = project_path(root, source_path)
    if not source.is_file() or source.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('Missing source or source byte budget exceeded')
    if not isinstance(params, dict) or set(params) - {'initial_bbox', 'start_seconds', 'end_seconds', 'max_frames'}:
        raise ValueError('Unsupported tracking parameters')
    try:
        bbox = [_number(v) for v in params['initial_bbox']]
        start = _number(params.get('start_seconds', 0))
        end = _number(params['end_seconds'])
    except (KeyError, TypeError) as exc:
        raise ValueError('initial_bbox and end_seconds are required') from exc
    maximum = params.get('max_frames', 300)
    if type(maximum) is not int or not 2 <= maximum <= 300:
        raise ValueError('max_frames must be an integer in 2..300')
    if len(bbox) != 4 or min(bbox[:2]) < 0 or min(bbox[2:]) < 8:
        raise ValueError('Invalid initial pixel bbox')
    if not 0 <= start < end or end - start > 30:
        raise ValueError('Invalid tracking time budget')
    meta = _probe(source)
    try:
        stream = meta['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        duration = float(meta['format']['duration'])
        timeline_start = float(stream.get('start_time', meta['format'].get('start_time', 0)))
        stream_duration = float(stream.get('duration', duration))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError('Video dimensions/duration unavailable') from exc
    if (not all(math.isfinite(v) for v in (duration, timeline_start, stream_duration))
            or not 0 < duration <= 30 or not 0 < stream_duration <= 30
            or start < timeline_start or end > timeline_start + stream_duration + 1e-6):
        raise ValueError('Source duration budget exceeded or interval outside source')
    if min(width, height) < 1 or max(width, height) > 1920 or width * height > 1920 * 1080:
        raise ValueError('Video resolution budget exceeded')
    if bbox[0] + bbox[2] > width or bbox[1] + bbox[3] > height:
        raise ValueError('Initial bbox outside source frame')
    try:
        declared_count = int(stream.get('nb_frames', 0))
    except (ValueError, TypeError):
        declared_count = 0
    if declared_count < 2:
        raise ValueError('A declared bounded source frame count is required')
    if declared_count > 300 or declared_count * width * height > 150_000_000:
        raise ValueError('Decode work budget exceeded')
    before = source_identity(source)
    try:
        timestamps = [float(f['best_effort_timestamp_time']) for f in _probe(source, True)['frames']]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Real presentation timestamps unavailable') from exc
    if (len(timestamps) != declared_count or len(timestamps) > 300 or len(timestamps) * width * height > 150_000_000
            or any(not math.isfinite(t) for t in timestamps)
            or any(b <= a for a, b in zip(timestamps, timestamps[1:]))):
        raise ValueError('PTS/decode budget invalid')
    selected = [i for i, t in enumerate(timestamps) if start <= t < end]
    if not 2 <= len(selected) <= maximum:
        raise ValueError('Interval must contain 2..max_frames actual frames')
    normalized = dict(initial_bbox=bbox.copy(), start_seconds=start, end_seconds=end, max_frames=maximum)
    receipt = dict(version='1.0', capability='object_tracking', tool='object_tracker',
                   algorithm='lk_translation_v1', source=dict(path=source.relative_to(root).as_posix(), **before),
                   params=normalized, status='lost', samples=[], diagnostics={})
    capture = cv2.VideoCapture(str(source))
    previous = points = None
    initial_count = 0
    reason = 'decode_failed'
    try:
        for index in range(selected[-1] + 1):
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[:2] != (height, width):
                raise ValueError('Decoded dimensions differ from probe')
            if index < selected[0]:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if previous is None:
                mask = np.zeros_like(gray)
                x, y, w, h = bbox
                mask[math.ceil(y):math.floor(y+h), math.ceil(x):math.floor(x+w)] = 255
                points = cv2.goodFeaturesToTrack(gray, maxCorners=80, qualityLevel=.02, minDistance=3, mask=mask)
                initial_count = 0 if points is None else len(points)
                if initial_count < 6:
                    reason = 'insufficient_texture'
                    break
                confidence = 1.0
            else:
                options = dict(winSize=(15, 15), maxLevel=2,
                               criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, .01))
                forward, valid, _ = cv2.calcOpticalFlowPyrLK(previous, gray, points, None, **options)
                if forward is None:
                    reason = 'optical_flow_lost'
                    break
                backward, back_valid, _ = cv2.calcOpticalFlowPyrLK(gray, previous, forward, None, **options)
                if backward is None:
                    reason = 'optical_flow_lost'
                    break
                old = points.reshape(-1, 2)
                new = forward.reshape(-1, 2)
                good = ((valid.ravel() == 1) & (back_valid.ravel() == 1)
                        & (np.linalg.norm(old - backward.reshape(-1, 2), axis=1) <= 1.0)
                        & np.isfinite(new).all(axis=1))
                delta = new - old
                if good.sum() >= 6:
                    motion = np.median(delta[good], axis=0)
                    good &= np.linalg.norm(delta - motion, axis=1) <= 2.0
                if good.sum() < max(6, math.ceil(initial_count * .35)):
                    reason = 'insufficient_consistent_features'
                    break
                motion = np.median(delta[good], axis=0)
                bbox[0] += float(motion[0])
                bbox[1] += float(motion[1])
                if bbox[0] < 0 or bbox[1] < 0 or bbox[0]+bbox[2] > width or bbox[1]+bbox[3] > height:
                    reason = 'region_left_frame'
                    break
                # Texture must still be present; forward/backward consistency alone
                # can survive a disappearing object on a uniform background.
                x, y, w, h = bbox
                region = gray[math.ceil(y):math.floor(y+h), math.ceil(x):math.floor(x+w)]
                if region.size == 0 or float(region.std()) < 5:
                    reason = 'texture_lost'
                    break
                points = forward[good].reshape(-1, 1, 2)
                confidence = float(good.sum() / initial_count)
            receipt['samples'].append(dict(timestamp_seconds=timestamps[index],
                                           bbox=[round(v, 6) for v in bbox], confidence=round(confidence, 6)))
            previous = gray
        if len(receipt['samples']) == len(selected):
            receipt['status'] = 'complete'
            reason = 'covered_requested_interval'
    finally:
        capture.release()
    project_path(root, source)
    if source_identity(source) != before:
        raise ValueError('Source changed during tracking')
    receipt['diagnostics'] = dict(reason=reason, decoded_frame_count=len(timestamps),
                                   selected_frame_count=len(selected), initial_features=initial_count)
    return receipt


class ObjectTracker(BaseTool):
    name = 'object_tracker'
    tier = ToolTier.ANALYZE
    capability = 'object_tracking'
    capabilities = ['object_tracking']
    dependencies = ['cmd:ffprobe', 'python:cv2', 'python:numpy']
    description = 'Track an agent-observed pixel bbox with local LK translation; no semantic detection.'
    input_schema = {'type': 'object', 'required': ['project_dir', 'source_path', 'params'], 'properties': {
        'project_dir': {'type': 'string'}, 'source_path': {'type': 'string'},
        'params': {'type': 'object'}, 'output_path': {'type': 'string'}}}
    side_effects = ['Writes one atomic receipt under project_dir/artifacts/tracking']

    def execute(self, inputs):
        try:
            root = Path(inputs['project_dir']).resolve(strict=True)
            destination = project_path(root, inputs.get('output_path', f'artifacts/tracking/{uuid.uuid4().hex}.json'))
            tracking = project_path(root, 'artifacts/tracking')
            if not destination.is_relative_to(tracking) or destination.suffix.lower() != '.json':
                raise ValueError('Receipt output must be JSON under artifacts/tracking')
            receipt = track_region(inputs['project_dir'], inputs['source_path'], inputs['params'])
            _commit_receipt(destination, receipt)
            return ToolResult(success=receipt['status'] == 'complete', data=receipt,
                              artifacts=[str(destination)], error=None if receipt['status'] == 'complete' else receipt['diagnostics']['reason'])
        except (ValueError, KeyError, OSError) as exc:
            return ToolResult(success=False, error=str(exc))
