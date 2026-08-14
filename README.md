# 🛡️ AgentRouter Setup & Integration Guide — Claude Code & AI Gateway

[![Live Guide](https://img.shields.io/badge/Live_Guide-GitHub_Pages-6366f1?style=for-the-badge&logo=github)](https://marko1olo.github.io/agentrouter-setup-guide/)
[![PWA Ready](https://img.shields.io/badge/PWA-Installable-22c55e?style=for-the-badge&logo=pwa)](https://marko1olo.github.io/agentrouter-setup-guide/manifest.json)
[![AI Index](https://img.shields.io/badge/LLM_Search-llms.txt-38bdf8?style=for-the-badge)](https://marko1olo.github.io/agentrouter-setup-guide/llms.txt)
[![Invite Link](https://img.shields.io/badge/AgentRouter-Invite_Discount_60%25-ff6600?style=for-the-badge)](https://agentrouter.org/register?aff=KM29)

The definitive integration manual and configuration generator for routing **Claude Code CLI**, Cursor, Cline, Roo-Code, and VS Code through high-throughput AI reverse proxies with WAF homoglyph sanitization and zero-latency TLS 1.3 multiplexing.

---

## 🏛️ Network Flow & Proxy Architecture

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer (IDE / CLI)
    participant Proxy as Local Proxy (127.0.0.1:8318)
    participant WAF as WAF Sanitizer & Cipher
    participant AR as AgentRouter Upstream Gateway
    participant AI as Upstream Provider (Claude / OpenAI)

    Dev->>Proxy: POST /v1/messages (JSON payload)
    Proxy->>WAF: Homoglyph normalization & entropy check
    WAF->>AR: TLS 1.3 Multiplexed Stream
    AR->>AI: Authenticated Upstream Forward
    AI-->>AR: Server-Sent Events (SSE) Stream
    AR-->>Dev: Zero-lag Token Stream
```

---

## 🚀 Rapid IDE Setup Matrix

### Claude Code CLI Setup
```bash
# Export environment variable
export ANTHROPIC_BASE_URL="https://agentrouter.org/v1"
export ANTHROPIC_API_KEY="your-agentrouter-api-key"

# Run Claude Code CLI
claude
```

### Cursor & VS Code Settings (`settings.json`)
```json
{
  "cursor.openAiBaseUrl": "https://agentrouter.org/v1",
  "cursor.apiKey": "your-agentrouter-api-key"
}
```

---

### 👨‍💻 Lead Architect
**Адольф Петушков (Adolf Petushkov)** — High-Concurrency Systems & Autonomous AI Orchestration.  
GitHub: [@marko1olo](https://github.com/marko1olo)
