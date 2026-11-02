"""Verify local execution evidence. Tracking data never proves overlay consumption."""
from __future__ import annotations

import json

from lib.reference_style import _require, _schema, _sha, project_path


def verify_tracking_receipt(project_dir, receipt_path, target_path):
    """Recompute deterministic tracking rather than trusting declared success."""
    receipt_file = project_path(project_dir, receipt_path, exists=True)
    tracking_root = project_path(project_dir, 'artifacts/tracking')
    _require(receipt_file.is_relative_to(tracking_root) and receipt_file.suffix.lower() == '.json', 'tracking receipt must be JSON under artifacts/tracking')
    _require(receipt_file.stat().st_size <= 1024 * 1024, 'tracking receipt exceeds size budget')
    try:
        receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ValueError('Reference BLOCKED: invalid tracking receipt') from exc
    _schema('object_tracking_receipt', receipt)
    target = project_path(project_dir, target_path, exists=True)
    source = project_path(project_dir, receipt['source']['path'], exists=True)
    _require(source == target, 'tracking receipt source differs from target video')
    fingerprint = _sha(target)
    _require(receipt['source']['sha256'] == fingerprint and receipt['source']['size_bytes'] == target.stat().st_size, 'tracking source changed')
    _require(receipt['status'] == 'complete', 'Reference BLOCKED: tracking lost or incomplete')
    from tools.analysis.object_tracker import track_region
    try:
        replay = track_region(project_dir, target, receipt['params'])
    except Exception as exc:
        raise ValueError('Reference BLOCKED: tracking replay failed') from exc
    fields = ('version', 'capability', 'tool', 'algorithm', 'source', 'params', 'status', 'samples')
    _require(all(receipt[field] == replay.get(field) for field in fields), 'tracking receipt does not match deterministic replay')
    _require(_sha(target) == fingerprint, 'tracking source changed during replay')
    return receipt


def validate_mapping_execution(project_dir, brief, shot, mapping, asset_manifest, edit_decisions=None, *, stage='assets'):
    required = set(mapping['required_capabilities'])
    unsupported = required - {'object_tracking'}
    _require(not unsupported, 'Reference BLOCKED: no verified execution adapter for capabilities: ' + ', '.join(sorted(unsupported)))
    _require(project_dir is not None and mapping.get('target_video_path'), 'Reference BLOCKED: tracking requires target_video_path')
    target = project_path(project_dir, mapping['target_video_path'], exists=True)
    reference = project_path(project_dir, brief.get('source', {}).get('local_path', ''), exists=True)
    _require(target != reference and _sha(target) != _sha(reference), 'tracking target must not be the reference video')
    assets = (asset_manifest or {}).get('assets', [])
    matches = [a for a in assets if a.get('scene_id') == mapping['scene_id'] and a.get('type') == 'video' and project_path(project_dir, a.get('path', '')) == target]
    _require(bool(matches), 'Reference BLOCKED: tracking target missing from scene video asset_manifest')
    ids = [a.get('id') for a in assets]
    _require(len(ids) == len(set(ids)), 'duplicate asset IDs make tracking target ambiguous')
    events = {e['id']: e for e in shot['events']}
    needed = {eid for eid, e in events.items() if 'object_tracking' in e['required_capabilities']}
    covered = set()
    bindings = mapping.get('execution_receipts', [])
    _require(bool(bindings), 'Reference BLOCKED: object_tracking requires execution receipts')
    for binding in bindings:
        _require(binding['capability'] == 'object_tracking', 'Reference BLOCKED: unsupported execution receipt capability')
        claimed = set(binding['event_ids'])
        _require(claimed <= needed and not claimed & covered, 'tracking receipt has unknown, unrelated or duplicate event coverage')
        receipt = verify_tracking_receipt(project_dir, binding['receipt_path'], target)
        times = [s['timestamp_seconds'] for s in receipt['samples']]
        _require(len(times) >= 2 and all(a < b for a,b in zip(times, times[1:])), 'tracking requires at least two ordered samples')
        for eid in claimed:
            # Reference events become local target shot time. No arbitrary tolerance.
            start = events[eid]['start_seconds'] - shot['start_seconds']
            end = events[eid]['end_seconds'] - shot['start_seconds']
            _require(times[0] <= start <= end <= times[-1], 'tracking samples do not cover event target interval')
        covered |= claimed
    _require(needed <= covered, 'Reference BLOCKED: tracking receipts omit required events')
    if stage in {'edit', 'compose', 'publish'}:
        target_ids = {a['id'] for a in matches}
        cuts = (edit_decisions or {}).get('cuts', [])
        consumed = False
        for cut in cuts:
            source = cut.get('source')
            if source in target_ids:
                consumed = True
            elif isinstance(source, str) and source not in ids:
                consumed |= project_path(project_dir, source) == target
        _require(consumed, 'Reference BLOCKED: edit cuts do not consume tracked target')
        raise ValueError('Reference BLOCKED: no verified trajectory consumption adapter; tracking data alone cannot prove subject_follow composition')
