*RCAP — Rotate-Contain-Attribute Protocol*

| **RCAP** **Rotate-Contain-Attribute Protocol** *A Concentric Rotating Defense Architecture for* *Attacker Entrapment and Cryptographic Attribution* |
| --- |

*Research Proposal and Technical Specification*

| **Author** | CHALLAYOGESWAR |
| --- | --- |
| **Contact** | challayogeswar8790@gmail.com |
| **Classification** | Confidential — Controlled Distribution |
| **Version** | 1.0 — July 2026 |
| **Document Type** | Technical Whitepaper / Research Proposal |
| **Related Work by Author** | QAP — Quiesce-Authorize Protocol (v1.0, May 2026) |

# Document Information and Disclaimer

| **Author** | CHALLAYOGESWAR |
| --- | --- |
| **Contact** | challayogeswar8790@gmail.com |
| **Document title** | RCAP — Rotate-Contain-Attribute Protocol: A Concentric Rotating Defense Architecture for Attacker Entrapment and Cryptographic Attribution |
| **Version** | 1.0 |
| **Date** | July 2026 |
| **Classification** | Confidential — Authorized Distribution Only |

This document is intended for cybersecurity researchers, enterprise security architects, critical-infrastructure defenders, and security product firms.

Prior published work exists on the general premise of modelling layered cybersecurity defense on the Chakravyuh — the concentric battlefield formation described in the Indian epic Mahabharata. Gupta (2025) models attacker-versus-layer strength using chaos theory and fuzzy logic; Dutta (2025) models the same formation as a reinforcement-learning problem using Q-learning and Markov decision processes. A closed-source, deployed endpoint-protection product bearing the name "Chakravyuh" has also been in use by India's Ministry of Defence, bundled with the indigenous Maya operating system, since 2023. RCAP does not claim originality over the Chakravyuh metaphor, nor over either cited academic work, nor over the naming or existence of the deployed product referenced above. Where this document draws on that prior work it is cited in Section 2 and in the References.

RCAP's original contribution, to the best of the author's knowledge, is a protocol-level specification that has not been previously published: a cryptographically-seeded ring-rotation mechanism (ROTATE), a one-way admission and resource-throttled containment state machine (CONTAIN), a session-fingerprinting attribution pipeline (ATTRIBUTE), a distributed consensus orchestrator, and a parallel authenticated fast-lane bypass — combined into a single deployable systems architecture with a defined threat model, integration pathway, and cost model. The two cited academic works are mathematical and simulation models; neither specifies a deployable protocol. The deployed "Chakravyuh" product is closed-source with no public specification.

References to existing work — including Moving Target Defense frameworks, deception and honeypot research, zero-trust architectures, and the Chakravyuh-inspired academic literature — are provided for comparative and contextual purposes only and do not imply endorsement, affiliation, or an intellectual property claim over those works.

This document is intended solely for the use of the authorized recipient. Reproduction, redistribution, or disclosure to any third party without the express written consent of the author is prohibited.

# Table of Contents

# Abstract

This paper presents the Rotate-Contain-Attribute Protocol (RCAP), a concentric, cryptographically-rotating defense architecture for enterprise and critical-infrastructure networks. RCAP arranges defensive controls into a sequence of rings around a protected core and governs three coordinated mechanics: ROTATE, in which ring-level network identifiers and routing positions are re-seeded on an unpredictable, cryptographically-verifiable schedule; CONTAIN, in which unauthenticated traffic that is admitted to a ring has its egress path closed and its session resource-throttled rather than dropped; and ATTRIBUTE, in which contained sessions are fingerprinted and correlated toward the infrastructure directing them, rather than merely logged and discarded.

RCAP is positioned as a protocol-level complement to two recent works that model the same Chakravyuh formation mathematically — a chaos-theory and fuzzy-logic model (Gupta, 2025) and a reinforcement-learning simulation (Dutta, 2025) — neither of which specifies a deployable system. It is also positioned relative to "Chakravyuh," a closed-source endpoint-protection product deployed by India's Ministry of Defence since 2023, for which no public specification exists. This document covers related work, a full protocol specification, an explicit threat model that separates volumetric from targeted attacks, identified design flaws and their resolutions, integration pathways with existing security infrastructure, and a realistic resource and cost model for enterprise deployment.

RCAP is designed to interoperate with the author's previously specified Quiesce-Authorize Protocol (QAP): both protocols share a distributed Orchestrator design pattern, and a joint deployment can use a single consensus cluster and a single trust-scoring subsystem for both the rest-state negotiation governed by QAP and the ring containment governed by RCAP.

# 1. Introduction

Two defensive postures dominate current practice. The first is blocking: a firewall, WAF, or IDS drops or rejects traffic it identifies as hostile. Blocking is fast and cheap, but it is also an immediate, unambiguous signal to the attacker — a rejected connection tells an automated scanner exactly where the boundary is, and the scanner adjusts its next attempt within milliseconds. The second posture is passive observation: a honeypot or decoy logs attacker behavior without interfering, trading immediate protection for intelligence. Neither posture, used alone, addresses networks that face both reconnaissance-driven intrusion attempts and high-volume automated or AI-directed load in the same threat landscape.

A third posture — entrapment with attribution — sits between these two. Rather than rejecting suspicious traffic outright or merely observing it, the network admits it into a controlled region, prevents it from backtracking or freely re-scanning, imposes a real resource cost on continued attempts, and uses the time this buys to fingerprint the session and the infrastructure behind it. This is architecturally close to how the Chakravyuh battlefield formation is described in the Mahabharata, and the parallel has already attracted academic attention (Section 2.1) and at least one closed-source deployed implementation (Section 2.2). What has not yet been published is a protocol-level specification: a concrete state machine, admission mechanism, rotation scheme, and orchestration model that a defender could actually implement.

The Rotate-Contain-Attribute Protocol addresses this gap. It defines a ring-based state machine governing how traffic is admitted, throttled, rotated past, and instrumented; a cryptographically-seeded rotation scheme that keeps ring topology from becoming a learnable pattern; a parallel authenticated bypass so legitimate high-volume traffic is never subject to the same friction as unauthenticated traffic; and a distributed orchestrator so no single node's compromise or failure disables the protocol's guarantees. RCAP draws on Moving Target Defense, deception and honeypot research, distributed consensus, and reinforcement-learning-based adaptive defense.

# 2. Background and Related Work

## 2.1 The Chakravyuh Formation as a Cybersecurity Model

The Chakravyuh is a concentric, multi-layered battlefield formation described in the Mahabharata, in which each ring conceals the entrance to the next and the formation rotates continuously about its axis. Two 2025 papers independently formalize this as a cybersecurity model.

Gupta (2025) develops a hybrid chaos-theory and fuzzy-logic model in which an attacker's residual strength decays as it crosses each defensive layer, subject to a non-linear feedback term representing the system's adaptive response. The model treats entry-point selection as the system's most sensitive initial condition — a small change in where an attacker enters determines whether the whole simulated formation destabilizes — and uses Monte Carlo simulation together with a binary-search procedure to compute the minimum defensive strength each layer requires to hold an attacker of a given initial strength below a 1% breach probability.

Dutta (2025) goes further, framing the same formation as a reinforcement-learning problem: a Q-learning and Markov-decision-process framework in which a defender deploys adaptive traps and allocates resources across layers under uncertainty while an attacker attempts sequential breaches. The result most relevant to RCAP's design is that trap efficiency is not stable over time — roughly 82% to 88% of traps triggered successfully in early simulation episodes, but that figure fell to approximately 45% as the simulated attacker itself adapted using reinforcement learning. A static, however elaborate, layered pattern is learnable and gets routed around; only a defense that itself keeps retraining holds its efficacy.

Both works are mathematical or simulation models. Neither specifies network protocols, admission mechanics, message formats, or a deployment architecture; both operate on abstract "layers" rather than concrete network controls.

## 2.2 Deployed Precedent — the "Chakravyuh" Endpoint System

A concentric-ring, deception-oriented endpoint defense product named "Chakravyuh" has been deployed by India's Ministry of Defence since 2023, bundled with the indigenously developed Maya operating system as an endpoint detection and protection layer intended to cloak Defence Ministry computers and trap lateral movement by advanced persistent threat groups (Wikipedia contributors, "Maya (operating system)"; The Hindu, as reported August 2023). The product is closed-source; no public protocol specification, threat model, or architecture document has been released. Its existence is nonetheless material prior art for anyone using the Chakravyuh name commercially or academically, and is the reason this document does not use that name for RCAP.

## 2.3 Moving Target Defense

Moving Target Defense (MTD) is a proactive paradigm in which system properties — IP addresses, ports, protocol characteristics, and topology — are continuously altered to invalidate attacker reconnaissance and raise the cost of a successful exploit. Sciancalepore et al. (2022) survey the practical state of MTD for networks; the MPD framework (Mei et al., 2021) generates dynamically-varying communication protocol dialects between endpoints from a pre-shared secret; and a factored-MDP approach (Sengupta et al., AAMAS 2024) uses reinforcement learning to select optimal configuration-switching policies without assuming knowledge of the attacker's reward function. These frameworks establish the feasibility of runtime reconfiguration as a defense but operate at the level of individual network properties rather than a coordinated multi-ring admission and containment architecture.

## 2.4 Deception Technology and Network Tarpitting

Zhang and Thing (2021) review three decades of honeypot, honeytoken, and Moving Target Defense research together, and propose a four-layer deception-stack taxonomy mapped against the cyber kill chain — the first systematic treatment of these three domains as complementary rather than separate. Network tarpitting, introduced by Liston's LaBrea (2001) and discussed academically by Crane et al. (2018), deliberately delays or holds open connections to waste an automated attacker's connection pool and time rather than rejecting the connection outright. Scottberg, Yurcik, and Doss (2002) examine the legal ambiguity of honeypot-style deception, framing the central tension RCAP must respect: passive deception inside a defender's own network is well precedented, while any action that reaches into an attacker's own infrastructure is not.

## 2.5 Gap in Existing Work

No published work combines a cryptographically-seeded ring rotation scheme, a one-way admission and resource-throttled containment mechanism, a session-attribution pipeline, and a distributed consensus orchestrator into a single deployable protocol specification. The two Chakravyuh-inspired academic papers (Sections 2.1) are mathematical and simulation models operating on abstract layers. The deployed Chakravyuh product (Section 2.2) is closed-source with no public specification. MTD and deception research (Sections 2.3–2.4) each address individual mechanisms — rotation, or deception, or tarpitting — but not their combination into one coordinated ring architecture with an explicit threat model and cost model. RCAP is proposed to close this gap.

# 3. Protocol Specification — Rotate-Contain-Attribute Protocol (RCAP)

## 3.1 Ring Roles

RCAP arranges defensive controls into four concentric rings around a protected core, plus a parallel authenticated fast-lane path:

| **Ring / Path** | **Identifier** | **Function** |
| --- | --- | --- |
| Ring 1 | PERIMETER | Rate limiting and tarpit-based throttling of unauthenticated traffic. No inspection beyond volumetric and connection-rate signals. |
| Ring 2 | INSPECT | Behavioral and signature-based anomaly detection; tags sessions for containment versus pass-through. |
| Ring 3 | ROTATE | Moving-target layer. Addresses, ports, and routing positions for this ring are re-seeded on the ROTATE schedule (Section 3.2). |
| Ring 4 | DECEIVE | Deception assets (honeytokens, decoy services) and deep packet inspection immediately outside the authentication boundary. |
| Core | CORE | The protected asset. Reachable only via a verified Ring 4 transition or the FASTLANE path. |
| Fast-lane | FASTLANE | Parallel authenticated path for pre-verified identities. Bypasses Rings 1–4 entirely (Section 3.5). |

## 3.2 The ROTATE Mechanism

Ring 3 positions — the addresses, ports, and internal routing assignments that determine which physical or virtual node currently answers for a given ring position — are re-seeded on a configurable interval (recommended: 15–60 seconds, consistent with MTD rotation intervals established in prior work). The seed for each rotation is produced by a keyed pseudorandom function evaluated by the Orchestrator over a rotation counter and a per-deployment secret, in the manner of a verifiable random function: any Orchestrator instance can verify that a claimed rotation seed was produced correctly, but no external observer can predict the next seed from observed traffic.

This is a deliberate divergence from a fixed or narrative rotation schedule. Gupta (2025) identifies entry-point selection as the most sensitive initial condition in the Chakravyuh model — the single variable most responsible for whether a simulated breach succeeds. A rotation scheme that is regular, guessable, or inferable from repeated probing reintroduces exactly that sensitivity as an exploitable weakness. Coordinating the rotation through an SDN controller keeps routing consistent for legitimate in-flight sessions across a rotation boundary.

## 3.3 The CONTAIN Mechanism

A session that is admitted into Ring 1 or Ring 2 without a valid FASTLANE credential enters the CONTAIN state. On entry, three things happen atomically: the session's route back out of the ring is not re-advertised (it is not blocked — the TCP handshake completes and traffic continues to flow — but the routing information that would let the session discover or return to a prior ring position is withheld); the session is bound to a resource-throttling policy (connection-rate limiting, TCP window manipulation, or a computational proof-of-work challenge, selected by the RL Decision Engine, Section 6.2); and a CONTAIN ledger entry is opened.

| **Design note — CONTAIN is not a block.** A dropped connection tells an automated attacker its probe failed and to try a different approach immediately. A CONTAIN session appears to the attacker as a live, if slow or unstable, target — exactly what a network under legitimate heavy load would also look like from outside. This is intentional: CONTAIN is only useful if it does not immediately disclose its own presence. |
| --- |

A session in CONTAIN transitions out in one of three ways: it is reclassified as benign by the INSPECT ring and released to normal routing; it exhausts a maximum CONTAIN duration (default: 300 seconds) and is escalated to a hard block with its fingerprint published to the CONTAIN ledger; or, if it presents a valid FASTLANE credential mid-session, it is transitioned directly to authenticated routing.

## 3.4 The ATTRIBUTE Mechanism

Every session in CONTAIN is passed through the Ring 4 deception and inspection layer regardless of which ring it entered at. This layer fingerprints TLS/JA3-style client characteristics, timing and protocol-deviation signatures, and any interaction with a Ring 4 honeytoken or decoy service, and correlates these signals against prior CONTAIN sessions to build a picture of the infrastructure directing the traffic — its automation tooling, its likely command-and-control relay pattern, and its behavioral fingerprint — rather than attempting to identify an individual human operator.

This distinction matters structurally. The Chakravyuh myth places an individual combatant, Abhimanyu, physically inside the formation; a real attacker's directing operator is almost never inside the defended network — only their tooling, payloads, or a compromised session are. ATTRIBUTE therefore targets the session and the infrastructure behind it, not a person, and its output is a fingerprint suitable for threat-intelligence correlation and for informing the RL Decision Engine's containment policy for future sessions with a matching signature.

## 3.5 The FASTLANE Bypass

A session presenting a valid mutual-TLS certificate and, where required by deployment policy, hardware attestation, is routed via FASTLANE directly to CORE, bypassing Rings 1 through 4 entirely. FASTLANE exists specifically to resolve a failure mode of the underlying Chakravyuh metaphor: a formation optimized to confuse an unrecognized entity is, by the same design, hostile to legitimate high-volume traffic — an authenticated partner integration or a genuine flash-sale traffic spike should never be routed through tarpit or containment logic intended for unauthenticated traffic. FASTLANE is implemented as a zero-trust overlay in the style of dark, non-broadcast network services: unauthenticated probes cannot discover that a FASTLANE listener exists at all.

## 3.6 The Orchestrator

Ring rotation seeds, containment policy, the CONTAIN ledger, and ring-count scaling decisions are governed by a distributed Orchestrator cluster running a Raft-style consensus protocol, with a minimum of three instances in any production deployment. No single Orchestrator instance can unilaterally authorize a rotation, alter containment policy, or approve a FASTLANE credential; all such actions require quorum agreement across the cluster.

| **Design note — command redundancy.** A single central controller is a natural target: its compromise or failure disables every guarantee the protocol provides. This mirrors the ancient formation's own hierarchical structure, in which subordinate captains continue executing the formation if the supreme commander is engaged elsewhere. RCAP formalizes that instinct as a consensus requirement rather than relying on any single node. |
| --- |

# 4. Threat Model

## 4.1 Volumetric / Heavy-Load Attacks

Ring-level rotation, containment, and inspection are not designed to absorb a volumetric Layer 3/4 flood, and RCAP does not claim to. A multi-hundred-gigabit-per-second flood must be absorbed by upstream anycast or CDN-based scrubbing capacity before it reaches Ring 1; RCAP governs what happens to traffic that survives that upstream filtering and reaches the ring stack. Section 6.1 specifies the scrubbing integration point.

## 4.2 Adaptive and AI-Directed Reconnaissance

An attacker that adapts across repeated attempts — whether a human operator iterating manually or an automated system using reinforcement learning, consistent with the attacker model in Dutta (2025) — is the primary threat RCAP's rotation and containment mechanics are designed against. Section 6.2 specifies the RL Decision Engine that keeps containment policy and rotation cadence from becoming a static, learnable target in the way Dutta's results show a fixed formation eventually does.

## 4.3 Self-Denial-of-Service Under Legitimate Load

A containment policy that treats all unauthenticated traffic identically risks throttling a legitimate load spike — a flash sale, a breaking-news traffic surge, a genuine partner integration that has not yet completed FASTLANE onboarding — as aggressively as it throttles an actual attack. RCAP treats this as a first-class threat to the protocol's own availability guarantees, not merely an edge case, and requires that CONTAIN policy be driven by behavioral and identity scoring rather than raw connection volume alone (Section 5).

## 4.4 Lateral Movement

A session that reaches Ring 4 without triggering containment, or that pivots from a compromised internal host, is treated as a lateral-movement attempt. Ring 3's continuous rotation of internal routing positions is intended to make a previously-mapped internal topology stale within one rotation interval, consistent with the rotation-based lateral-movement mitigation established in the MTD literature (Section 2.3).

## 4.5 Containment Extended Into Unauthorized Access

RCAP's containment mechanics are strictly passive: they alter what a contained session can observe and how quickly it can proceed inside the defender's own network. Any action that reaches into or interacts with infrastructure outside the defender's own network boundary — commonly termed "hack-back" — is explicitly out of scope and prohibited by this specification. This boundary is not a implementation detail; it is the line between well-precedented honeypot-style deception and unauthorized access to a third party's systems, a distinction discussed in the honeypot legal literature (Scottberg et al., 2002) and one that varies by jurisdiction. Deployments should obtain jurisdiction-specific legal review before enabling any containment behavior beyond passive throttling and observation.

# 5. Identified Design Flaws and Resolutions

The following table documents design weaknesses identified during protocol analysis — several surfaced directly from a comparison against Gupta (2025) and Dutta (2025) — and the corresponding resolutions incorporated into RCAP:

| **Identified Flaw** | **Risk** | **Resolution in RCAP** |
| --- | --- | --- |
| Commander conflated with a remote human operator | Design targets literal capture of an individual, which is rarely possible since a real attacker's operator is almost never inside the network. | ATTRIBUTE (3.4) targets the session and its directing infrastructure, not an individual; output feeds threat intelligence rather than attempting personal identification. |
| Naive uniform admission throttling | One-size containment policy throttles a legitimate heavy-load spike as aggressively as an attack, becoming a self-inflicted denial of service. | CONTAIN policy is driven by behavioral and identity scoring (Section 4.3), not raw connection volume; scoring inputs are tunable per deployment. |
| No path for already-trusted traffic | Formation is optimized to confuse unrecognized entities, which by design also degrades legitimate authenticated traffic. | FASTLANE (3.5) routes pre-verified identities directly to CORE, bypassing all four rings. |
| "Fatigue" modeled on human combatants | Automated attackers do not tire; a script can open thousands of parallel sessions instantly. | CONTAIN imposes a computational or connection-rate cost per session (proof-of-work, TCP window throttling) rather than relying on attacker exhaustion over time. |
| Predictable or fixed rotation schedule | A guessable rotation pattern is mappable by repeated probing, reintroducing the sensitivity-to-entry-point weakness identified in Gupta (2025). | Rotation seeds are produced by a keyed pseudorandom function verifiable only by the Orchestrator cluster (Section 3.2). |
| Ring architecture assumed to stop volumetric floods | Concentric containment logic cannot absorb a multi-hundred-Gbps flood; conflating this with targeted intrusion leaves the deployment under-defended against DDoS. | Threat model (4.1) explicitly scopes volumetric attacks to upstream scrubbing infrastructure ahead of Ring 1 (Section 6.1). |
| Static containment pattern degrades over time | Dutta (2025) shows trap efficiency falling from roughly 82–88% to 45% as a simulated attacker adapts via reinforcement learning. | RL Decision Engine (6.2) retrains containment and rotation policy against live telemetry on a scheduled cycle rather than relying on a fixed pattern. |
| Single Orchestrator as a point of failure | Compromise or failure of a single controlling node disables every protocol guarantee. | Orchestrator is a minimum three-instance Raft consensus cluster; no single instance can authorize protocol-level actions alone (Section 3.6). |
| Containment mechanics extended into hack-back | Ambiguity over whether containment could extend to actively interacting with attacker-controlled infrastructure risks unauthorized access liability. | Threat model (4.5) explicitly restricts all mechanics to the defender's own network; any external interaction is out of specification scope. |

# 6. Additional Security Enhancements

## 6.1 Upstream Volumetric Scrubbing Integration

Upstream volumetric absorption (anycast or CDN-based scrubbing, e.g. commercial DDoS protection services) sits ahead of Ring 1 and is treated as a prerequisite rather than an optional enhancement for any deployment exposed to the public internet. RCAP's ring stack governs traffic that has already passed this filtering; it is not a substitute for it.

## 6.2 Reinforcement Learning Decision Engine

The Orchestrator integrates an optional reinforcement-learning engine, modeled as a Markov Decision Process consistent with the approach in Dutta (2025) and with the MTD-MDP research cited in Section 2.3. The engine observes CONTAIN outcomes, ATTRIBUTE fingerprint recurrence, and rotation-interval effectiveness to adjust containment thresholds, rotation cadence, and behavioral-scoring weights on a scheduled retraining cycle, directly addressing the trap-efficiency decay Dutta's results document for a static formation.

## 6.3 Cryptographically-Seeded Rotation

Rotation seeds are generated by a keyed pseudorandom function over a rotation counter and per-deployment secret, verifiable by any Orchestrator instance but not predictable from observed traffic, as specified in Section 3.2.

## 6.4 Distributed Consensus Orchestrator

The Orchestrator is deployed as a minimum three-instance cluster using Raft consensus, as specified in Section 3.6, so that rotation, containment policy, and FASTLANE credential verification remain available under the failure of any single instance.

## 6.5 Zero-Trust Fast-Lane

FASTLANE is implemented using a zero-trust, non-broadcast overlay network model (in the style of dark, identity-gated services) so that unauthenticated scanning cannot discover the existence of the authenticated path, as specified in Section 3.5.

## 6.6 Immutable Attribution Ledger

CONTAIN ledger entries, ATTRIBUTE fingerprints, rotation events, and FASTLANE credential issuance are committed to an append-only, Orchestrator-signed ledger, replicated across the consensus cluster, serving as the authoritative record for forensic investigation, compliance reporting, and RL Decision Engine training data.

## 6.7 Chaos Engineering / Adversarial Validation

RCAP deployments should incorporate scheduled adversarial simulation — a controlled, authorized process that repeatedly probes CONTAIN response, rotation predictability, and FASTLANE isolation against defined performance baselines — before a real threat event does so instead. This is the direct practical countermeasure to the finding in Dutta (2025) that a formation's effectiveness against an adapting attacker cannot be assumed static and must be measured.

# 7. Integration with Existing Security Infrastructure

RCAP is designed as a complementary protocol layer, not a replacement for existing security infrastructure. The following table describes integration pathways with commonly deployed security technologies:

| **Technology** | **Role in RCAP Integration** | **Benefit** |
| --- | --- | --- |
| Anycast / CDN scrubbing (e.g. commercial DDoS protection) | Absorbs volumetric floods ahead of Ring 1. | Keeps ring-level logic scoped to session-based threats it is designed for. |
| SDN controller (ONOS / OpenDaylight) | Executes Ring 3 rotation commands from the Orchestrator. | Consistent routing for legitimate in-flight sessions across a rotation boundary. |
| eBPF-based microsegmentation (e.g. Cilium) | Enforces Ring-to-Ring traffic policy at the kernel level. | Containment cannot be bypassed by a compromised user-space process. |
| Behavioral IDS (Suricata / Zeek) | Powers Ring 2 (INSPECT) anomaly classification. | Reuses mature, well-tested detection logic rather than reimplementing it. |
| Deception platform (open-source or commercial) | Populates Ring 4 (DECEIVE) honeytokens and decoy services. | Attacker interaction with a decoy is an unambiguous ATTRIBUTE signal. |
| Zero-trust overlay (e.g. dark-service ZTNA) | Implements the FASTLANE authenticated bypass. | Authenticated traffic is invisible to unauthenticated scanning. |
| Raft/etcd consensus cluster | Backs the distributed Orchestrator. | No single-instance point of failure for rotation or containment authority. |
| RL framework (e.g. Ray RLlib) | Powers the optional RL Decision Engine. | Containment and rotation policy adapts against telemetry rather than remaining static. |
| SIEM / log platform (e.g. Wazuh) | Ingests the CONTAIN/ATTRIBUTE ledger. | Forensic and compliance visibility using existing security operations tooling. |
| HashiCorp Vault | Manages Orchestrator signing keys and rotation secrets. | Secrets are never exposed in application memory; rotation is automatable. |

# 8. Real-World Deployment Considerations

## 8.1 Applicable Environments

RCAP is applicable to environments where an unauthenticated public or semi-public attack surface exists alongside a protected core asset. Primary target environments include:

- Enterprise perimeters and application gateways where reconnaissance-driven intrusion attempts and legitimate high-volume customer traffic must be distinguished automatically.

- Critical-infrastructure and government networks where adversarial, potentially AI-assisted, probing is a realistic and ongoing threat scenario.

- Cloud-native environments where service-mesh sidecars can implement Ring 1–4 logic per service boundary.

- Deployments that already run the author's QAP protocol, where the Orchestrator cluster and trust-scoring subsystem can be shared between the two protocols.

## 8.2 Constraints and Limitations

RCAP is not recommended where sub-millisecond response latency is a hard requirement: traversal of the full ring stack adds roughly 5–15 milliseconds of round-trip latency (Section 8.3), concentrated in Ring 2 inspection and Ring 3 rotation-boundary routing. RCAP does not defend against volumetric Layer 3/4 attacks and must be paired with upstream scrubbing capacity for any internet-facing deployment (Section 6.1). Deployments without hardware attestation support for all trusted endpoints cannot use the strongest FASTLANE credential tier and should fall back to certificate-only authentication. Ongoing adversarial validation (Section 6.7) is an operational requirement, not an optional hardening step — an untested rotation and containment policy calcifies into a learnable pattern, per the finding in Dutta (2025).

## 8.3 Resource and Cost Model

The following estimates are based on a reference deployment of 10 to 50 nodes on cloud infrastructure:

| **Component** | **Technology** | **Estimated Cost (USD/month)** |
| --- | --- | --- |
| Upstream DDoS scrubbing | Commercial anycast / CDN scrubbing service | $200 – $3,000+ (traffic-dependent) |
| SDN / rotation controller | ONOS or OpenDaylight (open source) | $0 – $300 |
| Microsegmentation | eBPF-based (e.g. Cilium) or cloud security groups | $0 – $200 |
| Deception platform (Ring 4) | Open-source honeypot stack or managed deception platform | $0 – $500+ |
| Behavioral IDS (Ring 2) | Suricata or Zeek (open source) | $50 – $200 |
| Zero-trust fast lane | Open-source or commercial ZTNA overlay | $0 – $300 |
| RL Decision Engine (optional) | Ray RLlib, GPU spot instance | $70 – $360 |
| Orchestrator cluster (3 nodes) | Raft/etcd consensus | $150 – $400 |
| CONTAIN/ATTRIBUTE ledger + SIEM | Managed log platform or open-source SIEM | $0 – $400 |
| Total (10–50 nodes) | Full stack deployment | $500 – $5,000+ / month |

| **Performance overhead** Ring traversal (Rings 1–4, unauthenticated path): 5–15 ms round trip under normal load. Rotation-boundary routing update: 2–5 ms per rotation event. Proof-of-work containment challenge, where enabled: 50–200 ms, imposed only on CONTAIN-state sessions. FASTLANE authenticated path: latency comparable to a standard mutual-TLS handshake, with no ring traversal overhead. |
| --- |

# 9. Comparison with Existing Approaches

| **Capability** | **Classic Defense-in-Depth** | **Deception Platforms** | **Gupta 2025** | **Dutta 2025** | **RCAP (Proposed)** |
| --- | --- | --- | --- | --- | --- |
| Concentric layered structure | Yes | Partial | Yes (abstract) | Yes (abstract) | Yes |
| Rotating / MTD topology | No | Partial | No | No | Yes |
| One-way admission, closing gate | No | No | No | No | Yes |
| Entrapment with attribution | No | Yes | No | No | Yes |
| Self-retraining containment policy | No | Partial | No | Yes (simulated) | Yes |
| Authenticated fast-lane bypass | No | No | No | No | Yes |
| Distributed command, no single point of failure | No | No | No | No | Yes |
| Explicit volumetric-attack scoping | No | No | No | No | Yes |
| Open, deployable protocol specification | Yes | Proprietary | No (math model) | No (simulation model) | Yes (proposed) |

# 10. Conclusions and Recommended Next Steps

The Rotate-Contain-Attribute Protocol introduces a protocol-level specification for a defense pattern that, until now, existed only as mathematical simulation (Gupta, 2025; Dutta, 2025) or as closed-source deployed tooling with no public specification. By governing how sessions are rotated past, admitted and contained without immediate disclosure, and fingerprinted for attribution — while explicitly scoping out volumetric attacks and hack-back-adjacent behavior — RCAP addresses a gap that neither the academic Chakravyuh literature nor conventional Moving Target Defense or deception platforms independently cover.

The protocol is grounded in the two most directly relevant 2025 academic treatments of the same underlying formation, architecturally compatible with mature open-source and commercial security infrastructure, and deployable at a cost accessible to mid-to-large enterprise and critical-infrastructure organizations. The design flaws identified during specification review have been resolved within the protocol as written, and RCAP is designed to interoperate with the author's previously specified QAP protocol via a shared Orchestrator and trust-scoring subsystem.

The following steps are recommended before production deployment:

- Pilot the full Ring 1–4 and FASTLANE stack on a single non-critical network segment before wider rollout.

- Run scheduled adversarial validation against the pilot deployment specifically targeting rotation predictability, replicating the attacker-adaptation conditions in Dutta (2025).

- Commission jurisdiction-specific legal review of the CONTAIN mechanism's boundary against hack-back liability before enabling anything beyond passive throttling and observation.

- Evaluate FASTLANE hardware-attestation latency against deployment-specific SLA requirements.

- Pilot the RL Decision Engine against historical traffic data to establish baseline containment-policy performance before enabling live retraining.

- Engage academic collaborators to publish a formal comparison against the Gupta (2025) and Dutta (2025) models using a shared evaluation methodology.

# References

[1] Gupta, K. (2025). A hybrid cybersecurity model inspired by Chakravyuh formation using chaos theory and fuzzy logic. Archives of Control Sciences, 35(4), 713–728. DOI: 10.24425/acs.2025.157144.

[2] Dutta, K. (2025). Dynamic optimization of multi-layered defenses inspired by Chakravyuh. International Journal of Critical Infrastructure Protection. DOI: 10.1016/j.ijcip.2025.100794.

[3] Wikipedia contributors. Maya (operating system). Wikipedia, The Free Encyclopedia. Retrieved July 2026.

[4] The Hindu (2023, August). India's Defence Ministry to replace Microsoft Windows with indigenous Maya OS and the "Chakravyuh" endpoint protection system. As reported.

[5] Zhang, L., and Thing, V. L. L. (2021). Three Decades of Deception Techniques in Active Cyber Defense — Retrospect and Outlook. arXiv:2104.03594.

[6] Scottberg, B., Yurcik, W., and Doss, D. (2002). Internet honeypots: protection or entrapment? Proceedings of the International Symposium on Technology and Society (ISTAS).

[7] Liston, T. (2001). LaBrea: a new approach to securing our networks. Original tarpit implementation.

[8] Crane, S., et al. (2018). Chaff Bugs: Deterring Attackers by Making Software Buggier. arXiv:1808.00659.

[9] Mei, Y., Gogineni, K., Lan, T., and Venkataramani, G. (2021). MPD: Moving Target Defense through Communication Protocol Dialects. George Washington University. arXiv:2110.03798.

[10] Sengupta, S., et al. (2024). A Factored MDP Approach to Moving Target Defense with Dynamic Threat Modeling and Cost Efficiency. AAMAS 2024. arXiv:2408.08934.

[11] Sciancalepore, S., et al. (2022). A Survey on Moving Target Defense for Networks: A Practical View. MDPI Electronics, 11(18), 2886.

[12] Google (2014). BeyondCorp: A New Approach to Enterprise Security. Google Technical Report.

[13] Trusted Computing Group (2019). TPM 2.0 Library Specification. TCG Published Standard.

[14] CHALLAYOGESWAR (2026). QAP — Quiesce-Authorize Protocol: A Peer-Authenticated Dynamic Defense Architecture for Zero-Trust Network Security. Version 1.0, May 2026.

Page  of