# Research: Security Lessons from OpenClaw

## What OpenClaw Is

OpenClaw is an open-source autonomous AI agent platform (100k+ GitHub stars, early 2026) that allows interaction via messaging apps. It's the closest existing project to Foundation and its security failures are directly instructive.

## CVE-2026-25253: One-Click RCE (CVSS 8.8)

### The Attack Chain

1. **Victim visits a malicious URL** (phishing link, forum post, etc.)
2. **Token exfiltration in milliseconds:** The Control UI trusted a `gatewayUrl` parameter from the query string without validation. It auto-connected to the attacker-specified URL via WebSocket, transmitting the stored authentication token.
3. **Cross-site WebSocket hijacking:** Attacker uses the stolen token to connect back to the victim's local OpenClaw instance, bypassing localhost restrictions via the victim's own browser.
4. **Disable sandbox:** Attacker sends `exec.approvals.set = 'off'` via the API.
5. **Container escape:** `tools.exec.host = 'gateway'` moves execution to the gateway process (which runs on the host).
6. **Full RCE on host.**

### Scale of Impact

17,500+ exposed instances of OpenClaw found across 52 countries (highest concentrations in US and China). An unauthenticated attacker could achieve host-level code execution through an unpatched deployment in under 90 seconds.

### Remediation

Version 2026.1.29 patched the WebSocket hijacking vulnerability. All gateway tokens issued before February 2026 needed rotation.

Source: [hunt.io analysis](https://hunt.io/blog/cve-2026-25253-openclaw-ai-agent-exposure), [socradar.io](https://socradar.io/blog/cve-2026-25253-rce-openclaw-auth-token/)

## Docker Sandbox Config Injection

Malicious configurations could:
- Mount host directories into containers
- Mount Docker socket (giving container root-equivalent access)
- Disable seccomp/AppArmor profiles
- Run containers with `--privileged` flag

The root cause: container configuration was influenced by external input (user configs, agent output) rather than being hardcoded in the application.

### Foundation's Defense

Container specs are constructed programmatically in Python code. No field in the Docker configuration is ever derived from AI output, user text, or external data. The container template is a hardcoded constant in the orchestrator.

## Prompt-Based Security Failures

OpenClaw relied on system prompt instructions like "don't access sensitive files" as a security boundary. This fails because:
- Prompt injection in code files, error messages, or dependency descriptions can override instructions
- Models don't reliably follow negative constraints under adversarial pressure
- There is no enforcement mechanism — it's a suggestion, not a rule

### Foundation's Defense

Prompt instructions are defense-in-depth only, never the primary boundary. Actual enforcement is architectural:
- Filesystem: container only has access to the workspace mount
- Network: internal Docker network + Squid proxy
- Capabilities: `--cap-drop=ALL`
- No sensitive paths mounted

## Shared Context / Secret Leakage

OpenClaw loaded secrets for one user's session that were visible to other sessions. Memory systems that persist across tasks can accumulate sensitive information.

### Foundation's Defense

- Each task gets its own container and workspace
- Memory files are curated by the orchestrator, not raw agent output
- No cross-task context sharing within containers
- Sensitive values (API keys, tokens) are never written to memory files

## Skills Marketplace Attacks

OpenClaw's community-contributed skills (plugins) executed with full agent privileges. Cisco's Talos team found data exfiltration in third-party skills.

### Foundation's Defense

Foundation has no plugin/skill system. Scope is limited to development orchestration with a fixed set of capabilities. The only extensibility is through memory files and system prompts, which cannot execute code.

## Microsoft's Recommendation

Microsoft's security team recommended treating OpenClaw as "untrusted code execution with persistent credentials" and recommending full VM isolation. Foundation's Docker hardening (internal network, Squid proxy, capability dropping, read-only filesystem, no socket mount) aims to meet this bar without the overhead of full VMs. gVisor can be added later for stronger isolation.

## Key Principles for Foundation

1. **Every string is a prompt injection vector.** Code from repos, test output, error messages, dependency descriptions, git commit messages — all of it.
2. **Container configs are code, not data.** Never parameterize security-critical Docker settings from external input.
3. **The sandbox is the security boundary.** Everything else (prompts, tool restrictions) is defense-in-depth.
4. **Assume the agent will be compromised.** Design so that a compromised agent can only damage its disposable workspace.
5. **No web UI.** Every exposed HTTP endpoint is an attack surface. Telegram's auth is Telegram's problem.
