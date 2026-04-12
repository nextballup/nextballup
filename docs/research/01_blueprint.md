AI Vision Company Blueprint for Basketball Player Analysis
Executive summary
A modern "hidden impact" basketball evaluation product is fundamentally a video-to-structured-data platform: ingest practice/game footage, derive player + ball + court state over time, and translate that into events, context, and predictive/derived metrics that coaches and recruiters can use quickly and trust in high-stakes decisions. The most credible proof that this is both possible and valuable is that top leagues already invest in optical tracking + derived insights (e.g., shot probabilities, off-ball metrics, pose tracking), and vendors compete on the quality and usability of the resulting "machine understanding" of games. 

A practical company-building path (especially for teams/academies) is batch-first with near-real-time turnaround, then expand into true real-time. In 2026, consumer-facing products in this space commonly accept uploaded video and return stats/highlights (SportsVisio), while higher-end platforms monetize deep tagging and scouting workflows (Hudl, Synergy) and elite tracking ecosystems exist at league scale (Hawk-Eye/Second Spectrum; sensor stacks like ShotTracker). 

Your differentiator can be framed as: "Decision intelligence from movement + tendencies"—not just box scores, not just highlight reels. A defensible wedge is a curated set of conversion-rate metrics (play outcome likelihood conditional on movement patterns), Spatial IQ (space creation/usage under constraints), and predictive features (shot quality, pass risk, rotation anticipation) that are tightly tied to coach workflows and recruiting questions. League references show the direction of travel: richer tracking, including pose, enables new feature sets and new metrics. 

Operationally, your biggest existential risk is data quality scaled across diverse gyms (lighting, camera angles, occlusions, non-standard capture). Research and competition benchmarks show basketball is hard: fine-grained understanding lags human performance, and robust court registration/homography is a core enabling technology. 

Reference notes file (provided in this workspace): Pasted text.txt

Competitive landscape and differentiation
The market splits into (a) auto-stat/highlight tools for youth/HS/club, (b) video + human/assisted breakdown plus recruiting management, (c) elite tracking & scouting sold enterprise-to-pro/major programs, and (d) skill-training apps. Your product can bridge (a) and (b) with a stronger "tracking-first" analytics layer, then graduate toward (c) for higher tiers.

Competitor comparison table
Competitor	Segment	What they emphasize	Pricing signals (public)	Strengths	Weaknesses / openings for you
PlayVision	College/pro + scouting workflows	Computer vision tracking, automatic tagging, "1M+ data points per game," recruiting/portal database; "Backed by Y Combinator." 	$29.99/month portals; $49.99/month "Pro Suite" shown publicly. 	Clear "AI Moneyball" narrative; tracking-first positioning; strong marketing. 	Mostly positioned as portals + generalized analytics; opportunity to own coach-specific "hidden impact" metrics + on-court decision coaching; also a chance to win in youth/academy with workflow + price + faster turnaround.
SportsVisio	Youth/HS/club (basketball + volleyball)	Upload game film → automated stats + highlights; explicitly markets both basketball and volleyball "pro-style stats." 	Blog lists $34/game, $199/month, and multi-game packs (promo-based). 	Clear value prop: "stats + highlights quickly," multi-sport traction, funding coverage. 	Opportunity: differentiate on deeper movement/tendency features, predictive analytics, and recruiter-grade reporting (not only box-score automation). 
Hudl (+ Assist, Instat, Sportscode)	Ubiquitous across HS/club/college/pro	End-to-end video platform: capture, analyze, share; Assist for detailed breakdown reports; Instat for analysis/scouting; Sportscode for elite coding/workflows. 	Example public pricing: club basketball subscriptions $400–$1,600/yr (tiered); HS packages $1,500–$4,000/yr; Assist packages for a state association example $399–$999 for 10–30 games plus subscription requirements. 	Huge distribution + "system of record" for video; entrenched workflows; many sports. 	Opportunity: win where coaches want automatic tracking-derived insights (movement, spacing, predictive) without heavy manual coding, and provide an API/SDK layer that complements existing Hudl libraries rather than competes head-on. 
Synergy Sports	Pro/college + media	Deep possession-centric tagging & stats views (play types, shot types, PPP formulas) and APIs marketed as very granular. 	Pricing not broadly public; sold as premium/enterprise in practice. 	Trusted for play-type analytics; definition rigor (PPP/possession taxonomy). 	Opportunity: attach "tracking-first biomechanics + decision" features (pose-based initiation cues, defender distance, advantage creation) that go beyond classic play-type tagging. 
Second Spectrum	League/enterprise	Official tracking & analytics engine for major leagues; associated with advanced optical tracking insights + broadcast augmentation; NBA uses Hawk-Eye for raw tracking while retaining Second Spectrum as analytics/augmentation provider. 	Enterprise/league contracts (not public SaaS pricing). 	Best-in-class scale + accuracy signals; league-grade data products. 	Not accessible for most teams; you can productize "league-like" insights for academies/colleges with lower-cost capture sources. 
ShotTracker	College/pro; practice + game	Sensor-based real-time team stats and analytics; emphasizes instant access/streaming analytics. 	Pricing mostly "demo/quote." 	Real-time workflow; strong fits for facilities and programs that can install hardware. 	Opportunity: a camera-first product can be adopted by more programs (less install friction), and can add richer "movement/tendency" outputs if tracking is robust. 
Noah Basketball	Shooting-focused; facilities	Shot tracking (arc/depth/left-right), installs at scale; a "Noahlytics" data service shows a hardware + subscription shape. 	Example published: $4,800 activation (two systems) + $100/month data charge (HS example). 	Clear outcome (shooting) with measurable metrics; facility adoption claims.	Narrower than full player-impact analysis; your opening is holistic "5-on-5 decision intelligence," not just shot mechanics. 
HomeCourt	Individual skill development	On-device training analytics; subscriptions via app stores and team pricing. 	$7.99/month or $69.99/year (public). 	Consumer distribution + athlete engagement; strong training UX. 	Not a team tactical product; your opening is team-level film, opponents, scouting reports, and recruiter workflows. 

Differentiation thesis (recommended). Use a two-layer positioning:

"Auto film → stats + clips" parity layer (must-have to compete with SportsVisio/Hudl expectations). 
"Hidden impact engine" premium layer: tracking-derived conversion rates, Spatial IQ, predictive features, and automatic tagging built around coach questions (e.g., "Which off-ball cuts create the highest expected points?" "Which defenders 'shrink the floor' without steals/blocks?"). This aligns with the direction of league tracking products that explicitly mention shot probabilities and off-ball metrics. 
Technical architecture and stack
A robust end-to-end system for your product typically has three pipelines: (1) capture/ingestion, (2) vision inference + feature computation, (3) product delivery (search, dashboards, clips, APIs). Standards and vendor docs strongly suggest designing for both streaming (WebRTC/SRT) and chunked playback (HLS) patterns, because coaches want fast review and teams increasingly want live or near-live experiences. 

This blueprint references Amazon Web Services primitives for managed video ingest/playback and object storage; GPU acceleration and inference tooling from NVIDIA (DeepStream/TensorRT/Triton); and protocol standardization from the IETF (HLS RFC) and W3C (WebRTC). 

System architecture diagram
mermaid
Copy
flowchart LR
  A[Capture: phone/cam + optional scoreboard feed] --> B[Upload/Stream Gateway]
  B --> C[Object Storage: raw + mezzanine video]
  B --> D[Realtime Stream Bus (optional)]
  C --> E[Transcode + Segmenter]
  E --> F[Playback CDN + HLS/DASH manifests]

  C --> G[Batch Vision Inference Queue]
  D --> H[Realtime Vision Inference (edge or cloud)]

  G --> I[Tracking + Court Registration]
  H --> I

  I --> J[Event Detection + Auto-tagging]
  I --> K[Pose + Kinematics Features]
  I --> L[Derived Metrics + Predictive Models]

  J --> M[(Relational DB: games/players/events)]
  K --> N[(Feature Store/Analytics DB)]
  L --> N

  M --> O[Search + Clip Builder]
  N --> O

  O --> P[Coach/Recruiter App: dashboards, alerts, reports]
  O --> Q[Partner APIs/SDKs]
Technical stack table
Layer	MVP recommendation	Scale recommendation	Why it fits basketball tracking + tagging
Capture	iOS/Android app + "tripod baseline" guidance; accept any camera if file upload works	Optional multi-cam rigs for academies; edge mini-PC for stream relay	Basketball robustness improves with stable framing; but adoption depends on low friction. Court registration research explicitly targets broadcast-like constraints where homography is foundational. 
Transport	Upload-first; optional live via WebRTC for sub-second interactive streams	Add SRT for resilient long-haul contribution feeds	WebRTC is standardized for real-time media APIs; SRT emphasizes encrypted, low-latency transport with jitter/loss handling. 
Playback	HLS for clips, playlists, "instance search"	Low-latency HLS variants when needed	HLS is defined by RFC 8216 and is widely used for adaptive streaming. 
Managed video ingest	Kinesis Video Streams (WebRTC ingestion + HLS playback patterns)	Add edge recording + scheduled upload when bandwidth is limited	AWS documents WebRTC ingestion for real-time streaming into cloud storage and HLS/DASH playback. 
Storage (video)	Object storage: "raw," "mezzanine," "derived clips"	Tiered lifecycle: hot → warm → archive	S3 storage classes and Glacier options are designed for tiering; Deep Archive retrieval is asynchronous with longer restore times. 
Metadata DB	PostgreSQL for core entities (teams, games, rosters, events, permissions)	Separate analytics DB for high-volume time-series + event logs	Clean relational integrity is crucial for recruiting/coaching workflows (who/what/when/permissions). This is a standard architectural pattern for event-rich products. 
Annotation	CVAT for multi-object tracking + keyframes; Label Studio for flexible UIs and exports	Add active learning loops + QA tooling; vendor workforce for bursts	CVAT and Label Studio both support video workflows and keyframe interpolation; CVAT cites 5–10× speedups vs frame-by-frame. 
Core models	Detection → tracking → court registration → event detection; start with batch inference	Add streaming inference for live; expand to pose + predictive models	Modern SOTA building blocks exist: tracking papers (ByteTrack), action models (SlowFast, VideoMAE), pose (OpenPose). 
Real-time pipeline	Prototype with DeepStream for multi-stream decode + inference + tracking	Edge deployment (Jetson/RTX) + cloud autoscaling	DeepStream explicitly provides GPU-accelerated ingest, multi-camera tracking pipelines with message broker hooks to cloud. 
Inference serving	Triton for batch/online microservices; optimize with TensorRT	Multi-model, multi-tenant serving with gRPC/HTTP + versioned model repos	Triton standardizes model repositories and exposes HTTP/gRPC; TensorRT docs focus on inference performance optimization. 
APIs/SDKs	REST/GraphQL for product; signed URLs for video; webhook exports	Partner SDKs; "data products" APIs	Partners want structured outputs (events, clips, profiles). The existence of Synergy APIs shows strong demand for programmatic access. 


Homography Matrix. Did you know that Artificial… | by Abel Joshua Cruzada | Medium
IVI Lab @ GT
Project: Basketball Game Tracking with Multi-Camera Networks | Gaurav Raj Singh posted on the topic | LinkedIn
Frontiers | A Video-Based Framework for Automatic 3D Localization of Multiple Basketball Players: A Combinatorial Optimization Approach
Machine Learning in Sports Analytics | Catapult

Data requirements and labeling schema
Your product needs to turn video into a unified internal representation: (A) space-time tracks, (B) events, and (C) context (game state, lineup, score/clock if available). League tracking references illustrate the ambition: pose tracking and off-ball metrics become feasible when the system knows where bodies and the ball are at high frequency. 

What you must store
Raw video. Keep original uploads for reprocessing and model iteration; store "mezzanine" versions (standardized resolution/bitrate) for consistent inference and for clip generation. HLS-style chunking and managed playback patterns make downstream review and clip assembly easier. 

Court mapping. A stable mapping from image coordinates → court coordinates is a keystone. Basketball court registration frameworks and sports-field registration research treat this as a homography estimation problem that enables projection of detections/tracks to a canonical field model. 

Tracks (players + ball). Your core "truth table" is typically: frame/timestamp → object_id → position (image + court), velocity, acceleration, and (optionally) pose keypoints. Tracking and multi-camera work in basketball is well-studied, but occlusions and identity persistence remain hard parts in single-view settings. 

Events and possessions. Coaches reason in possessions and actions. Synergy's public glossary illustrates that "points per possession" style measures are definitional primitives for many coaching workflows. 

Derived / predictive metrics. "Shot probabilities" and "off-ball metrics" are explicitly cited as outcomes of optical tracking deployments in league contexts—use this as a north star for your metric roadmap. 

Recommended labeling schema (basketball)
The table below is designed to be implementable in commercial annotation tools and exportable to downstream training (object detection, tracking, action localization, prediction). It is intentionally modular: you can ship MVP value before full pose/biomechanics is perfect.

Annotation domain	Schema objects	Key fields (examples)	What it trains/enables	Suggested tooling
Game segmentation	game, period, possession	start/end timestamps; offense/defense team; shot clock if known	Possession-level metrics, scouting filters, "per possession" analysis	Light manual + rules; later automate
Entities	player, team, lineup	jersey number (optional), role, handedness, position tags	Player profiles, lineup impact, matchup queries	DB-first; minimal labeling
Court registration	court_keypoints, homography	known court landmarks; per-segment transform	Convert all spatial outputs to court space; Spatial IQ metrics	Specialized calibration workflow
Object detection	bbox / seg	players, ball, rim/backboard; occlusion flags	Inputs to tracking; ball possession inference	CVAT / Label Studio 
Multi-object tracking	track	persistent IDs; keyframes; interpolation	Player movement, speeds, distances; trajectories	CVAT track mode 
Pose (optional early)	keypoints_2d (or 3D later)	joints, confidence, visibility	Movement biomechanics, planting foot cues, change-of-direction signals	Model-assisted; sparse manual QA
Events	event	shot attempt/make/miss, pass, turnover, rebound, foul; x/y location; actor(s)	Auto-tagging timelines; clip generation; conversion rates by action	Label Studio templates; guided UI
Tactical tags	play_tag	P&R, DHO, flare, stagger, zone/man, help rotation	Search, scouting reports, opponent tendencies	Human-in-the-loop; scale later
Derived metrics	metric_def + metric_series	PPP by tag; spacing score; pass risk; advantage created	"Hidden impact" dashboards/alerts	Computed (not hand-labeled)

Annotation workflows and estimated labeling effort
Workflow shape (recommended). Use model-assisted labeling and keyframe interpolation from day one. CVAT explicitly frames track interpolation as a major time saver (5–10× vs frame-by-frame), and Label Studio supports exporting interpolated keyframes for downstream training data creation. 

Suggested human effort allocation (MVP). Because full-game frame-level ground truth is extremely expensive, focus on (a) high-value segments and (b) "gold" validation sets:

Court registration "gold set." 200–500 representative clips across gyms/angles to validate homography robustness (especially because court registration is the multiplier for spatial metrics). 
Tracking "gold set." 30–60 possessions per environment archetype (bright gym, dim gym, crowd occlusion, baseline camera, sideline camera) with high-quality player+ball tracks.
Event "gold set." 2,000–5,000 event instances per major category (shot, rebound, turnover, P&R, etc.), because coaches will judge the product on timeline accuracy and clip relevance.
Pose QA set (optional early). 10k–50k frames with keypoint QA rather than full manual pose from scratch; use this to calibrate and select pose models.
Cost levers. If you use active learning / automation in a managed labeling workflow, AWS claims ("up to 70%" cost reduction) as a directional benchmark for how human labeling effort can drop once a model triages easy vs hard cases. Treat this as an achievable target only with strong QA and careful task design. 

Ball tracking is the hard bottleneck. The ball is small/fast, occluded, and often motion-blurred in single-phone capture; this forces either (a) higher frame rate/better camera placement guidance or (b) specialized models and aggressive temporal smoothing. This is why elite tracking stacks invest heavily in capture and precision. 

Infrastructure, cost, and scalability
Cloud vs on-prem/edge
Cloud-first (recommended for MVP). Cloud object storage and managed video ingestion simplify early shipping. AWS documentation highlights managed ingest via WebRTC into Kinesis Video Streams and managed HLS playback patterns; this reduces the amount of custom media plumbing you build early. 

Edge acceleration (recommended for scale and latency tiers). If you want "live bench insights" later, you'll end up pushing at least part of inference to edge GPUs (or doing cloud inference with strict network and cost constraints). NVIDIA DeepStream is explicitly built for GPU-accelerated multi-camera ingest, inference, and tracking, and it provides patterns for sending analytics metadata to cloud services. 

Storage tiers and lifecycle
A sports video company accumulates large data quickly, so tiering is not optional.

Hot (recent uploads / active season): fast reads for clip generation, coach review, debugging.
Warm (season archive, occasional access): lower cost, still accessible without long restore waits.
Cold/archive (legal hold, historical benchmarks): minimize cost; accept asynchronous retrieval.
AWS defines multiple storage classes and notes that Glacier Deep Archive is archival and not for real-time access; retrieval can require restore workflows. Pricing pages provide publicly visible anchors (e.g., Glacier Deep Archive listed at $0.00099/GB-month). 

Latency vs cost tradeoffs
Batch turnaround targets (practical). For youth/academy, "same day" or "within hours" can be competitive; SportsVisio marketing emphasizes quick conversion of video into stats/highlights and prices per game or per month, which aligns with a batch compute model.

Real-time coaching targets (premium tier). WebRTC enables low-latency media exchange in standardized browser/device APIs, but real-time CV still must contend with GPU cost and reliable uplink in gyms. 

Scalability design patterns
Separate media from metadata. Keep video in object storage; keep indexed metadata (events, pointers, embeddings) in databases optimized for query. Then your app loads video via signed, time-bounded URLs and loads analytics via fast API calls.

Inference as an elastic fleet. Use a job queue for batch inference and autoscale GPU nodes. When you adopt an inference server like Triton, you get standardized model repository and protocol management plus HTTP/gRPC interfaces; TensorRT provides performance optimization guidance for deployment efficiency. 

Security, privacy, and legal best practices
Sports performance video is frequently personal data, and when minors are involved it can become high sensitivity. A serious product must treat this as privacy/security-by-design, not "later."

This section references requirements and guidance from European Union (GDPR), California Department of Justice (CCPA overview), the Federal Trade Commission (COPPA), the U.S. Department of Education (FERPA videos/photos guidance), the Illinois General Assembly (BIPA text), the National Institute of Standards and Technology (CSF 2.0), and the AICPA (SOC 2 overview). 

Privacy classification and consent
GDPR. GDPR defines "biometric data" as personal data from specific technical processing of physical/physiological/behavioral characteristics that allow or confirm unique identification. If your product does face/identity recognition or pose-based identification in a way that uniquely identifies a player, you must treat it as potentially regulated biometric processing. 

CCPA/CPRA directionally. California privacy guidance describes personal information broadly as data that identifies, relates to, or could reasonably be linked to a person/household. Video tied to a roster or a profile is plausibly "personal information" in many deployments. 

COPPA risk (youth). If you knowingly collect personal information online from children under 13, COPPA requirements are triggered; the FTC is explicit that COPPA applies to operators directed to children or with actual knowledge. If you sell to youth academies or parent-led teams, you need a COPPA-aware account and consent design. 

FERPA risk (schools). US DOE guidance states that photos/videos can become education records when directly related to a student and maintained by an educational institution (or its agent). If K-12 schools upload and maintain film in your platform, your product can become part of FERPA compliance obligations (access, redaction, disclosure constraints). 

BIPA risk (Illinois and "face geometry"). Illinois BIPA requires a publicly available retention schedule and destruction guidelines for biometric identifiers/information and states a "3 years of last interaction" style timing in its statutory language. If your models store face geometry templates or similar identifiers, get specialized legal review and build retention tooling early. 

Security controls baseline (what buyers will expect)
Framework alignment. NIST CSF 2.0 is designed to help organizations understand and manage cybersecurity risk; aligning policies and controls to CSF categories is a credible baseline for enterprise buyers. 

SOC 2 readiness. A SOC 2 report is an examination of controls relevant to security/availability/processing integrity/confidentiality/privacy. Even if you don't pursue SOC 2 immediately, designing with those control domains in mind reduces future rework. 

Practical minimum controls (recommended). Encrypt in transit (TLS), encrypt at rest, strong tenant isolation (RBAC + least privilege), audit logs, secure key management, backups, and incident response drills. Many managed cloud storage products advertise encryption and compliance certifications, but you still own correct configuration and access governance. 

Video + athlete data legal best practices (non-exhaustive)
You should implement: (1) clear data ownership terms for teams vs players, (2) explicit consent language for capture/upload and for derived analytics, (3) retention controls (team-configurable deletion schedules), (4) controlled sharing (who can export, share links, or generate recruiting pages), and (5) model training governance (decide whether customer data is used to train global models, opt-in vs opt-out, and how you handle deletion requests). These are standard expectations in regulated media/data contexts and become critical under FERPA/COPPA/BIPA risk profiles. 

Product modules, branding, go-to-market, and business model
Coach and recruiter product modules
Your product should be designed as a workflow accelerator: reduce film time, surface insights, and produce shareable recruiting artifacts.

A strong module set (basketball MVP → v1) is:

Video library + timeline. Searchable games/practices with auto-generated timelines (events + tags). This is table stakes versus platforms that already focus on rapid breakdown and playback. 

Auto clips + playlists. One-click clip generation for shot attempts, turnovers, P&R coverages, closeouts, paint touches, etc. (This is explicitly the type of benefit tools like SportsVisio market in "stats + highlights" positioning.) 

Player profiles. Rolling per-player dashboards: usage, efficiency, shot quality estimates, movement profiles, and "tendency cards" (e.g., drive direction under pressure, catch-and-shoot release consistency proxies). Synergy's multi-view "play types / shot types" models show the appetite for such partitions. 

Hidden impact dashboards (your premium wedge). Examples (define precisely as product specs, not marketing):

Conversion rates: outcome probabilities conditional on action state (drive → help rotation → kickout shot quality), anchored in possession efficiency measures (PPP-style baselines). 
Spatial IQ: proposed as a composite of (a) spacing quality, (b) advantage creation (defender displacement), (c) decision latency (time-to-pass/shot after advantage), and (d) off-ball value events (screens, cuts, relocations). League tracking references explicitly mention off-ball metrics as a target. 
Predictive features: shot probability, pass risk, next-action likelihood. League deployments cite shot probabilities as a tracking-derived insight class. 
Alerts. Coach-configured alerts: "opponent switches late on Spain P&R," "player routinely rejects side P&R," "defensive low-man rotation timing drift," etc. This is where automatic tagging becomes a daily habit rather than an occasional report. 

Scouting reports. Auto-generated PDFs/slides: "top actions," "shot chart + quality," "lineup vulnerabilities," "press break tendencies," with embedded clips and stat links.

Integrations. Export to common workflows (CSV events, clip playlists, roster exports). Synergy's promotion of programmatic APIs highlights the demand for structured outputs. 

Branding and naming process and tools
A durable sports-tech brand should be built as a category statement: "vision-based decision intelligence for sport," with a modular naming system that can extend to volleyball and beyond (e.g., "Brand / Brand Court / Brand Scout / Brand IQ").

Name selection constraints (recommended). Short pronunciation, defensible trademark, domain availability, and "future sport" neutrality (avoid names that hard-code "basketball" unless you want to be permanently niche).

Trademark clearance tools / workflow.

Use the United States Patent and Trademark Office trademark search system as part of clearance. 
Use WIPO tools like the Global Brand Database to search international marks; WIPO explicitly frames this database as a starting point before filing internationally. 
Use EUIPO search tools (eSearch plus/TMview) for EU coverage queries. 
For domain checks, use ICANN Lookup and note the industry transition toward RDAP as the successor protocol to WHOIS for registration data. 
(Trademark and domain clearance is jurisdiction-specific and should be reviewed by counsel before filing or large marketing spends.)

Go-to-market channels for teams, colleges, academies
A basketball-first go-to-market can tier as follows:

Academies & AAU / club programs (pilot wedge). They feel "assistant coach time" pain, want highlights for recruiting, and can adopt new tools quickly if upload workflow is simple and price is manageable—this is the positioning that "per game" pricing pages target. 

High school athletic departments. Buying is often package-based; Hudl's HS pricing pages show a program/department structure and storage limits, which implies you should plan packaging by "program" and include storage/retention policy choices. 

College programs and recruiting operations. Competition is intense and workflows are fragmented across video, scouting, and recruiting platforms; the YC launch narrative for PlayVision claims programs can spend very large sums across multiple products, which you can use as a narrative hook if you can consolidate workflow and add analytics depth. 

Channels (recommended). Clinics/conferences, coach association partnerships, recruiting/training influencers, "film breakdown" content marketing, and direct outreach to video coordinators (the operational champions). If you have API/SDK hooks, sell "data products" to recruiting media and league operators over time. 

Business model options and pricing
Given the market signals above, three viable pricing shapes are:

Team subscription (SaaS). Tiered by level (youth/HS/college), with caps on games, storage hours, and "premium analytics" modules—similar to how public pricing is displayed for club/HS tiers in established platforms. 

Pay-per-game + packs. This aligns with SportsVisio's public discussion of per-game credits and packs and is attractive to youth programs. 

Usage-based compute + seats (enterprise). If you eventually support real-time or near-real-time high-resolution multi-cam, pricing needs to track GPU and storage cost drivers (minutes processed, concurrent streams, embedding/search volume). Managed cloud pricing models encourage pay-for-use patterns. 

Roadmap and multi-sport expansion
Basketball-to-volleyball expansion is realistic if you architect around court registration + multi-object tracking + event grammar, and isolate sport-specific rules/taxonomies at the top of the stack. SportsVisio already markets both basketball and volleyball, and Hudl's product ecosystem includes volleyball analytics tools (VolleyMetrics, Balltime), validating that multi-sport expansion is commercially meaningful. 

Roadmap table with milestones, resources, and budget ranges
Assumptions: target market size and exact budget are unspecified; ranges below are planning heuristics for a venture-backed or revenue-funded early company.

Stage	Duration	Primary goal	Deliverables	Team (typical)	Budget drivers
MVP	12–16 weeks	Upload → stats + basic auto-tagging + clips	Ingest + playback; player/ball detection+tracking; basic events (shots, rebounds, turnovers); player profiles; clip export	1 PM, 2–3 CV/ML, 2 backend, 1 frontend, 0.5 design, 0.5 QA	Labeling + GPU inference; storage; initial security posture
Pilot	3–6 months	Prove "hidden impact" metrics with 3–10 programs	Court registration stability across gyms; conversion-rate metrics; Spatial IQ v0; coach dashboards + alerts; QA workflow	Add 1–2 data/ML + 1 customer success + part-time legal/privacy	Annotation throughput; compute autoscaling; support load
Scale	6–12 months	Multi-tenant platform + integrations	API/SDK; SOC2 readiness; advanced tagging; search at scale; pricing tiers	Add SRE/platform, security lead, sales lead	Compliance program; reliability; CAC spend
Expansion	12–18 months	Volleyball v1 + additional sports readiness	Volleyball event grammar; volleyball-specific metrics; shared core tracking stack	Add sport specialist + extra annotators	New labels, new QA, new model tuning

Branching to volleyball: what changes and what stays
What stays (core platform). Video ingest/playback, object storage tiering, annotation infrastructure, model serving (Triton/TensorRT), and the general "detect → track → map to court → events → metrics" architecture remains intact. 

What changes (sport layer).

Event grammar + labels. Volleyball needs a rally-centric taxonomy (serve, reception/pass, set, attack, block, dig) and rotation context. SportsVisio's public volleyball stat list (kills, digs, attack attempts/percentages, reception, etc.) is a practical target output schema for coach expectations. 
Ball dynamics. Volleyball ball flight is fast with frequent occlusions at the net; camera placement and shutter speed become even more critical.
Court mapping. Volleyball court geometry differs; but the same homography-based field registration approach applies after you swap the template and landmarks. 
Player identification. Jersey number visibility and role constraints differ; libero rules create role-dependent tracking/metrics needs.
Product modules. Volleyball buyers often emphasize serve/receive patterns, setter decision distributions, and rotation efficiencies—design dashboards around those questions, while reusing the clip + search engine.
Commercial reality check. Volleyball has fewer players on court, which can reduce tracking complexity, but the ball/hand contact events are subtle. Expect to reuse the platform efficiently while still budgeting meaningful annotation and model tuning time for "event truth."