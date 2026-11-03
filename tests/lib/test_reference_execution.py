import copy
import json
import subprocess

import numpy as np
import pytest

from lib import reference_style as guards
from test_reference_style import brief, plan, setup_project, persist


def tracking_plan():
    b, p = brief(), plan()
    b['visual_analysis']['shots'][0]['events'][0]['required_capabilities'] = ['object_tracking']
    p['shots'][0].update(required_capabilities=['object_tracking'], target_video_path='assets/target.mp4', execution_receipts=[{'capability':'object_tracking','receipt_path':'artifacts/tracking/target.json','event_ids':['text1']}])
    return b, p


def test_tracking_plan_schema_accepts_explicit_target_and_receipt():
    b,p = tracking_plan()
    guards.validate_reference_style_plan(b,p,{'scenes':[{'id':'s1'}]})


@pytest.fixture
def tracking_project(tmp_path):
    setup_project(tmp_path)
    from tools.analysis.object_tracker import track_region
    (tmp_path/'assets').mkdir()
    target = tmp_path/'assets/target.mp4'
    rng = np.random.default_rng(11)
    texture = rng.integers(0,255,(24,24,3),dtype=np.uint8)
    frames = []
    for i in range(16):
        frame = np.zeros((80,96,3),dtype=np.uint8)
        frame[24:48,16+i:40+i] = texture
        frames.append(frame.tobytes())
    subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','96x80','-r','10','-i','pipe:0','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(target)],input=b''.join(frames),check=True,timeout=15)
    receipt = track_region(tmp_path,'assets/target.mp4',{'initial_bbox':[16,24,24,24],'start_seconds':0,'end_seconds':1.5,'max_frames':30})
    assert receipt['status'] == 'complete'
    (tmp_path/'artifacts/tracking').mkdir()
    receipt_path = tmp_path/'artifacts/tracking/target.json'
    receipt_path.write_text(json.dumps(receipt),encoding='utf-8')
    b,p = tracking_plan()
    persisted = json.loads((tmp_path/'artifacts/video_analysis_brief.json').read_text())
    b['source'] = persisted['source']
    manifest = {'version':'1.0','assets':[{'id':'target','scene_id':'s1','type':'video','path':'assets/target.mp4','source_tool':'fixture'}]}
    persist(tmp_path,'video_analysis_brief',b)
    persist(tmp_path,'reference_style_plan',p)
    persist(tmp_path,'asset_manifest',manifest)
    return tmp_path,b,p,receipt,manifest


def test_valid_tracking_allows_assets_only(tracking_project):
    root,b,p,receipt,manifest = tracking_project
    assert guards.validate_reference_stage(root,'assets',{}) is True
    persist(root,'edit_decisions',{'version':'1.0','render_runtime':'ffmpeg','cuts':[{'id':'cut','source':'target','in_seconds':0,'out_seconds':1.4}]})
    for stage in ['edit','compose','publish']:
        with pytest.raises(ValueError,match='trajectory consumption'):
            guards.validate_reference_stage(root,stage,{})


@pytest.mark.parametrize('damage',['tamper','source_changed','wrong_scene','wrong_target','missing_events','no_receipts','reference_target','outside_receipt','unknown_capability','lost','short_samples','params_changed','missing_manifest'])
def test_invalid_tracking_evidence_blocks_assets(tracking_project, damage):
    root,b,p,receipt,manifest = tracking_project
    if damage == 'tamper': receipt['samples'][1]['bbox'][0] += 4
    if damage == 'source_changed': (root/'assets/target.mp4').write_bytes(b'changed')
    if damage == 'wrong_scene': manifest['assets'][0]['scene_id'] = 's2'
    if damage == 'wrong_target': p['shots'][0]['target_video_path'] = 'source.mp4'
    if damage == 'missing_events': p['shots'][0]['execution_receipts'][0]['event_ids'] = ['other']
    if damage == 'no_receipts': p['shots'][0]['execution_receipts'] = []
    if damage == 'reference_target':
        p['shots'][0]['target_video_path'] = 'source.mp4'
        manifest['assets'][0]['path'] = 'source.mp4'
    if damage == 'outside_receipt': p['shots'][0]['execution_receipts'][0]['receipt_path'] = '../outside.json'
    if damage == 'unknown_capability': p['shots'][0]['required_capabilities'].append('masking')
    if damage == 'lost': receipt['status'] = 'lost'
    if damage == 'short_samples': receipt['samples'] = receipt['samples'][:2]
    if damage == 'params_changed': receipt['params']['initial_bbox'][0] += 3
    if damage == 'missing_manifest': manifest['assets'] = []
    (root/'artifacts/tracking/target.json').write_text(json.dumps(receipt),encoding='utf-8')
    persist(root,'reference_style_plan',p)
    persist(root,'asset_manifest',manifest)
    with pytest.raises(ValueError): guards.validate_reference_stage(root,'assets',{})


def test_nonreference_assets_compatible(tmp_path):
    assert guards.validate_reference_stage(tmp_path,'assets',{}) is False

@pytest.mark.parametrize('source, expected',[(None,'do not consume'),('unrelated','do not consume'),('assets/target.mp4','trajectory consumption'),('target','trajectory consumption')])
def test_edit_requires_actual_target_cut_before_consumption_guard(tracking_project, source, expected):
    root,*_ = tracking_project
    cuts = [] if source is None else [{'id':'cut','source':source,'in_seconds':0,'out_seconds':1.4}]
    persist(root,'edit_decisions',{'version':'1.0','render_runtime':'ffmpeg','cuts':cuts})
    with pytest.raises(ValueError,match=expected): guards.validate_reference_stage(root,'edit',{})


@pytest.mark.parametrize('damage',['source_escape','receipt_escape','source_path_changed','event_interval_exceeds_samples'])
def test_strict_receipt_paths_and_event_interval(tracking_project,damage):
    root,b,p,receipt,manifest = tracking_project
    if damage == 'source_escape': receipt['source']['path'] = '../escape.mp4'
    if damage == 'source_path_changed': receipt['source']['path'] = 'source.mp4'
    if damage == 'receipt_escape':
        outside = root.parent/(root.name+'-outside.json')
        outside.write_text(json.dumps(receipt),encoding='utf-8')
        p['shots'][0]['execution_receipts'][0]['receipt_path'] = str(outside)
    if damage == 'event_interval_exceeds_samples':
        b['visual_analysis']['shots'][0]['events'][0]['end_seconds'] = 1.9
        persist(root,'video_analysis_brief',b)
    persist(root,'reference_style_plan',p)
    (root/'artifacts/tracking/target.json').write_text(json.dumps(receipt),encoding='utf-8')
    with pytest.raises(ValueError): guards.validate_reference_stage(root,'assets',{})


def test_receipt_symlink_rejected(tracking_project):
    root,b,p,receipt,manifest = tracking_project
    link = root/'artifacts/tracking/link.json'
    try:
        link.symlink_to(root/'artifacts/tracking/target.json')
    except OSError:
        pytest.skip('symlink privilege unavailable')
    p['shots'][0]['execution_receipts'][0]['receipt_path'] = 'artifacts/tracking/link.json'
    persist(root,'reference_style_plan',p)
    with pytest.raises(ValueError,match='links/junctions'): guards.validate_reference_stage(root,'assets',{})
