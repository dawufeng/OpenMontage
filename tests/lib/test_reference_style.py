import copy
import hashlib
import shutil
import subprocess
import json
from pathlib import Path

import pytest

from lib import reference_style as guards
from lib.checkpoint import CheckpointValidationError, write_checkpoint
from tools.video.video_compose import VideoCompose
from tools.base_tool import ToolResult


def brief():
    aspects = {k: {'status': 'absent' if k in {'subject_motion','interaction'} else 'observed', 'description': 'visible', 'confidence': .9, 'evidence_timestamps': [0, 1]} for k in guards.ASPECTS}
    return {'version':'1.0','source':{'type':'local_file','duration_seconds':2},'content_analysis':{'summary':'x','topics':[],'target_audience':'x'},'structure_analysis':{'total_scenes':1,'scenes':[{'scene_index':0,'start_time':0,'end_time':2,'description':'x'}],'pacing_profile':{}},'sampled_frames':[{'shot_id':'shot_000','timestamp_seconds':t,'path':f'frames/{t}.png'} for t in [0,1]],'visual_analysis':{'version':'1.0','status':'ready','issues':[],'shots':[{'id':'shot_000','start_seconds':0,'end_seconds':2,'aspects':aspects,'events':[{'id':'text1','type':'text','start_seconds':0,'end_seconds':1,'description':'label','evidence_timestamps':[0,1],'required_capabilities':[],'anchor':{'render_mode':'flat','binding':'screen'}}]}]}}


def plan():
    return {'version':'1.0','shots':[{'reference_shot_id':'shot_000','scene_id':'s1','preserve_event_ids':['text1'],'subject_generation':{'description':'actor'},'overlays':[{'event_id':'text1','description':'label'}],'required_capabilities':[]}],'available_capabilities':[]}


def persist(root, name, value):
    (root/'artifacts').mkdir(parents=True,exist_ok=True)
    (root/'artifacts'/f'{name}.json').write_text(json.dumps(value), encoding='utf-8')


def setup_project(root):
    (root/'frames').mkdir(parents=True,exist_ok=True)
    source=root/'source.mp4'
    cached=getattr(setup_project,'media_bytes',None)
    if cached is None:
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32:r=2:d=2','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)],check=True,timeout=15)
        frame=root/'frames'/'0.png'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-frames:v','1','-threads','1',str(frame)],check=True,timeout=15)
        cached=(source.read_bytes(),frame.read_bytes())
        setup_project.media_bytes=cached
    source.write_bytes(cached[0])
    for t in [0,1]: (root/'frames'/f'{t}.png').write_bytes(cached[1])
    b=brief();b['source'].update(local_path='source.mp4',sha256=hashlib.sha256(cached[0]).hexdigest(),size_bytes=len(cached[0]))
    persist(root,'video_analysis_brief',b)
    persist(root,'reference_style_plan',plan())
    persist(root,'scene_plan',{'scenes':[{'id':'s1'}]})


def test_empty_analysis_rejected():
    with pytest.raises(ValueError): guards.validate_visual_analysis({})


@pytest.mark.parametrize('mutate', [
    lambda b:b['visual_analysis'].update(status='incomplete'),
    lambda b:b['visual_analysis']['shots'][0]['aspects']['subject'].update(status='unknown'),
    lambda b:b['visual_analysis']['shots'][0]['aspects']['subject'].update(evidence_timestamps=[.5]),
    lambda b:b['visual_analysis']['shots'][0]['events'][0].update(type='interaction',evidence_timestamps=[0]),
    lambda b:b['visual_analysis']['shots'][0].update(end_seconds=1),
    lambda b:b['visual_analysis'].update(shots=[]),
])
def test_incomplete_or_ungrounded_visual_rejected(mutate):
    b=brief();mutate(b)
    with pytest.raises(ValueError):guards.validate_visual_analysis(b)


def test_ready_visual(): guards.validate_visual_analysis(brief())


@pytest.mark.parametrize('mutate',[
    lambda p:p['shots'][0].update(preserve_event_ids=[]),
    lambda p:p['shots'][0].update(overlays=[]),
    lambda p:p['shots'][0].update(scene_id='missing'),
    lambda p:p['shots'].append(copy.deepcopy(p['shots'][0])),
])
def test_invalid_plan_rejected(mutate):
    p=plan();mutate(p)
    with pytest.raises(ValueError):guards.validate_reference_style_plan(brief(),p,{'scenes':[{'id':'s1'}]})


def test_capability_strings_do_not_prove_execution(tmp_path):
    b=brief(); b['visual_analysis']['shots'][0]['events'][0]['required_capabilities']=['object_tracking']
    p=plan();p['shots'][0]['required_capabilities']=['object_tracking'];p['available_capabilities']=['object_tracking']
    with pytest.raises(ValueError,match='[Bb][Ll][Oo][Cc][Kk]'): guards.validate_reference_style_plan(b,p,{'scenes':[{'id':'s1'}]},require_execution=True,project_dir=tmp_path)


def test_checkpoint_empty_reference_blocks_success(tmp_path):
    persist(tmp_path/'run','video_analysis_brief',{})
    with pytest.raises(CheckpointValidationError,match='[Rr]eference'):
        write_checkpoint(tmp_path,'run','scene_plan','completed',{'scene_plan':{'version':'1.0','scenes':[{'id':'s1','type':'animation','description':'x','start_seconds':0,'end_seconds':1}]}})


def test_nonreference_checkpoint_compatible(tmp_path):
    assert write_checkpoint(tmp_path,'run','scene_plan','completed',{'scene_plan':{'version':'1.0','scenes':[{'id':'s1','type':'animation','description':'x','start_seconds':0,'end_seconds':1}]}}).exists()


def test_reference_failure_heartbeat_allowed(tmp_path):
    persist(tmp_path/'run','video_analysis_brief',{})
    assert write_checkpoint(tmp_path,'run','scene_plan','failed',{},error='blocked').exists()


def test_production_entry_blocks_empty_reference_before_render(tmp_path, monkeypatch):
    persist(tmp_path,'video_analysis_brief',{})
    called=[]
    monkeypatch.setattr(VideoCompose,'_compose',lambda s,i: called.append(i) or ToolResult(success=True))
    result=VideoCompose().execute({'operation':'compose','output_path':str(tmp_path/'renders'/'final.mp4')})
    assert not result.success and not called


def test_encode_not_blocked_by_reference(tmp_path, monkeypatch):
    persist(tmp_path,'video_analysis_brief',{})
    monkeypatch.setattr(VideoCompose,'_encode',lambda s,i: ToolResult(success=True))
    assert VideoCompose().execute({'operation':'encode','output_path':str(tmp_path/'renders'/'final.mp4')}).success


def test_render_without_review_preserves_old_final(tmp_path, monkeypatch):
    setup_project(tmp_path)
    target=tmp_path/'renders'/'final.mp4';target.parent.mkdir();target.write_bytes(b'old final')
    def render(self,inputs):
        Path(inputs['output_path']).write_bytes(b'new candidate')
        return ToolResult(success=True,data={'output':inputs['output_path']})
    monkeypatch.setattr(VideoCompose,'_compose',render)
    result=VideoCompose().execute({'operation':'compose','output_path':str(target)})
    assert not result.success
    assert target.read_bytes()==b'old final'
    assert result.data['reference_candidate_path'] != str(target)


@pytest.mark.parametrize('status',['failed','unknown','passed'])
def test_review_requires_bound_target_evidence(tmp_path,status):
    setup_project(tmp_path)
    output=tmp_path/'renders'/'final.mp4';output.parent.mkdir();output.write_bytes(b'target')
    ref=tmp_path/'reference.png';ref.write_bytes(b'reference')
    review={'version':'1.0','output_path':str(output),'checks':[{'reference_shot_id':'shot_000','event_id':'text1','status':status,'evidence_paths':[str(ref)],'notes':'claimed pass'}]}
    with pytest.raises(ValueError): guards.validate_reference_style_review(brief(),plan(),review,tmp_path,output)


def test_output_escape_rejected(tmp_path):
    setup_project(tmp_path)
    with pytest.raises(ValueError):guards.validate_reference_stage(tmp_path,'compose',{},output_path=tmp_path.parent/'outside.mp4')


@pytest.mark.parametrize("aspect,event_type",[("typography","text"),("interaction","interaction"),("subject_motion","subject_motion")])
def test_observed_event_aspect_cannot_drop_event(aspect,event_type):
    b=brief();b['visual_analysis']['shots'][0]['aspects'][aspect]['status']='observed'
    b['visual_analysis']['shots'][0]['events']=[]
    with pytest.raises(ValueError):guards.validate_visual_analysis(b)


def test_interaction_cannot_omit_explicit_interaction_mode():
    b=brief();shot=b['visual_analysis']['shots'][0];shot['aspects']['typography']['status']='absent';shot['aspects']['interaction']['status']='observed';shot['events'][0]['type']='interaction'
    with pytest.raises(ValueError):guards.validate_visual_analysis(b)


def test_typed_3d_cannot_omit_render_requirement():
    b=brief();b['visual_analysis']['shots'][0]['events'][0]['anchor']={'render_mode':'3d'}
    with pytest.raises(ValueError):guards.validate_visual_analysis(b)

@pytest.fixture
def real_target(tmp_path):
    if not shutil.which('ffmpeg'): pytest.skip('local ffmpeg unavailable')
    setup_project(tmp_path)
    candidate=tmp_path/'renders'/'candidate.mp4';candidate.parent.mkdir()
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32:r=2:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(candidate)],check=True,timeout=15)
    frame=tmp_path/'target.png'
    subprocess.run(['ffmpeg','-v','error','-ss','0','-i',str(candidate),'-frames:v','1',str(frame)],check=True,timeout=15)
    receipt=tmp_path/'evidence.json'
    receipt.write_text(json.dumps({'source_video':str(candidate),'source_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),'timestamp_seconds':0,'frame_path':str(frame)}),encoding='utf-8')
    review={'version':'1.0','output_path':str(candidate),'checks':[{'reference_shot_id':'shot_000','event_id':'text1','status':'passed','evidence_paths':[str(receipt)],'notes':'Real visual tool observation supplied by caller'}]}
    return candidate,review


def test_real_target_frame_evidence_and_atomic_publish(tmp_path, real_target):
    candidate,review=real_target
    target=tmp_path/'renders'/'final.mp4';target.write_bytes(b'old final')
    guards.validate_reference_style_review(brief(),plan(),review,tmp_path,candidate)
    published=guards.publish_reference_candidate(tmp_path,candidate,target,review)
    assert target.read_bytes()==candidate.read_bytes()
    assert published['output_path']==str(target)
    guards.validate_reference_style_review(brief(),plan(),published,tmp_path,target)


def test_forged_frame_receipt_does_not_publish(tmp_path,real_target):
    candidate,review=real_target
    receipt_path=Path(review['checks'][0]['evidence_paths'][0]);receipt=json.loads(receipt_path.read_text());receipt['timestamp_seconds']=100;receipt_path.write_text(json.dumps(receipt))
    target=tmp_path/'renders'/'final.mp4';target.write_bytes(b'old final')
    with pytest.raises(ValueError):guards.publish_reference_candidate(tmp_path,candidate,target,review)
    assert target.read_bytes()==b'old final'


def test_reference_from_checkpoint_cannot_be_omitted_at_compose(tmp_path,monkeypatch):
    (tmp_path/'artifacts').mkdir()
    (tmp_path/'checkpoint_research.json').write_text(json.dumps({'artifacts':{'video_analysis_brief':{}}}))
    called=[];monkeypatch.setattr(VideoCompose,'_compose',lambda s,i:called.append(i) or ToolResult(success=True))
    result=VideoCompose().execute({'operation':'compose','output_path':str(tmp_path/'renders'/'final.mp4')})
    assert not result.success and not called


def test_absolute_output_escape_blocks_reference_render(tmp_path, monkeypatch):
    setup_project(tmp_path)
    called=[];monkeypatch.setattr(VideoCompose,'_compose',lambda s,i:called.append(i) or ToolResult(success=True))
    result=VideoCompose().execute({'operation':'compose','project_dir':str(tmp_path),'output_path':str(tmp_path.parent/'outside.mp4')})
    assert not result.success and not called


def test_null_reference_is_present_and_blocks(tmp_path):
    persist(tmp_path,'video_analysis_brief',None)
    with pytest.raises(ValueError):guards.validate_reference_stage(tmp_path,'scene_plan',{})


def test_sample_path_escape_blocks_planning(tmp_path):
    setup_project(tmp_path)
    b=brief();b['sampled_frames'][0]['path']=str(tmp_path.parent/'reference.png')
    with pytest.raises(ValueError):guards.validate_reference_stage(tmp_path,'scene_plan',{'video_analysis_brief':b})


@pytest.mark.parametrize('anchor',[None,{}, {'render_mode':'flat'}, {'render_mode':'garbage','binding':'screen'}])
def test_text_requires_typed_anchor(anchor):
    b=brief()
    if anchor is None:b['visual_analysis']['shots'][0]['events'][0].pop('anchor',None)
    else:b['visual_analysis']['shots'][0]['events'][0]['anchor']=anchor
    with pytest.raises(ValueError):guards.validate_visual_analysis(b)


def test_missing_output_uses_project_default_candidate(tmp_path,monkeypatch):
    setup_project(tmp_path)
    def render(self,inputs):
        Path(inputs['output_path']).write_bytes(b'candidate')
        return ToolResult(success=True)
    monkeypatch.setattr(VideoCompose,'_compose',render)
    result=VideoCompose().execute({'operation':'compose','project_dir':str(tmp_path)})
    assert not result.success
    assert result.data['requested_output_path']==str((tmp_path/'renders'/'final.mp4').resolve())
    assert Path(result.data['reference_candidate_path']).is_file()


def test_publish_rolls_back_when_review_persistence_fails(tmp_path,real_target,monkeypatch):
    candidate,review=real_target
    output=tmp_path/'renders'/'final.mp4';output.write_bytes(b'old final')
    persist(tmp_path,'reference_style_review',{'previous':'review'})
    original=guards.os.replace
    def fail_review(source,target):
        if Path(target).name=='reference_style_review.json':raise OSError('simulated metadata failure')
        return original(source,target)
    monkeypatch.setattr(guards.os,'replace',fail_review)
    with pytest.raises(OSError): guards.publish_reference_candidate(tmp_path,candidate,output,review)
    assert output.read_bytes()==b'old final'
    assert json.loads((tmp_path/'artifacts'/'reference_style_review.json').read_text())=={'previous':'review'}


def test_stale_source_fingerprint_blocks_production(tmp_path):
    setup_project(tmp_path)
    source=tmp_path/'source.mp4';source.write_bytes(b'replaced source')
    with pytest.raises(ValueError):guards.validate_reference_stage(tmp_path,'scene_plan',{})


def test_fake_sample_png_blocks_production(tmp_path):
    setup_project(tmp_path)
    (tmp_path/'frames'/'0.png').write_bytes(b'not a PNG')
    with pytest.raises(ValueError):guards.validate_reference_stage(tmp_path,'scene_plan',{})


def test_publication_uses_cross_process_lock(tmp_path, real_target):
    import sys
    candidate,review=real_target
    target=tmp_path/'renders'/'final.mp4';target.write_bytes(b'old final')
    review_file=tmp_path/'provided_review.json';review_file.write_text(json.dumps(review))
    script="from lib.reference_style import publish_reference_candidate;import json,sys;publish_reference_candidate(sys.argv[1],sys.argv[2],sys.argv[3],json.load(open(sys.argv[4])),lock_timeout=.1)"
    with guards._publication_lock(tmp_path, timeout=1):
        result=subprocess.run([sys.executable,'-c',script,str(tmp_path),str(candidate),str(target),str(review_file)],capture_output=True,text=True,timeout=20)
    assert result.returncode != 0 and 'publication lock timeout' in result.stderr
    assert target.read_bytes()==b'old final'


def test_source_frame_rounding_tolerance_uses_actual_pts(tmp_path):
    setup_project(tmp_path)
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=32x32:r=24:d=2','-c:v','libx264','-threads','1','-y',str(source)],check=True,timeout=15)
    for stamp,path in [(.4,tmp_path/'frames'/'0.png'),(1,tmp_path/'frames'/'1.png')]:
        subprocess.run(['ffmpeg','-v','error','-copyts','-ss',str(stamp),'-i',str(source),'-frames:v','1','-fps_mode','passthrough','-threads','1','-y',str(path)],check=True,timeout=15)
    b=brief();b['source'].update(local_path='source.mp4',sha256=hashlib.sha256(source.read_bytes()).hexdigest(),size_bytes=source.stat().st_size)
    b['sampled_frames'][0]['timestamp_seconds']=.416667
    for aspect in b['visual_analysis']['shots'][0]['aspects'].values(): aspect['evidence_timestamps']=[.416667,1]
    b['visual_analysis']['shots'][0]['events'][0]['evidence_timestamps']=[.416667,1]
    assert guards.validate_reference_stage(tmp_path,'scene_plan',{'video_analysis_brief':b})


def static_reference_contract():
    b = brief()
    b['visual_analysis']['shots'][0]['events'] = []
    b['visual_analysis']['shots'][0]['aspects']['typography']['status'] = 'absent'
    p = plan()
    p['shots'][0]['preserve_event_ids'] = []
    p['shots'][0]['overlays'] = []
    return b, p


def test_eventless_reference_still_requires_shot_review(tmp_path, real_target):
    candidate, review = real_target
    b, p = static_reference_contract()
    review['checks'] = []
    with pytest.raises(ValueError, match='every preserved event'):
        guards.validate_reference_style_review(b, p, review, tmp_path, candidate)


def test_eventless_reference_accepts_grounded_shot_review(tmp_path, real_target):
    candidate, review = real_target
    b, p = static_reference_contract()
    review['checks'][0]['event_id'] = '__shot__'
    guards.validate_reference_style_review(b, p, review, tmp_path, candidate)


@pytest.mark.parametrize('intent',[{'reference_style_required':True},{'reference_video':'inputs/clip.mp4'}])
def test_explicit_marker_reference_requires_analysis(tmp_path,intent):
    (tmp_path/'project.json').write_text(json.dumps(intent))
    with pytest.raises(ValueError, match='reference.*analysis|Reference.*analysis'):
        guards.validate_reference_stage(tmp_path,'scene_plan',{})


@pytest.mark.parametrize('directory',['inputs','input'])
def test_reference_filename_requires_analysis(tmp_path,directory):
    folder=tmp_path/directory;folder.mkdir();(folder/'reference_video.mp4').write_bytes(b'explicit reference')
    with pytest.raises(ValueError):guards.validate_reference_stage(tmp_path,'compose',{})
    assert guards.find_reference_project({'output_path':str(tmp_path/'renders'/'final.mp4')})==tmp_path.resolve()


def test_marker_intent_found_from_output_before_render(tmp_path,monkeypatch):
    (tmp_path/'project.json').write_text(json.dumps({'reference_style_required':True}))
    called=[];monkeypatch.setattr(VideoCompose,'_compose',lambda s,i:called.append(i) or ToolResult(success=True))
    result=VideoCompose().execute({'operation':'compose','output_path':str(tmp_path/'renders'/'final.mp4')})
    assert not result.success and not called


def test_marker_reference_blocks_success_checkpoint(tmp_path):
    root=tmp_path/'run';root.mkdir();(root/'project.json').write_text(json.dumps({'reference_video':'input/reference_video.webm'}))
    with pytest.raises(CheckpointValidationError):
        write_checkpoint(tmp_path,'run','scene_plan','completed',{'scene_plan':{'version':'1.0','scenes':[{'id':'s1','type':'animation','description':'x','start_seconds':0,'end_seconds':1}]}})


@pytest.mark.parametrize('directory',['uploads','inputs','input'])
def test_ordinary_source_upload_does_not_imply_reference(tmp_path,directory):
    folder=tmp_path/directory;folder.mkdir();(folder/'source_video.mp4').write_bytes(b'ordinary source')
    (tmp_path/'project.json').write_text(json.dumps({'reference_style_required':False,'reference_video':''}))
    assert guards.validate_reference_stage(tmp_path,'compose',{}) is False
    assert guards.find_reference_project({'output_path':str(tmp_path/'renders'/'final.mp4')}) is None


def test_reference_intent_does_not_block_research_heartbeat(tmp_path):
    (tmp_path/'project.json').write_text(json.dumps({'reference_style_required':True}))
    assert guards.validate_reference_stage(tmp_path,'research',{}) is True


def interaction_brief(mode, *, binding='screen', capabilities=(), render_mode='flat'):
    b=brief()
    shot=b['visual_analysis']['shots'][0]
    shot['aspects']['typography']['status']='absent'
    shot['aspects']['interaction']['status']='observed'
    event=shot['events'][0]
    event['type']='interaction'
    event['anchor']={'render_mode':render_mode,'binding':binding,'interaction_mode':mode}
    event['required_capabilities']=list(capabilities)
    return b


def test_fixed_text_temporal_sync_does_not_require_spatial_tracking():
    guards.validate_visual_analysis(interaction_brief('temporal_sync'))


def test_subject_follow_requires_subject_binding_and_tracking():
    with pytest.raises(ValueError):
        guards.validate_visual_analysis(interaction_brief('subject_follow',binding='subject'))
    with pytest.raises(ValueError):
        guards.validate_visual_analysis(interaction_brief('subject_follow',capabilities=['object_tracking']))
    guards.validate_visual_analysis(interaction_brief('subject_follow',binding='subject',capabilities=['object_tracking']))


def test_occlusion_requires_masking_independent_of_screen_binding():
    with pytest.raises(ValueError):
        guards.validate_visual_analysis(interaction_brief('occlusion'))
    guards.validate_visual_analysis(interaction_brief('occlusion',capabilities=['masking']))


def test_interaction_3d_still_requires_render_execution():
    with pytest.raises(ValueError):
        guards.validate_visual_analysis(interaction_brief('temporal_sync',render_mode='3d'))
    guards.validate_visual_analysis(interaction_brief('temporal_sync',render_mode='3d',capabilities=['3d_rendering']))
