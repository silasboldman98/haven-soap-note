"""
Haven SOAP Note Generator
=========================
Receives Vapi end-of-call webhooks, generates a clinical SOAP note
from the full call transcript, and delivers it via email (Resend) and SMS (Twilio).
"""

import os
import logging
import resend
from flask import Flask, request, jsonify
from openai import OpenAI
from twilio.rest import Client as TwilioClient
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────
OPENAI_API_KEY       = os.environ.get('OPENAI_API_KEY', '')
TWILIO_ACCOUNT_SID   = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN    = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_FROM_NUMBER   = os.environ.get('TWILIO_FROM_NUMBER', '')
DEFAULT_NURSE_NUMBER = os.environ.get('DEFAULT_NURSE_NUMBER', '')
VAPI_WEBHOOK_SECRET  = os.environ.get('VAPI_WEBHOOK_SECRET', '')
RESEND_API_KEY       = os.environ.get('RESEND_API_KEY', '')
NURSE_EMAIL          = os.environ.get('NURSE_EMAIL', 'silasaboldman@gmail.com')
# ───────────────────────────────────────────────────────────────────────────────

openai_client = OpenAI(api_key=OPENAI_API_KEY)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
resend.api_key = RESEND_API_KEY


def extract_transcript(vapi_payload: dict) -> str:
    """Extract clean transcript text from Vapi webhook payload."""
    message = vapi_payload.get('message', {})
    artifact = message.get('artifact', {})

    if artifact.get('transcript'):
        return artifact['transcript']

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

    if message.get('transcript'):
        return message['transcript']

    return ""


def generate_soap_note(transcript: str, call_metadata: dict) -> str:
    """Generate a clinical SOAP note from the call transcript."""
    call_time    = call_metadata.get('call_time', datetime.now().strftime('%B %d, %Y at %I:%M %p'))
    service_line = call_metadata.get('service_line', 'Unknown')
    patient_name = call_metadata.get('patient_name', 'Unknown')

    prompt = f"""You are a clinical documentation assistant for a home health agency.
You have been given a transcript of an after-hours on-call phone call.

Generate a concise, professional SOAP note from this transcript.
This note will be emailed to the nurse immediately after the call so they can paste it into MatrixCare.

SOAP NOTE FORMAT:
S (Subjective): What the patient/caregiver reported — symptoms, concerns, complaints
O (Objective): Observable/factual information — time of call, who called, vital signs if mentioned
A (Assessment): Clinical impression of urgency based on what was reported
P (Plan): What was discussed, advised, or arranged during the call

RULES:
- Be concise. Each section should be 1-3 sentences max.
- Use clinical language appropriate for a home health SOAP note.
- If information for a section was not mentioned, write "Not reported."
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


def send_soap_note_email(soap_note: str, recipient_email: str, patient_name: str, call_time: str) -> bool:
    """Send the SOAP note as a formatted email via Resend."""
    try:
        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
  <div style="background: #1a2744; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
    <h2 style="margin: 0; font-size: 18px;">&#128203; Haven On-Call SOAP Note</h2>
    <p style="margin: 4px 0 0 0; font-size: 13px; opacity: 0.8;">{call_time}</p>
  </div>
  <div style="background: white; padding: 24px; border-radius: 0 0 8px 8px; border: 1px solid #ddd;">
    <pre style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.8; white-space: pre-wrap; margin: 0; color: #222;">{soap_note}</pre>
    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
    <p style="font-size: 11px; color: #999; margin: 0;">
      Generated by Haven AI After-Hours Service &nbsp;|&nbsp; Copy and paste into MatrixCare
    </p>
  </div>
</body>
</html>"""

        plain_text = f"HAVEN ON-CALL SOAP NOTE\n{'='*40}\n\n{soap_note}\n\n{'='*40}\nGenerated by Haven AI. Copy and paste into MatrixCare."

        params = {
            "from": "Haven AI <onboarding@resend.dev>",
            "to": [recipient_email],
            "subject": f"Haven On-Call Note — {patient_name} — {call_time}",
            "html": html_body,
            "text": plain_text,
        }
        result = resend.Emails.send(params)
        logger.info(f"✅ Email sent to {recipient_email} — ID: {result}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {recipient_email}: {e}")
        return False


def send_soap_note_sms(soap_note: str, nurse_number: str, patient_name: str) -> bool:
    """Send the SOAP note as SMS (secondary/best-effort delivery)."""
    header = f"HAVEN ON-CALL NOTE\n{'─'*20}\n"
    message_body = header + soap_note
    if len(message_body) > 1580:
        message_body = message_body[:1577] + "..."
    try:
        message = twilio_client.messages.create(
            body=message_body,
            from_=TWILIO_FROM_NUMBER,
            to=nurse_number
        )
        logger.info(f"SMS sent to {nurse_number} — SID: {message.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to send SMS to {nurse_number}: {e}")
        return False


def extract_call_metadata(vapi_payload: dict) -> dict:
    """Extract useful metadata from the Vapi payload."""
    message = vapi_payload.get('message', {})
    call = message.get('call', {})
    return {
        'call_time': datetime.now().strftime('%B %d, %Y at %I:%M %p'),
        'call_id': call.get('id', 'unknown'),
        'caller_number': call.get('customer', {}).get('number', 'unknown'),
        'duration_seconds': message.get('durationSeconds', 0),
        'patient_name': 'See transcript',
        'service_line': 'See transcript'
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'Haven SOAP Note Generator'})


@app.route('/vapi-webhook', methods=['POST'])
def vapi_webhook():
    """Main webhook — receives Vapi end-of-call events."""
    try:
        payload = request.get_json(force=True)
        if not payload:
            return jsonify({'error': 'No payload'}), 400

        message = payload.get('message', {})
        event_type = message.get('type', '')
        logger.info(f"Vapi webhook received: type={event_type}")

        if event_type != 'end-of-call-report':
            return jsonify({'status': 'ignored', 'type': event_type}), 200

        transcript = extract_transcript(payload)
        if not transcript or len(transcript.strip()) < 50:
            logger.warning("Transcript too short — skipping SOAP note generation")
            return jsonify({'status': 'skipped', 'reason': 'transcript too short'}), 200

        logger.info(f"Transcript length: {len(transcript)} chars")
        metadata = extract_call_metadata(payload)

        logger.info("Generating SOAP note...")
        soap_note = generate_soap_note(transcript, metadata)
        logger.info(f"SOAP note generated ({len(soap_note)} chars)")

        # Primary: email via Resend
        email_success = send_soap_note_email(
            soap_note,
            NURSE_EMAIL,
            metadata.get('patient_name', 'Patient'),
            metadata.get('call_time', '')
        )

        # Secondary: SMS via Twilio (best-effort)
        sms_success = send_soap_note_sms(
            soap_note,
            DEFAULT_NURSE_NUMBER,
            metadata.get('patient_name', 'Patient')
        )

        if email_success or sms_success:
            logger.info(f"✅ Delivered — email: {email_success}, sms: {sms_success}")
            return jsonify({
                'status': 'success',
                'email_sent': email_success,
                'sms_sent': sms_success,
                'soap_note_length': len(soap_note)
            }), 200
        else:
            return jsonify({'status': 'error', 'reason': 'all delivery failed'}), 500

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/test-soap', methods=['POST'])
def test_soap():
    """Test endpoint — verify SOAP note generation and email delivery."""
    data = request.get_json(force=True)
    transcript   = data.get('transcript', '')
    nurse_number = data.get('nurse_number', DEFAULT_NURSE_NUMBER)
    nurse_email  = data.get('nurse_email', NURSE_EMAIL)

    if not transcript:
        transcript = """Haven AI: Thank you for calling Haven. Are you calling for a Home Health, Hospice, or Home Care patient?
Caller: Home health.
Haven AI: What is the patient's name?
Caller: Dorothy Williams.
Haven AI: Can you briefly tell me what's going on tonight?
Caller: She has pain in her left leg wound site, about seven out of ten. Dressing looks soaked through.
Haven AI: I'm connecting you with the Home Health nurse on call now. Please hold.
Nurse: This is Silas, on-call. What's going on with Dorothy?
Caller: Her wound on her left leg is really hurting, about seven out of ten, bandage looks wet and yellowish.
Nurse: When was the dressing last changed?
Caller: Yesterday afternoon around two.
Nurse: Any fever?
Caller: 99.8 about an hour ago.
Nurse: Don't remove the dressing tonight. Keep the leg elevated. Give her prescribed pain medication if due. I'll flag this for a visit first thing tomorrow. If temp goes above 101 or pain gets significantly worse, call 911 or go to ER.
Caller: Thank you so much.
Nurse: You did the right thing calling. Goodnight."""

    patient_name = data.get('patient_name', 'Dorothy Williams')
    service_line = data.get('service_line', 'Home Health')
    call_time    = datetime.now().strftime('%B %d, %Y at %I:%M %p')

    metadata = {'call_time': call_time, 'patient_name': patient_name, 'service_line': service_line}
    soap_note = generate_soap_note(transcript, metadata)

    email_sent = False
    sms_sent   = False

    if data.get('send_email', True):
        email_sent = send_soap_note_email(soap_note, nurse_email, patient_name, call_time)
    if data.get('send_sms', False):
        sms_sent = send_soap_note_sms(soap_note, nurse_number, patient_name)

    return jsonify({
        'soap_note': soap_note,
        'email_sent': email_sent,
        'sms_sent': sms_sent,
        'transcript_length': len(transcript)
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
