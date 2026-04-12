# Deep Research on Open-Source and Academic Tools for Basketball AI Vision

## Executive summary

Building a commercial basketball player/ball analytics product primarily from open-source components is feasible today, but only if you carefully choose licenses, sports-specific datasets, and tracking + calibration components. The most production-ready path is to assemble a pipeline from permissively licensed "foundation" toolkits (detection, pose, video understanding, OCR, evaluation) and then add sports-specific modeling + data. In practice, the biggest blockers are not model availability but (a) basketball ball tracking reliability (tiny object, motion blur, frequent occlusion), (b) identity persistence across heavy contact/occlusion and camera cuts, and (c) court registration robustness under variable camera angles, lens distortion, and partial court visibility.

Licensing constraints materially change the shortlist. Popular end-to-end "easy" stacks like Ultralytics YOLO are distributed under AGPL-3.0 (with an enterprise alternative), which is frequently incompatible with proprietary SaaS unless you open-source your whole service or obtain a commercial license. Similarly, several strong pose systems historically used in research (e.g., OpenPose, AlphaPose) are non-commercial without separate licensing.

A pragmatic MVP stack for a proprietary product is therefore: Apache/MIT/BSD detection + tracking + pose + action recognition frameworks (e.g., MMDetection / YOLOX / RT-DETR + ByteTrack / BoT-SORT / OC-SORT + MMPose/ViTPose + MMAction2/SlowFast/PyTorchVideo + Tesseract/MMOCR + TrackEval).

## Inventory of open-source projects and repos

### High Production Readiness (Apache-2.0 / MIT / BSD)

- **OpenCV** (Apache-2.0) — Geometry, vision utilities, homography, camera calibration
- **MMDetection** (Apache-2.0) — Detection/instance segmentation framework, strong model zoo
- **Detectron2** (Apache-2.0 code) — Detection/segmentation, weights may have separate license terms
- **YOLOX** (Apache-2.0) — Real-time object detector, good non-AGPL YOLO alternative
- **RT-DETR** (Apache-2.0) — Real-time DETR-style transformer detector
- **ByteTrack** (MIT) — Multi-object tracking, "associate every detection" strategy
- **BoT-SORT** (MIT) — Tracking with appearance + camera motion compensation
- **OC-SORT** (MIT) — Occlusion/nonlinear motion robustness, extremely fast
- **Norfair** (BSD-3-Clause) — Lightweight detector-agnostic tracking glue
- **TrackEval** (MIT) — HOTA/IDF1/MOTA evaluation, MOTChallenge official kit
- **MMPose** (Apache-2.0) — Human pose estimation framework
- **ViTPose** (Apache-2.0) — Strong transformer pose baselines
- **MMAction2** (Apache-2.0) — Video action recognition/detection
- **PySlowFast** (Apache-2.0) — SlowFast-family action models
- **PyTorchVideo** (Apache-2.0) — Video models library
- **PySKL** (Apache-2.0) — Skeleton action recognition
- **MMOCR** (Apache-2.0) — OCR toolkit
- **Tesseract** (Apache-2.0) — OCR engine, mature

### Medium Readiness / License Caution

- **Ultralytics YOLO** (AGPL-3.0) — Enterprise licensing exists; AGPL implications significant for SaaS
- **DeepSORT** (GPL-3.0) — GPL complicates proprietary distribution
- **KpSFR** (MIT) — Soccer-focused field registration, concepts transfer to courts
- **KaliCalib** (CeCILL 2.1) — Basketball court registration, CeCILL can behave GPL-like
- **ScoreboardOCR** (MIT) — Narrow but relevant for venue scoreboard reading
- **basketball_detector** (GPL-3.0) — Useful reference only

### Low Readiness for Commercial Use

- **OpenPose** — Non-commercial license, commercial license required
- **AlphaPose** — Non-commercial license, commercial queries require separate agreement
- **sportsfield_release** — Custom research license agreement
- **DeepSportLab** (AGPLv3+) — Strong conceptually but AGPL impacts SaaS
- **deepsport** (CC BY-NC-ND 4.0) — Non-commercial + no-derivatives

## Key academic papers and datasets

### Basketball-Specific

- **DeepSportradar-v1** — Most product-adjacent public basketball CV resource: ball 3D, camera calibration, instance segmentation, player ReID
- **DeepSportradar camera-calibration-challenge** — 728 labeled pairs, baseline pipeline for court calibration
- **KaliCalib** — Practical basketball court registration approach (CeCILL 2.1)
- **APIDIS dataset** — Classic multi-view basketball tracking (explicitly non-commercial research only)
- **SpaceJam dataset** (MIT) — Basketball single-player action recognition with skeleton data
- **BARD dataset 2026** (CC BY 4.0) — Basketball action recognition with contextual annotations
- **BASKET dataset (CVPR 2025)** — Fine-grained basketball skill estimation, gated access, no license text visible
- **TrackID3x3** — Multi-player tracking + identification in 3x3 basketball

### Tracking Papers

- **ByteTrack** (MIT) — Strong baseline for fluctuating detections
- **BoT-SORT** (MIT) — Motion + appearance + camera motion compensation
- **OC-SORT** (MIT) — Non-linear motion robustness
- **HOTA metric + TrackEval** (MIT) — Balanced detection + association evaluation

### Other Relevant

- **MultiSports (ICCV 2021)** (CC BY-NC 4.0) — Spatio-temporal action detection, likely not usable commercially
- **Sports ball detection & tracking (BMVC 2023)** — Ball-specific failure modes and evaluation

## Gaps between open-source and production

1. **Ball tracking** — Sharpest gap. Basketball-specific implementations often AGPL or non-commercial
2. **Identity persistence** — Needs basketball-specific ReID (jersey numbers, team colors, pose cues)
3. **Court registration** — Workable but brittle without sport-specific data and QA
4. **Phone capture robustness** — Most datasets skew toward broadcast; phone footage has blur, auto-exposure flicker, framing failures
5. **Multi-camera fusion** — Mostly academic, not plug-and-play

## MVP integration plan

### Component flow

Video input → Frame sampler → Detector (players + ball + hoop) → Multi-object tracker (player IDs + trajectories) + Ball track module → Court keypoints → Homography/calibration → Feature builder (speed, spacing, zones) → Event layer (heuristics + classifiers) → Storage/API

### Recommended commercial-friendly components

| Module | Choice | License | Effort |
|--------|--------|---------|--------|
| Player/ball/hoop detection | YOLOX or RT-DETR via MMDetection | Apache-2.0 | Medium |
| Player tracking | ByteTrack first; BoT-SORT for occlusion | MIT | Medium |
| Ball tracking | Detection + temporal filtering + reacquire logic | — | High |
| Court mapping | Keypoints → homography via OpenCV | Apache-2.0 | Medium/High |
| Pose estimation | MMPose / ViTPose | Apache-2.0 | Medium |
| Action/event detection | MMAction2 or PySlowFast or PyTorchVideo | Apache-2.0 | High |
| Score/clock OCR | Tesseract or MMOCR | Apache-2.0 | Medium |
| Evaluation | TrackEval + custom ball & event metrics | MIT | Medium |

## Data and labeling needs

### Fine-tuning dataset targets (heuristic)

- Player detection: few thousand labeled frames
- Ball detection: order of magnitude more instances than frames
- Court keypoints: low-thousands of labeled images
- Events/actions: 2,000-5,000 instances per major category

### Annotation schema adjustments needed

- Ball: bbox + visibility flag + in-hand/in-air state
- Players: bbox + track ID + team color + jersey number
- Court: keypoints + quality flag for partial visibility
- Events: clip-level labels + actor IDs

### Key augmentation strategies

- Ball: motion blur, exposure flicker, random occluders, scale jitter
- Court: random homographies, lens distortion, partial crops, varying floor colors
- Track stability: simulate dropped detections and short occlusions

## Licensing, IP, and compliance risks

### Software license pitfalls

- **AGPL-3.0**: Network service triggers source disclosure (Ultralytics YOLO)
- **GPL-3.0**: Copyleft on distribution (basketball_detector, DeepSORT)
- **Non-commercial**: OpenPose, AlphaPose block commercial use by default
- **Missing license**: No right to use/modify/distribute

### Dataset licensing pitfalls

- APIDIS: explicitly non-commercial research
- MultiSports: CC BY-NC 4.0, prohibits commercial use
- BASKET: gated access, no visible license
- Multiple audits show dataset compliance cannot be assumed from a single license tag

## Evaluation metrics

| Layer | Metrics | Tool |
|-------|---------|------|
| Detection | Precision/Recall, mAP@IoU | COCO-style evaluation |
| Tracking (players) | HOTA, IDF1, MOTA, ID switches | TrackEval |
| Ball tracking | Ball recall, track continuity, re-acquisition time | Custom metrics |
| Court mapping | Reprojection error, % frames with valid homography | Custom evaluation |
| Event detection | Event F1 (macro + micro), temporal IoU | Sports action detection benchmarks |
| System | End-to-end latency, throughput (fps), GPU utilization | Per-stage profiling |

## Implementation timeline

### Proof of concept (Weeks 1-4)
- Week 1-2: Data ingestion + baseline detector + simple tracker
- Week 3-4: Court mapping v1 + trajectory features + evaluation harness

### Pilot (Months 2-4)
- Month 2-3: Ball tracking hardening + event classifier v1 + OCR/score alignment
- Month 3-4: Labeling workflow + domain fine-tuning + coach-facing exports

### Production (Months 5-9)
- Month 5-6: Scale testing + monitoring + model/version governance
- Month 6-9: Multi-camera support + identity persistence + sport expansion prep

### Resource estimates
- POC: 1 CV/ML engineer + 1 software engineer; part-time annotator
- Pilot: 2 CV/ML engineers + 1 platform/backend engineer; consistent labeling
- Production: add 1 MLOps/infra specialist + (optional) data engineer
