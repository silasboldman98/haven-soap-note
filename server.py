"""
Haven SOAP Note Generator
=========================
Receives Vapi end-of-call webhooks, generates a clinical SOAP note
from the full call transcript (AI intake + nurse-patient conversation),
and texts it to the on-call nurse via Twilio SMS.

Architecture:
- Vapi fires a POST to /vapi-webhook when a call ends
- This server extracts the transcript, sends it to GPT-4o
- GPT-4o generates a formatted SOAP note
- Twilio sends the SOAP note as an SMS to the on-call nurse

Deploy on Railway / Render / Fly.io — see README.md
"""

import os
import json
import logging
from flask import Flask, request, jsonify
from openai import OpenAI
from twilio.rest import Client as TwilioClient
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Configuration (set as environment variables on your server) ───────────────
OPENAI_API_KEY      = os.environ.get('OPENAI_API_KEY', '')
TWILIO_ACCOUNT_SID  = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN   = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_FROM_NUMBER  = os.environ.get('TWILIO_FROM_NUMBER', '')
# On-call nurse number — in production this should come from a schedule/database
# For demo, defaults to Silas's cell
DEFAULT_NURSE_NUMBER = os.environ.get('DEFAULT_NURSE_NUMBER', '')
# Vapi webhook secret (optional but recommended for security)
VAPI_WEBHOOK_SECRET = os.environ.get('VAPI_WEBHOOK_SECRET', '')
# ──────────────────────────────────────────────────────────────────────────────

openai_client = OpenAI(api_key=OPENAI_API_KEY)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def extract_transcript(vapi_payload: dict) -> str:
    """Extract clean transcript text from Vapi webhook payload."""
    transcript = ""
    
    # Vapi sends transcript in message.artifact.transcript or message.transcript
    message = vapi_payload.get('message', {})
    
    # Try artifact first (end-of-call report)
    artifact = message.get('artifact', {})
    if artifact.get('transcript'):
        return artifact['transcript']
    
    # Try messages array
    messages = artifact.get('messages', [])
    if messages:
        lines = []
        for msg in messages:
            role = msg.get('role', 'unknown').capitalize()
            content = msg.get('message', msg.get('content', ''))
            if content and role in ['User', 'Assistant', 'Bot']:
                speaker = 'Patient/Caller' if role == 'User' else 'Haven AI'
                lines.append(f"{speaker}: {content}")
        return '\n'.join(lines)
    
    # Fallback: raw transcript string
    if message.get('transcript'):
        return message['transcript']
    
    return ""


def generate_soap_note(transcript: str, call_metadata: dict) -> str:
    """Generate a clinical SOAP note from the call transcript using GPT-4o."""
    
    call_time = call_metadata.get('call_time', datetime.now().strftime('%B %d, %Y at %I:%M %p'))
    service_line = call_metadata.get('service_line', 'Unknown')
    patient_name = call_metadata.get('patient_name', 'Unknown')
    
    prompt = f"""You are a clinical documentation assistant for a home health agency. 
You have been given a transcript of an after-hours on-call phone call between a nurse and a patient or their caregiver.

Generate a concise, professional SOAP note from this transcript. 
This note will be texted to the nurse immediately after the call so they can paste it into MatrixCare.

SOAP NOTE FORMAT:
S (Subjective): What the patient/caregiver reported — symptoms, concerns, complaints in their own words
O (Objective): Observable/factual information — time of call, who called, vital signs if mentioned, any measurable data
A (Assessment): Clinical impression of urgency and situation based on what was reported
P (Plan): What was discussed, advised, or arranged during the call

RULES:
- Be concise. This is a text message. Each section should be 1-3 sentences max.
- Use clinical language appropriate for a home health SOAP note.
- If information for a section was not mentioned in the call, write "Not reported."
- Do NOT add information that was not in the transcript.
- Start with patient name and call time on the first line.

CALL METADATA:
- Date/Time: {call_time}
- Service Line: {service_line}
- Patient Name: {patient_name}

TRANSCRIPT:
{transcript}

Generate the SOAP note now:"""

    response = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a precise clinical documentation assistant. Generate accurate, concise SOAP notes from call transcripts."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
        max_tokens=500
    )
    
    return response.choices[0].message.content.strip()


def send_soap_note_sms(soap_note: str, nurse_number: str, patient_name: str) -> bool:
    """Send the SOAP note as an SMS to the on-call nurse."""
    
    header = f"📋 HAVEN ON-CALL NOTE\n{'─'*20}\n"
    message_body = header + soap_note
    
    # SMS has a 1600 char limit — truncate gracefully if needed
    if len(message_body) > 1580:
        message_body = message_body[:1577] + "..."
    
    try:
        message = twilio_client.messages.create(
            body=message_body,
            from_=TWILIO_FROM_NUMBER,
            to=nurse_number
        )
        logger.info(f"SOAP note SMS sent to {nurse_number} — SID: {message.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to send SMS to {nurse_number}: {e}")
        return False


def extract_call_metadata(vapi_payload: dict) -> dict:
    """Extract useful metadata from the Vapi payload."""
    message = vapi_payload.get('message', {})
    call = message.get('call', {})
    
    # Try to extract patient name and service line from transcript summary
    # Vapi sometimes includes structured data from tool calls
    artifact = message.get('artifact', {})
    tool_calls = artifact.get('toolCallResults', [])
    
    metadata = {
        'call_time': datetime.now().strftime('%B %d, %Y at %I:%M %p'),
        'call_id': call.get('id', 'unknown'),
        'caller_number': call.get('customer', {}).get('number', 'unknown'),
        'duration_seconds': message.get('durationSeconds', 0),
        'patient_name': 'See transcript',
        'service_line': 'See transcript'
    }
    
    # Try to get structured data from the AI's tool calls during the call
    messages = artifact.get('messages', [])
    for msg in messages:
        if msg.get('role') == 'tool_call_result':
            # Tool calls may have captured patient name and service line
            pass
    
    return metadata


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'Haven SOAP Note Generator'})


@app.route('/vapi-webhook', methods=['POST'])
def vapi_webhook():
    """
    Main webhook endpoint — receives Vapi end-of-call events.
    Configure this URL in Vapi dashboard under:
    Assistant Settings → Advanced → Server URL
    """
    try:
        payload = request.get_json(force=True)
        
        if not payload:
            return jsonify({'error': 'No payload'}), 400
        
        message = payload.get('message', {})
        event_type = message.get('type', '')
        
        logger.info(f"Vapi webhook received: type={event_type}")
        
        # We only care about end-of-call reports
        if event_type != 'end-of-call-report':
            return jsonify({'status': 'ignored', 'type': event_type}), 200
        
        # Extract transcript
        transcript = extract_transcript(payload)
        
        if not transcript or len(transcript.strip()) < 50:
            logger.warning("Transcript too short or empty — skipping SOAP note generation")
            return jsonify({'status': 'skipped', 'reason': 'transcript too short'}), 200
        
        logger.info(f"Transcript length: {len(transcript)} chars")
        
        # Extract call metadata
        metadata = extract_call_metadata(payload)
        
        # Determine which nurse to notify
        # In production: look up on-call schedule from database
        # For demo: use default nurse number
        nurse_number = DEFAULT_NURSE_NUMBER
        
        # Generate SOAP note
        logger.info("Generating SOAP note...")
        soap_note = generate_soap_note(transcript, metadata)
        logger.info(f"SOAP note generated ({len(soap_note)} chars)")
        
        # Send SMS to nurse
        success = send_soap_note_sms(soap_note, nurse_number, metadata.get('patient_name', 'Patient'))
        
        if success:
            logger.info(f"✅ SOAP note sent to {nurse_number}")
            return jsonify({
                'status': 'success',
                'soap_note_length': len(soap_note),
                'sent_to': nurse_number
            }), 200
        else:
            logger.error("Failed to send SOAP note SMS")
            return jsonify({'status': 'error', 'reason': 'SMS send failed'}), 500
            
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/test-soap', methods=['POST'])
def test_soap():
    """
    Test endpoint — send a sample transcript to verify SOAP note generation.
    POST with JSON: {"transcript": "...", "nurse_number": "+1..."}
    """
    data = request.get_json(force=True)
    transcript = data.get('transcript', '')
    nurse_number = data.get('nurse_number', DEFAULT_NURSE_NUMBER)
    
    if not transcript:
        # Use a sample transcript for testing
        transcript = """Haven AI: Thank you for calling Haven. This is Haven's after-hours service. Are you calling for a Home Health patient, a Hospice patient, or a Home Care patient?
Caller: Home health.
Haven AI: And what is the patient's name?
Caller: Dorothy Williams.
Haven AI: Can you briefly tell me what's going on tonight?
Caller: She's been having a lot of pain in her left leg, around the wound site. She says it's a seven out of ten and the dressing looks like it might be soaked through.
Haven AI: Thank you. I'm connecting you with the Home Health nurse on call right now. Please hold.
Nurse: This is Silas, on-call nurse. What's going on with Dorothy?
Caller: She's my mom. Her wound on her left leg is really hurting her, she says about a seven out of ten, and the bandage looks wet and kind of yellowish.
Nurse: Okay, when was the dressing last changed?
Caller: Yesterday afternoon, around two o'clock.
Nurse: Any fever? Is she running a temperature?
Caller: I took it about an hour ago, it was 99.8.
Nurse: Alright. I want you to do a few things. First, don't remove the dressing tonight. Keep the leg elevated on a pillow. Give her the pain medication she has prescribed if she hasn't taken it in the last four hours. I'm going to flag this for a visit first thing tomorrow morning, we'll get that wound assessed. If her temperature goes above 101 or the pain gets significantly worse, call 911 or take her to the ER. Do you understand?
Caller: Yes, thank you so much.
Nurse: Of course. You did the right thing calling. Goodnight."""
    
    metadata = {
        'call_time': datetime.now().strftime('%B %d, %Y at %I:%M %p'),
        'patient_name': 'Dorothy Williams',
        'service_line': 'Home Health'
    }
    
    soap_note = generate_soap_note(transcript, metadata)
    
    # Optionally send SMS
    send_sms = data.get('send_sms', False)
    sms_sent = False
    if send_sms:
        sms_sent = send_soap_note_sms(soap_note, nurse_number, 'Dorothy Williams')
    
    return jsonify({
        'soap_note': soap_note,
        'sms_sent': sms_sent,
        'transcript_length': len(transcript)
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
