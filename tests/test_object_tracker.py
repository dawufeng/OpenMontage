import hashlib
import json
import subprocess

import cv2
import numpy as np
import pytest


def make_video(root, kind='move', count=12):
    path = root / 'assets' / f'{kind}.avi'
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
    rng = np.random.default_rng(481)
    texture = rng.integers(40, 255, (40, 40, 3), dtype=np.uint8)
    for i in range(count):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        if kind != 'blank' and not (kind == 'lost' and i >= 5):
            frame[30+i:70+i, 20+2*i:60+2*i] = texture
        writer.write(frame)
    writer.release()
    return path


def core():
    from tools.analysis import object_tracker
    return object_tracker


def params(**changes):
    return dict(initial_bbox=[20, 30, 40, 40], start_seconds=0, end_seconds=1.2,
                max_frames=30, **changes)


def test_tracks_actual_texture_motion_with_pts_and_without_writes(tmp_path):
    video = make_video(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*'))
    receipt = core().track_region(tmp_path, video, params())
    assert receipt['status'] == 'complete'
    assert receipt['source'] == {'path': 'assets/move.avi', 'sha256': hashlib.sha256(video.read_bytes()).hexdigest(), 'size_bytes': video.stat().st_size}
    assert len(receipt['samples']) == 12
    assert receipt['samples'][-1]['timestamp_seconds'] == pytest.approx(1.1)
    assert receipt['samples'][-1]['bbox'][:2] == pytest.approx([42, 41], abs=.8)
    assert before == sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*'))
    assert core().track_region(tmp_path, video, receipt['params']) == receipt


@pytest.mark.parametrize('kind', ['blank', 'lost'])
def test_insufficient_or_lost_texture_never_complete(tmp_path, kind):
    receipt = core().track_region(tmp_path, make_video(tmp_path, kind), params())
    assert receipt['status'] == 'lost'
    assert receipt['diagnostics']['reason']
    assert len(receipt['samples']) < 12


@pytest.mark.parametrize('change', [dict(max_frames=301), dict(max_frames=2), dict(end_seconds=31), dict(initial_bbox=[-1,0,20,20]), dict(initial_bbox=[0,0,999,10]), dict(start_seconds=float('nan')), dict(max_frames=True), dict(untrusted=True)])
def test_rejects_budget_and_invalid_parameters(tmp_path, change):
    video = make_video(tmp_path)
    arguments = params()
    arguments.update(change)
    with pytest.raises(ValueError):
        core().track_region(tmp_path, video, arguments)


def test_rejects_source_outside_project(tmp_path):
    video = make_video(tmp_path)
    project = tmp_path / 'other'
    project.mkdir()
    with pytest.raises(ValueError):
        core().track_region(project, video, params())


def test_source_identity_changes_after_media_replacement(tmp_path):
    video = make_video(tmp_path)
    first = core().track_region(tmp_path, video, params())
    video.write_bytes(video.read_bytes() + b'changed')
    second = core().track_region(tmp_path, video, params())
    assert first['source'] != second['source']


def test_tool_persists_only_tracking_receipt(tmp_path):
    video = make_video(tmp_path)
    result = core().ObjectTracker().execute(dict(project_dir=str(tmp_path), source_path=str(video), params=params()))
    assert result.success, result.error
    assert len(result.artifacts) == 1
    receipt = json.loads(__import__('pathlib').Path(result.artifacts[0]).read_text())
    assert receipt['status'] == 'complete'
    assert __import__('pathlib').Path(result.artifacts[0]).parent == tmp_path / 'artifacts' / 'tracking'
    rejected = core().ObjectTracker().execute(dict(project_dir=str(tmp_path), source_path=str(video), params=params(), output_path='assets/overwrite.json'))
    assert not rejected.success
    assert not (tmp_path / 'assets' / 'overwrite.json').exists()


def test_true_nonzero_pts_are_preserved(tmp_path):
    original = make_video(tmp_path)
    shifted = original.with_name('shifted.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(original), '-vf', 'setpts=PTS+2/TB', '-vsync', '0', '-c:v', 'mpeg4', str(shifted)], check=True, capture_output=True)
    result = core().track_region(tmp_path, shifted, dict(initial_bbox=[20,30,40,40],start_seconds=2,end_seconds=3.2,max_frames=30))
    assert result['status'] == 'complete'
    assert result['samples'][0]['timestamp_seconds'] == pytest.approx(2)
    assert result['samples'][-1]['timestamp_seconds'] == pytest.approx(3.1)
    assert result['samples'][-1]['bbox'][:2] == pytest.approx([42,41], abs=.8)


def test_source_changed_during_tracking_is_rejected(tmp_path, monkeypatch):
    video = make_video(tmp_path)
    module = core()
    original_identity = module.source_identity
    calls = []
    def identity(path):
        calls.append(1)
        if len(calls) == 2:
            path.write_bytes(path.read_bytes() + b'changed-mid-run')
        return original_identity(path)
    monkeypatch.setattr(module, 'source_identity', identity)
    with pytest.raises(ValueError, match='changed'):
        module.track_region(tmp_path, video, params())


def test_rejects_junction_source_and_output(tmp_path):
    import os
    from pathlib import Path
    source = make_video(tmp_path)
    project = tmp_path / 'project'
    project.mkdir()
    link = project / 'link'
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(source.parent)], capture_output=True)
        if result.returncode:
            pytest.skip('junction creation unavailable')
    else:
        link.symlink_to(source.parent, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            core().track_region(project, link / source.name, params())
        result = core().ObjectTracker().execute(dict(project_dir=str(project), source_path=str(link / source.name), params=params(), output_path='link/fake.json'))
        assert not result.success
        assert not (source.parent / 'fake.json').exists()
    finally:
        if os.name == 'nt':
            os.rmdir(link)
        else:
            link.unlink()


def test_rejects_high_resolution_before_tracking(tmp_path):
    path = tmp_path / 'wide.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (2000,20))
    writer.write(np.zeros((20,2000,3), np.uint8))
    writer.write(np.zeros((20,2000,3), np.uint8))
    writer.release()
    with pytest.raises(ValueError, match='resolution'):
        core().track_region(tmp_path, path, dict(initial_bbox=[0,0,10,10],start_seconds=0,end_seconds=.2,max_frames=30))


def test_rejects_whole_clip_frame_budget_even_short_requested_interval(tmp_path):
    video = make_video(tmp_path, kind='blank', count=301)
    with pytest.raises(ValueError, match='budget'):
        core().track_region(tmp_path, video, dict(initial_bbox=[0,0,20,20],start_seconds=0,end_seconds=.2,max_frames=30))


def test_receipt_cannot_overwrite_media_or_existing_receipt(tmp_path):
    video = make_video(tmp_path)
    output = tmp_path / 'artifacts' / 'tracking' / 'existing.json'
    output.parent.mkdir(parents=True)
    output.write_text('original')
    result = core().ObjectTracker().execute(dict(project_dir=str(tmp_path), source_path=str(video), params=params(),output_path=str(output)))
    assert not result.success
    assert output.read_text() == 'original'



def test_decode_budget_is_bounded_even_if_container_frame_count_missing(tmp_path, monkeypatch):
    video = make_video(tmp_path)
    module = core()
    original_probe = module._probe
    def probe(path, frames=False):
        result = original_probe(path, frames)
        if not frames:
            result['streams'][0].pop('nb_frames', None)
        return result
    monkeypatch.setattr(module, '_probe', probe)
    with pytest.raises(ValueError, match='frame count'):
        module.track_region(tmp_path, video, params())


def test_receipt_commit_preserves_file_created_during_serialization(tmp_path, monkeypatch):
    video = make_video(tmp_path)
    module = core()
    output = tmp_path / 'artifacts' / 'tracking' / 'raced.json'
    original_dump = module.json.dump
    committed = b'{"committed_by":"concurrent writer"}\n'
    def racing_dump(data, handle, *args, **kwargs):
        result = original_dump(data, handle, *args, **kwargs)
        if isinstance(data, dict) and data.get('capability') == 'object_tracking':
            output.write_bytes(committed)
        return result
    monkeypatch.setattr(module.json, 'dump', racing_dump)
    result = module.ObjectTracker().execute(dict(project_dir=str(tmp_path), source_path=str(video), params=params(), output_path=str(output)))
    assert not result.success
    assert output.read_bytes() == committed
    assert not list(output.parent.glob('*.tmp'))


def test_parallel_receipt_writers_have_exactly_one_winner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    video = make_video(tmp_path)
    module = core()
    output = tmp_path / 'artifacts' / 'tracking' / 'parallel.json'
    original_dump = module.json.dump
    barrier = Barrier(2, timeout=10)
    def simultaneous_dump(data, handle, *args, **kwargs):
        result = original_dump(data, handle, *args, **kwargs)
        if isinstance(data, dict) and data.get('capability') == 'object_tracking':
            barrier.wait()
        return result
    monkeypatch.setattr(module.json, 'dump', simultaneous_dump)
    arguments = dict(project_dir=str(tmp_path), source_path=str(video), params=params(), output_path=str(output))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: module.ObjectTracker().execute(arguments), range(2)))
    assert sum(result.success for result in results) == 1
    winner = next(result for result in results if result.success)
    assert json.loads(output.read_text()) == winner.data
    assert not list(output.parent.glob('*.tmp'))
