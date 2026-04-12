# NextBallUp: Comprehensive Validation and Strategic Research Report

## Overview

This report validates the two research documents (Blueprint and Open-Source Deep Research) against current market data, identifies factual corrections, missing competitors, updated OSS tools, startup foundation requirements, and assesses readiness for Claude Code repository scaffolding.

## Competitive Landscape Corrections

### Three Critical Corrections

1. **Second Spectrum was acquired by Genius Sports for $200M in 2021** — not by Catapult Sports as implied. The NBA restructured tracking for 2023-24: Sony's Hawk-Eye Innovations handles optical tracking (14 cameras per arena, 29 body points per player in 3D), while Second Spectrum (Genius Sports) was retained as analytics/broadcast augmentation provider. Second Spectrum is developing "Dragon," a next-gen synthesis platform.

2. **Catapult Sports** is a separate company focused on wearable athlete monitoring (GPS, accelerometers) with a $684M market cap and 4,600+ elite teams — never in basketball vision analytics.

3. **Synergy Sports is no longer independent.** Acquired by Atrium Sports (December 2019, $90M), subsequently absorbed into Sportradar Group.

### Missing Competitors

- **PlayVision** — Confirmed YC F25, 2-3 person team, launched 2025-26 season. Partners with SEC, Big 10, Big East. Pricing confirmed ($29.99/$49.99).

- **SportsVisio** — Better-funded than documents suggest. Raised $3.2M in June 2025 (total >$9M). Investors include Sony Innovation Fund, Sapphire Sport. ARR grew $10K to $800K in 12 months. New Coach Mode at $750/20-game pack.

- **Ballin AI** — Tulsa-based, $1.27M raised, 13 staff. AI film analysis with "BASE" metric system for recruiting.

- **Veo** — Danish AI camera company, $113.3M funded. Dual 4K panoramic cameras, auto-tracking and highlights. Entering basketball aggressively but lacking deep stats.

- **NBA + AWS Partnership** (October 2025) — Multi-year deal building exactly the "hidden impact" metrics NextBallUp targets: Gravity (defensive pressure), Shot Difficulty (xFG%), Defensive Box Score, Play Finder (generative AI search over millions of plays). Validates product thesis but means NBA-level teams already have access.

- **Swish Basket** — Selected for NBA Launchpad 2026, camera + lidar shooting analytics.
- **Pixellot** — AI auto-tracking cameras.
- **Peripheral Labs** — Spatial intelligence, NBA Launchpad 2026.

## Updated OSS Stack (2026)

### Detection: RF-DETR replaces YOLOX

RF-DETR (Apache-2.0, March 2025, ICLR 2026) from Roboflow — first real-time detector to exceed 60 mAP on COCO. DINOv2 vision transformer backbone with deformable attention decoder. Exceptional transfer learning for basketball fine-tuning.

Grounding DINO (Apache-2.0) added for zero-shot detection and auto-labeling. Combined with SAM 2 enables Grounded SAM 2 pipeline.

### Tracking: BoxMOT is the new meta-framework

BoxMOT provides pluggable access to ByteTrack, BoT-SORT, OC-SORT, DeepOCSORT, StrongSORT plus built-in ReID model zoo and hyperparameter tuning.

McByte (CVPR 2025) extends ByteTrack with mask propagation — outperforms all methods on SportsMOT without training data.

### Jersey OCR: SmolVLM2 replaces Tesseract

Fine-tuned SmolVLM2 dramatically outperforms traditional OCR for jersey number recognition on basketball footage.

### Pose: ViTPose++ confirmed SOTA

Now available through HuggingFace Transformers (January 2025). Lowest MPJPE of 0.192m on athletic movements.

### State of the Art Advances

- **Sports-specific foundation models** (SoccerMaster, MatchVision) consistently outperform task-specific experts
- **BioPose** (WACV 2025) — biomechanically accurate 3D pose from monocular video
- **AthletePose3D** (CVPR 2025) — fine-tuning reduces pose error 70% on sports data
- **TrackNetV5** — Motion Direction Decomposition achieves F1 0.9859 for ball tracking
- **PnLCalib** (CVPR 2025) — 3D camera calibration replacing simple homography

## Startup Foundations

### Formation
- Delaware C-Corp via Stripe Atlas ($500) or Clerky ($799)
- IP: blended trade secret + selective patent strategy
- Day-one documents: IP Assignment, CIIA, NDAs, SAFE notes

### Cloud Credits (stackable to $600K+)
- NVIDIA Inception Program (free, no equity)
- Google Cloud AI track (up to $350K)
- AWS Activate Portfolio (up to $100K)
- Microsoft for Startups (up to $150K)

### Monthly Operating Costs (pre-revenue): $600-$2,200

### Fundraising
- Pre-seed: $7.5-8M post-money SAFE caps, $500K-$2M raises
- Seed: $16-20M pre-money, $3-5M raises
- AI startups captured ~50% of all global VC in 2025 ($202.3B)

### Key Tools
- GitHub, Lambda Labs/RunPod for GPU, Weights & Biases, DVC, uv
- Mercury (banking), Carta (cap table), Linear (PM), Notion (docs)

## Pricing Validation

All competitor pricing confirmed. ShotTracker facility installation ~$45,000.

Unit economics: $3-$15 compute cost per game → 55-85% gross margins at $34/game.

TAM: $40M-$230M across NBA, D1, HS, club programs.

Series A target: $1M+ ARR within 18-24 months.

## Repository Readiness Assessment

Documents rated 7/10 for strategy, 2/10 for code generation. Seven supplementary specification documents created to bridge the gap:

1. CLAUDE.md — Architecture, conventions, constraints
2. API_SPEC.md — 50+ endpoints with schemas
3. DATABASE_SCHEMA.md — 18 SQLAlchemy ORM models
4. FRONTEND_ARCH.md — Next.js 15 architecture
5. PRD.md — 11 feature specs with MVP boundary
6. pyproject.toml — uv workspace configuration
7. docker-compose.yml — Local development services

With these additions, Claude Code can scaffold a functional monorepo.
