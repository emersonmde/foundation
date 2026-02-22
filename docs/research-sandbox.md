# Research: Docker Sandbox Architecture

## Recommended Approach: Squid Proxy on Internal Network

After evaluating six approaches, the Squid forward proxy on an internal Docker network is the clear winner for domain-based egress filtering that is self-contained within Docker (no host-level iptables changes).

### Architecture

```
┌───────────────────────────────────────────────────────────┐
│  docker-compose stack (per task)                          │
│                                                           │
│  ┌────────────┐  HTTP CONNECT  ┌────────────────────┐    │
│  │ dev         │──────────────▶│ squid proxy         │    │
│  │ container   │               │ - domain allowlist  │───▶ Internet
│  │             │               │ - TLS SNI filtering │    │
│  │ claude -p   │               │ - access logging    │    │
│  │ runs here   │               │                     │    │
│  └────────────┘               └────────────────────┘    │
│    internal network              external network        │
└───────────────────────────────────────────────────────────┘
```

- Dev container is on an `--internal` Docker network (no direct external routing).
- The ONLY path to the internet is through the Squid proxy.
- Squid inspects TLS SNI on CONNECT requests for domain filtering — no MITM/decryption needed.
- Container cannot bypass the proxy by hardcoding IPs or using alternate DNS.

### Why Other Approaches Fall Short

| Approach | Verdict |
|---|---|
| `--network none` | No network at all — can't install dependencies |
| DNS filtering only | Trivially bypassed by using IP addresses directly |
| IP-based iptables in gateway container | CDN IP ranges overlap — allowlisting Cloudflare's range permits millions of domains |
| Cilium/Calico | Kubernetes-focused, requires host-level installation |
| AppArmor/seccomp | Can block socket() syscall entirely but cannot filter by destination |
| **Squid + internal network** | **Domain-level filtering, cannot be bypassed, self-contained** |

### Squid Configuration

```squid
http_port 3128

# Domain allowlist
acl allowed_domains dstdomain .npmjs.org
acl allowed_domains dstdomain .npmjs.com
acl allowed_domains dstdomain .yarnpkg.com
acl allowed_domains dstdomain .pypi.org
acl allowed_domains dstdomain .pythonhosted.org
acl allowed_domains dstdomain .crates.io
acl allowed_domains dstdomain .github.com
acl allowed_domains dstdomain .github.io
acl allowed_domains dstdomain .githubusercontent.com
acl allowed_domains dstdomain .gitlab.com

acl SSL_ports port 443
acl CONNECT method CONNECT
http_access allow CONNECT SSL_ports allowed_domains
http_access allow allowed_domains
http_access deny all

cache deny all
access_log /var/log/squid/access.log
```

### Compose Template

```yaml
services:
  proxy:
    image: ubuntu/squid:latest
    volumes:
      - ./squid.conf:/etc/squid/squid.conf:ro
    networks:
      internal:
        ipv4_address: 172.20.0.2
      external: {}

  dev:
    build: ./dev
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    read_only: true
    tmpfs:
      - /tmp:size=2G
      - /home/dev/.cache:size=4G
    environment:
      - HTTP_PROXY=http://proxy:3128
      - HTTPS_PROXY=http://proxy:3128
      - http_proxy=http://proxy:3128
      - https_proxy=http://proxy:3128
      - NO_PROXY=localhost,127.0.0.1
    networks: [internal]

networks:
  internal:
    internal: true
    ipam:
      config:
        - subnet: 172.20.0.0/24
  external:
    driver: bridge
```

### SSH/Git Handling

Squid handles HTTP/HTTPS only. For git over SSH:
```bash
git config --global url."https://github.com/".insteadOf "git@github.com:"
```
Force all git traffic through HTTPS, which goes through the proxy.

### Package Manager Proxy Support

All major package managers respect `HTTP_PROXY`/`HTTPS_PROXY` environment variables: npm, yarn, pip, cargo, git, curl, wget.

## Container Hardening

### Capability Dropping

```yaml
cap_drop: [ALL]
cap_add: [NET_BIND_SERVICE]  # Only if dev server needs port < 1024
security_opt: [no-new-privileges:true]
```

`--cap-drop=ALL` removes all Linux capabilities. `--security-opt=no-new-privileges` prevents setuid binaries from gaining elevated privileges.

### Read-Only Root Filesystem

```yaml
read_only: true
tmpfs:
  - /tmp:size=2G
  - /home/dev/.cache:size=4G
  - /home/dev/.local:size=2G
```

Writable areas limited to explicit tmpfs mounts. The workspace directory is bind-mounted read-write.

### Resource Limits

```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'
      memory: 8G
    reservations:
      cpus: '1.0'
      memory: 2G
ulimits:
  nproc: 256
  nofile:
    soft: 65536
    hard: 65536
```

### Sensitive Path Protection

| Path | Risk | Mitigation |
|---|---|---|
| `/var/run/docker.sock` | Full host compromise | Never mounted |
| `/proc/kcore` | Host memory dump | Masked by Docker by default |
| `/proc/sys` | Kernel tuning | Read-only by default |
| `/sys/firmware` | UEFI/BIOS access | AppArmor deny rules |
| Host home dir, SSH keys, AWS creds | Data exfiltration | Never mounted — only project workspace |

### Defense-in-Depth Layers

1. **Network:** Internal Docker network → Squid proxy → domain allowlist
2. **DNS:** CoreDNS/dnsmasq filtering as second layer (optional, defense-in-depth)
3. **Filesystem:** Read-only root, explicit tmpfs, minimal bind mounts
4. **Capabilities:** `--cap-drop=ALL`, `no-new-privileges`
5. **Resources:** CPU, memory, process count limits
6. **Docker socket:** Never accessible

## Container Config Security

**Critical lesson from OpenClaw:** Container configurations must be constructed programmatically in Foundation's Python code. Never let AI output, external input, or user-provided text influence Docker run parameters, volume mounts, capability additions, or network settings. The container spec is hardcoded in the orchestrator.

## Stronger Isolation (Future)

For even stronger isolation than Docker provides:
- **gVisor** (`--runtime=runsc`): User-space kernel, intercepts all syscalls. Significant performance overhead but much stronger isolation.
- **Firecracker/Kata Containers**: Full micro-VM per container. Strongest isolation, highest overhead.

Docker with the hardening described above is sufficient for the initial implementation. gVisor can be added later by changing the runtime flag.
