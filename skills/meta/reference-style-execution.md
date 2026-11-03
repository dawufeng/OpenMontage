# Reference Style Execution Contract

Use this skill for reference-inspired production, alongside the selected pipeline's existing directors. This is a production data contract; it does not replace manifest approval gates or the advisory reviewer.

## 1. Analyze actual visual input

Discover `reference_analyzer` through the registry. Pass explicit `project_dir` and a source within that project; download authorized remote inputs with the existing downloader first. Never write into another project or rely on the working directory.

Use the tool's `prepare` operation for bounded temporal sampling; use `analyze` only with an explicitly selected, already installed visual model supported by the tool. Do not silently download a model or transmit frames to an unapproved service. If the agent's native vision is available, actually read the sampled image contents in timestamp order, then use `enrich` to validate and persist its observations. A file path by itself is not visual input. If image reading fails, retain `incomplete` and report the vision gap; do not fill fields from generic domain knowledge.

For each shot, cover subject, subject_motion, scene, spatial_framing, camera, typography, interaction. Distinguish observed, absent, and unknown. Tie every observation to actual sampled timestamps in that shot. A single frame cannot establish an action or actor/text synchronization. Request bounded second-pass samples around uncertain events. Never claim the original was AI-generated based on optical flow.

Text is a separate layer. Record thickness, perspective, shadow and motion when visible. Distinguish ordinary subtitles from headline/3D typography. Record chronological gesture/text/interaction events, anchors and required execution capabilities. Do not hide an unknown interaction by marking it absent.

Only a validated ready `video_analysis_brief.visual_analysis` authorizes production planning to finish. Preserve the source structure and frame evidence. Report sampling/model/call-budget failures explicitly.

## 2. Plan without dropping effects

The agent authors `artifacts/reference_style_plan.json` against its registered schema. Map every reference shot to an actual target scene ID and every detected event to preserve_event_ids. Include subject_generation descriptions, separately authored text overlays for text events, and the required tracking/mask/3D capabilities. Carry timing and layout constraints into scene_plan, asset prompts and edit decisions; preserve event IDs instead of re-parsing prose.

Check available capabilities through registry tool contracts and actual execution support. Strings in available_capabilities are a shortlist, not proof that tracking or rendering occurred. Do not promise a palm-anchored 3D effect merely because generic overlays or a face tracker exist. If an essential effect has no supported adapter, surface the missing capability and keep production blocked until the approved path is available or the user explicitly changes the creative requirement.

## 3. Generate the target layers

Use the selected pipeline and its existing manifest approval gates. Subject video generation owns the gesture and layout; typography owns exact readable text, depth and animation. Re-detect target gestures and anchors in the generated footage: reference-video coordinates and timing cannot be reused blindly. Preserve output-specific execution evidence for any tracking, masks or 3D layers. Ordinary subtitle burn is not an implementation of gesture-linked typography.

The production checkpoint and video_compose enforce the reference contract. Do not bypass a blocked result with an ad-hoc FFmpeg script, a different runtime, or manual artifact edits. Low-level trims/encodes do not certify a reference recreation.

## 4. Review a staged output before promotion

Render a new candidate without overwriting the approved playable video. Inspect actual candidate frames across every retained event; compare exact text, gesture timing, visible text depth, movement and occlusion against reference evidence. A structural check or a file hash only verifies provenance, not visual fidelity. Discover `reference_reviewer` through the registry and call it with explicit project_dir, the reference_candidate_path returned by video_compose as output_path, and the approved existing local visual model. It sends actual reference and candidate frame pixels to the visual backend and writes candidate-specific evidence. If no local model is available, the agent may inspect actual frames using its native vision and supply the same grounded review contract; it must not invent model results. Write `reference_style_review` with passed/failed/unknown per event and candidate-specific evidence as required by its schema and validator. A shot with no retained events still needs a whole-shot check with event_id `__shot__`; an empty report is not a successful recreation.

Missing, failed or unknown observations cannot be marked passed. On failure, report the affected shot/event and retry only that approved unit within budget. Keep the prior final version intact. Only a complete, validated review of this exact output can authorize candidate promotion and compose completion. Do not edit is_latest directly to bypass the checks.


After a validated review, use `lib.reference_style.publish_reference_candidate(project_dir, candidate_path, output_path)` to promote the retained candidate to its requested deliverable path. This utility verifies evidence, acquires a bounded project publication lock, and compensates a failed metadata write. Do not rerender a candidate merely because video_compose returned awaiting_review. Then write the compose checkpoint against the published output. This utility does not update business DB latest records or replace a pipeline's human approval gates.


## Bounded target-region tracking

After generating a target shot, discover `object_tracker` and inspect its input contract. An actual observation of the target clip supplies initial_bbox in pixel [x,y,width,height] and the target PTS interval; never copy reference-video coordinates or claim this is semantic hand detection. This local LK translation tool has strict clip/frame/pixel budgets, does not download models, and reports lost when texture or tracking is insufficient.

Store the generated receipt below artifacts/tracking, bind it to target_video_path and the corresponding event_ids in reference_style_plan.shots[].execution_receipts, and register that same video under its scene_id in asset_manifest. The reference execution verifier recomputes the trajectory from target pixels instead of trusting a capability name or a complete string. A valid receipt may satisfy the assets-stage tracking requirement. It does not prove that an overlay consumed the trajectory: edit/compose/publish remain blocked until there is a verified trajectory-consumption adapter. Masking and 3D requirements remain blocked if their real executors are unavailable.
