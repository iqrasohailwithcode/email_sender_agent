import os
import pickle
import base64
import datetime
import json
import re
import requests
from email.mime.text import MIMEText
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Load ENV
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Gmail Config
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

COUNTER_FILE = "email_counter.json"   # daily counter store karne ke liye file

# Load / Reset Daily Counter
def load_daily_counter():
    today = datetime.date.today().isoformat()
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") == today:
            return data.get("count", 0)
    return 0

def save_daily_counter(count):
    today = datetime.date.today().isoformat()
    with open(COUNTER_FILE, "w") as f:
        json.dump({"date": today, "count": count}, f)

# Gmail Auth
def get_gmail_service():
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("gmail", "v1", credentials=creds)

# Fetch Unread Emails
def get_unread_emails(service):
    results = service.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        maxResults=50   # pehle 50 fetch
    ).execute()
    messages = results.get("messages", [])
    return messages

# Extract Email Details
def get_email_details(service, msg_id):
    message = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = message["payload"]["headers"]
    subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
    sender = next((h["value"] for h in headers if h["name"] == "From"), "")
    body = ""
    if "data" in message["payload"]["body"]:
        body = base64.urlsafe_b64decode(message["payload"]["body"]["data"]).decode("utf-8")
    elif "parts" in message["payload"]:
        for part in message["payload"]["parts"]:
            if part["mimeType"] == "text/plain" and "data" in part["body"]:
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                break
    return subject, sender, body

# Extract only email address
def extract_email(sender):
    match = re.search(r'<(.*?)>', sender)
    return match.group(1) if match else sender

# Generate AI Reply (via OpenRouter)
def generate_reply(sender, subject, body):
    sender_name = sender.split("<")[0].strip().replace('"', "")
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")  # 👈 AM/PM format
    prompt = f"""
You are an AI email assistant. Write a professional, polite reply.

Sender: {sender_name}
Subject: {subject}
Received At: {current_time}
Message: {body}

Reply politely and professionally. At the end, include:
---
Best regards,
Sohail Nawaz
Date & Time: {current_time}
---
"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "mistralai/mistral-7b-instruct:free",
        "messages": [
            {"role": "system", "content": "You are an AI email assistant. Keep replies polite and professional."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        print("⚠️ Error generating reply:", e)
        return "Sorry, I couldn’t generate a proper reply at the moment."

# Send Email
def send_reply(service, to, subject, message_text):
    message = MIMEText(message_text)
    message["to"] = to
    message["subject"] = f"Re: {subject}"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    message = {"raw": raw}
    service.users().messages().send(userId="me", body=message).execute()

# Mark Email as Read
def mark_as_read(service, msg_id):
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()

# Main Function
def main():
    service = get_gmail_service()
    messages = get_unread_emails(service)

    daily_count = load_daily_counter()
    max_daily = 20  # 👈 Rozana max 20 emails

    print(f"📊 Today replies so far: {daily_count}/{max_daily}")

    if not messages:
        print("📭 No new unread emails.")
        return

    for msg in messages:
        if daily_count >= max_daily:
            print("⏳ Daily limit reached (20 emails). Try again tomorrow.")
            break

        subject, sender, body = get_email_details(service, msg["id"])
        print(f"\n📨 Processing email from {sender} with subject '{subject}'")

        reply = generate_reply(sender, subject, body)
        print(f"🤖 Generated reply:\n{reply}\n")

        to_email = extract_email(sender)
        send_reply(service, to_email, subject, reply)
        mark_as_read(service, msg["id"])
        print(f"✅ Reply sent to {to_email}")
        print(f"📩 Marked email {msg['id']} as read.\n")

        daily_count += 1
        save_daily_counter(daily_count)
        print(f"📊 Updated counter: {daily_count}/{max_daily}")

if __name__ == "__main__":
    main()
