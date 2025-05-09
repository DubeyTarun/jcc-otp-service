
# OTP Sender API

This API allows sending and verifying OTPs using AWS SNS and Redis, built with Flask.

---

## 🚀 Setup

### 1. Clone & Install:
```bash
git clone https://github.com/your-username/otp-sender-api.git
cd otp-sender-api
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 2. Add `.env` file:
Create a `.env` file and add the following environment variables:
```
REDIS_URL=redis://localhost:6379  # Run Redis on Docker or locally
AWS_REGION=your_aws_region
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
```

### 3. Run the server:
```bash
python app.py
```

---

## 📮 Endpoints

### POST /send-otp

**Request JSON:**
```json
{
  "phone_number": "target_phone_number"
}
```

**Response:**
```json
{
  "otp_sent_to": "target_phone_number",
  "status": "success"
}
```

**Note**: The phone number should be provided in the local format (e.g., `8209998944`). The system will automatically prefix `+91` to the phone number before sending the OTP.

### POST /verify-otp

**Request JSON:**
```json
{
  "phone_number": "target_phone_number",
  "otp": "received_otp"
}
```

**Response:**
```json
{
  "message": "OTP verified successfully",
  "status": "success"
}
```

---

## 🛡️ Notes

- OTPs are stored in Redis with a 5-minute expiry.
- `.env` file should be added to `.gitignore` for security reasons.
- Make sure to set up Redis and AWS SNS with appropriate access credentials.
- The phone number provided in the `send-otp` and `verify-otp` endpoints should follow E.164 format, and the system will automatically prepend the `+91` country code if needed.

