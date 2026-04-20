# Haven SOAP Note Generator

Automatically generates a clinical SOAP note from every after-hours on-call conversation and texts it to the nurse immediately after the call ends.

## How It Works

1. Patient calls Haven's after-hours number (+1 417-203-6466)
2. Vapi AI collects service line, patient name, and reason for call
3. Call transfers to the on-call nurse
4. Vapi records and transcribes the full conversation
5. When the call ends, Vapi fires a webhook to this server
6. This server sends the transcript to GPT-4o-mini
7. GPT generates a formatted SOAP note
8. Twilio texts the SOAP note to the on-call nurse's cell

**The nurse receives the SOAP note within 30 seconds of hanging up.**

---

## Deployment (Railway — Recommended, ~5 minutes)

### Step 1 — Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Create new project
railway init

# Deploy
railway up
```

### Step 2 — Set Environment Variables on Railway

In the Railway dashboard, go to your project → Variables and add:

```
OPENAI_API_KEY=sk-your-openai-key-here
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_FROM_NUMBER=+14172036466
DEFAULT_NURSE_NUMBER=+1xxxxxxxxxx
```

### Step 3 — Get Your Public URL

Railway gives you a URL like: `https://haven-soap-note-production.up.railway.app`

Your webhook URL will be: `https://haven-soap-note-production.up.railway.app/vapi-webhook`

### Step 4 — Configure Vapi to Send Webhooks

1. Go to [dashboard.vapi.ai](https://dashboard.vapi.ai)
2. Open the Haven After-Hours Routing Agent
3. Click **Advanced** settings
4. Under **Server URL**, enter your webhook URL:
   `https://YOUR-RAILWAY-URL.up.railway.app/vapi-webhook`
5. Save

That's it. Every call will now generate and send a SOAP note automatically.

---

## Alternative Deployment (Render — Also Free Tier Available)

1. Push code to a GitHub repo
2. Go to [render.com](https://render.com) → New Web Service
3. Connect your GitHub repo
4. Set environment variables in Render dashboard
5. Deploy — Render gives you a public URL automatically

---

## Testing

### Test SOAP Note Generation (without a real call)

```bash
curl -X POST https://YOUR-URL/test-soap \
  -H "Content-Type: application/json" \
  -d '{"send_sms": true, "nurse_number": "+14172396812"}'
```

This sends a sample transcript through the full pipeline and texts the SOAP note to the nurse number.

### Health Check

```bash
curl https://YOUR-URL/health
```

---

## Sample SOAP Note Output

```
📋 HAVEN ON-CALL NOTE
────────────────────
Dorothy Williams — Home Health
May 15, 2026 at 2:14 AM

S: Caller (patient's daughter) reports Dorothy has pain at left leg wound site, rated 7/10. 
Dressing appears saturated and yellowish. Last dressing change was yesterday at 2:00 PM.

O: Call received at 2:14 AM. Caller is patient's daughter. Temperature reported at 99.8°F 
approximately 1 hour prior to call.

A: Wound site showing possible signs of infection (saturated dressing with discoloration, 
low-grade fever, increased pain). Non-emergent but requires next-day assessment.

P: Advised to keep dressing intact overnight, elevate leg, administer prescribed pain 
medication if due. Wound visit scheduled for first thing tomorrow morning. 
Caller instructed to call 911 or go to ER if temp exceeds 101°F or pain significantly worsens.
```

---

## Future Enhancements (Phase 2)

- **On-call schedule integration**: Automatically route SOAP note to the correct nurse based on who is on call that night (pulled from a Google Sheet or simple database)
- **MatrixCare integration**: Automatically pre-populate the note in the patient's chart
- **Urgency scoring**: Flag high-urgency calls for immediate supervisor notification
- **Email delivery**: Send SOAP note to nurse's email in addition to SMS for easier copy-paste into MatrixCare
