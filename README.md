# Zenvy Hospital Pilot — AI Receptionist System

**Status:** Week 2 (STT + TTS Microservices) | **Team:** A (Voice & Infrastructure) | **Timeline:** 25 weeks

Zenvy is a multilingual, healthcare-focused AI receptionist system built for hospital pilot deployment. The system processes patient inquiries across phone, WhatsApp, and web channels, handles appointment bookings, and includes ambient transcription and prescription management.

This repository contains the **voice infrastructure layer** (Speech-to-Text & Text-to-Speech microservices) and orchestration for the 10-microservice architecture.

---

## 🎯 Current Phase

### **Week 2: Hardened STT & TTS Microservices**

| Component | Port | Status | Purpose |
|-----------|------|--------|---------|
| **STT Service** | 8001 | ✅ Running | Speech-to-Text (Sarvam Saaras v2) |
| **TTS Service** | 8005 | ✅ Running | Text-to-Speech (Sarvam Bulbul v2) |
| **PostgreSQL** | 5432 | ✅ Ready | Session & audit logging |
| **Redis** | 6379 | ✅ Ready | Session cache |

---

## 🗂️ Architecture

### **10 Microservices (Planned)**

**Phase 1: AI Receptionist (Weeks 1–18)**
1. **Channel Gateway** (8000) — phone/WhatsApp/web router
2. **STT** (8001) — Sarvam Saaras v2 wrapper ← **You are here**
3. **NLU** (8002) — Intent + entity extraction
4. **Orchestrator** (8003) — Conversation state machine
5. **LLM/Response Gen** (8004) — Q&A + translation
6. **TTS** (8005) — Sarvam Bulbul v2 wrapper ← **You are here**
7. **HIS Integration** (8006) — Hospital booking mock
8. **Notification** (8007) — SMS/WhatsApp
9. **Auth** (8008) — JWT + admin access
10. **Admin Dashboard** (3000) — React UI for doctors

**Phase 2: Ambient Scribe + Prescription Engine (Weeks 19–21)**

---

## 📋 Requirements

- **Docker Desktop** (Windows/Mac) or Docker Engine (Linux)
- **Python 3.11+** (for local development)
- **Sarvam AI API key** (free tier: https://www.sarvam.ai)
- **Postman** (optional, for testing)

---

## ⚡ Quick Start

### **Windows PowerShell**

```powershell
# 1. Navigate to repo
cd zenvy

# 2. Ensure Docker Desktop is running (search Start menu, launch app)

# 3. Create .env file
@"
SARVAM_API_KEY=sk_65cznqeb_FgcBax3XLO3lFUFdATzWs2GX
POSTGRES_USER=zenvy_user
POSTGRES_PASSWORD=zenvy_password
POSTGRES_DB=zenvy_hospital
"@ | Out-File -Encoding UTF8 .env -Force

# 4. Start services
docker compose up -d --build

# 5. Check status (all should be "Up")
docker compose ps

# 6. Test STT service
$response = Invoke-WebRequest http://localhost:8001/health
$response.Content
```

### **Linux/Mac**

```bash
# 1. Navigate to repo
cd zenvy

# 2. Create .env file
cat > .env << EOF
SARVAM_API_KEY=sk_65cznqeb_FgcBax3XLO3lFUFdATzWs2GX
POSTGRES_USER=zenvy_user
POSTGRES_PASSWORD=zenvy_password
POSTGRES_DB=zenvy_hospital
EOF

# 3. Start services
docker compose up -d --build

# 4. Check status
docker compose ps

# 5. Test STT service
curl http://localhost:8001/health
```

---

## 🧪 Testing

### **1. Health Checks**

```bash
# STT Service
curl http://localhost:8001/health

# TTS Service
curl http://localhost:8005/health
```

Expected response:
```json
{"status": "healthy", "service": "stt", "api_configured": true}
```

### **2. STT (Speech-to-Text) — PowerShell**

```powershell
# Transcribe audio file (Kannada example)
$Form = @{
    file = Get-Item "kannada_test.wav"
    language_code = "kn-IN"
    session_id = "test_001"
}

Invoke-WebRequest -Uri http://localhost:8001/transcribe `
    -Method Post `
    -Form $Form
```

Expected response:
```json
{
  "transcript": "ನಮಸ್ಕಾರ",
  "language_code": "kn-IN",
  "session_id": "test_001",
  "confidence": 0.92,
  "duration_ms": 2340
}
```

### **3. TTS (Text-to-Speech) — PowerShell**

```powershell
# Synthesize speech (Kannada example)
$Form = @{
    text = "ನಮಸ್ಕಾರ"
    language_code = "kn-IN"
    speaker = "shubh"
}

$response = Invoke-WebRequest -Uri http://localhost:8005/synthesize-json `
    -Method Post `
    -Form $Form

$response.Content | ConvertFrom-Json
```

---

## 📁 Project Structure
